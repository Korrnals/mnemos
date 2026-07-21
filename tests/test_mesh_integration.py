"""Integration tests for the M3 gRPC client against the LIVE M2 mesh binary.

These tests start the real ``mnemos-mesh`` Go binary (built by
``make build`` in ``mnemos-mesh/``) with a per-test mTLS config, generate a
test CA + node cert + key in-process using the ``cryptography`` library
(already a mnemos dependency), and exercise :class:`MeshClient` over the
Unix socket the binary creates.

The M2 binary is a stub: it returns ``UNIMPLEMENTED`` for all RPCs except
``Heartbeat``. The tests accept both the happy path and the UNIMPLEMENTED
degradation where the contract permits it.

No cert fixtures are committed — everything is generated in ``tmp_path``
and discarded at the end of each test.

Architectural note (2026-07-22): the M2 mesh binary serves the
``FederationPeer`` API (peer-to-peer, mTLS TCP) and dials OUT to mnemos
as a client over the Unix socket. It does NOT serve ``MnemosCore`` (the
API :class:`MeshClient` speaks) on the Unix socket — that is mnemos's
job, which lands in M4. Until M4 ships, the integration tests skip with
``M2 serves FederationPeer, not MnemosCore on the Unix socket`` so CI
stays green without a flaky timeout.
"""

from __future__ import annotations

import datetime
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from mnemos.mesh_client import (
    MeshClient,
    MeshUnavailableError,
    MeshUnimplementedError,
)

# ── Binary detection ──────────────────────────────────────────────────────────

M2_BINARY = Path(__file__).resolve().parent.parent.parent / "mnemos-mesh" / "bin" / "mnemos-mesh"

pytestmark = pytest.mark.skipif(
    not M2_BINARY.exists(),
    reason="M2 binary missing — run 'make build' in mnemos-mesh",
)


# ── mTLS cert generation (in-process, no committed fixtures) ──────────────────


def _generate_test_certs(cert_dir: Path) -> dict[str, Path]:
    """Generate a test CA, node cert, and node key in ``cert_dir``.

    Uses the ``cryptography`` library (already a mnemos dependency for
    Fernet TOTP). All certificate Common Names are prefixed with
    ``test-`` so a misconfigured production scanner can never mistake
    them for real material. The keys are RSA-2048 — fast enough for a
    test, strong enough that no linter flags them as weak.

    Returns a dict mapping the role (``ca_cert``, ``node_cert``,
    ``node_key``) to its file path under ``cert_dir``.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_dir.mkdir(parents=True, exist_ok=True)

    # ── CA ───────────────────────────────────────────────────────────────────
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-mnemos-mesh-ca")])
    ca_cert_obj = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    ca_cert_path = cert_dir / "ca.crt"
    ca_cert_path.write_bytes(ca_cert_obj.public_bytes(serialization.Encoding.PEM))

    # ── Node cert ────────────────────────────────────────────────────────────
    node_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    node_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-node")])
    node_cert_obj = (
        x509.CertificateBuilder()
        .subject_name(node_subject)
        .issuer_name(ca_subject)
        .public_key(node_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("test-node")]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    node_cert_path = cert_dir / "node.crt"
    node_cert_path.write_bytes(node_cert_obj.public_bytes(serialization.Encoding.PEM))

    node_key_path = cert_dir / "node.key"
    node_key_path.write_bytes(
        node_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    # Lock the key file so a linter does not flag world-readable secrets.
    os.chmod(node_key_path, 0o600)

    return {
        "ca_cert": ca_cert_path,
        "node_cert": node_cert_path,
        "node_key": node_key_path,
    }


def _free_tcp_port() -> int:
    """Reserve and immediately release a free TCP port for the mesh to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    return port


# ── Fixture: start the M2 binary ──────────────────────────────────────────────


