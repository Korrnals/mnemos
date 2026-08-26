"""ADR-0018 P1-b — issuance-layer tests (M1 / m2 / m5, findings 1+4).

P1-b fix-track tests (issue #146, items 3-4 part-2 + Security-review
additions) for the content-echo channels:

* M1 — scan-at-issuance on the search/recall paths: MCP
  ``mnemos_search`` / ``mnemos_agent_recall`` / ``mnemos_recall_context``
  and REST ``/search`` (incl. the ``raw_content`` swap) / ``/recall/agent``
  scan the CONTENT field of each result at the boundary — redacted copy
  issued, per-item ``redactions`` note, refuse mode drops the item;
* m2 — FTS5 snippet marker-split hardening: highlight markers
  (``>>>``/``<<<``) and the ``' ... '`` separator split multi-token
  secrets so ``detect_secrets`` misses them; snippets are scanned on a
  marker-stripped copy and a hit withholds the WHOLE snippet (offsets in
  the stripped copy do not map back to the marked text);
* m5 — a scanner exception maps to the ``refused`` shape
  (``reason="scanner error"``) instead of a raw 500 / MCP error —
  fail-closed, observable;
* finding 1+4 (CWE-668 ergonomics) — unscoped retrieval of a
  project-scoped CCR entry logs a WARNING; ``ccr.require_project_match``
  (default False) upgrades it to a denial;
* m3 follow-up — chained 3-overlap detector resolution (aws-key →
  high-entropy → jwt extends one span to max(end));
* scoped-retrieve pass-through proven at the MCP and REST level
  (P1-a regression: the ``project`` argument reaches the store lookup).

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
from mnemos.manager import MemoryManager, _snippet_scan_text
from mnemos.mcp_server import _dispatch
from mnemos.models import MemoryCreate, MemorySource, MemoryStatus
from mnemos.secrets_detector import detect_secrets, redact_content

# ── Fake (EXAMPLE-style) secrets from the detector's own regexes ──────────────

# aws-key pattern: AKIA + 16 chars of [0-9A-Z] (all-caps body, entropy leg quiet).
FAKE_AWS_KEY = "AKIAEXAMPLEABCDEFGH1"
# github-token pattern: ghp_ + 36 chars of [A-Za-z0-9] (same-char body → zero
# entropy, so the high-entropy leg never fires on this value).
FAKE_GITHUB_TOKEN = "ghp_" + "f" * 36
# jwt pattern: three base64url segments, first two starting with eyJ.
FAKE_JWT = "eyJfakeHeaderContent22.eyJfakePayloadXYZ.fakesigABCDEF1234567890"

# m3 follow-up: a chained 3-overlap fixture. The contiguous 39-char alnum
# run ("AKIA"+16 caps+"eyJ"+mixed) clears the high-entropy leg (~4.88
# bits/char >= 4.8, len 39 >= 32) and starts with a valid aws-key; the
# JWT regex starts inside that run (at the eyJ) and runs past its end.
# Resolution must chain BOTH partial overlaps into ONE span covering the
# whole construct, labelled by the first-match (aws-key).
CHAIN_OVERLAP_CONTENT = (
    "AKIA" + "QZ7WJ4XEPLMRT82H" + "eyJbR9uHk2NqX5sVd7W.eyJpayloadFake123.fakesigABCDEF1234567890"
)

PROJECT = "p1b-proj"
AGENT = "p1b-agent"


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
def scoped_manager() -> Iterator[MemoryManager]:
    """Manager with ccr.require_project_match=True (finding 1 deny knob)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = _manager(_settings(Path(tmpdir), require_project_match=True))
        yield mgr
        mgr.close()


@pytest.fixture
def rest_client(manager: MemoryManager) -> Iterator[TestClient]:
    """TestClient wired to the shared ``manager`` fixture (test_api pattern)."""
    api_main._manager = manager
    test_app = FastAPI(title="Mnemos-P1b-Test", version="0.1.0", lifespan=lifespan)
    for route in app.routes:
        test_app.routes.append(route)
    with TestClient(test_app) as tc:
        yield tc
    api_main._manager = None


