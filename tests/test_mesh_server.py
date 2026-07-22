"""Unit tests for the MnemosCore gRPC server (#105 M4.0).

Exercises :class:`mnemos.mesh_server.MeshServer` end-to-end over a real
gRPC Unix socket on a ``tmp_path`` — no mocks on the gRPC layer. The
tests cover:

* Server starts on a Unix socket and stops cleanly.
* :rpc:`Heartbeat` returns a non-empty version + ``healthy=True``.
* :rpc:`ListMemories` returns seeded records (moderation-processed).
* :rpc:`ListMemories` with a disallowed ``project_scope`` → ACL refuses
  (empty response, ``PERMISSION_DENIED``).
* :rpc:`WriteMemory` writes to SQLite (verified via a direct DB query).
* :rpc:`WriteMemory` with a disallowed scope → ``PERMISSION_DENIED``.
* :rpc:`GetSubscriptionState` returns an empty cursor (M4: mesh-side
  persistence in M5) and enforces the ACL.
* Server lifecycle (start/stop cleanly, socket cleanup, context manager).

The tests use the real :class:`MemoryManager` against a tmp SQLite store
so the moderation pipeline + Layer 1 secrets scanner run for real.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import grpc
import pytest

from mnemos import _mesh_gen
from mnemos.compact import CompactRecord
from mnemos.config import FederationConfig, PeerConfig, Settings
from mnemos.manager import MemoryManager
from mnemos.mesh_server import MeshServer
from mnemos.models import MemoryCreate, MemorySource

# ── Constants ────────────────────────────────────────────────────────────────

#: Test project slug — the peer is allowed to access this project only.
_PROJECT = "test-project"

#: A second project the peer is NOT allowed to access — for ACL refusal.
_PROJECT_DENIED = "project-secret"

#: A2A id of the single configured peer (the mesh in these tests).
_PEER_ID = "mnemos-A"

#: Agent slug used in seeded memories + imported records.
_AGENT = "gcw-test-agent"

#: Bearer token env var name — value is irrelevant for the mesh server
#: (auth is via the Unix socket + filesystem perms, not bearer tokens),
#: but :class:`PeerConfig` requires the field.
_TOKEN_ENV = "MNEMOS_FED_PEER_TEST_TOKEN"


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _settings_with_peer(tmp_path: Path, *, allow_wildcard: bool = False) -> Settings:
    """Build a :class:`Settings` with one configured peer + isolated store."""
    allowed = ["*"] if allow_wildcard else [_PROJECT]
    # Pydantic coerces dict kwargs into the nested config models at
    # runtime; the cast keeps mypy --strict happy without changing behaviour.
    settings = Settings(
        **{  # type: ignore[arg-type]  # pydantic dict→model coercion
            "mnemos": {
                "vault_path": str(tmp_path / "vault"),
                "data_dir": str(tmp_path / "data"),
                "db_name": "test_mesh_server.db",
            },
            "embedding": {"provider": "onnx"},
            "scanner": {"enabled": False},
            "federation": FederationConfig(
                shared_projects=[_PROJECT],
                peers={
                    _PEER_ID: PeerConfig(
                        bearer_token_env=_TOKEN_ENV,
                        allowed_projects=allowed,
                        allowed_types=["decision", "learning"],
                        rate_limit_per_minute=600,
                    ),
                },
            ),
        }
    )
    settings.resolve_paths()
    return settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with a single peer allowed to access ``_PROJECT`` only."""
    return _settings_with_peer(tmp_path)


@pytest.fixture
def manager(settings: Settings) -> Generator[MemoryManager, None, None]:
    """Real :class:`MemoryManager` against a tmp SQLite store.

    The embedder is mocked so the tests do not require the ONNX runtime.
    """
    mgr = MemoryManager(settings)
    # Stub the embedder so MemoryManager construction + search work
    # without the real ONNX model download.
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 384
    mgr._embedder = mock_embedder
    yield mgr
    mgr.close()


