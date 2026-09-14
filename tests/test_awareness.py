"""Awareness v0 — presence + delta + conflict-hints (mnemos #254, R3).

Acceptance map (issue #254 acceptance/scope clauses → test):

* presence snapshot → ``TestPresence``
* empty delta → ``TestEmptyDelta``
* per-agent slot cap (ONE line per neighbor) → ``TestPerAgentSlot``
* trust-level rendering (observed vs self-reported, disclaimer) →
  ``TestTwoLevelTrust``
* scan refusal (injection screen on neighbor goal titles) →
  ``TestScanRefusal``
* project=None fail-closed (+ bad cursor) → ``TestBoundaries``
* off-path equivalence (``include_awareness`` absent → ``pre_llm_call``
  output byte-identical, E1-style pin) → ``TestOffPathEquivalence``
* cursor roundtrip via the E1 helpers → ``TestCursorRoundtrip``
* conflict-hints determinism → ``TestConflictHints``
* abstention attribution chain reconstructable from traces →
  ``TestAbstentionAttribution``
* dedup + trivial-reject REUSED, not duplicated (awareness writes no
  memories) → ``TestNoCheckpointDuplication``
* never pinnable (no applyTo/severity, no memory_id) → ``TestNotPinnable``
* origin=federated exclusion hook → ``TestFederationExclusion``
* awareness renders LAST (E1 guard wired) → ``TestTailGuard``
* MCP ``mnemos_awareness`` tool + ``mnemos_hooks`` passthrough →
  ``TestMcpAwarenessTool`` / ``TestMcpHooksPassthrough``

Repair round (consolidated review findings → test):

* P2-1 agent-filtered ``_my_goal`` (noisy-neighbor overflow) →
  ``TestRepairMyGoalAgentFilter``
* P2-2 single-feed cursor high-water (race deleted with query C) →
  ``TestRepairSingleFeedCursor``
* P2-8 observed-only blocks + inline ``[unverified]`` qualifiers →
  ``TestPerAgentSlot::test_slot_line_wording`` /
  ``TestTwoLevelTrust::test_conflict_hint_lines_carry_unverified_marker``
* P2-9 import paths stamp ``federated_origin`` →
  ``TestRepairFederatedImportStamp``
* P2-10 goal-echo admissibility gate (presence stays, goal gates) →
  ``TestRepairAdmissibilityGate``
* P2-11 abstention hardening (actor leg, self/stale rejection, note
  cap + scan) → ``TestRepairAbstentionHardening``
* P3-3 section top-N cap → ``TestRepairSectionCap``
* P3-4 hardcoded committee disclaimer →
  ``TestRepairDisclaimerHardcoded``
* P3-5 cross-project abstention + REST parity →
  ``TestRepairAbstentionHardening::test_cross_project_basis_rejected`` /
  ``TestRepairRestHooksParity``
* P3-12 cursor-key tuple aliasing →
  ``test_lanes.TestAwarenessContracts::test_cursor_key_no_tuple_aliasing``

All secrets below are obviously fake EXAMPLE-style values built from the
detector's own pattern catalogue; real credentials never appear.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mnemos.mcp_server as mcp_mod
from mnemos.api import main as api_main
from mnemos.api.main import app, lifespan
from mnemos.awareness import (
    ABSTENTION_TASK_LABEL,
    AWARENESS_DISCLAIMER,
    AWARENESS_LANE,
    AWARENESS_MAX_RENDERED_AGENTS,
    DELTA_MAX_WINDOW_SEC,
    assert_awareness_tail,
    compose_pre_llm_awareness,
    compose_session_presence,
    conflict_hints,
    delta_blocks,
    is_delta_excluded,
    pre_flight_snapshot,
    presence_snapshot,
    project_delta,
    record_abstention,
    render_awareness_section,
)
from mnemos.compact import CompactRecord
from mnemos.config import Settings
from mnemos.hooks import dispatch_hook
from mnemos.lanes import AWARENESS_CURSOR_PREFIX, awareness_cursor_key, read_awareness_cursor
from mnemos.manager import MemoryManager
from mnemos.mcp_server import _dispatch, list_tools
from mnemos.models import Memory, MemoryCreate, MemorySource, MemoryStatus

PROJECT = "awr-proj"
AGENT = "awr-agent"
SESSION = "awr-session"
NEIGHBOR = "awr-neighbor-b"
NEIGHBOR_SESSION = "sess-neighbor-b"

FAKE_AWS_KEY = "AKIAEXAMPLEABCDEFGH1"  # detector-catalogue example shape

#: Anything past the presence window suffices for the stale-neighbor test.
PRESENCE_WINDOW_MARGIN_SEC = 2 * 900

FROZEN_ISO = "2026-09-13T12:00:00+00:00"
FROZEN = datetime.fromisoformat(FROZEN_ISO)


def _settings(
    tmp: Path,
    *,
    lanes_enabled: bool = False,
    **ccr: Any,
) -> Settings:
    settings = Settings(
        mnemos={
            "vault_path": str(tmp / "vault"),
            "data_dir": str(tmp / "data"),
            "db_name": "test.db",
        },
        scanner={"enabled": False},
        ccr={"min_size_chars": 100, **ccr},  # type: ignore[arg-type]
        lanes={"enabled": lanes_enabled},
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
    """Refuse-mode deployment (ccr.retrieve_refuse_on_secret=True)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = _manager(_settings(Path(tmpdir), retrieve_refuse_on_secret=True))
        yield mgr
        mgr.close()


@pytest.fixture
def lanes_manager() -> Iterator[MemoryManager]:
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = _manager(_settings(Path(tmpdir), lanes_enabled=True))
        yield mgr
        mgr.close()


def _checkpoint(
    mgr: MemoryManager,
    *,
    goals: str,
    agent: str,
    session: str,
    project: str = PROJECT,
    in_progress: str = "",
) -> str:
    """Store a checkpoint through the REAL #251 channel."""
    memory, _dup = mgr.save_checkpoint(
        {"goals": goals, "in_progress": in_progress or "wiring"},
        project=project,
        agent=agent,
        session=session,
    )
    return memory.id