def _add(mgr: MemoryManager, content: str, *, tags: list[str] | None = None) -> object:
    data = MemoryCreate(
        content=content,
        tags=tags or [f"project:{PROJECT}", f"agent:{AGENT}", "mnemos:learning"],
        source=MemorySource.MCP,
        status=MemoryStatus.PUBLISHED,
    )
    return mgr.add(data, project=PROJECT, agent=AGENT)


def _secret_note(secret: str, marker: str = "unobtanium") -> str:
    return (
        f"Service {marker} deployment notes.\n"
        f"The {marker} service authenticates with api key {secret}\n"
        "Rotate quarterly per policy."
    )


def _cacheable_secret_log(secret: str) -> str:
    lines = [f"2026-08-26T10:00:{i % 60:02d}Z INFO worker processing item {i}" for i in range(20)]
    lines.append(f"2026-08-26T10:01:00Z CONFIG the unobtanium service uses key {secret}")
    lines.append("2026-08-26T10:01:01Z INFO shutdown complete")
    return "\n".join(lines)


# ── M1: MCP mnemos_search ─────────────────────────────────────────────────────


class TestMcpSearchScan:
    def test_secret_redacted_with_per_item_note(self, manager, monkeypatch):
        _add(manager, _secret_note(FAKE_AWS_KEY))
        monkeypatch.setattr(mcp_mod, "_manager", manager)

        results = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_search", {"query": "unobtanium", "project": PROJECT})
        )

        assert isinstance(results, list) and results, "FTS must match 'unobtanium'"
        hit = results[0]
        assert FAKE_AWS_KEY not in hit["content"]
        assert "<REDACTED:aws-key>" in hit["content"]
        assert hit["redactions"] == 1
        assert hit["redacted_patterns"] == {"aws-key": 1}
        # The stored memory is never mutated (zero-loss storage).
        stored = manager.sqlite.get(hit["id"])
        assert stored is not None
        assert FAKE_AWS_KEY in stored.content

    def test_clean_result_carries_zero_redactions(self, manager, monkeypatch):
        _add(manager, _secret_note("plain-text-key-no-pattern"))
        monkeypatch.setattr(mcp_mod, "_manager", manager)

        results = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_search", {"query": "unobtanium", "project": PROJECT})
        )

        assert results
        assert results[0]["redactions"] == 0
        assert "redacted_patterns" not in results[0]

    def test_refuse_mode_drops_the_item(self, refuse_manager, monkeypatch):
        secret_mem = _add(refuse_manager, _secret_note(FAKE_AWS_KEY))
        _add(refuse_manager, "quokka facts entirely clean note")
        monkeypatch.setattr(mcp_mod, "_manager", refuse_manager)

        results = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_search", {"query": "unobtanium", "project": PROJECT})
        )

        # The secret-bearing item is dropped; the mock embedder makes the
        # vector leg surface the clean sibling (equidistant vectors), which
        # must survive — only the dirty item is refused.
        assert all(r["id"] != secret_mem.id for r in results)
        assert all(FAKE_AWS_KEY not in r["content"] for r in results)

    def test_refuse_mode_keeps_clean_items(self, refuse_manager, monkeypatch):
        _add(refuse_manager, "quokka facts entirely clean note")
        monkeypatch.setattr(mcp_mod, "_manager", refuse_manager)

        results = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_search", {"query": "quokka", "project": PROJECT})
        )

        assert len(results) == 1
        assert "quokka" in results[0]["content"]


# ── M1: MCP mnemos_agent_recall ───────────────────────────────────────────────


class TestMcpAgentRecallScan:
    def test_agent_recall_content_redacted(self, manager, monkeypatch):
        _add(manager, _secret_note(FAKE_GITHUB_TOKEN))
        monkeypatch.setattr(mcp_mod, "_manager", manager)

        results = asyncio.new_event_loop().run_until_complete(
            _dispatch(
                "mnemos_agent_recall",
                {"agent": AGENT, "project": PROJECT, "query": "unobtanium"},
            )
        )

        assert results
        assert FAKE_GITHUB_TOKEN not in results[0]["content"]
        assert "<REDACTED:github-token>" in results[0]["content"]
        assert results[0]["redactions"] == 1
        assert results[0]["redacted_patterns"] == {"github-token": 1}