@pytest.fixture
def seeded_manager(manager: MemoryManager) -> MemoryManager:
    """Manager with two seeded memories (one decision, one learning)."""
    manager.add(
        MemoryCreate(
            content="We chose bearer+TOTP 2FA for remote sessions.",
            title="ADR-0014 auth decision",
            tags=[f"project:{_PROJECT}", f"agent:{_AGENT}", "mnemos:decision"],
            source=MemorySource.MANUAL,
        ),
        project=_PROJECT,
        agent=_AGENT,
    )
    manager.add(
        MemoryCreate(
            content="Rate-limiting slowapi needs a reset between test runs.",
            title="Rate-limit fixture gotcha",
            tags=[f"project:{_PROJECT}", f"agent:{_AGENT}", "mnemos:learning"],
            source=MemorySource.MANUAL,
        ),
        project=_PROJECT,
        agent=_AGENT,
    )
    return manager


@pytest.fixture
def server(
    tmp_path: Path,
    settings: Settings,
    seeded_manager: MemoryManager,
) -> Generator[MeshServer, None, None]:
    """Start a :class:`MeshServer` on a tmp Unix socket and stop it after."""
    socket_path = str(tmp_path / "core.sock")
    srv = MeshServer(socket_path, seeded_manager, settings, max_workers=2)
    srv.start()
    yield srv
    srv.stop(grace=0.5)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _channel(server: MeshServer) -> grpc.Channel:
    """Open an insecure gRPC channel to the running server's Unix socket."""
    return grpc.insecure_channel(f"unix://{server.socket_path}")


def _stub(server: MeshServer) -> Any:
    """Build a ``MnemosCoreStub`` against the running server."""
    return _mesh_gen.core_pb2_grpc.MnemosCoreStub(_channel(server))


def _wait_for_server(server: MeshServer, timeout: float = 2.0) -> None:
    """Block until the server's gRPC channel is ready (or timeout)."""
    channel = _channel(server)
    grpc.channel_ready_future(channel).result(timeout=timeout)


def _make_compact_record(
    *,
    record_id: str = "fed:gcw-test-agent:abc-123",
    project: str = _PROJECT,
    agent: str = _AGENT,
    title: str = "Imported decision",
    summary: str = "A decision imported from a peer via the mesh.",
    tags: list[str] | None = None,
) -> CompactRecord:
    """Build a :class:`CompactRecord` for :rpc:`WriteMemory` tests."""
    if tags is None:
        tags = [f"project:{project}", f"agent:{agent}", "mnemos:decision"]
    return CompactRecord(
        id=record_id,
        type="decision",
        title=title,
        summary=summary,
        key_points=["point one"],
        tags=tags,
        source_agent=agent,
        timestamp="2026-07-22T10:00:00Z",
    )


def _to_proto_record(record: CompactRecord) -> Any:
    """Marshal a :class:`CompactRecord` to the protobuf message for the request."""
    return _mesh_gen.fed_pb2.CompactRecord(
        id=record.id,
        type=record.type,
        title=record.title,
        summary=record.summary,
        key_points=list(record.key_points),
        tags=list(record.tags),
        source_agent=record.source_agent,
        timestamp=record.timestamp,
    )


# ── Lifecycle ────────────────────────────────────────────────────────────────


class TestServerLifecycle:
    def test_server_starts_and_stops_cleanly(
        self,
        tmp_path: Path,
        settings: Settings,
        manager: MemoryManager,
    ) -> None:
        """MeshServer.start() creates the socket; stop() removes it."""
        socket_path = str(tmp_path / "lifecycle.sock")
        srv = MeshServer(socket_path, manager, settings, max_workers=2)
        assert not srv.is_running
        srv.start()
        assert srv.is_running
        assert Path(socket_path).exists()
        srv.stop(grace=0.1)
        assert not srv.is_running
        assert not Path(socket_path).exists()

    def test_context_manager_starts_and_stops(
        self,
        tmp_path: Path,
        settings: Settings,
        manager: MemoryManager,
    ) -> None:
        """MeshServer works as a context manager (start on enter, stop on exit)."""
        socket_path = str(tmp_path / "ctx.sock")
        with MeshServer(socket_path, manager, settings, max_workers=2) as srv:
            assert srv.is_running
            assert Path(socket_path).exists()
        assert not srv.is_running

    def test_double_start_raises(
        self,
        tmp_path: Path,
        settings: Settings,
        manager: MemoryManager,
    ) -> None:
        """Calling start() twice raises RuntimeError (no double-bind)."""
        socket_path = str(tmp_path / "double.sock")
        srv = MeshServer(socket_path, manager, settings, max_workers=2)
        srv.start()
        try:
            with pytest.raises(RuntimeError, match="already started"):
                srv.start()
        finally:
            srv.stop(grace=0.1)

    def test_stop_without_start_is_noop(
        self,
        tmp_path: Path,
        settings: Settings,
        manager: MemoryManager,
    ) -> None:
        """stop() before start() is a safe no-op."""
        socket_path = str(tmp_path / "noop.sock")
        srv = MeshServer(socket_path, manager, settings, max_workers=2)
        srv.stop()  # must not raise


