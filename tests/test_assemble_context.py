"""ADR-0017 D1 / ADR-0018 — ``assemble_context`` provider contract tests (#125).

Wave 1 (contract core) acceptance for the assemble_context pipeline:

* fixed stage order (recall → CCR → filter → scan → align → budget),
  recorded verbatim in ``stats.stages``;
* provenance on EVERY injected block, exact format
  ``[mnemos:<id> project=<slug> status=<status> retrieved=<iso>]``;
* MANDATORY secret scan — a planted (fake, EXAMPLE-style) secret is
  redacted in the assembled output with per-block counts; refuse mode
  drops the block (fail-closed);
* contentType filter (``mode=code|prose``) backed by the ingest-side
  ``detect_profile`` capture stored in ``metadata["content_type"]``;
* token budget respected (``tokens.estimated <= budget``), oversized
  blocks skipped whole, never truncated;
* sync/async shapes — async stores + returns a handle, fetched (once) via
  ``async_handle``; boundary validation raises ``ValueError``;
* CCR stage on/off — inline ``[compressed: …]`` markers expand via
  project-scoped retrieval when ``expand_ccr=True`` and the original fits
  the budget; marker stays otherwise;
* entry-invariant status gate — ``raw`` memories never surface;
* MCP (``mnemos_assemble_context``) and REST (``POST /context/assemble``)
  surfaces ride the same manager path.

All secrets below are obviously fake EXAMPLE-style values built from the
detector's own pattern catalogue (src/mnemos/secrets_detector.py); real
credentials never appear in this file.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mnemos.mcp_server as mcp_mod
from mnemos.api import main as api_main
from mnemos.api.main import app, lifespan
from mnemos.config import Settings
from mnemos.manager import MemoryManager
from mnemos.mcp_server import _dispatch
from mnemos.models import Memory, MemoryCreate, MemorySource, MemoryStatus

# ── Fake (EXAMPLE-style) secrets from the detector's own regexes ──────────────

# aws-key pattern: AKIA + 16 chars of [0-9A-Z].
FAKE_AWS_KEY = "AKIAEXAMPLEABCDEFGH1"

PROJECT = "asm-proj"
AGENT = "asm-agent"
SESSION = "sess-42"

PROVENANCE_RE = re.compile(
    r"^\[mnemos:(?P<id>[0-9a-f-]{36}) project=(?P<project>\S+) "
    r"status=(?P<status>\S+) retrieved=(?P<iso>\S+)\]$"
)

CODE_CONTENT = (
    "import json\n"
    "import logging\n"
    "import os\n"
    "\n"
    "def handler(event, context):\n"
    '    """Route the deployment event."""\n'
    "    return json.dumps(event)\n"
    "\n"
    "class Runner:\n"
    "    def run(self):\n"
    "        return handler({}, None)\n"
)

PROSE_CONTENT = (
    "Deployment guide for the handler service.\n"
    "The service is configured through the deployment manifest and the\n"
    "access policy is rotated quarterly per the security baseline.\n"
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _settings(tmp: Path, **ccr_overrides: object) -> Settings:
    ccr: dict[str, object] = {
        "min_size_chars": 100,
        "max_entries": 100,
        "ttl_days": 1,
    }
    ccr.update(ccr_overrides)
    settings = Settings(
        mnemos={
            "vault_path": str(tmp / "vault"),
            "data_dir": str(tmp / "data"),
            "db_name": "test.db",
        },
        scanner={"enabled": False},
        ccr=ccr,  # type: ignore[arg-type]
    )
    settings.resolve_paths()
    return settings


def _manager(settings: Settings) -> MemoryManager:
    mgr = MemoryManager(settings)
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 384
    mgr._embedder = mock_embedder
    return mgr


@pytest.fixture
def manager() -> Iterator[MemoryManager]:
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = _manager(_settings(Path(tmpdir)))
        yield mgr
        mgr.close()


@pytest.fixture
def refuse_manager() -> Iterator[MemoryManager]:
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = _manager(_settings(Path(tmpdir), retrieve_refuse_on_secret=True))
        yield mgr
        mgr.close()


@pytest.fixture
def rest_client(manager: MemoryManager) -> Iterator[TestClient]:
    """TestClient wired to the shared ``manager`` fixture (test_p1b pattern)."""
    api_main._manager = manager
    test_app = FastAPI(title="Mnemos-Assemble-Test", version="0.1.0", lifespan=lifespan)
    for route in app.routes:
        test_app.routes.append(route)
    with TestClient(test_app) as tc:
        yield tc
    api_main._manager = None


def _add(
    mgr: MemoryManager,
    content: str,
    *,
    status: MemoryStatus = MemoryStatus.PUBLISHED,
    tags: list[str] | None = None,
) -> Memory:
    data = MemoryCreate(
        content=content,
        tags=tags or [f"project:{PROJECT}", f"agent:{AGENT}", "mnemos:learning"],
        source=MemorySource.MCP,
        status=status,
    )
    return mgr.add(data, project=PROJECT, agent=AGENT)


# ── Pipeline shape ────────────────────────────────────────────────────────────


class TestPipelineContract:
    def test_stage_order_recorded_verbatim(self, manager: MemoryManager) -> None:
        _add(manager, PROSE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT)
        assert result["stats"]["stages"] == ["recall", "ccr", "filter", "scan", "align", "budget"]

    def test_status_gate_raw_never_surfaces(self, manager: MemoryManager) -> None:
        _add(manager, "raw draft about handler deployment", status=MemoryStatus.RAW)
        published = _add(manager, PROSE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT)
        statuses = {b["status"] for b in result["blocks"]}
        assert statuses <= {"published", "processed"}
        ids = {b["memory_id"] for b in result["blocks"]}
        assert published.id in ids

    def test_empty_project_returns_empty_block(self, manager: MemoryManager) -> None:
        result = manager.assemble_context(session=SESSION, project=PROJECT)
        assert result["text"] == ""
        assert result["blocks"] == []
        assert result["tokens"]["estimated"] == 0
        assert result["stats"]["stages"] == ["recall", "ccr", "filter", "scan", "align", "budget"]


# ── Provenance ────────────────────────────────────────────────────────────────


class TestProvenance:
    def test_every_block_carries_exact_provenance_line(self, manager: MemoryManager) -> None:
        m1 = _add(manager, CODE_CONTENT)
        m2 = _add(manager, PROSE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT)

        assert result["blocks"], "recall must surface the published entries"
        text_lines = result["text"].splitlines()
        prov_lines = [ln for ln in text_lines if ln.startswith("[mnemos:")]
        assert len(prov_lines) == len(result["blocks"])

        for block, line in zip(result["blocks"], prov_lines, strict=True):
            assert block["provenance"] == line
            match = PROVENANCE_RE.match(line)
            assert match is not None, f"provenance format drift: {line!r}"
            assert match.group("id") == block["memory_id"]
            assert match.group("project") == PROJECT
            assert match.group("status") == block["status"]
            # retrieved=<iso> parses as an ISO-8601 timestamp.
            from datetime import datetime

            datetime.fromisoformat(match.group("iso"))

        assert {b["memory_id"] for b in result["blocks"]} == {m1.id, m2.id}

    def test_provenance_timestamps_survive_alignment(self, manager: MemoryManager) -> None:
        """Align runs BEFORE wrapping — the provenance line stays parseable."""
        _add(manager, PROSE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT)
        assert result["blocks"]
        assert PROVENANCE_RE.match(result["blocks"][0]["provenance"]) is not None


# ── Secret scan (mandatory stage) ─────────────────────────────────────────────


class TestSecretScan:
    def test_planted_secret_redacted_with_count(self, manager: MemoryManager) -> None:
        _add(
            manager,
            "Deployment notes for the unobtanium service.\n"
            f"The service authenticates with api key {FAKE_AWS_KEY}\n"
            "Rotate quarterly per policy.",
        )
        result = manager.assemble_context(session=SESSION, project=PROJECT)

        assert FAKE_AWS_KEY not in result["text"], "secret leaked into assembled block"
        hit = next(b for b in result["blocks"] if b["redactions"])
        assert "<REDACTED:aws-key>" in hit["content"]
        assert hit["redactions"] >= 1
        assert hit["redacted_patterns"].get("aws-key") == 1
        assert result["stats"]["scan"]["blocks_scanned"] >= 1
        # Zero-loss storage: the stored memory still carries the original.
        stored = manager.sqlite.get(hit["memory_id"])
        assert stored is not None
        assert FAKE_AWS_KEY in stored.content

    def test_refuse_mode_drops_block_fail_closed(self, refuse_manager: MemoryManager) -> None:
        _add(
            refuse_manager,
            f"unobtanium credentials: {FAKE_AWS_KEY}\nDeployment notes with a planted secret.",
        )
        _add(refuse_manager, PROSE_CONTENT)
        result = refuse_manager.assemble_context(session=SESSION, project=PROJECT)

        assert FAKE_AWS_KEY not in result["text"]
        assert result["stats"]["scan"]["blocks_refused"] == 1
        assert all(b["redactions"] == 0 for b in result["blocks"]), "refused block leaked"
        # The clean entry still assembles.
        assert len(result["blocks"]) == 1


# ── contentType filter (mode=code|prose) ──────────────────────────────────────


class TestContentTypeFilter:
    def test_ingest_captures_content_type_metadata(self, manager: MemoryManager) -> None:
        code_mem = _add(manager, CODE_CONTENT)
        prose_mem = _add(manager, PROSE_CONTENT)
        assert code_mem.metadata["content_type"] == "code"
        assert prose_mem.metadata["content_type"] == "prose"
        # Persisted, not just on the returned object.
        stored = manager.sqlite.get(code_mem.id)
        assert stored is not None
        assert stored.metadata["content_type"] == "code"

    def test_mode_code_filters_recall_candidates(self, manager: MemoryManager) -> None:
        _add(manager, CODE_CONTENT)
        prose_mem = _add(manager, PROSE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT, mode="code")

        assert result["content_type"] == "code"
        assert result["blocks"], "code fixture must surface under mode=code"
        assert all(b["content_type"] == "code" for b in result["blocks"])
        assert prose_mem.id not in {b["memory_id"] for b in result["blocks"]}
        assert result["stats"]["recall"]["content_type_filtered"] >= 1

    def test_mode_prose_filters_recall_candidates(self, manager: MemoryManager) -> None:
        code_mem = _add(manager, CODE_CONTENT)
        _add(manager, PROSE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT, mode="prose")

        assert result["content_type"] == "prose"
        assert result["blocks"], "prose fixture must surface under mode=prose"
        assert all(b["content_type"] == "prose" for b in result["blocks"])
        assert code_mem.id not in {b["memory_id"] for b in result["blocks"]}

    def test_legacy_row_falls_back_to_on_the_fly_detection(self, manager: MemoryManager) -> None:
        """Rows without stored content_type are classified on the fly."""
        mem = _add(manager, CODE_CONTENT)
        # Simulate a pre-#125 row: strip the captured key in storage.
        # (metadata is not in the update_fields whitelist, so go direct —
        # test-only surgery on the row, not a production path.)
        conn = manager.sqlite._get_conn()
        conn.execute("UPDATE memories SET metadata = '{}' WHERE id = ?", (mem.id,))
        conn.commit()

        plain = manager.assemble_context(session=SESSION, project=PROJECT, mode="code")
        assert plain["stats"]["recall"]["content_type_fallbacks"] >= 1
        assert mem.id in {b["memory_id"] for b in plain["blocks"]}


# ── Token budget ──────────────────────────────────────────────────────────────


class TestTokenBudget:
    def test_budget_respected_and_skips_counted(self, manager: MemoryManager) -> None:
        # One small entry (fits) + three oversized entries (skipped whole).
        _add(manager, "Deployment note zero for the handler service.\ndetail " * 3)
        for i in range(1, 4):
            _add(manager, f"Deployment note {i} for the handler service.\n" + "detail " * 60)
        result = manager.assemble_context(session=SESSION, project=PROJECT, budget=120)

        assert result["tokens"]["budget"] == 120
        assert result["tokens"]["estimated"] <= 120
        assert result["stats"]["budget"]["estimated_tokens"] == result["tokens"]["estimated"]
        assert result["stats"]["budget"]["blocks_included"] == len(result["blocks"])
        assert result["stats"]["budget"]["blocks_included"] >= 1
        assert result["stats"]["budget"]["blocks_skipped"] >= 1
        # Whole blocks only — no mid-block truncation marker.
        assert "[...truncated...]" not in result["text"]

    def test_block_tokens_sum_to_estimate(self, manager: MemoryManager) -> None:
        _add(manager, PROSE_CONTENT)
        _add(manager, CODE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT, budget=4096)
        assert result["blocks"]
        assert sum(b["tokens"] for b in result["blocks"]) == result["tokens"]["estimated"]


# ── sync / async shapes ───────────────────────────────────────────────────────


class TestModes:
    def test_sync_returns_full_result(self, manager: MemoryManager) -> None:
        _add(manager, PROSE_CONTENT)
        result = manager.assemble_context(session=SESSION, project=PROJECT)
        assert result["mode"] == "sync"
        assert result["session"] == SESSION
        assert result["project"] == PROJECT
        assert result["blocks"]

    def test_async_stores_and_fetches_once(self, manager: MemoryManager) -> None:
        _add(manager, PROSE_CONTENT)
        envelope = manager.assemble_context(session=SESSION, project=PROJECT, mode="async")

        assert envelope["mode"] == "async"
        assert envelope["status"] == "ready"
        handle = envelope["handle"]
        assert isinstance(handle, str) and len(handle) >= 16
        assert "blocks" not in envelope, "async envelope must not carry the payload"

        fetched = manager.assemble_context(session=SESSION, project=PROJECT, async_handle=handle)
        # The stored block records how it was produced (async) + the handle
        # it was fetched by — the fetch itself is a synchronous envelope.
        assert fetched["mode"] == "async"
        assert fetched["async_handle"] == handle
        assert fetched["blocks"]

        with pytest.raises(ValueError, match="async_handle"):
            manager.assemble_context(session=SESSION, project=PROJECT, async_handle=handle)

    def test_validation_errors(self, manager: MemoryManager) -> None:
        with pytest.raises(ValueError, match="session"):
            manager.assemble_context(session="", project=PROJECT)
        with pytest.raises(ValueError, match="project"):
            manager.assemble_context(session=SESSION, project=" ")
        with pytest.raises(ValueError, match="mode"):
            manager.assemble_context(session=SESSION, project=PROJECT, mode="bogus")
        with pytest.raises(ValueError, match="budget"):
            manager.assemble_context(session=SESSION, project=PROJECT, budget=0)


# ── CCR stage ─────────────────────────────────────────────────────────────────


class TestCcrStage:
    def _marker_memory(self, mgr: MemoryManager, original: str) -> Memory:
        """Compress ``original`` (caching it under PROJECT) and store a
        memory whose content carries the inline CCR marker."""
        compressed = mgr.compress_content(original, profile="default", project=PROJECT)
        marker = str(compressed["marker"])
        assert marker, "fixture requires content above ccr.min_size_chars"
        return _add(
            mgr,
            "Context block summary for the deployment run.\n"
            f"{marker}\n"
            "(original available via the marker above)",
        )

    def test_expand_ccr_off_keeps_marker(self, manager: MemoryManager) -> None:
        original = "deployment log line with unique marker quokka-77\n" + "payload " * 80
        mem = self._marker_memory(manager, original)
        result = manager.assemble_context(session=SESSION, project=PROJECT, expand_ccr=False)

        assert result["stats"]["ccr"]["enabled"] is False
        assert result["stats"]["ccr"]["markers_found"] == 1
        assert result["stats"]["ccr"]["expanded"] == 0
        block = next(b for b in result["blocks"] if b["memory_id"] == mem.id)
        assert "[compressed:" in block["content"]
        assert block["ccr_expanded"] is False

    def test_expand_ccr_on_expands_within_budget(self, manager: MemoryManager) -> None:
        original = "deployment log line with unique marker quokka-88\n" + "payload " * 80
        mem = self._marker_memory(manager, original)
        result = manager.assemble_context(
            session=SESSION, project=PROJECT, expand_ccr=True, budget=4096
        )

        assert result["stats"]["ccr"]["enabled"] is True
        assert result["stats"]["ccr"]["expanded"] == 1
        block = next(b for b in result["blocks"] if b["memory_id"] == mem.id)
        assert block["ccr_expanded"] is True
        assert "[compressed:" not in block["content"]
        assert "quokka-88" in block["content"], "expanded original must replace the marker"
        assert result["tokens"]["estimated"] <= 4096

    def test_expand_ccr_budget_aware_keeps_compressed(self, manager: MemoryManager) -> None:
        original = "deployment log line with unique marker quokka-99\n" + "payload " * 80
        mem = self._marker_memory(manager, original)
        # Budget above the compressed block's estimate but far below the
        # original's → expansion declined, marker preserved so the model
        # keeps the on-demand handle.
        result = manager.assemble_context(
            session=SESSION, project=PROJECT, expand_ccr=True, budget=100
        )

        assert result["stats"]["ccr"]["skipped_budget"] == 1
        block = next(b for b in result["blocks"] if b["memory_id"] == mem.id)
        assert block["ccr_expanded"] is False
        assert "[compressed:" in block["content"]

    def test_expansion_secret_redaction_counted(self, manager: MemoryManager) -> None:
        original = (
            "deployment log line with unique marker quokka-aa\n"
            f"api key {FAKE_AWS_KEY} in the cached original\n" + "payload " * 80
        )
        self._marker_memory(manager, original)
        result = manager.assemble_context(
            session=SESSION, project=PROJECT, expand_ccr=True, budget=8192
        )

        assert FAKE_AWS_KEY not in result["text"]
        # The rehydrate channel's own scan already redacted the original;
        # the merged per-block count reflects the CCR redaction.
        blocks_with_redactions = [b for b in result["blocks"] if b["redactions"]]
        assert blocks_with_redactions, "CCR-channel redaction must be attributed to the block"
        assert any(b.get("redacted_patterns", {}).get("aws-key") for b in blocks_with_redactions)


# ── applyTo pinning (file context) ────────────────────────────────────────────


class TestFileContext:
    def test_applyto_rule_pinned_to_top(self, manager: MemoryManager) -> None:
        _add(manager, PROSE_CONTENT)
        rule = _add(
            manager,
            "# Python rule\nAlways run ruff before committing handler code.",
            tags=[
                f"project:{PROJECT}",
                "mnemos:rule",
                "applyTo:**/*.py",
                "source:path-scoped-rule",
            ],
        )
        result = manager.assemble_context(session=SESSION, project=PROJECT, file="src/handler.py")
        assert result["file"] == "src/handler.py"
        assert result["stats"]["recall"]["applyto_pinned"] == 1
        assert result["blocks"]
        assert result["blocks"][0]["memory_id"] == rule.id, "pinned rule must lead the order"


# ── Surfaces: MCP + REST ride the manager path ────────────────────────────────


class TestSurfaces:
    def test_mcp_tool_dispatch(self, manager: MemoryManager, monkeypatch) -> None:
        _add(manager, PROSE_CONTENT)
        monkeypatch.setattr(mcp_mod, "_manager", manager)

        result = asyncio.new_event_loop().run_until_complete(
            _dispatch(
                "mnemos_assemble_context",
                {"session": SESSION, "project": PROJECT},
            )
        )
        assert isinstance(result, dict)
        assert result["blocks"]
        assert result["stats"]["stages"] == ["recall", "ccr", "filter", "scan", "align", "budget"]

    def test_mcp_tool_validation_error_shape(self, manager: MemoryManager, monkeypatch) -> None:
        monkeypatch.setattr(mcp_mod, "_manager", manager)
        result = asyncio.new_event_loop().run_until_complete(
            _dispatch(
                "mnemos_assemble_context",
                {"session": SESSION, "project": PROJECT, "mode": "bogus"},
            )
        )
        assert result == {"error": "invalid mode 'bogus'; valid values: async, code, prose, sync"}

    def test_mcp_tool_async_roundtrip(self, manager: MemoryManager, monkeypatch) -> None:
        _add(manager, PROSE_CONTENT)
        monkeypatch.setattr(mcp_mod, "_manager", manager)
        loop = asyncio.new_event_loop()
        envelope = loop.run_until_complete(
            _dispatch(
                "mnemos_assemble_context",
                {"session": SESSION, "project": PROJECT, "mode": "async"},
            )
        )
        fetched = loop.run_until_complete(
            _dispatch(
                "mnemos_assemble_context",
                {
                    "session": SESSION,
                    "project": PROJECT,
                    "async_handle": envelope["handle"],
                },
            )
        )
        loop.close()
        assert fetched["blocks"]
        assert fetched["async_handle"] == envelope["handle"]

    def test_rest_endpoint_mirrors_manager(
        self, manager: MemoryManager, rest_client: TestClient
    ) -> None:
        _add(manager, PROSE_CONTENT)
        resp = rest_client.post("/context/assemble", json={"session": SESSION, "project": PROJECT})
        assert resp.status_code == 200
        body = resp.json()
        assert body["blocks"]
        assert body["stats"]["stages"] == ["recall", "ccr", "filter", "scan", "align", "budget"]

    def test_rest_endpoint_validation_422(
        self, manager: MemoryManager, rest_client: TestClient
    ) -> None:
        resp = rest_client.post(
            "/context/assemble",
            json={"session": SESSION, "project": PROJECT, "mode": "bogus"},
        )
        assert resp.status_code == 422

    def test_rest_endpoint_secret_redacted(
        self, manager: MemoryManager, rest_client: TestClient
    ) -> None:
        _add(
            manager,
            f"unobtanium deployment api key {FAKE_AWS_KEY} rotate quarterly",
        )
        resp = rest_client.post("/context/assemble", json={"session": SESSION, "project": PROJECT})
        assert resp.status_code == 200
        assert FAKE_AWS_KEY not in resp.text