# ── M1: MCP mnemos_recall_context ─────────────────────────────────────────────


class TestMcpRecallContextScan:
    def test_checkpoint_content_redacted(self, manager, monkeypatch):
        checkpoint_tags = [
            f"project:{PROJECT}",
            f"agent:{AGENT}",
            "mnemos:checkpoint",
        ]
        _add(manager, _secret_note(FAKE_AWS_KEY), tags=checkpoint_tags)
        monkeypatch.setattr(mcp_mod, "_manager", manager)

        rendered = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_recall_context", {"project": PROJECT})
        )

        assert isinstance(rendered, str)
        assert FAKE_AWS_KEY not in rendered
        assert "<REDACTED:aws-key>" in rendered


# ── M1: REST /search (incl. raw_content swap) and /recall/agent ───────────────


class TestRestSearchScan:
    def test_search_content_redacted(self, rest_client, manager):
        _add(manager, _secret_note(FAKE_AWS_KEY))

        resp = rest_client.post("/search", json={"query": "unobtanium", "project": PROJECT})

        assert resp.status_code == 200
        results = resp.json()
        assert results
        assert FAKE_AWS_KEY not in results[0]["content"]
        assert "<REDACTED:aws-key>" in results[0]["content"]
        assert results[0]["redactions"] == 1
        assert results[0]["redacted_patterns"] == {"aws-key": 1}

    def test_raw_content_swap_is_scanned(self, rest_client, manager):
        """include_raw drills into raw_content — the swapped string is the
        one echoed, so IT must be scanned (not the clean effective content)."""
        mem = _add(manager, _secret_note("harmless-visible-key"))
        # Public store path: persist a secret-bearing raw_content while the
        # effective content stays clean (raw is the audit drill-down field).
        mem.raw_content = _secret_note(FAKE_AWS_KEY, marker="unobtanium")
        manager.sqlite.save(mem)

        resp = rest_client.post(
            "/search",
            json={"query": "unobtanium", "project": PROJECT, "include_raw": True},
        )

        assert resp.status_code == 200
        results = resp.json()
        assert results
        assert FAKE_AWS_KEY not in results[0]["content"]
        assert "<REDACTED:aws-key>" in results[0]["content"]
        assert results[0]["redactions"] == 1

    def test_recall_agent_content_redacted(self, rest_client, manager):
        _add(manager, _secret_note(FAKE_AWS_KEY))

        resp = rest_client.get(
            f"/recall/agent/{AGENT}", params={"project": PROJECT, "q": "unobtanium"}
        )

        assert resp.status_code == 200
        results = resp.json()
        assert results
        assert FAKE_AWS_KEY not in results[0]["content"]
        assert "<REDACTED:aws-key>" in results[0]["content"]
        assert results[0]["redactions"] == 1


# ── m2: snippet marker-split hardening ────────────────────────────────────────


class TestSnippetMarkerSplit:
    def test_snippet_scan_text_reconstitutes_split_secrets(self):
        # Query matched the payload token → FTS5 wraps it in highlight
        # markers, breaking the jwt regex on the raw snippet text.
        full_marked = (
            f"log {FAKE_JWT.replace('.eyJfakePayloadXYZ.', '.>>>eyJfakePayloadXYZ<<<.')} end"
        )
        assert detect_secrets(full_marked) == [], "fixture: markers must break the pattern"
        stripped = _snippet_scan_text(full_marked)
        assert FAKE_JWT in stripped
        findings = detect_secrets(stripped)
        assert [f.pattern_name for f in findings] == ["jwt"]

    def test_marker_split_jwt_snippet_withheld(self, manager):
        """Query matches the JWT payload token; FTS5 markers split the JWT so
        the raw marked snippet evades detect_secrets — the whole snippet must
        be withheld (offsets in the stripped copy are unmappable)."""
        text = _cacheable_secret_log(FAKE_JWT)
        h = manager.compress_content(text, profile="log", project="m2")["hash"]

        result = manager.retrieve_content(h, query="eyJfakePayloadXYZ")

        assert result["found"] is True
        snippets = result["snippets"]
        assert snippets, "FTS5 must match the payload token"
        for snippet in snippets:
            body = str(snippet["snippet"])
            stripped = _snippet_scan_text(body)
            # No fragment of the token may survive in any snippet.
            for segment in FAKE_JWT.split("."):
                assert segment not in body
                assert segment not in stripped
        assert any(s["snippet"] == "<REDACTED:snippet>" for s in snippets)
        assert result["redactions"] >= 1
        assert result["redacted_patterns"] == {"jwt": 1}


