"""mnemos #251 D0 — checkpoint channel agent identity (save_context).

Covers the un-hardcoded ``agent``/``session`` params on both surfaces
(MCP ``mnemos_save_context`` + REST ``POST /context/save``) against a
REAL MemoryManager (tmp SQLite), because binding and dedup are
server-side store behaviours that a MagicMock cannot exercise:

  (a) agent param recorded — column AND tag AND metadata stamp
  (b) default — no agent → "user", response shape unchanged
  (c) session→agent binding — first writer wins, mismatch raises
  (d) issuer-keyed dedup — identical payload → same id, duplicate=true;
      different agent, same payload → two rows
  (e) trivial-reject — all fields empty → ValueError before any store
  (f) REST parity for (a)/(c)/(d)/(e) with proper HTTP codes
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mnemos.api import main as api_main
from mnemos.api.main import app, lifespan
from mnemos.config import Settings
from mnemos.manager import MemoryManager, SessionAgentMismatchError
from mnemos.mcp_server import _dispatch

# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_api.py — isolated manager per test)
# ---------------------------------------------------------------------------


@pytest.fixture
def mgr():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        settings = Settings(
            mnemos={
                "vault_path": str(tmp / "vault"),
                "data_dir": str(tmp / "data"),
                "db_name": "test.db",
            },
            embedding={"provider": "onnx"},
            scanner={"enabled": False},
        )
        settings.resolve_paths()
        manager = MemoryManager(settings)
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 384
        manager._embedder = mock_embedder
        yield manager
        manager.close()


@pytest.fixture
def client(mgr):
    """TestClient whose get_manager returns the same isolated manager."""
    test_app = FastAPI(title="Mnemos-Test-251", version="0.1.0", lifespan=lifespan)
    for route in app.routes:
        test_app.routes.append(route)
    api_main._manager = mgr
    with TestClient(test_app) as tc:
        yield tc
    api_main._manager = None


async def _save(mgr: MemoryManager, **overrides: object) -> str:
    """Dispatch mnemos_save_context against a real manager, return the text."""
    args: dict[str, object] = {"project": "p251", "goals": "ship #251"}
    args.update(overrides)
    with patch("mnemos.mcp_server.get_manager", return_value=mgr):
        result = await _dispatch("mnemos_save_context", dict(args))
    assert isinstance(result, str)
    return result


def _total(mgr: MemoryManager) -> int:
    return int(mgr.stats()["total"])


# ---------------------------------------------------------------------------
# (a) agent param recorded — column AND tag AND metadata stamp
# ---------------------------------------------------------------------------


async def test_mcp_agent_param_recorded(mgr: MemoryManager) -> None:
    text = await _save(mgr, agent="alice", completed="wrote tests")
    assert "Context saved (id=" in text

    memories = mgr.list_recent(limit=5, project="p251")
    assert len(memories) == 1
    memory = memories[0]
    # Server column — the source of truth (#254 awareness reads this).
    assert memory.agent == "alice"
    # Display tag built from the VALIDATED agent, not a hardcoded literal.
    assert "agent:alice" in memory.tags
    # Server-controlled metadata stamp.
    assert memory.metadata["checkpoint_agent"] == "alice"
    assert "checkpoint_dedup_key" in memory.metadata
    assert "checkpoint_session" not in memory.metadata


# ---------------------------------------------------------------------------
# (b) default — no agent → "user", response shape unchanged
# ---------------------------------------------------------------------------


async def test_mcp_default_agent_is_user(mgr: MemoryManager) -> None:
    text = await _save(mgr)
    assert text.startswith("✅ Context saved (id=")

    memories = mgr.list_recent(limit=5, project="p251")
    assert len(memories) == 1
    memory = memories[0]
    assert memory.agent == "user"
    assert "agent:user" in memory.tags
    assert memory.metadata["checkpoint_agent"] == "user"


# ---------------------------------------------------------------------------
# (c) session→agent binding — first writer wins, mismatch raises
# ---------------------------------------------------------------------------


async def test_mcp_session_binding_first_write_wins(mgr: MemoryManager) -> None:
    # First presentation binds sess-1 → alice and stamps the checkpoint.
    await _save(mgr, agent="alice", session="sess-1", in_progress="binding")
    memory = mgr.list_recent(limit=1, project="p251")[0]
    assert memory.agent == "alice"
    assert memory.metadata["checkpoint_session"] == "sess-1"

    # Same session + same agent is fine (a second, different payload).
    await _save(mgr, agent="alice", session="sess-1", in_progress="second checkpoint")
    assert _total(mgr) == 2

    # Same session + different agent → SessionAgentMismatchError (ValueError).
    with pytest.raises(SessionAgentMismatchError):
        await _save(mgr, agent="bob", session="sess-1", in_progress="spoof attempt")
    assert _total(mgr) == 2  # nothing stored by the rejected call


# ---------------------------------------------------------------------------
# (d) issuer-keyed dedup — same payload+issuer → same id; other issuer → new row
# ---------------------------------------------------------------------------


async def test_mcp_dedup_identical_payload_returns_existing(mgr: MemoryManager) -> None:
    payload = {"goals": "g", "completed": "c", "in_progress": "i", "decisions": "d"}
    first = await _save(mgr, agent="alice", **payload)
    before = _total(mgr)

    second = await _save(mgr, agent="alice", **payload)
    assert "duplicate=true" in second
    id_first = first.split("id=")[1].split(")")[0]
    id_second = second.split("id=")[1].split(",")[0]
    assert id_first == id_second
    assert _total(mgr) == before  # no second row


async def test_mcp_dedup_issuer_scoped(mgr: MemoryManager) -> None:
    """Issuer in the dedup key: same content, different agent → two rows.

    A copied checkpoint re-issued by another agent must NOT dedup onto the
    victim's row (CWE-294 replay control from the #251 rationale).
    """
    payload = {"goals": "shared goal"}
    await _save(mgr, agent="alice", **payload)
    await _save(mgr, agent="bob", **payload)
    assert _total(mgr) == 2
    agents = {m.agent for m in mgr.list_recent(limit=5, project="p251")}
    assert agents == {"alice", "bob"}


# ---------------------------------------------------------------------------
# (e) trivial-reject + identity validation — ValueError before any store
# ---------------------------------------------------------------------------


async def test_mcp_trivial_reject_all_fields_empty(mgr: MemoryManager) -> None:
    with pytest.raises(ValueError, match="all fields"):
        await _save(mgr, agent="alice", goals=None)
    assert _total(mgr) == 0


async def test_mcp_whitespace_identity_rejected(mgr: MemoryManager) -> None:
    # _require_identity semantics: whitespace-only is not a non-empty string.
    with pytest.raises(ValueError, match="agent"):
        await _save(mgr, agent="   ")
    with pytest.raises(ValueError, match="session"):
        await _save(mgr, session="   ")
    assert _total(mgr) == 0


# ---------------------------------------------------------------------------
# (f) REST parity — POST /context/save
# ---------------------------------------------------------------------------


def test_rest_agent_recorded(client: TestClient, mgr: MemoryManager) -> None:
    resp = client.post(
        "/context/save",
        json={"project": "p251", "goals": "rest goal", "agent": "alice"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["duplicate"] is False
    memory = mgr.sqlite.get(body["id"])
    assert memory is not None
    assert memory.agent == "alice"
    assert "agent:alice" in memory.tags
    assert memory.metadata["checkpoint_agent"] == "alice"


def test_rest_default_agent_and_response_shape(client: TestClient, mgr: MemoryManager) -> None:
    resp = client.post("/context/save", json={"project": "p251", "goals": "g"})
    assert resp.status_code == 201
    body = resp.json()
    # Backward compatible shape + the additive duplicate flag.
    assert set(body) == {"status", "id", "title", "duplicate"}
    assert body["status"] == "saved"
    memory = mgr.sqlite.get(body["id"])
    assert memory is not None
    assert memory.agent == "user"
    assert "agent:user" in memory.tags


def test_rest_binding_mismatch_409(client: TestClient, mgr: MemoryManager) -> None:
    ok = client.post(
        "/context/save",
        json={"project": "p251", "goals": "g", "agent": "alice", "session": "sess-9"},
    )
    assert ok.status_code == 201

    spoof = client.post(
        "/context/save",
        json={"project": "p251", "goals": "g2", "agent": "bob", "session": "sess-9"},
    )
    assert spoof.status_code == 409
    assert "already bound" in spoof.json()["detail"]
    assert _total(mgr) == 1  # nothing stored by the rejected call


def test_rest_dedup_duplicate_flag(client: TestClient, mgr: MemoryManager) -> None:
    payload = {"project": "p251", "goals": "same goal", "agent": "alice"}
    first = client.post("/context/save", json=payload).json()
    second = client.post("/context/save", json=payload).json()
    assert first["id"] == second["id"]
    assert second["duplicate"] is True
    assert _total(mgr) == 1


def test_rest_trivial_reject_400(client: TestClient, mgr: MemoryManager) -> None:
    resp = client.post("/context/save", json={"project": "p251"})
    assert resp.status_code == 400
    assert "all fields" in resp.json()["detail"]
    assert _total(mgr) == 0