# ── Heartbeat ─────────────────────────────────────────────────────────────────


class TestHeartbeat:
    def test_heartbeat_returns_nonempty_version(self, server: MeshServer) -> None:
        """Heartbeat returns healthy=True + a non-empty version string."""
        _wait_for_server(server)
        stub = _stub(server)
        response = stub.Heartbeat(
            _mesh_gen.core_pb2.HeartbeatRequest(peer_id=_PEER_ID, component="mesh"),
            timeout=2.0,
        )
        assert response.healthy is True
        assert response.version
        assert response.version.startswith("mnemos ")

    def test_heartbeat_uptime_is_nonnegative(self, server: MeshServer) -> None:
        """Uptime seconds is >= 0 for a freshly started server."""
        _wait_for_server(server)
        stub = _stub(server)
        response = stub.Heartbeat(
            _mesh_gen.core_pb2.HeartbeatRequest(peer_id=_PEER_ID, component="mesh"),
            timeout=2.0,
        )
        assert response.uptime_seconds >= 0


# ── ListMemories ─────────────────────────────────────────────────────────────


class TestListMemories:
    def test_list_returns_seeded_memories(self, server: MeshServer) -> None:
        """ListMemories returns the seeded compact records (moderation-processed)."""
        _wait_for_server(server)
        stub = _stub(server)
        response = stub.ListMemories(
            _mesh_gen.core_pb2.ListMemoriesRequest(projects=[_PROJECT]),
            timeout=2.0,
        )
        assert response.total >= 2
        assert len(response.records) >= 2
        # Each record is a CompactRecord with an id, type, title.
        for rec in response.records:
            assert rec.id
            assert rec.type in {"decision", "learning", "session"}
            assert rec.title

    def test_list_acl_denied_scope_refuses(self, server: MeshServer) -> None:
        """ListMemories with a disallowed project → PERMISSION_DENIED."""
        _wait_for_server(server)
        stub = _stub(server)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.ListMemories(
                _mesh_gen.core_pb2.ListMemoriesRequest(projects=[_PROJECT_DENIED]),
                timeout=2.0,
            )
        assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    def test_list_filter_by_type(self, server: MeshServer) -> None:
        """ListMemories with types=["decision"] returns only decision records."""
        _wait_for_server(server)
        stub = _stub(server)
        response = stub.ListMemories(
            _mesh_gen.core_pb2.ListMemoriesRequest(
                projects=[_PROJECT],
                types=["decision"],
            ),
            timeout=2.0,
        )
        assert len(response.records) >= 1
        for rec in response.records:
            assert rec.type == "decision"

    def test_list_no_federate_excluded_by_default(self, server: MeshServer) -> None:
        """Records tagged mnemos:no-federate are excluded by default."""
        _wait_for_server(server)
        # Seed a no-federate record directly in the store.
        servicer = server.servicer
        assert servicer is not None
        servicer._manager.add(
            MemoryCreate(
                content="secret access key AKIAEXAMPLE123",
                title="Should be excluded",
                tags=[
                    f"project:{_PROJECT}",
                    f"agent:{_AGENT}",
                    "mnemos:decision",
                    "mnemos:no-federate",
                ],
                source=MemorySource.MANUAL,
            ),
            project=_PROJECT,
            agent=_AGENT,
        )
        stub = _stub(server)
        response = stub.ListMemories(
            _mesh_gen.core_pb2.ListMemoriesRequest(projects=[_PROJECT]),
            timeout=2.0,
        )
        for rec in response.records:
            assert "mnemos:no-federate" not in list(rec.tags)