# ── m3 follow-up: chained 3-overlap detector resolution ───────────────────────


class TestChainedOverlap:
    _AWS_RE = re.compile(r"AKIA[0-9A-Z]{16}")
    _JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
    _SPAN_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")

    def test_fixture_legs_fire(self):
        """The chain fixture really is three overlapping findings before
        resolution (aws-key, high-entropy, jwt)."""
        raw: list[tuple[str, int, int]] = [
            ("aws-key", *self._AWS_RE.search(CHAIN_OVERLAP_CONTENT).span())
        ]
        entropy = self._SPAN_RE.search(CHAIN_OVERLAP_CONTENT)
        assert entropy is not None
        raw.append(("high-entropy", *entropy.span()))
        raw.append(("jwt", *self._JWT_RE.search(CHAIN_OVERLAP_CONTENT).span()))
        assert len(raw) == 3
        # aws-key and high-entropy share start 0 (tie → declaration order);
        # the jwt starts inside the entropy span and ends past it — two
        # chained partial overlaps.
        starts = [f[1] for f in raw]
        assert starts.count(0) == 2

    def test_chain_collapses_into_one_max_end_span(self):
        findings = detect_secrets(CHAIN_OVERLAP_CONTENT)
        assert len(findings) == 1
        only = findings[0]
        assert only.pattern_name == "aws-key"  # first-match precedence label
        assert only.start == 0
        # The span must cover the ENTIRE construct including the jwt tail.
        assert only.end == len(CHAIN_OVERLAP_CONTENT)
        assert only.matched_value == CHAIN_OVERLAP_CONTENT

    def test_redaction_leaves_no_fragment(self):
        findings = detect_secrets(CHAIN_OVERLAP_CONTENT)
        redacted = redact_content(CHAIN_OVERLAP_CONTENT, findings)
        assert redacted == "<REDACTED:aws-key>"
        for segment in CHAIN_OVERLAP_CONTENT.split("."):
            assert segment not in redacted


# ── m5: scanner exception → refused shape ─────────────────────────────────────


class TestScannerExceptionRefusedShape:
    def _break_detector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(content: str) -> list[object]:
            raise RuntimeError("simulated detector crash")

        monkeypatch.setattr("mnemos.secrets_detector.detect_secrets", _raise)

    def test_full_original_returns_refused_scanner_error(self, manager, monkeypatch):
        text = _cacheable_secret_log(FAKE_AWS_KEY)
        h = manager.compress_content(text, profile="log")["hash"]
        self._break_detector(monkeypatch)

        result = manager.retrieve_content(h)

        assert result["found"] is True
        assert result["refused"] is True
        assert result["reason"] == "scanner error"
        assert "original" not in result
        assert result["redactions"] == 0

    def test_snippet_path_returns_refused_scanner_error(self, manager, monkeypatch):
        text = _cacheable_secret_log(FAKE_AWS_KEY)
        h = manager.compress_content(text, profile="log")["hash"]
        self._break_detector(monkeypatch)

        result = manager.retrieve_content(h, query="unobtanium")

        assert result["found"] is True
        assert result["refused"] is True
        assert result["reason"] == "scanner error"
        assert "snippets" not in result

    def test_scan_issuance_helper_refuses_on_scanner_error(self, manager, monkeypatch):
        self._break_detector(monkeypatch)

        scan = manager.scan_issuance("anything", context="test")

        assert scan.refused is True
        assert scan.reason == "scanner error"
        assert scan.text == ""
        assert scan.redactions == 0

    def test_mcp_search_survives_scanner_error_fail_closed(self, manager, monkeypatch):
        """List-returning channels degrade to dropping the item (no 500,
        no MCP error string) — the unscanned string never leaves."""
        _add(manager, _secret_note(FAKE_AWS_KEY))
        monkeypatch.setattr(mcp_mod, "_manager", manager)
        self._break_detector(monkeypatch)

        results = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_search", {"query": "unobtanium", "project": PROJECT})
        )

        assert results == []

    def test_scan_issuance_clean_passthrough(self, manager):
        scan = manager.scan_issuance("clean text", context="test")
        assert scan.text == "clean text"
        assert scan.refused is False
        assert scan.reason is None
        assert scan.redactions == 0


