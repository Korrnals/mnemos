"""Tests for the auto-cron federation bridge (#104).

Covers ``scripts/sync-peers.sh`` (the ExecStart of
``contrib/systemd/mnemos-sync.service``) and the two systemd unit files. The
script reads its config from ``MNEMOS_SYNC_*`` env vars; these tests exercise
the env-var validation, the dry-run command logging, and the unit file
shape. They do NOT run a real mnemos CLI, rsync, or ssh — dry-run mode
(``MNEMOS_SYNC_DRY_RUN=1``) logs the commands and exits before any network
or filesystem side effect.

All secret/PII fixtures use RFC-reserved values (per
``sensitive-data.instructions.md``): 192.0.2.0/24 (RFC 5737),
example.invalid (RFC 6761). The placeholder SSH key files are EMPTY — the
script only checks ``-r`` readability, never parses the key material. No
real ed25519 key is generated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync-peers.sh"
SERVICE = REPO_ROOT / "contrib" / "systemd" / "mnemos-sync.service"
TIMER = REPO_ROOT / "contrib" / "systemd" / "mnemos-sync.timer"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def placeholder_keys(tmp_path: Path) -> tuple[Path, Path]:
    """Create two EMPTY placeholder key files (script only checks -r)."""
    push_key = tmp_path / "sync-push-key"
    trigger_key = tmp_path / "sync-trigger-key"
    push_key.write_text("")
    trigger_key.write_text("")
    push_key.chmod(0o600)
    trigger_key.chmod(0o600)
    return push_key, trigger_key


def _full_env(push_key: Path, trigger_key: Path, export_dir: Path) -> dict[str, str]:
    """A complete, valid env-var set using RFC-reserved dummies.

    Aligns to the script's actual contract (two keys: push + trigger).
    """
    env = {
        "MNEMOS_SYNC_PEER_HOST": "192.0.2.10",
        "MNEMOS_SYNC_PEER_USER": "mnemos-sync",
        "MNEMOS_SYNC_PEER_SSH_KEY": str(push_key),
        "MNEMOS_SYNC_PEER_IMPORT_SSH_KEY": str(trigger_key),
        "MNEMOS_SYNC_LOCAL_EXPORT_DIR": str(export_dir),
        "MNEMOS_SYNC_REMOTE_IMPORT_DIR": "/var/lib/mnemos-sync/incoming",
        "MNEMOS_SYNC_SHARED_PROJECTS": "project-test",
        "MNEMOS_SYNC_ENCRYPT": "true",
        "MNEMOS_SYNC_PASSPHRASE_ENV": "MNEMOS_EXPORT_PASSPHRASE",
        "MNEMOS_EXPORT_PASSPHRASE": "dummy-passphrase-not-a-secret",
        "MNEMOS_SYNC_DRY_RUN": "1",
        # Force a mnemos binary path so the script does not fail auto-discovery
        # in the test environment (where `mnemos` may not be on PATH).
        "MNEMOS_SYNC_MNEMOS_BIN": "/usr/bin/true",
    }
    # Inherit PATH so bash/date/rsync resolve.
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    return env


def _run_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run sync-peers.sh with the given env, capturing stdout+stderr."""
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


# ── 1. refuses without required env vars ──────────────────────────────────────


