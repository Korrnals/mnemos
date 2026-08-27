"""MnemosSDK facade (mnemos #125, Wave 3) — delegation contract.

The facade owns NO logic (src/mnemos/sdk.py): every verb is a one-line
delegation to a ``MemoryManager`` method. These tests pin the DELEGATION
(spy/monkeypatch the manager methods) plus the facade's two boundary
behaviours: the constructor's exactly-one-of rule and ``forget``'s
project guard. The delegated-to paths themselves are covered by their
own suites (test_assemble_context.py, test_context_rewrite.py, …).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mnemos.config import Settings
from mnemos.manager import MemoryManager
from mnemos.models import MemoryCreate
from mnemos.sdk import MnemosSDK

PROJECT = "sdk-proj"
AGENT = "sdk-agent"
SESSION = "sdk-session"


def _settings(tmp: Path, **ccr: Any) -> Settings:
    settings = Settings(
        mnemos={
            "vault_path": str(tmp / "vault"),
            "data_dir": str(tmp / "data"),
            "db_name": "test.db",
        },
        ccr={"min_size_chars": 100, **ccr},  # type: ignore[arg-type]
    )
    settings.resolve_paths()
    return settings


@pytest.fixture
def manager() -> Iterator[MemoryManager]:
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = MemoryManager(_settings(Path(tmpdir)))
        yield mgr
        mgr.close()


@pytest.fixture
def sdk(manager: MemoryManager) -> MnemosSDK:
    return MnemosSDK(manager=manager)


# ── Constructor ───────────────────────────────────────────────────────────────


class TestConstructor:
    def test_both_args_rejected(self, manager: MemoryManager) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            MnemosSDK(_settings(Path("/nonexistent")), manager=manager)

    def test_neither_arg_rejected(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            MnemosSDK()

    def test_manager_exposed(self, manager: MemoryManager, sdk: MnemosSDK) -> None:
        assert sdk.manager is manager


# ── remember → add ────────────────────────────────────────────────────────────


class TestRemember:
    def test_delegates_to_add(self, sdk: MnemosSDK, manager: MemoryManager) -> None:
        calls: list[dict[str, Any]] = []
        original = manager.add

        def _spy(data: MemoryCreate, **kw: Any) -> Any:
            calls.append({"data": data, **kw})
            return original(data, **kw)

        manager.add = _spy  # type: ignore[method-assign]

        memory = sdk.remember("note body", PROJECT, AGENT, title="a title")

        assert memory.content == "note body"
        assert memory.project == PROJECT
        assert memory.agent == AGENT
        assert len(calls) == 1
        assert calls[0]["data"].title == "a title", "kw passes through to MemoryCreate"
        assert calls[0]["project"] == PROJECT
        assert calls[0]["agent"] == AGENT


# ── recall → search ───────────────────────────────────────────────────────────


class TestRecall:
    def test_delegates_to_search(self, sdk: MnemosSDK, manager: MemoryManager) -> None:
        calls: list[dict[str, Any]] = []
        original = manager.search

        def _spy(query: str, **kw: Any) -> list[Any]:
            calls.append({"query": query, **kw})
            return original(query, **kw)

        manager.search = _spy  # type: ignore[method-assign]

        sdk.remember("recall target body about quokka-sdk", PROJECT, AGENT)
        results = sdk.recall("quokka-sdk", PROJECT, limit=3)

        assert isinstance(results, list)
        assert len(calls) == 1
        assert calls[0]["query"] == "quokka-sdk"
        assert calls[0]["project"] == PROJECT
        assert calls[0]["limit"] == 3, "kw passes through to search"


# ── forget → get + delete (project-guarded) ──────────────────────────────────


class TestForget:
    def test_own_project_deletes(self, sdk: MnemosSDK, manager: MemoryManager) -> None:
        memory = sdk.remember("forget me", PROJECT, AGENT)
        assert sdk.forget(memory.id, PROJECT) is True
        assert manager.get(memory.id) is None

    def test_unknown_id_is_false(self, sdk: MnemosSDK) -> None:
        assert sdk.forget("no-such-id", PROJECT) is False

    def test_cross_project_denied(self, sdk: MnemosSDK, manager: MemoryManager) -> None:
        memory = sdk.remember("other project memory", "other-proj", AGENT)
        deleted: list[str] = []
        original = manager.delete

        def _spy(memory_id: str) -> bool:
            deleted.append(memory_id)
            return original(memory_id)

        manager.delete = _spy  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="different project"):
            sdk.forget(memory.id, PROJECT)

        assert deleted == [], "delete must not run for another project's memory"
        assert manager.get(memory.id) is not None, "memory must survive"


# ── stats → stats (project slice is presentation) ─────────────────────────────


class TestStats:
    def test_global_verbatim(self, sdk: MnemosSDK, manager: MemoryManager) -> None:
        envelope = manager.stats()
        assert sdk.stats() == envelope

    def test_project_slice_keys(self, sdk: MnemosSDK, manager: MemoryManager) -> None:
        sdk.remember("counted", PROJECT, AGENT)
        result = sdk.stats(PROJECT)
        assert result["project"] == PROJECT
        assert result["project_total"] == 1
        assert result["status"] == "ok", "the full manager envelope is preserved"


# ── assemble_context → assemble_context ───────────────────────────────────────


class TestAssembleContext:
    def test_delegates_with_passthrough(
        self, sdk: MnemosSDK, manager: MemoryManager
    ) -> None:
        memory = sdk.remember("assembled body quokka-asm", PROJECT, AGENT)
        manager.publish(memory.id, skip_quality_check=True)

        calls: list[dict[str, Any]] = []
        original = manager.assemble_context

        def _spy(**kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return original(**kw)

        manager.assemble_context = _spy  # type: ignore[method-assign]

        result = sdk.assemble_context(
            SESSION, PROJECT, query="quokka-asm", budget=512
        )

        assert result["session"] == SESSION
        assert result["project"] == PROJECT
        assert calls == [
            {
                "session": SESSION,
                "project": PROJECT,
                "query": "quokka-asm",
                "budget": 512,
            }
        ]


# ── rewrite → context_rewrite ─────────────────────────────────────────────────


class TestRewrite:
    def test_delegates_to_context_rewrite(
        self, sdk: MnemosSDK, manager: MemoryManager
    ) -> None:
        calls: list[dict[str, Any]] = []
        original = manager.context_rewrite

        def _spy(**kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return original(**kw)

        manager.context_rewrite = _spy  # type: ignore[method-assign]

        result = sdk.rewrite(
            "original block body", PROJECT, AGENT, SESSION, diff="was->becomes"
        )

        assert result["status"] in ("stored", "deduplicated")
        assert calls == [
            {
                "content": "original block body",
                "project": PROJECT,
                "agent": AGENT,
                "session": SESSION,
                "diff": "was->becomes",
            }
        ]