# ── Findings 1+4 (CWE-668): unscoped retrieval of scoped rows ────────────────


class TestProjectScopeErgonomics:
    def test_unscoped_retrieve_of_scoped_entry_warns_but_issues(self, manager, caplog):
        text = _cacheable_secret_log("plain-value-no-pattern")
        h = manager.compress_content(text, profile="log", project="alpha")["hash"]

        with caplog.at_level("WARNING", logger="mnemos.manager"):
            result = manager.retrieve_content(h)

        assert result["found"] is True
        assert "original" in result  # legacy behavior preserved
        assert any(
            "Unscoped CCR retrieval of project-scoped entry" in r.message for r in caplog.records
        )

    def test_require_project_match_denies_unscoped_retrieval(self, scoped_manager):
        text = _cacheable_secret_log("plain-value-no-pattern")
        h = scoped_manager.compress_content(text, profile="log", project="alpha")["hash"]

        result = scoped_manager.retrieve_content(h)  # no project passed

        assert result["found"] is True
        assert result["refused"] is True
        assert "project" in result["reason"]
        assert "original" not in result

    def test_require_project_match_allows_matching_scope(self, scoped_manager):
        text = _cacheable_secret_log("plain-value-no-pattern")
        h = scoped_manager.compress_content(text, profile="log", project="alpha")["hash"]

        result = scoped_manager.retrieve_content(h, project="alpha")

        assert result["found"] is True
        assert "refused" not in result
        assert result["original"] == text

    def test_unscoped_entry_never_warns(self, manager, caplog):
        text = _cacheable_secret_log("plain-value-no-pattern")
        h = manager.compress_content(text, profile="log")["hash"]

        with caplog.at_level("WARNING", logger="mnemos.manager"):
            result = manager.retrieve_content(h)

        assert result["found"] is True
        assert not any("Unscoped CCR retrieval" in r.message for r in caplog.records)


# ── P1-a regression: project pass-through at MCP/REST level ──────────────────


class TestScopedRetrievePassThrough:
    def test_mcp_retrieve_project_scoping(self, manager, monkeypatch):
        text = _cacheable_secret_log("plain-value-no-pattern")
        h = manager.compress_content(text, profile="log", project="alpha")["hash"]
        monkeypatch.setattr(mcp_mod, "_manager", manager)

        wrong = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_retrieve", {"hash": h, "project": "beta"})
        )
        right = asyncio.new_event_loop().run_until_complete(
            _dispatch("mnemos_retrieve", {"hash": h, "project": "alpha"})
        )

        assert wrong["found"] is False
        assert right["found"] is True
        assert right["original"] == text

    def test_rest_retrieve_project_scoping(self, rest_client, manager):
        text = _cacheable_secret_log("plain-value-no-pattern")
        h = manager.compress_content(text, profile="log", project="alpha")["hash"]

        wrong = rest_client.post("/retrieve", json={"hash": h, "project": "beta"})
        right = rest_client.post("/retrieve", json={"hash": h, "project": "alpha"})

        assert wrong.status_code == 200 and wrong.json()["found"] is False
        assert right.status_code == 200 and right.json()["found"] is True


# ── Shape sanity for the helper contract ──────────────────────────────────────


class TestIssuanceScanShape:
    def test_refuse_mode_outcome_never_carries_content(self, refuse_manager):
        scan = refuse_manager.scan_issuance(_secret_note(FAKE_AWS_KEY), context="test")

        assert scan.refused is True
        assert scan.text == ""
        assert scan.reason == "secret detected"
        assert scan.redactions == 1
        assert scan.redacted_patterns == {"aws-key": 1}
