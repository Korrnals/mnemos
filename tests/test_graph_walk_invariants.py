"""ADR-0030 A0 (issue #324) — Memory Graph walk invariants I1-I3.

The CLOSING slice of wave A0-1: the 1-hop ``relates_to`` walk (the
search graph leg's second edge kind, behind the default-OFF
``mnemos.graph_walk`` flag) may only ship BECAUSE these contract tests
exist — Security's "inert until codified" condition and the chair's
same-PR sequencing ruling (ArchCom 2026-09-15). The tests extend the
#315 two-test pattern to the second kind and to the three binding
invariants of ADR-0030 §4:

* **I1 worst-link** — a record reachable through an edge chain inherits
  the STRICTEST status/gate among ALL nodes of the path; gates bind to
  the QUERY SCOPE, never to the current traversal frontier. At the
  1-hop surface the path is anchor→neighbour: the surfaced row passes
  the query's gates on its OWN merits — it never rides the anchor's
  admissibility, and the gate consulted is the query's (project scope,
  explicit status drill-down), not "whatever the frontier passed".
* **I2 absorbing quarantine** — no path of any length, no expansion,
  touches the §5 quarantine (ADR-0019). An edge is never a quarantine
  side door — including under an explicit ``status=PUBLISHED``
  drill-down, where the quarantine check is the ONLY standing guard
  (quarantined rows carry status='published' and the allowed-set gate
  is skipped for drill-downs).
* **I3 post-gate weights** — edge weights participate in ranking only
  AFTER gate filtering; they never restore eligibility (a heavy edge
  does not buy a gated row back in) and never REMOVE it (a light edge
  does not pre-filter an eligible row); the ADR-0028 id tiebreak stands
  (the A0 decay is a pure function of the fused ranking + edge table;
  the ``w_edge`` ranking formula is A1, #325).

MUTATION PROTOCOL — each named test below is mutation-verified: the
listed mutant (a minimal, plausible break of the invariant, applied to
``src/vesmaro/manager.py`` / the walk surface) turns the test RED; the
unmutated code is GREEN. The mutation runs are documented in the PR
body (mutant diff → failing test id → revert). Killers:

* ``test_i1_*`` — M1a strips the explicit-status drill-down check on
  graph rows; M1b strips the A9 project guard on graph rows.
* ``test_i2_*`` — M2 strips the absolute §5 quarantine check on graph
  rows.
* ``test_i3_*`` — M3a makes weights pre-filter eligibility in the
  walk; M3b lets a heavy edge bypass the status gate; M3c reorders the
  expansion block by edge weight (breaking the id-tiebreak line).

Test embedder: ``_HashEmbedder`` (deterministic hashed bag-of-tokens,
cosine ≈ token overlap) — same rationale as the minting suite: a
MagicMock embedder cannot discriminate and would fake the decay/
eligibility semantics. The ``_fts_only`` wipe pins neighbours to be
reachable ONLY through the edge under test (a fused row keeps its own
provenance and never exercises the walk gates).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from vesmaro.config import Settings
from vesmaro.manager import MemoryManager
from vesmaro.models import (
    Memory,
    MemoryCreate,
    MemorySource,
    MemoryStatus,
    PipelineState,
)

PROJECT = "walk-proj"
PROJECT_B = "walk-other"
AGENT = "walk-agent"


class _HashEmbedder:
    """Deterministic test embedder: hashed bag-of-tokens vectors.

    Mirrors the minting suite's embedder (``embed`` a pure function of
    the text; cosine tracks token overlap) so lexical overlap is the
    only similarity signal and every gate under test is the DECIDER.
    """

    DIM = 256

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % self.DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def _settings(tmp: Path, *, walk: bool = False, mint: bool = False) -> Settings:
    settings = Settings(
        mnemos={
            "vault_path": str(tmp / "vault"),
            "data_dir": str(tmp / "data"),
            "db_name": "test.db",
            "graph_walk": walk,
            "graph_auto_mint": mint,
        },
        scanner={"enabled": False},  # type: ignore[arg-type]
    )
    settings.resolve_paths()
    return settings


@pytest.fixture
def walk_manager(tmp_path: Path) -> Iterator[MemoryManager]:
    """Walk flag ON — the leg under test (mint stays OFF: edges here are
    hand-declared so each test pins exactly the edge it means)."""
    mgr = MemoryManager(_settings(tmp_path, walk=True))
    mgr._embedder = _HashEmbedder()
    yield mgr
    mgr.close()


@pytest.fixture
def plain_manager(tmp_path: Path) -> Iterator[MemoryManager]:
    """Both flags OFF (the shipped default) — baseline semantics."""
    mgr = MemoryManager(_settings(tmp_path))
    mgr._embedder = _HashEmbedder()
    yield mgr
    mgr.close()


@pytest.fixture
def fuel_manager(tmp_path: Path) -> Iterator[MemoryManager]:
    """Mint + walk both ON — the acceptance-telemetry surface (ADR-0030
    Decision 2, "Acceptance and guards")."""
    mgr = MemoryManager(_settings(tmp_path, walk=True, mint=True))
    mgr._embedder = _HashEmbedder()
    yield mgr
    mgr.close()


def _add(
    mgr: MemoryManager,
    content: str,
    *,
    status: MemoryStatus = MemoryStatus.PUBLISHED,
    project: str = PROJECT,
) -> Memory:
    tags = [f"project:{project}", f"agent:{AGENT}", "mnemos:test"]
    return mgr.add(
        MemoryCreate(content=content, tags=tags, source=MemorySource.MCP, status=status),
        project=project,
        agent=AGENT,
    )


def _fts_only(mgr: MemoryManager) -> None:
    """Drop every vector row — the vector leg returns nothing, so a row
    the FTS query does not match is reachable ONLY through an edge (the
    prior-art pin from tests/test_search_v2_graph_leg.py)."""
    mgr.vectors.wipe()


# ── Flag contract: the second kind is inert until the walk flag is ON ────────


class TestWalkFlagContract:
    def test_default_settings_flag_off(self) -> None:
        assert Settings().mnemos.graph_walk is False

    def test_relates_to_inert_when_flag_off(self, plain_manager: MemoryManager) -> None:
        """A DECLARED relates_to edge does nothing for search under the
        shipped default — the minted-edge variant of this pin lives in
        tests/test_graph_auto_minting.py; both kinds of fuel stay inert
        behind the same flag."""
        anchor = _add(plain_manager, "anchor note about tide schedules")
        sibling = _add(plain_manager, "ledger reconciliation figures dormant note")
        plain_manager.add_memory_edge(anchor.id, sibling.id, kind="relates_to")
        _fts_only(plain_manager)

        results = plain_manager.search("anchor tide schedules", limit=5)
        ids = {r.memory.id for r in results}
        assert anchor.id in ids
        assert sibling.id not in ids, "relates_to walked with the flag OFF"
        assert all(not r.via_graph for r in results)

    def test_relates_to_walked_when_flag_on_outgoing(
        self, walk_manager: MemoryManager
    ) -> None:
        """Flag ON: the anchor surfaces its outgoing relates_to neighbour
        with via_graph provenance and the decayed slot (the leg extends
        to the second kind; supersedes behaviour is unchanged from #315).
        """
        anchor = _add(walk_manager, "anchor note about tide schedules")
        sibling = _add(walk_manager, "ledger reconciliation figures dormant note")
        walk_manager.add_memory_edge(anchor.id, sibling.id, kind="relates_to")
        _fts_only(walk_manager)

        results = walk_manager.search("anchor tide schedules", limit=5)
        by_id = {r.memory.id: r for r in results}
        assert anchor.id in by_id
        assert sibling.id in by_id, "the relates_to neighbour must surface via the walk"
        assert by_id[sibling.id].via_graph is True
        assert by_id[anchor.id].via_graph is False
        # The decay rule binds the second kind identically: below its anchor.
        assert by_id[sibling.id].score < by_id[anchor.id].score

    def test_relates_to_walked_when_flag_on_incoming(
        self, walk_manager: MemoryManager
    ) -> None:
        """Flag ON, reverse direction: an incoming relates_to edge (the
        neighbour declared the edge TO the anchor) surfaces the neighbour
        too — the walk is direction-agnostic, like the supersedes leg."""
        anchor = _add(walk_manager, "anchor note about tide schedules")
        sibling = _add(walk_manager, "ledger reconciliation figures dormant note")
        walk_manager.add_memory_edge(sibling.id, anchor.id, kind="relates_to")
        _fts_only(walk_manager)

        results = walk_manager.search("anchor tide schedules", limit=5)
        by_id = {r.memory.id: r for r in results}
        assert sibling.id in by_id
        assert by_id[sibling.id].via_graph is True

    def test_supersedes_still_walked_flag_off(self, plain_manager: MemoryManager) -> None:
        """The flag gates ONLY the second kind: the v1 supersedes leg is
        unconditional (pinning that #324 did not narrow #315)."""
        anchor = _add(plain_manager, "anchor note about tide schedules")
        sibling = _add(plain_manager, "ledger reconciliation figures dormant note")
        plain_manager.add_memory_edge(anchor.id, sibling.id, kind="supersedes")
        _fts_only(plain_manager)

        results = plain_manager.search("anchor tide schedules", limit=5)
        by_id = {r.memory.id: r for r in results}
        assert sibling.id in by_id
        assert by_id[sibling.id].via_graph is True

    def test_minted_edge_reaches_search_end_to_end(
        self, fuel_manager: MemoryManager
    ) -> None:
        """The Product condition: fuel that never reaches search is a
        second dormant leg. With mint + walk ON, the edge the auto-dedupe
        rule minted on write is traversed by the next search — the full
        A0-1 loop (write mints → search walks)."""
        base = _add(fuel_manager, "conveyor belt alignment procedure station seven")
        revision = _add(fuel_manager, "conveyor belt alignment procedure station seven revision")
        rows = fuel_manager.sqlite._get_conn().execute(
            "SELECT from_memory_id, to_memory_id FROM memory_edges WHERE kind='relates_to'"
        ).fetchall()
        assert rows, "fixture: minting produced the edge"
        _fts_only(fuel_manager)

        # "revision" matches ONLY the newer row (FTS); the minted edge
        # points revision→base, so the walk surfaces the base.
        results = fuel_manager.search("revision", limit=5)
        by_id = {r.memory.id: r for r in results}
        assert revision.id in by_id
        assert base.id in by_id, "the MINTED edge must be walked (flag ON)"
        assert by_id[base.id].via_graph is True


# ── I1 — worst-link: the strictest node on the path governs ──────────────────


class TestI1WorstLink:
    def test_i1_strictest_status_governs_not_the_anchor(
        self, walk_manager: MemoryManager
    ) -> None:
        """I1 core: the neighbour inherits the STRICTEST status on the
        anchor→neighbour path — its OWN. The anchor is published and
        fused, but a RAW neighbour does not ride that admissibility.

        Killer mutants: stripping the default ``allowed``-set check on
        graph rows, or M3b (a heavy edge buying back eligibility — the
        weight here is the raised auto-dedupe weight) both turn this
        RED.
        """
        anchor = _add(walk_manager, "published anchor about harbour cranes")
        raw_sibling = _add(walk_manager, "raw dormant ledger sibling note", status=MemoryStatus.RAW)
        walk_manager.add_memory_edge(
            anchor.id, raw_sibling.id, kind="relates_to", weight=2.0
        )
        control = _add(walk_manager, "published dormant ledger control sibling")
        walk_manager.add_memory_edge(anchor.id, control.id, kind="relates_to")
        _fts_only(walk_manager)

        results = walk_manager.search("published anchor harbour cranes", limit=5)
        by_id = {r.memory.id: r for r in results}
        assert anchor.id in by_id
        assert raw_sibling.id not in by_id, (
            "I1 violated: the RAW neighbour rode the anchor's admissibility"
        )
        # Control: the published neighbour of the same anchor DOES walk —
        # the RAW exclusion is the gate, not a dead leg.
        assert control.id in by_id
        assert by_id[control.id].via_graph is True

    def test_i1_gates_bind_to_query_scope_status_drilldown(
        self, walk_manager: MemoryManager
    ) -> None:
        """I1 "gates bind to the QUERY SCOPE, never to the current
        traversal frontier": an explicit ``status=`` drill-down gates the
        relates_to walk exactly like the fused legs (review F1 extended
        to the second kind). The fixture rows all lexically match the
        query (the anchor must be findable); the siblings differ by
        STATUS, not tokens — the FTS store-level status filter keeps
        them out of the fused page, so only the walk could widen the
        drill-down.

        Killer mutant M1a: strip ``if status is not None and
        neighbour.status != status: continue`` from the walk loop.
        """
        query = "zeppelin fleet record"
        pub = _add(walk_manager, "published zeppelin fleet record entry")
        raw_sibling = _add(
            walk_manager, "raw zeppelin fleet record draft sibling", status=MemoryStatus.RAW
        )
        walk_manager.add_memory_edge(pub.id, raw_sibling.id, kind="relates_to")
        _fts_only(walk_manager)

        ids = {
            r.memory.id for r in walk_manager.search(query, status=MemoryStatus.PUBLISHED, limit=5)
        }
        assert pub.id in ids
        assert raw_sibling.id not in ids, (
            "I1/M1a violated: the RAW neighbour leaked into a PUBLISHED drill-down via relates_to"
        )

    def test_i1_gates_bind_to_query_scope_project(
        self, walk_manager: MemoryManager
    ) -> None:
        """I1 scope binding (review F2 extended to the second kind): a
        scoped search never leaks a cross-project relates_to neighbour —
        the edge stores ids only, so the gate must consult the query's
        project scope against the SQLite authority, never the frontier's.

        Killer mutant M1b: strip the A9 authoritative project guard on
        the graph rows.
        """
        anchor = _add(walk_manager, "anchor lighthouses beacon record")
        foreign = _add(
            walk_manager, "foreign lighthouse sibling from another project", project=PROJECT_B
        )
        walk_manager.add_memory_edge(anchor.id, foreign.id, kind="relates_to")
        _fts_only(walk_manager)

        scoped = walk_manager.search("anchor lighthouses beacon", project=PROJECT, limit=5)
        assert anchor.id in {r.memory.id for r in scoped}
        assert all(r.memory.project == PROJECT for r in scoped), (
            "I1/M1b violated: a cross-project relates_to neighbour leaked into a scoped search"
        )
        assert all(not r.project_scope_fallback for r in scoped)

        # Unscoped control: the explicit global mode DOES walk the same
        # edge — the gate is scope-only, not a kill of the leg.
        unscoped = walk_manager.search("anchor lighthouses beacon", limit=5)
        by_id = {r.memory.id: r for r in unscoped}
        assert foreign.id in by_id
        assert by_id[foreign.id].via_graph is True


# ── I2 — absorbing quarantine: no path touches §5 ────────────────────────────


class TestI2AbsorbingQuarantine:
    def test_i2_quarantined_neighbour_absorbs_every_path(
        self, walk_manager: MemoryManager
    ) -> None:
        """I2 core: a §5-quarantined relates_to neighbour never surfaces —
        no path, no framing. Three framings, each sufficient on its own:
        (a) the default gate, (b) an explicit ``status=PUBLISHED``
        drill-down — where the allowed-set check is SKIPPED and the §5
        predicate is the ONLY standing guard (quarantined rows carry
        status='published'), (c) ``include_raw=True``. The anchor stays
        surfaced throughout — quarantine absorbs the edge target, not
        the query.

        Killer mutant M2: strip ``if is_quarantined(neighbour):
        continue`` from the walk loop — all three framings go RED (the
        drill-down sharpest: nothing else stands between the row and the
        page).
        """
        anchor = _add(walk_manager, "gate note about tunnels")
        quarantined = _add(walk_manager, "old tunnels note now quarantined")
        walk_manager.add_memory_edge(anchor.id, quarantined.id, kind="relates_to")
        walk_manager.sqlite.update_fields(
            quarantined.id,
            pipeline_state=PipelineState.QUARANTINED.value,
            quarantine_reason="test quarantine",
        )
        _fts_only(walk_manager)

        default = walk_manager.search("gate tunnels", limit=5)
        assert anchor.id in {r.memory.id for r in default}
        assert quarantined.id not in {r.memory.id for r in default}, (
            "I2 violated under the default gate"
        )

        drilldown = walk_manager.search("gate tunnels", status=MemoryStatus.PUBLISHED, limit=5)
        assert quarantined.id not in {r.memory.id for r in drilldown}, (
            "I2/M2 violated: the quarantined neighbour leaked into a PUBLISHED "
            "drill-down — the §5 predicate was its only guard"
        )

        raw_mode = walk_manager.search("gate tunnels", include_raw=True, limit=5)
        assert quarantined.id not in {r.memory.id for r in raw_mode}, (
            "I2 violated under include_raw"
        )

    def test_i2_absorption_survives_maximal_path_pressure(
        self, walk_manager: MemoryManager
    ) -> None:
        """"No path of ANY length" at the 1-hop surface: even when the
        quarantined row is the ONLY remaining headroom filler (the fused
        page is one row short and the edge is the only candidate), the
        page ships short rather than absorbing quarantine. Heavy weight
        and both directions pinned — absorption is unconditional."""
        anchor = _add(walk_manager, "lonely anchor about mist valleys")
        quarantined = _add(walk_manager, "quarantined mist valleys sibling")
        walk_manager.add_memory_edge(
            quarantined.id, anchor.id, kind="relates_to", weight=1000.0
        )
        walk_manager.sqlite.update_fields(
            quarantined.id,
            pipeline_state=PipelineState.QUARANTINED.value,
            quarantine_reason="test quarantine",
        )
        _fts_only(walk_manager)

        results = walk_manager.search("lonely anchor mist valleys", limit=5)
        ids = {r.memory.id for r in results}
        assert anchor.id in ids
        assert quarantined.id not in ids
        assert all(not r.via_graph or r.memory.id != quarantined.id for r in results)


# ── I3 — post-gate weights: rank-only, eligibility untouchable ────────────────


class TestI3PostGateWeights:
    def test_i3_weight_never_restores_eligibility(
        self, walk_manager: MemoryManager
    ) -> None:
        """I3 clause 1: weights enter ranking only AFTER gate filtering —
        a heavy edge (1000.0, the extreme the validation still accepts)
        buys a gated row NOTHING. The RAW sibling is dropped by the
        status gate regardless of weight; the quarantined sibling is
        dropped by §5 regardless of weight (the I2 lens, weighted).

        Killer mutant M3b: let the edge weight bypass the status gates
        (the default ``allowed`` set AND the explicit drill-down check)
        when the walked edge is heavy.
        """
        anchor = _add(walk_manager, "published anchor about windmills")
        raw_heavy = _add(
            walk_manager, "raw dormant ledger sibling heavy", status=MemoryStatus.RAW
        )
        walk_manager.add_memory_edge(anchor.id, raw_heavy.id, kind="relates_to", weight=1000.0)
        quarantined_heavy = _add(walk_manager, "quarantined dormant ledger sibling heavy")
        walk_manager.add_memory_edge(
            anchor.id, quarantined_heavy.id, kind="relates_to", weight=1000.0
        )
        walk_manager.sqlite.update_fields(
            quarantined_heavy.id,
            pipeline_state=PipelineState.QUARANTINED.value,
            quarantine_reason="test quarantine",
        )
        _fts_only(walk_manager)

        default = walk_manager.search("published anchor windmills", limit=5)
        ids = {r.memory.id for r in default}
        assert anchor.id in ids
        assert raw_heavy.id not in ids, (
            "I3/M3b violated: weight restored a gated row's eligibility (default gate)"
        )

        drilldown = walk_manager.search(
            "published anchor windmills", status=MemoryStatus.PUBLISHED, limit=5
        )
        ids = {r.memory.id for r in drilldown}
        assert anchor.id in ids
        assert raw_heavy.id not in ids, (
            "I3/M3b violated: a 1000-weight edge walked a RAW row into a PUBLISHED drill-down"
        )
        assert quarantined_heavy.id not in ids, (
            "I3 violated: a 1000-weight edge walked a quarantined row into a drill-down"
        )

    def test_i3_weight_never_removes_eligibility(
        self, walk_manager: MemoryManager
    ) -> None:
        """I3 clause 2: eligibility cuts the OTHER way too — a LIGHT edge
        (0.25, valid per the write validation) must not pre-filter an
        eligible neighbour out of the walk. Weights scale ranking (A1),
        never membership.

        Killer mutant M3a: make the walk pre-filter by edge weight (e.g.
        drop weight<1.0 edges in the neighbour collection).
        """
        anchor = _add(walk_manager, "anchor note about tide schedules")
        light_sibling = _add(walk_manager, "ledger reconciliation figures dormant note")
        walk_manager.add_memory_edge(anchor.id, light_sibling.id, kind="relates_to", weight=0.25)
        _fts_only(walk_manager)

        results = walk_manager.search("anchor tide schedules", limit=5)
        by_id = {r.memory.id: r for r in results}
        assert light_sibling.id in by_id, (
            "I3/M3a violated: a 0.25-weight edge pre-filtered an eligible neighbour"
        )
        assert by_id[light_sibling.id].via_graph is True

    def test_i3_ranking_deterministic_id_tiebreak(
        self, walk_manager: MemoryManager
    ) -> None:
        """I3 clause 3: the ADR-0028 determinism line — the appended
        block is a pure function of the fused ranking + the edge table.
        Two eligible neighbours of the SAME anchor (equal decay: same
        first-anchor rank) surface in ID order regardless of their
        weights (5.0 vs 0.5): at A0 the decay ignores ``w_edge`` (the
        formula is A1), and no weight may reorder the block pre-gate.

        Killer mutant M3c: order the expansion by edge weight
        (heavier first) instead of the id-sorted neighbour walk.
        """
        anchor = _add(walk_manager, "anchor note about tide schedules")
        a = _add(walk_manager, "alpha dormant ledger reconciliation note")
        b = _add(walk_manager, "beta dormant ledger reconciliation note")
        # Deterministic opposition: the LEXICOGRAPHICALLY LAST id gets the
        # HEAVIEST weight, so a weight-ordered block is always the exact
        # reverse of the id-ordered one (random UUIDs would leave the two
        # orders coincidentally equal half the time and let the mutant
        # survive).
        lo, hi = sorted([a.id, b.id])
        walk_manager.add_memory_edge(anchor.id, lo, kind="relates_to", weight=0.5)
        walk_manager.add_memory_edge(anchor.id, hi, kind="relates_to", weight=5.0)
        _fts_only(walk_manager)

        results = walk_manager.search("anchor tide schedules", limit=5)
        graph_rows = [r for r in results if r.via_graph]
        assert {r.memory.id for r in graph_rows} == {a.id, b.id}
        # Equal decay ⇒ the id tiebreak decides; the weight opposition
        # (0.5 on the id-first row, 5.0 on the id-last) must not reorder.
        assert [r.memory.id for r in graph_rows] == [lo, hi]
        assert graph_rows[0].score == graph_rows[1].score


# ── Acceptance telemetry (ADR-0030 Decision 2: minting-rate AND walk share) ──


class TestWalkAcceptanceTelemetry:
    def test_flags_on_minting_rate_and_walk_share_positive(
        self, fuel_manager: MemoryManager
    ) -> None:
        """Slice acceptance with both flags ON on a fixture corpus:
        (1) minting-rate > 0 via ``graph_mint_stats()`` — the near-dup
        write minted a real edge; (2) the share of searches with
        ``via_graph=True`` > 0 — a live traversal enriched a real page,
        counted by the new ``graph_enriched_requests_total``. The
        bench-s1 recall guard runs with flags OFF (the stand measures
        default semantics); live-flag measurement belongs to the
        A0-review, not the stand."""
        base = _add(fuel_manager, "conveyor belt alignment procedure station seven")
        revision = _add(
            fuel_manager, "conveyor belt alignment procedure station seven revision"
        )
        dormant = _add(fuel_manager, "quarterly reconciliation figures dormant ledger")
        fuel_manager.add_memory_edge(base.id, dormant.id, kind="relates_to")
        _fts_only(fuel_manager)

        # Minting-rate: the near-dup write minted an edge (flag ON).
        mint = fuel_manager.graph_mint_stats()
        assert mint["auto_dedupe_edges_total"] > 0
        assert mint["auto_dedupe_edges_by_project"].get(PROJECT, 0) > 0

        # Live traversal: the query fuses the near-dup pair; the walk
        # (flag ON) appends the dormant row via the declared edge.
        results = fuel_manager.search("conveyor belt alignment procedure", limit=5)
        by_id = {r.memory.id: r for r in results}
        assert base.id in by_id and revision.id in by_id
        assert dormant.id in by_id
        assert by_id[dormant.id].via_graph is True

        stats = fuel_manager.search_stats()
        assert stats["requests_total"] >= 1
        assert stats["graph_enriched_requests_total"] >= 1
        share = stats["graph_enriched_requests_total"] / stats["requests_total"]
        assert share > 0, "the walk share (enriched/requests) must be measurable and positive"

        dashboard = fuel_manager.dashboard_stats()
        assert dashboard["graph"]["walk_enabled"] is True
        assert dashboard["graph"]["auto_mint_enabled"] is True
        assert dashboard["search"]["graph_enriched_requests_total"] >= 1

    def test_walk_share_zero_flag_off(self, plain_manager: MemoryManager) -> None:
        """Honest zeros: with the flag OFF the counter stays at 0 even
        when supersedes edges enrich pages — the counter measures the
        leg's flags-on acceptance, and the default deployment reads 0."""
        anchor = _add(plain_manager, "anchor note about tide schedules")
        sibling = _add(plain_manager, "ledger reconciliation figures dormant note")
        plain_manager.add_memory_edge(anchor.id, sibling.id, kind="relates_to")
        _fts_only(plain_manager)

        results = plain_manager.search("anchor tide schedules", limit=5)
        assert all(not r.via_graph for r in results)
        stats = plain_manager.search_stats()
        assert stats["graph_enriched_requests_total"] == 0
        assert plain_manager.dashboard_stats()["graph"]["walk_enabled"] is False

    def test_prometheus_carries_walk_share(self, fuel_manager: MemoryManager) -> None:
        """The counter renders in the Prometheus text (the A0-review's
        scrape surface), mirroring the minting counter's exposure."""
        from vesmaro.api.main import _prometheus_text

        base = _add(fuel_manager, "conveyor belt alignment procedure station seven")
        dormant = _add(fuel_manager, "quarterly reconciliation figures dormant ledger")
        fuel_manager.add_memory_edge(base.id, dormant.id, kind="relates_to")
        _fts_only(fuel_manager)
        enriched = fuel_manager.search("conveyor belt alignment procedure", limit=5)
        assert any(r.via_graph for r in enriched), "fixture: the walk enriched the page"

        text = _prometheus_text(fuel_manager)
        assert "# HELP mnemos_search_graph_enriched_requests_total" in text
        assert "# TYPE mnemos_search_graph_enriched_requests_total counter" in text
        assert "mnemos_search_graph_enriched_requests_total 1" in text