def test_refuses_without_required_env_vars() -> None:
    """Empty env → exit 2 with a 'missing required env var(s)' message."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    result = _run_script(env)
    assert result.returncode != 0
    assert result.returncode == 2, (
        f"expected exit 2 for missing env, got {result.returncode}; stderr:\n{result.stderr}"
    )
    assert "missing required env var" in result.stderr, (
        f"stderr should name the missing var; got:\n{result.stderr}"
    )


# ── 2. refuses with partial env vars ──────────────────────────────────────────


def test_refuses_with_partial_env_vars(placeholder_keys: tuple[Path, Path]) -> None:
    """Only some required vars set → exit 2 naming the missing ones."""
    push_key, _trigger_key = placeholder_keys
    env = {
        "MNEMOS_SYNC_PEER_HOST": "192.0.2.10",
        "MNEMOS_SYNC_PEER_SSH_KEY": str(push_key),
        # Intentionally omit the other 6 required vars.
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    result = _run_script(env)
    assert result.returncode == 2
    assert "missing required env var" in result.stderr
    # The error should list at least one of the omitted vars.
    omitted = [
        "MNEMOS_SYNC_PEER_IMPORT_SSH_KEY",
        "MNEMOS_SYNC_LOCAL_EXPORT_DIR",
        "MNEMOS_SYNC_REMOTE_IMPORT_DIR",
        "MNEMOS_SYNC_SHARED_PROJECTS",
        "MNEMOS_SYNC_ENCRYPT",
        "MNEMOS_SYNC_PASSPHRASE_ENV",
    ]
    assert any(v in result.stderr for v in omitted), (
        f"stderr should name an omitted var; got:\n{result.stderr}"
    )


# ── 3. dry-run logs expected commands ─────────────────────────────────────────


def test_dry_run_logs_expected_commands(
    placeholder_keys: tuple[Path, Path], tmp_path: Path
) -> None:
    """Full env + DRY_RUN=1 → exit 0, logs mnemos sync export, rsync, ssh."""
    push_key, trigger_key = placeholder_keys
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    env = _full_env(push_key, trigger_key, export_dir)
    result = _run_script(env)
    assert result.returncode == 0, (
        f"expected exit 0 in dry-run, got {result.returncode}; stderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    # The script logs the constructed export command including "sync export".
    assert "mnemos sync export" in combined or "sync export" in combined, (
        f"dry-run should log the export command; got:\n{combined}"
    )
    # The script logs the rsync command.
    assert "rsync" in combined, f"dry-run should log the rsync command; got:\n{combined}"
    # The script logs the ssh import-trigger command.
    assert "ssh" in combined, f"dry-run should log the ssh import trigger; got:\n{combined}"
    # Dry-run must NOT actually call rsync/ssh/mnemos — it logs and skips.
    # The script's dry-run path prints "dry-run: skipping ..." for each step.
    assert "dry-run" in combined, f"dry-run should announce skipping; got:\n{combined}"


# ── 4. systemd units valid ────────────────────────────────────────────────────


def test_systemd_units_valid() -> None:
    """mnemos-sync.service + .timer are well-formed systemd units."""
    assert SERVICE.is_file(), f"missing service unit: {SERVICE}"
    assert TIMER.is_file(), f"missing timer unit: {TIMER}"
    service_text = SERVICE.read_text()
    timer_text = TIMER.read_text()

    # Text assertions (always run — systemd-analyze may be absent on non-Linux CI).
    assert "[Unit]" in service_text
    assert "[Service]" in service_text
    assert "ExecStart=" in service_text
    assert "Type=oneshot" in service_text
    assert "[Unit]" in timer_text
    assert "[Timer]" in timer_text
    assert "OnCalendar=" in timer_text

    # If systemd-analyze is available, run the formal verifier.
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        # systemd not installed in this environment — text assertions suffice.
        return
    verify = subprocess.run(
        [analyzer, "verify", str(SERVICE), str(TIMER)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    # systemd-analyze verify exits 0 on a valid unit; non-zero with a message
    # on a problem. Treat a clean exit as pass.
    if verify.returncode != 0:
        # Some systemd versions warn about the missing EnvironmentFile at
        # verify time (the file is not present in CI). Distinguish that from a
        # real structural error: if the only complaint is the EnvironmentFile
        # or a missing binary, the unit structure is still valid.
        msg = verify.stderr + verify.stdout
        structural_errors = [
            line
            for line in msg.splitlines()
            if line and "EnvironmentFile" not in line and "Command" not in line
        ]
        assert not structural_errors, f"systemd-analyze verify reported structural errors:\n{msg}"


if __name__ == "__main__":
    # Allow running this file directly:  python tests/test_sync_peers_script.py
    sys.exit(pytest.main([__file__, "-v"]))