def _knowledge(mgr: MemoryManager, content: str, agent: str = NEIGHBOR) -> str:
    memory = mgr.add(
        MemoryCreate(
            content=content,
            tags=[f"project:{PROJECT}", f"agent:{agent}", "mnemos:learning"],
            source=MemorySource.MCP,
            status=MemoryStatus.PUBLISHED,
        ),
        project=PROJECT,
        agent=agent,
    )
    return memory.id


# ── Presence snapshot ─────────────────────────────────────────────────────────


class TestPresence:
    def test_snapshot_observed_only_server_columns(self, manager: MemoryManager) -> None:
        _checkpoint(
            manager,
            goals="ship the v4 release notes",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        _knowledge(manager, "neighbor knowledge row about deploy")

        snap = presence_snapshot(manager, project=PROJECT)
        agents = {a["agent"]: a for a in snap["agents"]}
        assert NEIGHBOR in agents
        entry = agents[NEIGHBOR]
        # Observed facts only: no goal fields anywhere in presence.
        assert entry["entries"] == 2
        assert entry["last_seen"]
        assert entry["sessions"] == [NEIGHBOR_SESSION]
        assert entry["trust"] == "observed"
        assert "goal" not in entry
        assert all("goal" not in a for a in snap["agents"])
        assert snap["disclaimer"] == AWARENESS_DISCLAIMER

    def test_presence_from_server_columns_not_client_tags(self, manager: MemoryManager) -> None:
        """A row whose agent TAG lies must not mint presence: the agent
        COLUMN (server-written by the #251/#254 channels) is the only
        identity source."""
        mgr = manager
        mgr.add(
            MemoryCreate(
                content="row with a lying agent tag",
                tags=[f"project:{PROJECT}", "agent:someone-else", "mnemos:learning"],
                source=MemorySource.MCP,
                status=MemoryStatus.PUBLISHED,
            ),
            project=PROJECT,
            agent=NEIGHBOR,  # server column — this is what counts
        )
        snap = presence_snapshot(mgr, project=PROJECT)
        assert [a["agent"] for a in snap["agents"]] == [NEIGHBOR]

    def test_stale_neighbor_outside_window(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="old goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        # A `now` far in the future puts the write outside the presence window.
        future = datetime.fromtimestamp(
            datetime.now(UTC).timestamp() + PRESENCE_WINDOW_MARGIN_SEC, tz=UTC
        )
        snap = presence_snapshot(manager, project=PROJECT, now=future)
        assert snap["agents"] == []


# ── Empty delta ───────────────────────────────────────────────────────────────


class TestEmptyDelta:
    def test_fresh_project_empty_delta_renders_nothing(self, manager: MemoryManager) -> None:
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        assert delta["agents"] == []
        assert render_awareness_section(delta, []) == ""
        assert delta_blocks(delta) == []

    def test_second_compose_after_consumption_is_empty(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="neighbor goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        first = compose_pre_llm_awareness(manager, session=SESSION, project=PROJECT, agent=AGENT)
        assert first["meta"]["agents"] == [NEIGHBOR]
        second = compose_pre_llm_awareness(manager, session=SESSION, project=PROJECT, agent=AGENT)
        # The cursor consumed the window: strictly-forward, no re-render.
        assert second["meta"]["agents"] == []
        assert second["text"] == ""


def _hour_ago_iso() -> str:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(seconds=DELTA_MAX_WINDOW_SEC)).isoformat()


# ── Per-agent slot cap ────────────────────────────────────────────────────────


class TestPerAgentSlot:
    def test_seven_rows_one_line_one_block(self, manager: MemoryManager) -> None:
        for i in range(7):
            _checkpoint(
                manager,
                goals="hold the release branch" if i == 6 else "iterate on docs",
                agent=NEIGHBOR,
                session=NEIGHBOR_SESSION,
                in_progress=f"step {i}",
            )
        _checkpoint(
            manager,
            goals="other neighbor",
            agent="awr-neighbor-c",
            session="sess-neighbor-c",
        )

        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        by_agent = {a["agent"]: a for a in delta["agents"]}
        assert set(by_agent) == {NEIGHBOR, "awr-neighbor-c"}
        assert by_agent[NEIGHBOR]["entries"] == 7
        # ONE block per agent (the E1 slot)…
        blocks = delta_blocks(delta)
        assert sorted(b["agent"] for b in blocks) == sorted(by_agent)
        # …and the goal title comes from the LATEST checkpoint only.
        assert by_agent[NEIGHBOR]["goal_title"] == "hold the release branch"

        text = render_awareness_section(delta, [])
        # ONE observed line per agent (the anti-DoS slot) — count inside the
        # observed section only; the self-reported section adds its own line.
        observed_section = text.split("### self-reported")[0]
        observed_lines = [
            ln for ln in observed_section.splitlines() if ln.startswith(f"- {NEIGHBOR}")
        ]
        assert len(observed_lines) == 1, "one observed line per agent (anti-DoS slot)"

    def test_slot_line_wording(self, manager: MemoryManager) -> None:
        """Blocks are OBSERVED-ONLY (repair P2-8): the canonical line carries
        counts + recency, NEVER the self-reported goal payload."""
        _checkpoint(manager, goals="guard the release", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        block = delta_blocks(delta)[0]
        assert block["content"] == (
            f"{NEIGHBOR}: 1 entries, last {delta['agents'][0]['last_seen']}"
        )
        assert "guard the release" not in block["content"]
        # The goal renders ONLY inside the section text, labeled.
        assert "guard the release" in render_awareness_section(delta, [])


# ── Two-level trust rendering ─────────────────────────────────────────────────


class TestTwoLevelTrust:
    def test_labeled_sections_and_disclaimer_verbatim(self, manager: MemoryManager) -> None:
        _checkpoint(
            manager,
            goals="rebuild the index pipeline",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        text = render_awareness_section(delta, [])

        # Repair P3-13: "server-verified" overclaimed — the WRITE is
        # server-recorded, the writer identity is self-asserted in v0.
        assert "### observed — server-recorded write events (identity self-asserted)" in text
        assert "### self-reported — unverified peer claims" in text
        # The R3 disclaimer frame rides VERBATIM.
        assert AWARENESS_DISCLAIMER in text
        # Repair P2-8: every self-reported line carries an INLINE
        # [unverified] qualifier (adjacent to the claim, not only the
        # once-per-section disclaimer).
        assert f"- {NEIGHBOR}: [unverified] goal rebuild the index pipeline" in text
        # The goal (self-reported) never appears in the observed section.
        observed_part = text.split("### self-reported")[0]
        assert "rebuild the index pipeline" not in observed_part
        self_reported_part = text.split("### self-reported")[1]
        assert "rebuild the index pipeline" in self_reported_part

    def test_conflict_hint_lines_carry_unverified_marker(self, manager: MemoryManager) -> None:
        """Repair P2-8: hint lines derive from unverified goals — they are
        inline-qualified too."""
        _checkpoint(manager, goals="cut the v4 payments release", agent=AGENT, session=SESSION)
        _checkpoint(
            manager,
            goals="ship the v4 payments release notes",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        hints = conflict_hints("cut the v4 payments release", delta)
        text = render_awareness_section(delta, hints)
        hint_lines = [
            ln for ln in text.splitlines() if ln.startswith(f"- {NEIGHBOR}: [unverified] shared")
        ]
        assert hint_lines, "hint lines must carry the [unverified] qualifier"

    def test_presence_section_in_on_session_start(self, manager: MemoryManager) -> None:
        _checkpoint(
            manager, goals="refactor the payments module for q3", agent=AGENT, session=SESSION
        )
        _checkpoint(
            manager,
            goals="refactor the payments module",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        result = dispatch_hook(
            manager,
            action="on_session_start",
            session=SESSION,
            project=PROJECT,
            agent=AGENT,
            include_awareness=True,
        )
        presence = result["presence"]
        assert [a["agent"] for a in presence["agents"]] == [NEIGHBOR]
        hints = presence["conflict_hints"]
        assert hints and hints[0]["neighbor"] == NEIGHBOR
        assert "payments" in hints[0]["shared_tokens"]
        assert presence["disclaimer"] == AWARENESS_DISCLAIMER


# ── Scan refusal (the injection screen) ───────────────────────────────────────


def _stale_secret_checkpoint(mgr: MemoryManager) -> None:
    """Seed an ADMISSIBLE checkpoint whose goal carries a secret.

    A secret-bearing checkpoint is refused by the publish gate at store
    time (status=raw, inadmissible) — that path is covered by
    ``TestRepairAdmissibilityGate``. The issuance scan exists for the
    OTHER case (``scan_issuance`` contract: patterns evolve and stored
    records age, so a store-time verdict alone goes stale): an
    admissible row whose goal trips the scanner at read time. Seeded
    directly through ``sqlite.save`` — a pre-gate legacy row's shape.
    """
    mgr.sqlite.save(
        Memory(
            id="awr-stale-secret-cp",
            content=f"# Session checkpoint\n## Goals\ndeploy with key {FAKE_AWS_KEY} inside\n",
            tags=[f"project:{PROJECT}", f"agent:{NEIGHBOR}", "mnemos:checkpoint"],
            source=MemorySource.MCP,
            status=MemoryStatus.PUBLISHED,
            metadata={"checkpoint_agent": NEIGHBOR, "checkpoint_session": NEIGHBOR_SESSION},
            project=PROJECT,
            agent=NEIGHBOR,
        )
    )


class TestScanRefusal:
    def test_redact_mode_goal_redacted(self, manager: MemoryManager) -> None:
        _stale_secret_checkpoint(manager)
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        title = delta["agents"][0]["goal_title"]
        assert title is not None
        assert FAKE_AWS_KEY not in title
        assert "<REDACTED:" in title
        assert delta["counts"]["redactions"] >= 1
        assert FAKE_AWS_KEY not in render_awareness_section(delta, [])

    def test_refuse_mode_goal_dropped_observed_facts_stay(
        self, refuse_manager: MemoryManager
    ) -> None:
        _stale_secret_checkpoint(refuse_manager)
        delta = project_delta(
            refuse_manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT
        )
        # Fail-closed: the self-reported claim is DROPPED…
        assert delta["agents"][0]["goal_title"] is None
        assert delta["counts"]["goals_refused"] == 1
        # …the server-observed facts stay, the secret never renders.
        assert delta["agents"][0]["entries"] == 1
        text = render_awareness_section(delta, [])
        assert FAKE_AWS_KEY not in text
        assert NEIGHBOR in text


# ── Fail-closed boundaries ────────────────────────────────────────────────────


class TestBoundaries:
    def test_project_none_fail_closed_presence(self, manager: MemoryManager) -> None:
        with pytest.raises(ValueError, match="project"):
            presence_snapshot(manager, project=None)

    def test_project_none_fail_closed_delta(self, manager: MemoryManager) -> None:
        with pytest.raises(ValueError, match="project"):
            project_delta(manager, project=None, since=_hour_ago_iso())

    def test_bad_cursor_rejected(self, manager: MemoryManager) -> None:
        with pytest.raises(ValueError, match="ISO-8601"):
            project_delta(manager, project=PROJECT, since="not-a-timestamp")

    def test_future_since_rejected(self, manager: MemoryManager) -> None:
        future = datetime.fromtimestamp(datetime.now(UTC).timestamp() + 10_000, tz=UTC).isoformat()
        with pytest.raises(ValueError, match="future"):
            project_delta(manager, project=PROJECT, since=future)


# ── Off-path equivalence (the E1-style pin) ───────────────────────────────────


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return FROZEN if tz is not None else FROZEN.replace(tzinfo=None)


def _freeze_retrieval_clock(monkeypatch: pytest.MonkeyPatch, target: MemoryManager) -> None:
    """Freeze the retrieval timestamp source (mnemos #282 migration).

    The provenance ``retrieved=<iso>`` stamp moved from a per-call
    ``assemble.py`` ``datetime.now()`` to the manager's session-keyed
    registry (``MemoryManager.retrieval_iso``), so the freeze point moves
    with it: pin the manager's clock and drop any live-clock stamp
    already cached for this session so the next assembly re-stamps
    frozen.
    """
    monkeypatch.setattr("mnemos.manager.datetime", _FrozenDatetime)
    target._retrieval_iso.pop(SESSION, None)


class TestOffPathEquivalence:
    def test_pre_llm_call_off_is_byte_identical_to_assemble(
        self, manager: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag ABSENT (the default) → the hook output is EXACTLY the
        pre-#254 shape: assemble result + the two enrichment keys, nothing
        else (no awareness key anywhere, byte-for-byte)."""
        _freeze_retrieval_clock(monkeypatch, manager)
        _knowledge(manager, "off-path equivalence knowledge body about quokka")
        direct = manager.assemble_context(
            session=SESSION, project=PROJECT, query="quokka", agent=AGENT
        )
        hooked = dispatch_hook(
            manager,
            action="pre_llm_call",
            session=SESSION,
            project=PROJECT,
            agent=AGENT,
            context_hint="quokka",
        )
        assert hooked == {
            **direct,
            "hook": "pre_llm_call",
            "injection": "prepend result['text'] to the model call prompt",
        }

    def test_off_path_has_no_awareness_keys_anywhere(self, manager: MemoryManager) -> None:
        def _walk(node: object) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    assert key not in ("awareness", "presence"), f"unexpected key: {key}"
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _knowledge(manager, "walk body for the off-path recursive key walk")
        _walk(
            dispatch_hook(
                manager,
                action="pre_llm_call",
                session=SESSION,
                project=PROJECT,
                agent=AGENT,
            )
        )
        _walk(
            dispatch_hook(
                manager,
                action="on_session_start",
                session=SESSION,
                project=PROJECT,
                agent=AGENT,
            )
        )

    def test_off_path_repeat_runs_identical(
        self, manager: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _freeze_retrieval_clock(monkeypatch, manager)
        _knowledge(manager, "repeat run body for byte stability")
        first = dispatch_hook(
            manager, action="pre_llm_call", session=SESSION, project=PROJECT, agent=AGENT
        )
        second = dispatch_hook(
            manager, action="pre_llm_call", session=SESSION, project=PROJECT, agent=AGENT
        )
        assert first == second
        assert first["text"] == second["text"]

    def test_flag_on_appends_awareness_last(
        self, manager: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _freeze_retrieval_clock(monkeypatch, manager)
        _knowledge(manager, "flag-on composition body for tail placement")
        _checkpoint(
            manager, goals="neighbor is active here", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        result = dispatch_hook(
            manager,
            action="pre_llm_call",
            session=SESSION,
            project=PROJECT,
            agent=AGENT,
            include_awareness=True,
        )
        assert "awareness" in result
        lanes = [b.get("lane") for b in result["blocks"]]
        # Awareness blocks form a contiguous TAIL after every recall block.
        assert lanes[-1] == AWARENESS_LANE
        assert AWARENESS_LANE not in lanes[:-1]
        assert AWARENESS_DISCLAIMER in result["text"]
        # The composed list passes the E1 guard (wired in the hook).
        assert_awareness_tail(result["blocks"])


# ── Cursor roundtrip via the E1 helpers ───────────────────────────────────────


class TestCursorRoundtrip:
    def test_compose_writes_e1_cursor_key(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="cursor goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        compose_pre_llm_awareness(manager, session=SESSION, project=PROJECT, agent=AGENT)
        # The length-prefixed E1 key (repair P3-12 — plain ":"-joins alias).
        key = awareness_cursor_key(project=PROJECT, agent=AGENT, session=SESSION)
        assert key.startswith(AWARENESS_CURSOR_PREFIX)
        cursor = manager.sqlite.get_meta(key)
        assert cursor is not None and cursor > _hour_ago_iso()
        assert (
            read_awareness_cursor(manager, project=PROJECT, agent=AGENT, session=SESSION) == cursor
        )

    def test_pre_flight_does_not_advance_cursor(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="read only goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        pre_flight_snapshot(manager, project=PROJECT, agent=AGENT, session=SESSION)
        assert (
            read_awareness_cursor(manager, project=PROJECT, agent=AGENT, session=SESSION) is None
        ), "pre-flight is read-only; consumption belongs to pre_llm_call"


# ── Conflict hints ────────────────────────────────────────────────────────────


class TestConflictHints:
    def test_deterministic_overlap(self) -> None:
        delta = {
            "agents": [
                {"agent": "n1", "goal_title": "ship release v4 of payments"},
                {"agent": "n2", "goal_title": "unrelated docs gardening"},
                {"agent": "n3", "goal_title": None},
            ]
        }
        first = conflict_hints("cut the v4 payments release", delta)
        second = conflict_hints("cut the v4 payments release", delta)
        assert first == second
        assert first == [{"neighbor": "n1", "shared_tokens": ["payments", "release", "v4"]}]

    def test_single_common_word_does_not_hint(self) -> None:
        delta = {"agents": [{"agent": "n1", "goal_title": "session about release"}]}
        assert conflict_hints("my release", delta) == []
        assert conflict_hints(None, delta) == []
        assert conflict_hints("anything", {"agents": []}) == []

    def test_224_replay_scenario(self, manager: MemoryManager) -> None:
        """The permanent scenario: my release goal vs the parallel session
        that is about to close the same release — the hint fires."""
        _checkpoint(
            manager, goals="cut the v4.0.0 release from green CI", agent=AGENT, session=SESSION
        )
        _checkpoint(
            manager,
            goals="close stale release PRs before the v4.0.0 announcement",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        presence = compose_session_presence(manager, project=PROJECT, agent=AGENT)
        assert presence["conflict_hints"]
        assert presence["conflict_hints"][0]["neighbor"] == NEIGHBOR
        assert "v4.0.0" in presence["conflict_hints"][0]["shared_tokens"]
        # And the same hint rides the pre_llm_call composition.
        composed = compose_pre_llm_awareness(manager, session=SESSION, project=PROJECT, agent=AGENT)
        assert composed["meta"]["conflict_hints"] >= 1


# ── Abstention attribution ────────────────────────────────────────────────────


class TestAbstentionAttribution:
    def test_chain_reconstructable_from_traces(self, manager: MemoryManager) -> None:
        basis_id = _checkpoint(
            manager,
            goals="neighbor claims the release branch",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        record = record_abstention(
            manager,
            project=PROJECT,
            agent=AGENT,
            session=SESSION,
            basis_checkpoint_id=basis_id,
            note="peer goal overlaps my zone",
        )

        # The chain is reconstructable from traces alone:
        # abstention → delta-block → checkpoint-id → writer-session.
        traces = manager.sqlite.list_traces(project=PROJECT, task_label=ABSTENTION_TASK_LABEL)
        assert len(traces) == 1
        trace = traces[0]
        assert trace.item_id == basis_id, "trace links the delta-block basis checkpoint"
        basis = manager.sqlite.get(basis_id)
        assert basis is not None
        assert basis.metadata["checkpoint_session"] == NEIGHBOR_SESSION
        assert record["chain"]["writer_session"] == NEIGHBOR_SESSION
        assert record["chain"]["neighbor_agent"] == NEIGHBOR
        # Repair P2-11: the ACTOR leg is persisted too — the abstainer
        # pair rides in the rationale, not just a log line.
        assert f"abstainer={AGENT}/{SESSION}" in trace.rationale_summary
        assert record["chain"]["abstainer_agent"] == AGENT
        assert record["chain"]["abstainer_session"] == SESSION
        assert f"writer_session={NEIGHBOR_SESSION}" in trace.rationale_summary
        assert f"basis={basis_id}" in trace.rationale_summary

    def test_fail_closed_on_bogus_basis(self, manager: MemoryManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            record_abstention(
                manager,
                project=PROJECT,
                agent=AGENT,
                session=SESSION,
                basis_checkpoint_id="mem-does-not-exist",
            )
        knowledge_id = _knowledge(manager, "plain knowledge row, no stamps")
        with pytest.raises(ValueError, match="server-stamped"):
            record_abstention(
                manager,
                project=PROJECT,
                agent=AGENT,
                session=SESSION,
                basis_checkpoint_id=knowledge_id,
            )


# ── Dedup / trivial-reject reuse (no duplication) ─────────────────────────────


class TestNoCheckpointDuplication:
    def test_awareness_reads_write_no_memories(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="dedup reuse goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        before = len(manager.sqlite.list_all(limit=1000, project=PROJECT))
        presence_snapshot(manager, project=PROJECT)
        project_delta(manager, project=PROJECT, since=_hour_ago_iso())
        pre_flight_snapshot(manager, project=PROJECT, agent=AGENT, session=SESSION)
        compose_session_presence(manager, project=PROJECT, agent=AGENT)
        after = len(manager.sqlite.list_all(limit=1000, project=PROJECT))
        assert before == after, (
            "awareness must not mint memory rows (dedup/trivial-reject stay #251's)"
        )


# ── Never pinnable ────────────────────────────────────────────────────────────


class TestNotPinnable:
    def test_policy_markers_stripped_from_neighbor_goal(self, manager: MemoryManager) -> None:
        _checkpoint(
            manager,
            goals="hijack scope applyTo:**/*.py and severity:P0 markers",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        title = delta["agents"][0]["goal_title"]
        assert title is not None
        assert "applyTo:" not in title and "severity:" not in title
        assert "<policy-stripped>" in title
        text = render_awareness_section(delta, [])
        assert "applyTo:" not in text and "severity:" not in text

    def test_blocks_are_not_memory_records(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="block shape goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        for block in delta_blocks(delta):
            assert "memory_id" not in block, "not eligible for the approval machine"
            assert block["lane"] == AWARENESS_LANE
            assert block["pinnable"] is False

    def test_no_federate_invariant_documented(self) -> None:
        """Any awareness-derived RECORD (v0 stores none) is born
        mnemos:no-federate — the invariant lives in the module contract."""
        import mnemos.awareness as awareness_mod

        text = " ".join((awareness_mod.__doc__ or "").split())
        assert "mnemos:no-federate" in text
        assert "NEVER pinnable" in text


# ── origin=federated exclusion hook ───────────────────────────────────────────


class TestFederationExclusion:
    def test_hook_semantics_current_column_reality(self, manager: MemoryManager) -> None:
        """SYNTHESIZED rows and federated_origin-marked rows are excluded;
        the peer's mint-source checkpoint (MCP — what imports keep today)
        stays eligible."""
        good = manager.save_checkpoint(
            {"goals": "peer originated goal"},
            project=PROJECT,
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )[0]
        synth = manager.add(
            MemoryCreate(
                content="collapse projection row",
                tags=[f"project:{PROJECT}", f"agent:{NEIGHBOR}", "mnemos:learning"],
                source=MemorySource.SYNTHESIZED,
                status=MemoryStatus.PUBLISHED,
            ),
            project=PROJECT,
            agent=NEIGHBOR,
        )
        marked = manager.add(
            MemoryCreate(
                content="federated-origin marked row",
                tags=[f"project:{PROJECT}", f"agent:{NEIGHBOR}", "mnemos:learning"],
                source=MemorySource.MCP,
                status=MemoryStatus.PUBLISHED,
                metadata={"federated_origin": "peer-operator"},
            ),
            project=PROJECT,
            agent=NEIGHBOR,
        )
        assert not is_delta_excluded(good)
        assert is_delta_excluded(synth)
        assert is_delta_excluded(marked)

        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        assert delta["counts"]["excluded_federated"] == 2
        assert delta["agents"][0]["entries"] == 1, "only the peer-originated row feeds the delta"
        assert delta["agents"][0]["goal_title"] == "peer originated goal"


# ── Tail guard (E1 wiring) ────────────────────────────────────────────────────


class TestTailGuard:
    def test_guard_fires_when_awareness_enters_pinned_prefix(self) -> None:
        with pytest.raises(AssertionError, match="pinned prefix"):
            assert_awareness_tail(
                [{"lane": "knowledge"}, {"lane": AWARENESS_LANE}, {"lane": "rules"}]
            )

    def test_guard_passes_for_tail_and_laneless(self) -> None:
        assert_awareness_tail([{"content": "lane-less recall block"}])
        assert_awareness_tail(
            [{"lane": "rules"}, {"lane": AWARENESS_LANE}, {"lane": AWARENESS_LANE}]
        )

    def test_lanes_on_awareness_still_last(
        self, lanes_manager: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _freeze_retrieval_clock(monkeypatch, lanes_manager)
        lanes_manager.add(
            MemoryCreate(
                content="# Handler rule\nAlways run ruff before committing handler code.",
                tags=[f"project:{PROJECT}", "mnemos:rule"],
                source=MemorySource.MCP,
                status=MemoryStatus.PUBLISHED,
            ),
            project=PROJECT,
            agent=AGENT,
        )
        _checkpoint(
            lanes_manager,
            goals="lanes-on neighbor goal",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        result = dispatch_hook(
            lanes_manager,
            action="pre_llm_call",
            session=SESSION,
            project=PROJECT,
            agent=AGENT,
            include_awareness=True,
        )
        lanes = [b["lane"] for b in result["blocks"]]
        assert lanes[-1] == AWARENESS_LANE
        assert lanes.index("rules") < lanes.index(AWARENESS_LANE)


# ── MCP surfaces ──────────────────────────────────────────────────────────────


def _mcp_call(mgr: MemoryManager, name: str, args: dict[str, Any]) -> Any:
    mcp_mod._manager = mgr
    try:
        return asyncio.new_event_loop().run_until_complete(_dispatch(name, args))
    finally:
        mcp_mod._manager = None


class TestMcpAwarenessTool:
    def test_tool_registered_in_manifest(self) -> None:
        tools = asyncio.run(list_tools())
        names = [t.name for t in tools]
        assert "mnemos_awareness" in names

    def test_pre_flight_action(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="mcp preflight goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        result = _mcp_call(
            manager,
            "mnemos_awareness",
            {
                "action": "pre_flight",
                "session": SESSION,
                "project": PROJECT,
                "agent": AGENT,
            },
        )
        assert result["action"] == "pre_flight"
        assert result["delta"]["agents"][0]["agent"] == NEIGHBOR
        assert AWARENESS_DISCLAIMER in result["text"]
        assert result["cursor_advanced"] is False

    def test_record_abstention_action(self, manager: MemoryManager) -> None:
        basis_id = _checkpoint(
            manager, goals="mcp abstention basis", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        result = _mcp_call(
            manager,
            "mnemos_awareness",
            {
                "action": "record_abstention",
                "session": SESSION,
                "project": PROJECT,
                "agent": AGENT,
                "basis_checkpoint_id": basis_id,
                "note": "overlapping zone",
            },
        )
        assert result["chain"]["checkpoint_id"] == basis_id
        assert result["chain"]["writer_session"] == NEIGHBOR_SESSION

    def test_unknown_action_error_dict(self, manager: MemoryManager) -> None:
        result = _mcp_call(
            manager,
            "mnemos_awareness",
            {"action": "push", "session": SESSION, "project": PROJECT, "agent": AGENT},
        )
        assert result == {"error": "action must be one of: pre_flight, record_abstention"}

    def test_missing_identity_error_dict(self, manager: MemoryManager) -> None:
        result = _mcp_call(
            manager, "mnemos_awareness", {"action": "pre_flight", "session": SESSION}
        )
        assert "error" in result


class TestMcpHooksPassthrough:
    def test_pre_llm_call_include_awareness_passthrough(self, manager: MemoryManager) -> None:
        _checkpoint(
            manager, goals="hooks passthrough goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        base = {
            "action": "pre_llm_call",
            "session": SESSION,
            "project": PROJECT,
            "agent": AGENT,
        }
        off = _mcp_call(manager, "mnemos_hooks", dict(base))
        assert "awareness" not in off
        on = _mcp_call(manager, "mnemos_hooks", {**base, "include_awareness": True})
        assert on["awareness"]["agents"] == [NEIGHBOR]

    def test_non_bool_flag_rejected(self, manager: MemoryManager) -> None:
        result = _mcp_call(
            manager,
            "mnemos_hooks",
            {
                "action": "pre_llm_call",
                "session": SESSION,
                "project": PROJECT,
                "agent": AGENT,
                "include_awareness": "yes",
            },
        )
        assert result == {"error": "include_awareness must be a boolean when provided"}


# ── Repair round (consolidated review: code P2/P3 + security P2/P3) ──────────


class TestRepairMyGoalAgentFilter:
    """P2-1: ``_my_goal`` must be agent-filtered — noisy neighbors must not
    push my checkpoint out of the scan window and silently disable
    conflict-hints exactly when awareness matters."""

    def test_noisy_neighbor_overflow_does_not_blind_hints(self, manager: MemoryManager) -> None:
        # My checkpoint FIRST (pre-fix it lands beyond the 200 newest
        # project rows once the noise lands)…
        _checkpoint(manager, goals="refactor the payments module", agent=AGENT, session=SESSION)
        # …then more neighbor rows than the DELTA_FEED_LIMIT scan bound…
        for i in range(205):
            manager.add(
                MemoryCreate(
                    content=f"noise row {i} about unrelated deploy plumbing",
                    tags=[f"project:{PROJECT}", "agent:awr-noise", "mnemos:learning"],
                    source=MemorySource.MCP,
                    status=MemoryStatus.PUBLISHED,
                ),
                project=PROJECT,
                agent="awr-noise",
            )
        # …then a neighbor goal overlapping mine.
        _checkpoint(
            manager,
            goals="refactor the payments module too",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        presence = compose_session_presence(manager, project=PROJECT, agent=AGENT)
        assert presence["conflict_hints"], (
            "conflict-hints must survive noisy-neighbor overflow (agent-filtered _my_goal)"
        )
        assert presence["conflict_hints"][0]["neighbor"] == NEIGHBOR


class TestRepairSingleFeedCursor:
    """P2-2: the cursor high-water comes from the SAME feed the delta
    consumed — no third time-unbounded query that a racing row could
    advance the cursor past."""

    def test_cursor_equals_consumed_feed_high_water(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="hw goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        _knowledge(manager, "newest eligible knowledge row")

        result = compose_pre_llm_awareness(manager, session=SESSION, project=PROJECT, agent=AGENT)
        newest = max(
            m.created_at
            for m in manager.list_recent(limit=200, project=PROJECT)
            if not is_delta_excluded(m)
        )
        expected = (newest + timedelta(microseconds=1)).isoformat()
        assert result["meta"]["cursor"] == expected
        assert (
            read_awareness_cursor(manager, project=PROJECT, agent=AGENT, session=SESSION)
            == expected
        )

    def test_compose_runs_exactly_two_list_recent_queries(
        self, manager: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """project_delta's window query + _my_goal's agent-filtered query —
        the deleted third query was the race window."""
        _checkpoint(manager, goals="spy goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        calls: list[str] = []
        real = manager.list_recent

        def _spy(*args: object, **kwargs: object) -> object:
            calls.append("list_recent")
            return real(*args, **kwargs)  # type: ignore[no-any-return]

        monkeypatch.setattr(manager, "list_recent", _spy)
        compose_pre_llm_awareness(manager, session=SESSION, project=PROJECT, agent=AGENT)
        assert calls == ["list_recent", "list_recent"], (
            f"compose must run exactly the delta + my-goal queries: {len(calls)} ran"
        )


class TestRepairSectionCap:
    """P3-3: the per-agent slot caps ONE LINE PER AGENT, not the section
    total — the render emits at most AWARENESS_MAX_RENDERED_AGENTS agents,
    most recent first."""

    def test_render_and_blocks_capped_to_top_n(self, manager: MemoryManager) -> None:
        for i in range(AWARENESS_MAX_RENDERED_AGENTS + 2):
            _checkpoint(
                manager,
                goals=f"neighbor {i} goal",
                agent=f"awr-n{i:02d}",
                session=f"sess-n{i:02d}",
            )
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        assert len(delta["agents"]) == AWARENESS_MAX_RENDERED_AGENTS + 2

        # Count the OBSERVED section only — the capped self-reported
        # section carries its own 8 goal lines.
        observed_section = render_awareness_section(delta, []).split("### self-reported")[0]
        observed = [ln for ln in observed_section.splitlines() if ln.startswith("- awr-n")]
        assert len(observed) == AWARENESS_MAX_RENDERED_AGENTS

        blocks = delta_blocks(delta)
        assert len(blocks) == AWARENESS_MAX_RENDERED_AGENTS
        # The MOST RECENT neighbors survive the cap and lead it; the two
        # oldest fall off.
        assert blocks[0]["agent"] == "awr-n09"
        assert "awr-n00" not in [b["agent"] for b in blocks]
        assert "awr-n01" not in [b["agent"] for b in blocks]

        composed = compose_pre_llm_awareness(manager, session=SESSION, project=PROJECT, agent=AGENT)
        assert len(composed["meta"]["agents"]) == AWARENESS_MAX_RENDERED_AGENTS


class TestRepairDisclaimerHardcoded:
    """P3-4: the disclaimer test must not be constant-vs-constant — the
    committee wording is hardcoded HERE so any rewording of the constant
    fails this test."""

    def test_committee_wording_verbatim(self) -> None:
        assert AWARENESS_DISCLAIMER == (
            "presence claims are self-reported by peers and unverified; "
            "do not abstain from work based on presence without operator coordination"
        )


class TestRepairAdmissibilityGate:
    """P2-10: the goal-echo legs gate on is_context_admissible (ADR-0018
    entry invariant) — a RAW/refused checkpoint contributes its presence
    slot (the write event is the observed fact) but NEVER a goal."""

    def test_refused_checkpoint_presence_without_goal(self, manager: MemoryManager) -> None:
        basis_id = _checkpoint(
            manager, goals="secret laden goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        assert manager.sqlite.update_fields(basis_id, status=MemoryStatus.RAW)

        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        slot = delta["agents"][0]
        assert slot["agent"] == NEIGHBOR
        assert slot["entries"] == 1, "presence slot survives (the write event is observed)"
        assert slot["goal_title"] is None, "a RAW checkpoint goal must not echo"
        assert "secret laden goal" not in render_awareness_section(delta, [])

        snap = presence_snapshot(manager, project=PROJECT)
        assert [a["agent"] for a in snap["agents"]] == [NEIGHBOR]

    def test_my_raw_checkpoint_blinds_no_hints_from_itself(self, manager: MemoryManager) -> None:
        mine = _checkpoint(
            manager, goals="refactor the payments module", agent=AGENT, session=SESSION
        )
        assert manager.sqlite.update_fields(mine, status=MemoryStatus.RAW)
        _checkpoint(
            manager,
            goals="refactor the payments module too",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        # My only goal is RAW → no hint basis; the neighbor goal renders
        # but hints against MY goal stay dark (nothing admissible to mine).
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        assert delta["agents"][0]["goal_title"] == "refactor the payments module too"
        assert conflict_hints("refactor the payments module", delta)  # explicit goal works
        composed = compose_session_presence(manager, project=PROJECT, agent=AGENT)
        assert composed["conflict_hints"] == [], "my RAW goal must not feed hints"


class TestRepairAbstentionHardening:
    """P2-11: abstainer leg persisted, self-abstention rejected, stale
    basis rejected (best-effort recency window), note capped + scanned."""

    def test_self_abstention_rejected(self, manager: MemoryManager) -> None:
        basis_id = _checkpoint(
            manager, goals="my own claim", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        with pytest.raises(ValueError, match="own agent"):
            record_abstention(
                manager,
                project=PROJECT,
                agent=NEIGHBOR,
                session=NEIGHBOR_SESSION,
                basis_checkpoint_id=basis_id,
            )

    def test_stale_basis_rejected(self, manager: MemoryManager) -> None:
        stale = Memory(
            id="awr-stale-basis",
            content="# Session checkpoint\n## Goals\nancient goal\n",
            tags=[f"project:{PROJECT}", f"agent:{NEIGHBOR}", "mnemos:checkpoint"],
            source=MemorySource.MCP,
            status=MemoryStatus.PUBLISHED,
            metadata={"checkpoint_agent": NEIGHBOR, "checkpoint_session": NEIGHBOR_SESSION},
            project=PROJECT,
            agent=NEIGHBOR,
            created_at=datetime.now(UTC) - timedelta(seconds=DELTA_MAX_WINDOW_SEC * 2),
        )
        manager.sqlite.save(stale)
        with pytest.raises(ValueError, match="recency window"):
            record_abstention(
                manager,
                project=PROJECT,
                agent=AGENT,
                session=SESSION,
                basis_checkpoint_id="awr-stale-basis",
            )

    def test_note_too_long_rejected(self, manager: MemoryManager) -> None:
        basis_id = _checkpoint(
            manager, goals="note cap goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        with pytest.raises(ValueError, match="note exceeds"):
            record_abstention(
                manager,
                project=PROJECT,
                agent=AGENT,
                session=SESSION,
                basis_checkpoint_id=basis_id,
                note="x" * 500,
            )

    def test_note_refused_fail_closed(self, refuse_manager: MemoryManager) -> None:
        basis_id = _checkpoint(
            refuse_manager, goals="note scan goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        with pytest.raises(ValueError, match="note refused"):
            record_abstention(
                refuse_manager,
                project=PROJECT,
                agent=AGENT,
                session=SESSION,
                basis_checkpoint_id=basis_id,
                note=f"key {FAKE_AWS_KEY} inside the note",
            )

    def test_note_redacted_in_rationale(self, manager: MemoryManager) -> None:
        basis_id = _checkpoint(
            manager, goals="note redact goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        record = record_abstention(
            manager,
            project=PROJECT,
            agent=AGENT,
            session=SESSION,
            basis_checkpoint_id=basis_id,
            note=f"deploy key {FAKE_AWS_KEY} context",
        )
        assert FAKE_AWS_KEY not in record["rationale"]
        assert "<REDACTED:" in record["rationale"]

    def test_cross_project_basis_rejected(self, manager: MemoryManager) -> None:
        """P3-5 gap: the cross-project abstention branch."""
        basis_id = _checkpoint(
            manager,
            goals="other project goal",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
            project="awr-other-proj",
        )
        with pytest.raises(ValueError, match="another project"):
            record_abstention(
                manager,
                project=PROJECT,
                agent=AGENT,
                session=SESSION,
                basis_checkpoint_id=basis_id,
            )


class TestRepairFederatedImportStamp:
    """P2-9: the real import paths stamp ``federated_origin`` so imported
    rows never read as LOCAL neighbors (CWE-359)."""

    def test_compact_sync_import_stamped_and_excluded(self, manager: MemoryManager) -> None:
        from mnemos.cli.sync import _compact_record_to_memory_create

        record = CompactRecord(
            id="fed:peer-a:0001",
            type="learning",
            title="peer record",
            summary="peer summary content",
            key_points=["one"],
            tags=[f"project:{PROJECT}", f"agent:{NEIGHBOR}", "mnemos:learning"],
            source_agent="peer-a",
            timestamp=datetime.now(UTC).isoformat(),
        )
        create = _compact_record_to_memory_create(record)
        assert create.metadata["federated_origin"] == "peer-a"

        # Persist exactly the way run_sync_import constructs the row.
        memory = Memory(
            id=record.id,
            content=create.content,
            title=create.title,
            tags=list(create.tags),
            source=create.source,
            memory_type=create.memory_type,
            status=create.status,
            metadata=dict(create.metadata),
            project=PROJECT,
            agent=NEIGHBOR,
        )
        manager.sqlite.save(memory)
        assert is_delta_excluded(memory)

        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        assert delta["counts"]["excluded_federated"] == 1
        assert delta["agents"] == [], "an imported row must not render a local neighbor slot"

    def test_local_rows_unstamped(self, manager: MemoryManager) -> None:
        memory, _dup = manager.save_checkpoint(
            {"goals": "local goal"}, project=PROJECT, agent=NEIGHBOR, session=NEIGHBOR_SESSION
        )
        assert "federated_origin" not in memory.metadata
        assert not is_delta_excluded(memory)


class TestRepairRestHooksParity:
    """P3-5 gap: REST ``POST /hooks/{action}`` carries include_awareness."""

    def test_rest_include_awareness_parity(self, manager: MemoryManager) -> None:
        _checkpoint(manager, goals="rest parity goal", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        api_main._manager = manager
        test_app = FastAPI(title="Mnemos-Awr-Test", version="0.1.0", lifespan=lifespan)
        for route in app.routes:
            test_app.routes.append(route)
        try:
            with TestClient(test_app) as tc:
                base = {"session": SESSION, "project": PROJECT, "agent": AGENT}
                off = tc.post("/hooks/pre_llm_call", json=base)
                assert off.status_code == 200
                assert "awareness" not in off.json()
                on = tc.post("/hooks/pre_llm_call", json={**base, "include_awareness": True})
                assert on.status_code == 200
                body = on.json()
                assert body["awareness"]["agents"] == [NEIGHBOR]
                assert AWARENESS_DISCLAIMER in body["text"]
        finally:
            api_main._manager = None