@pytest.fixture
def mesh_binary(tmp_path: Path) -> Generator[str, None, None]:
    """Start the M2 mesh binary and yield the Unix socket path.

    Writes a minimal ``mesh.yaml`` config to ``tmp_path`` with mTLS
    enabled using certs generated in ``tmp_path/certs``. The mesh binds
    a Unix socket for the core API (what MeshClient connects to) and a
    throwaway TCP port for the peer API (not exercised here).

    Teardown terminates the process, waits up to 5s, and cleans up the
    tmp files. If the binary fails to start, the fixture fails fast with
    the captured stderr.

    M2 caveat: the M2 binary serves the ``FederationPeer`` API on mTLS
    TCP and dials OUT to mnemos as a client — it does NOT serve
    ``MnemosCore`` on the Unix socket. The socket path in the config is
    where the binary *dials*, not where it *listens*. MnemosCore-on-Unix
    lands in M4. This fixture starts the binary anyway (so the cert
    generation + subprocess wiring is exercised) and skips the test if
    the socket never appears, with a message pointing at M4.
    """
    socket_path = tmp_path / "core.sock"
    listen_port = _free_tcp_port()
    cert_paths = _generate_test_certs(tmp_path / "certs")

    config = {
        "node_id": "test-node",
        "listen": f"127.0.0.1:{listen_port}",
        "unix_socket": str(socket_path),
        "peers": [],
        "mtls": {
            "ca_cert": str(cert_paths["ca_cert"]),
            "node_cert": str(cert_paths["node_cert"]),
            "node_key": str(cert_paths["node_key"]),
        },
    }

    import yaml  # pyyaml is a mnemos core dependency

    config_path = tmp_path / "mesh.yaml"
    config_path.write_text(yaml.safe_dump(config))

    proc = subprocess.Popen(
        [str(M2_BINARY), "serve", "-config", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for the socket to appear. At M2 the binary does not create it
    # (it dials out, not listens); at M4+ it will. Skip cleanly if absent.
    deadline = time.monotonic() + 10.0
    try:
        while time.monotonic() < deadline:
            if socket_path.exists():
                yield str(socket_path)
                return
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                pytest.fail(f"M2 mesh binary exited early (rc={proc.returncode}):\n{stderr}")
            time.sleep(0.1)
        pytest.skip(
            "M2 serves FederationPeer on mTLS TCP, not MnemosCore on the "
            "Unix socket — MnemosCore-on-Unix lands in M4"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if cert_paths["node_key"].exists():
            cert_paths["node_key"].unlink(missing_ok=True)
        shutil.rmtree(tmp_path / "certs", ignore_errors=True)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_heartbeat_round_trip(mesh_binary: str) -> None:
    """Heartbeat against the live M2 binary returns a non-empty version string.

    The M2 stub implements Heartbeat (the only RPC it wires); the version
    string is the build version of the Go binary. We assert it is non-empty
    so a future change that drops the version field is caught.
    """
    with MeshClient(mesh_binary, timeout=5.0) as client:
        healthy, version, _uptime = client.heartbeat("test-peer", component="mnemos")

    assert healthy is True
    assert isinstance(version, str)
    assert version != ""


def test_list_memories_empty_or_unimplemented(mesh_binary: str) -> None:
    """ListMemories returns an empty list OR raises MeshUnimplementedError.

    At M2 both outcomes are contract-valid: the stub may return an empty
    page or it may return UNIMPLEMENTED. Any other outcome is a bug.
    """
    with MeshClient(mesh_binary, timeout=5.0) as client:
        try:
            records = client.list_memories(projects=["mnemos"])
        except MeshUnimplementedError:
            return  # acceptable at M2
        except MeshUnavailableError:
            pytest.fail("ListMemories returned UNAVAILABLE — mesh socket down")

    assert isinstance(records, list)
    assert all(hasattr(r, "id") for r in records)


def test_write_memory_unimplemented(mesh_binary: str) -> None:
    """WriteMemory raises MeshUnimplementedError (M2 returns UNIMPLEMENTED for all writes).

    The M2 stub does not implement the write path; it returns UNIMPLEMENTED.
    The client must surface this as MeshUnimplementedError so the caller can
    degrade gracefully (fall back to local storage).
    """
    from mnemos.compact import CompactRecord

    record = CompactRecord(
        id="fed:test-agent:uuid-1",
        type="decision",
        title="test record",
        summary="generated by integration test",
        key_points=["one"],
        tags=["project:mnemos", "agent:test-agent", "mnemos:decision"],
        source_agent="test-agent",
        timestamp="2026-07-22T00:00:00Z",
    )

    with MeshClient(mesh_binary, timeout=5.0) as client, pytest.raises(MeshUnimplementedError):
        client.write_memory(record, import_mode="MERGE")
