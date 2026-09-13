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

All secrets below are obviously fake EXAMPLE-style values built from the
detector's own pattern catalogue; real credentials never appear.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import mnemos.mcp_server as mcp_mod
from mnemos.awareness import (
    ABSTENTION_TASK_LABEL,
    AWARENESS_DISCLAIMER,
    AWARENESS_LANE,
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
from mnemos.config import Settings
from mnemos.hooks import dispatch_hook
from mnemos.lanes import AWARENESS_CURSOR_PREFIX, read_awareness_cursor
from mnemos.manager import MemoryManager
from mnemos.mcp_server import _dispatch, list_tools
from mnemos.models import MemoryCreate, MemorySource, MemoryStatus

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
        _checkpoint(manager, goals="guard the release", agent=NEIGHBOR, session=NEIGHBOR_SESSION)
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        block = delta_blocks(delta)[0]
        assert block["content"].startswith(f"{NEIGHBOR}: 1 entries, last ")
        assert "goal guard the release" in block["content"]


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

        assert "### observed — server-verified hook facts" in text
        assert "### self-reported — unverified peer claims" in text
        # The R3 disclaimer frame rides VERBATIM.
        assert AWARENESS_DISCLAIMER in text
        # The goal (self-reported) never appears in the observed section.
        observed_part = text.split("### self-reported")[0]
        assert "rebuild the index pipeline" not in observed_part
        self_reported_part = text.split("### self-reported")[1]
        assert "rebuild the index pipeline" in self_reported_part

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


class TestScanRefusal:
    def test_redact_mode_goal_redacted(self, manager: MemoryManager) -> None:
        _checkpoint(
            manager,
            goals=f"deploy with key {FAKE_AWS_KEY} inside",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
        delta = project_delta(manager, project=PROJECT, since=_hour_ago_iso(), exclude_agent=AGENT)
        title = delta["agents"][0]["goal_title"]
        assert title is not None
        assert FAKE_AWS_KEY not in title
        assert delta["counts"]["redactions"] >= 1
        assert FAKE_AWS_KEY not in render_awareness_section(delta, [])

    def test_refuse_mode_goal_dropped_observed_facts_stay(
        self, refuse_manager: MemoryManager
    ) -> None:
        _checkpoint(
            refuse_manager,
            goals=f"deploy with key {FAKE_AWS_KEY} inside",
            agent=NEIGHBOR,
            session=NEIGHBOR_SESSION,
        )
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


class TestOffPathEquivalence:
    def test_pre_llm_call_off_is_byte_identical_to_assemble(
        self, manager: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flag ABSENT (the default) → the hook output is EXACTLY the
        pre-#254 shape: assemble result + the two enrichment keys, nothing
        else (no awareness key anywhere, byte-for-byte)."""
        monkeypatch.setattr("mnemos.assemble.datetime", _FrozenDatetime)
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
        monkeypatch.setattr("mnemos.assemble.datetime", _FrozenDatetime)
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
        monkeypatch.setattr("mnemos.assemble.datetime", _FrozenDatetime)
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
        key = f"{AWARENESS_CURSOR_PREFIX}{PROJECT}:{AGENT}:{SESSION}"
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
        monkeypatch.setattr("mnemos.assemble.datetime", _FrozenDatetime)
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