# ── WriteMemory ──────────────────────────────────────────────────────────────


class TestWriteMemory:
    def test_write_persists_to_sqlite(self, server: MeshServer) -> None:
        """WriteMemory writes to SQLite — verified via a direct DB query."""
        _wait_for_server(server)
        stub = _stub(server)
        record = _make_compact_record(summary="A clean decision from a peer.")
        response = stub.WriteMemory(
            _mesh_gen.core_pb2.WriteMemoryRequest(
                record=_to_proto_record(record),
                import_mode=_mesh_gen.core_pb2.ImportMode.MERGE,
            ),
            timeout=2.0,
        )
        assert response.written_id
        assert response.trigger_code == _mesh_gen.fed_pb2.TriggerCodes.EXHAUSTIVE
        # Verify the memory landed in SQLite.
        servicer = server.servicer
        assert servicer is not None
        memory = servicer._manager.sqlite.get(response.written_id)
        assert memory is not None
        assert memory.project == _PROJECT
        assert memory.agent == _AGENT
        # The federation id is stored in metadata for traceability.
        assert memory.metadata.get("fed_id") == record.id

    def test_write_acl_denied_returns_refused(self, server: MeshServer) -> None:
        """WriteMemory with a disallowed project → PERMISSION_DENIED."""
        _wait_for_server(server)
        stub = _stub(server)
        record = _make_compact_record(project=_PROJECT_DENIED)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.WriteMemory(
                _mesh_gen.core_pb2.WriteMemoryRequest(
                    record=_to_proto_record(record),
                    import_mode=_mesh_gen.core_pb2.ImportMode.MERGE,
                ),
                timeout=2.0,
            )
        assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    def test_write_unspecified_mode_rejected(self, server: MeshServer) -> None:
        """WriteMemory with UNSPECIFIED import_mode → INVALID_ARGUMENT."""
        _wait_for_server(server)
        stub = _stub(server)
        record = _make_compact_record()
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.WriteMemory(
                _mesh_gen.core_pb2.WriteMemoryRequest(
                    record=_to_proto_record(record),
                    import_mode=_mesh_gen.core_pb2.ImportMode.IMPORT_MODE_UNSPECIFIED,
                ),
                timeout=2.0,
            )
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    def test_write_restore_without_confirm_rejected(self, server: MeshServer) -> None:
        """RESTORE without confirm=True → FAILED_PRECONDITION (hard gate)."""
        _wait_for_server(server)
        stub = _stub(server)
        record = _make_compact_record()
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.WriteMemory(
                _mesh_gen.core_pb2.WriteMemoryRequest(
                    record=_to_proto_record(record),
                    import_mode=_mesh_gen.core_pb2.ImportMode.RESTORE,
                    confirm=False,
                ),
                timeout=2.0,
            )
        assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION


# ── GetSubscriptionState ──────────────────────────────────────────────────────


class TestGetSubscriptionState:
    def test_subscription_state_returns_empty_cursor(self, server: MeshServer) -> None:
        """GetSubscriptionState returns an empty cursor (M4: mesh-side persistence in M5)."""
        _wait_for_server(server)
        stub = _stub(server)
        response = stub.GetSubscriptionState(
            _mesh_gen.core_pb2.GetSubscriptionStateRequest(
                peer_id=_PEER_ID,
                project_scope=_PROJECT,
            ),
            timeout=2.0,
        )
        assert response.cursor == ""
        assert response.last_rev == 0
        assert response.last_sync_timestamp == ""

    def test_subscription_state_acl_denied(self, server: MeshServer) -> None:
        """GetSubscriptionState with a disallowed scope → PERMISSION_DENIED."""
        _wait_for_server(server)
        stub = _stub(server)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.GetSubscriptionState(
                _mesh_gen.core_pb2.GetSubscriptionStateRequest(
                    peer_id=_PEER_ID,
                    project_scope=_PROJECT_DENIED,
                ),
                timeout=2.0,
            )
        assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED
