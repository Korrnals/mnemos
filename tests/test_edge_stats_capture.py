"""ADR-0030 A0 (issue #323) — used/rejected feedback CAPTURE into edge_stats.

CAPTURE ONLY: events land in the append-only ``edge_stats`` table and
influence nothing — no ranking, no status, no eligibility (APPLY is
#325). The used-report binds to the existing search call: the ids are
the citation ids already riding the search response.

I5-capture requirements, each as a test:

* scoped — only records visible to the calling agent under the same
  gates as search (A9 project guard + the ADR-0018 admissible default
  set; quarantine excluded absolutely);
* uniform-404 — nonexistent / cross-project / inadmissible /
  quarantined ids land in ONE ``out_of_scope`` bucket, the response
  carries no per-id reason (no existence oracle);
* ``event_id`` PK idempotency — an agent retry of the same report
  contributes no double weight;
* append-only audit trail — enforced by schema triggers (UPDATE and
  DELETE abort at the DB level), not by convention;
* volume-cap per principal — over-cap events are dropped, never error;
* bounded counter clamp from day one — derived counters cannot exceed
  ``EDGE_STATS_COUNTER_CLAMP`` however many rows exist;
* scope fields (project/agent) recorded from the FIRST event;
* default-off flag — flag off means zero writes and zero telemetry;
* failure isolation — a capture-side failure never fails the caller.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vesmaro.config import Settings
from vesmaro.manager import MemoryManager
from vesmaro.models import MemoryCreate, MemorySource, MemoryStatus
from vesmaro.storage.sqlite_store import (
    _EDGE_STATS_KINDS,
    SQLiteStore,
)

PROJECT = "fb-proj"
AGENT = "fb-agent"


def _settings(tmp: Path, *, feedback: bool = False) -> Settings:
    settings = Settings(
        mnemos={
            "vault_path": str(tmp / "vault"),
            "data_dir": str(tmp / "data"),
            "db_name": "test.db",
        },
        search={"feedback_capture_enabled": feedback},
        scanner={"enabled": False},  # type: ignore[arg-type]
    )
    settings.resolve_paths()
    return settings


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    s = SQLiteStore(tmp_path / "edge_stats.db")
    yield s
    s.close()


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[MemoryManager]:
    """Flag OFF — the shipped default."""
    mgr = MemoryManager(_settings(tmp_path))
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 384
    mgr._embedder = mock_embedder
    yield mgr
    mgr.close()


@pytest.fixture
def manager_on(tmp_path: Path) -> Iterator[MemoryManager]:
    """Flag ON — the capture leg armed (explicit deployment opt-in)."""
    mgr = MemoryManager(_settings(tmp_path, feedback=True))
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 384
    mgr._embedder = mock_embedder
    yield mgr
    mgr.close()


def _add(
    mgr: MemoryManager,
    content: str,
    *,
    status: MemoryStatus = MemoryStatus.PUBLISHED,
    project: str = PROJECT,
) -> object:
    data = MemoryCreate(
        content=content,
        tags=[f"project:{project}", f"agent:{AGENT}", "mnemos:test"],
        source=MemorySource.MCP,
        status=status,
    )
    return mgr.add(data, project=project, agent=AGENT)


def _rows(mgr: MemoryManager) -> list[sqlite3.Row]:
    return mgr.sqlite._get_conn().execute(
        "SELECT event_id, memory_id, kind, project, agent, created_at"
        " FROM edge_stats ORDER BY created_at, event_id"
    ).fetchall()


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, dict[str, object]]:
    return {
        str(r[1]): {"type": str(r[2]), "notnull": int(r[3]), "dflt": r[4], "pk": int(r[5])}
        for r in conn.execute(f"PRAGMA table_info({table})")  # nosec B608 - test-local name
    }


def _object_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        is not None
    )


# ── Schema: final shape from day one ──────────────────────────────────────────


class TestSchema:
    def test_columns_pk_and_defaults(self, store: SQLiteStore) -> None:
        cols = _columns(store._get_conn(), "edge_stats")
        assert set(cols) == {"event_id", "memory_id", "kind", "project", "agent", "created_at"}
        # event_id is THE primary key — idempotency per event.
        assert cols["event_id"]["pk"] == 1
        assert [c for c, v in cols.items() if v["pk"]] == ["event_id"]
        # Scope fields present and NOT NULL from the first event (the
        # retrofit-loses-boundary lesson — they cannot be backfilled).
        assert cols["project"]["notnull"] == 1
        assert cols["agent"]["notnull"] == 1
        assert cols["memory_id"]["notnull"] == 1
        assert cols["created_at"]["notnull"] == 1

    def test_indexes_present(self, store: SQLiteStore) -> None:
        conn = store._get_conn()
        assert _object_exists(conn, "idx_edge_stats_memory")
        assert _object_exists(conn, "idx_edge_stats_scope")

    def test_check_accepts_exactly_the_whitelist(self, store: SQLiteStore) -> None:
        """The SQL CHECK and _EDGE_STATS_KINDS are the same set (DB-level
        sync, the _EDGE_KINDS rule), and anything else aborts."""
        assert set(_EDGE_STATS_KINDS) == {"used", "rejected"}
        conn = store._get_conn()
        for kind in _EDGE_STATS_KINDS:
            conn.execute(
                "INSERT INTO edge_stats (event_id, memory_id, kind, created_at)"
                " VALUES (?,?,?,?)",
                (f"ev-{kind}", "m-x", kind, "2026-09-16T00:00:00+00:00"),
            )
        assert conn.execute("SELECT COUNT(*) FROM edge_stats").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO edge_stats (event_id, memory_id, kind, created_at)"
                " VALUES (?,?,?,?)",
                ("ev-bad", "m-x", "maybe", "2026-09-16T00:00:00+00:00"),
            )

    def test_no_foreign_key_on_memory_id(self, store: SQLiteStore) -> None:
        """The audit trail outlives its subject: a row for a memory id
        that does not exist in `memories` is storable at the DB level
        (visibility is the manager's I5 gate, not a FK), and deleting
        the subject never rewrites history."""
        store._get_conn().execute(
            "INSERT INTO edge_stats (event_id, memory_id, kind, created_at)"
            " VALUES ('e1', 'never-existed', 'used', '2026-09-16T00:00:00+00:00')"
        )
        assert store.count_edge_stats() == 1

    def test_legacy_db_gains_table_on_connect(self, tmp_path: Path) -> None:
        """A DB written before this change has no edge_stats; the next
        connect creates it empty (CREATE TABLE IF NOT EXISTS in the
        schema script — no migration entry needed, the
        memory_workflow_history pattern)."""
        db = tmp_path / "legacy.db"
        s = SQLiteStore(db)
        assert s.count_edge_stats() == 0  # force the lazy bootstrap
        s.close()
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE edge_stats")  # simulate the pre-#323 shape
        conn.commit()
        conn.close()

        s2 = SQLiteStore(db)
        assert "event_id" in _columns(s2._get_conn(), "edge_stats")
        assert s2.count_edge_stats() == 0
        s2.close()


# ── Append-only audit trail (DB-level guarantee) ─────────────────────────────


class TestAppendOnly:
    def test_update_aborts(self, store: SQLiteStore) -> None:
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO edge_stats (event_id, memory_id, kind, created_at)"
            " VALUES ('e1', 'm-x', 'used', '2026-09-16T00:00:00+00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE edge_stats SET kind = 'rejected' WHERE event_id = 'e1'")

    def test_delete_aborts(self, store: SQLiteStore) -> None:
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO edge_stats (event_id, memory_id, kind, created_at)"
            " VALUES ('e1', 'm-x', 'used', '2026-09-16T00:00:00+00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM edge_stats WHERE event_id = 'e1'")

    def test_memory_deletion_neither_fails_nor_rewrites(
        self, manager_on: MemoryManager
    ) -> None:
        """Audit rows outlive their subject: deleting the cited memory
        succeeds and the event row stays (no FK, no cascade)."""
        m = _add(manager_on, "cited once, deleted later")
        assert (
            manager_on.report_search_feedback([m.id], kind="used", project=PROJECT)["captured"]
            == 1
        )
        manager_on.sqlite.delete(m.id)
        assert len(_rows(manager_on)) == 1


# ── Store: idempotency, volume-cap, clamp ─────────────────────────────────────


class TestStoreRecord:
    def test_insert_and_duplicate(self, store: SQLiteStore) -> None:
        assert store.record_edge_stat_event("e1", "m-a", kind="used") == "inserted"
        # Same event_id — an agent retry: no second row, no double weight.
        assert store.record_edge_stat_event("e1", "m-a", kind="used") == "duplicate"
        assert store.count_edge_stats() == 1

    def test_scope_fields_recorded_from_first_event(self, store: SQLiteStore) -> None:
        store.record_edge_stat_event("e1", "m-a", kind="used", project="p1", agent="ag1")
        row = store._get_conn().execute(
            "SELECT memory_id, kind, project, agent, created_at FROM edge_stats"
        ).fetchone()
        assert tuple(row) == ("m-a", "used", "p1", "ag1", row["created_at"])
        assert row["created_at"]  # timestamped

    def test_unknown_kind_rejected(self, store: SQLiteStore) -> None:
        with pytest.raises(ValueError, match="unknown feedback kind"):
            store.record_edge_stat_event("e1", "m-a", kind="maybe")

    def test_empty_event_id_rejected(self, store: SQLiteStore) -> None:
        with pytest.raises(ValueError, match="event_id"):
            store.record_edge_stat_event("", "m-a", kind="used")

    def test_volume_cap_per_principal(
        self, store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Over-cap events are DROPPED (a normal outcome, never an error),
        and the cap is per (project, agent) bucket — a hot principal
        cannot wedge unbounded rows (storage-DoS / APPLY pre-poisoning)."""
        monkeypatch.setattr("vesmaro.storage.sqlite_store.EDGE_STATS_EVENTS_PER_PRINCIPAL_CAP", 3)
        for i in range(3):
            assert (
                store.record_edge_stat_event(f"e-{i}", "m-a", kind="used", project="p1", agent="a1")
                == "inserted"
            )
        assert (
            store.record_edge_stat_event("e-3", "m-a", kind="used", project="p1", agent="a1")
            == "cap_dropped"
        )
        # A different principal is unaffected — the cap is per bucket.
        assert (
            store.record_edge_stat_event("e-x", "m-a", kind="used", project="p2", agent="a1")
            == "inserted"
        )
        assert store.count_edge_stats() == 4

    def test_counters_clamped_from_day_one(
        self, store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whatever the table holds, no derived counter exceeds
        EDGE_STATS_COUNTER_CLAMP — capture cannot drift before APPLY."""
        monkeypatch.setattr("vesmaro.storage.sqlite_store.EDGE_STATS_COUNTER_CLAMP", 5)
        for i in range(8):
            store.record_edge_stat_event(f"u-{i}", "m-a", kind="used", project="p1")
        for i in range(3):
            store.record_edge_stat_event(f"r-{i}", "m-a", kind="rejected", project="p1")
        assert store.get_edge_stats_counters("m-a") == {"used": 5, "rejected": 3}

    def test_counters_absent_kinds_zero(self, store: SQLiteStore) -> None:
        assert store.get_edge_stats_counters("m-unknown") == {"used": 0, "rejected": 0}

    def test_count_edge_stats_by_kind(self, store: SQLiteStore) -> None:
        store.record_edge_stat_event("e1", "m-a", kind="used")
        store.record_edge_stat_event("e2", "m-b", kind="rejected")
        assert store.count_edge_stats() == 2
        assert store.count_edge_stats(kind="used") == 1
        assert store.count_edge_stats(kind="rejected") == 1


# ── Manager: I5 gates, flag, isolation ────────────────────────────────────────


class TestFlagOff:
    def test_default_off_zero_capture(self, manager: MemoryManager) -> None:
        m = _add(manager, "visible and published")
        outcome = manager.report_search_feedback([m.id], kind="used", project=PROJECT)
        assert outcome["flag_enabled"] is False
        assert outcome["captured"] == 0
        assert len(_rows(manager)) == 0
        # Zero telemetry too — the disabled surface leaves no heatmap.
        stats = manager.feedback_capture_stats()
        assert stats["enabled"] is False
        assert stats["events_total"] == 0
        assert stats["since_restart"]["reports_total"] == 0

    def test_config_default_is_off(self) -> None:
        from vesmaro.config import SearchConfig

        assert SearchConfig().feedback_capture_enabled is False


class TestCaptureHappyPath:
    def test_used_event_lands_with_scope(self, manager_on: MemoryManager) -> None:
        m = _add(manager_on, "cited by the harness")
        outcome = manager_on.report_search_feedback(
            [m.id], kind="used", project=PROJECT, agent=AGENT, event_id="report-1"
        )
        assert outcome["captured"] == 1
        row = _rows(manager_on)[0]
        assert row["memory_id"] == m.id
        assert (row["kind"], row["project"], row["agent"]) == ("used", PROJECT, AGENT)

    def test_rejected_kind_captured(self, manager_on: MemoryManager) -> None:
        m = _add(manager_on, "dismissed citation")
        outcome = manager_on.report_search_feedback([m.id], kind="rejected", project=PROJECT)
        assert outcome["captured"] == 1
        assert _rows(manager_on)[0]["kind"] == "rejected"

    def test_unknown_kind_is_a_caller_bug(self, manager_on: MemoryManager) -> None:
        with pytest.raises(ValueError, match="unknown feedback kind"):
            manager_on.report_search_feedback(["m-x"], kind="maybe", project=PROJECT)

    def test_retry_with_same_event_id_no_double_weight(self, manager_on: MemoryManager) -> None:
        m1 = _add(manager_on, "first citation")
        m2 = _add(manager_on, "second citation")
        first = manager_on.report_search_feedback(
            [m1.id, m2.id], kind="used", project=PROJECT, event_id="report-1"
        )
        assert first["captured"] == 2
        # The agent retries the SAME report (at-least-once delivery).
        retry = manager_on.report_search_feedback(
            [m1.id, m2.id], kind="used", project=PROJECT, event_id="report-1"
        )
        assert retry["captured"] == 0
        assert retry["duplicates"] == 2
        assert len(_rows(manager_on)) == 2  # still two rows, not four

        # A different report id is a genuinely new event — it counts.
        later = manager_on.report_search_feedback(
            [m1.id], kind="used", project=PROJECT, event_id="report-2"
        )
        assert later["captured"] == 1

    def test_duplicate_ids_within_one_report_are_one_event(
        self, manager_on: MemoryManager
    ) -> None:
        m = _add(manager_on, "same citation twice in one batch")
        outcome = manager_on.report_search_feedback(
            [m.id, m.id], kind="used", project=PROJECT, event_id="report-1"
        )
        assert outcome["reported"] == 1
        assert outcome["captured"] == 1
        assert len(_rows(manager_on)) == 1

    def test_counters_reflect_capture(self, manager_on: MemoryManager) -> None:
        m = _add(manager_on, "counted citation")
        manager_on.report_search_feedback(
            [m.id], kind="used", project=PROJECT, event_id="r1"
        )
        manager_on.report_search_feedback(
            [m.id], kind="used", project=PROJECT, event_id="r2"
        )
        manager_on.report_search_feedback(
            [m.id], kind="rejected", project=PROJECT, event_id="r3"
        )
        assert manager_on.sqlite.get_edge_stats_counters(m.id) == {"used": 2, "rejected": 1}


class TestScopingAndUniform404:
    def test_nonexistent_id_out_of_scope(self, manager_on: MemoryManager) -> None:
        outcome = manager_on.report_search_feedback(
            ["no-such-id"], kind="used", project=PROJECT, event_id="r1"
        )
        assert outcome["captured"] == 0
        assert outcome["out_of_scope"] == 1
        assert len(_rows(manager_on)) == 0

    def test_cross_project_id_out_of_scope(self, manager_on: MemoryManager) -> None:
        foreign = _add(manager_on, "lives in another project", project="other-proj")
        outcome = manager_on.report_search_feedback(
            [foreign.id], kind="used", project=PROJECT, event_id="r1"
        )
        assert outcome["captured"] == 0
        assert outcome["out_of_scope"] == 1

    def test_raw_status_id_out_of_scope(self, manager_on: MemoryManager) -> None:
        raw = _add(manager_on, "not yet published", status=MemoryStatus.RAW)
        outcome = manager_on.report_search_feedback(
            [raw.id], kind="used", project=PROJECT, event_id="r1"
        )
        assert outcome["captured"] == 0
        assert outcome["out_of_scope"] == 1

    def test_quarantined_id_out_of_scope(self, manager_on: MemoryManager) -> None:
        m = _add(manager_on, "later quarantined")
        manager_on.sqlite._get_conn().execute(
            "UPDATE memories SET pipeline_state = 'quarantined' WHERE id = ?", (m.id,)
        )
        manager_on.sqlite._get_conn().commit()
        outcome = manager_on.report_search_feedback(
            [m.id], kind="used", project=PROJECT, event_id="r1"
        )
        assert outcome["captured"] == 0
        assert outcome["out_of_scope"] == 1

    def test_no_existence_oracle_uniform_outcome(self, manager_on: MemoryManager) -> None:
        """THE I5 uniform-404 test: a nonexistent id, a cross-project id,
        a raw-status id and a quarantined id produce IDENTICAL report
        dicts — the response cannot distinguish "does not exist" from
        "exists but invisible", so it is not an existence oracle."""
        foreign = _add(manager_on, "foreign project row", project="other-proj")
        raw = _add(manager_on, "raw row", status=MemoryStatus.RAW)
        q = _add(manager_on, "to be quarantined")
        conn = manager_on.sqlite._get_conn()
        conn.execute("UPDATE memories SET pipeline_state = 'quarantined' WHERE id = ?", (q.id,))
        conn.commit()

        outcomes = [
            manager_on.report_search_feedback([mid], kind="used", project=PROJECT, event_id=f"r{i}")
            for i, mid in enumerate(
                ["no-such-id", foreign.id, raw.id, q.id], start=1
            )
        ]
        assert len(outcomes) == 4
        first = outcomes[0]
        for other in outcomes[1:]:
            assert other == first  # byte-identical reports, no per-id reason
        assert first["out_of_scope"] == 1 and first["captured"] == 0
        assert len(_rows(manager_on)) == 0

    def test_global_mode_reports_cross_project(self, manager_on: MemoryManager) -> None:
        """project=None mirrors the search explicit-global mode: a row of
        another project IS reportable (the caller could have found it in
        a global search); the volume-cap bucket is the ('', '') one."""
        foreign = _add(manager_on, "global search finds me", project="other-proj")
        outcome = manager_on.report_search_feedback(
            [foreign.id], kind="used", project=None, event_id="r1"
        )
        assert outcome["captured"] == 1
        row = _rows(manager_on)[0]
        assert (row["project"], row["agent"]) == ("", "")

    def test_processed_status_visible(self, manager_on: MemoryManager) -> None:
        m = _add(manager_on, "processed rows are admissible", status=MemoryStatus.PROCESSED)
        outcome = manager_on.report_search_feedback([m.id], kind="used", project=PROJECT)
        assert outcome["captured"] == 1


class TestFailureIsolation:
    def test_store_failure_never_fails_caller(
        self, manager_on: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        m = _add(manager_on, "would be captured")
        monkeypatch.setattr(
            manager_on.sqlite,
            "record_edge_stat_event",
            MagicMock(side_effect=sqlite3.OperationalError("disk full")),
        )
        outcome = manager_on.report_search_feedback(
            [m.id], kind="used", project=PROJECT, event_id="r1"
        )  # must not raise
        assert outcome["captured"] == 0
        assert manager_on.feedback_capture_stats()["since_restart"]["errors_total"] == 1

    def test_telemetry_read_failure_degrades(
        self, manager_on: MemoryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            manager_on.sqlite,
            "count_edge_stats",
            MagicMock(side_effect=sqlite3.OperationalError("locked")),
        )
        stats = manager_on.feedback_capture_stats()  # must not raise
        assert stats["events_total"] == 0


# ── Telemetry (the D-behavioral payoff A0-review reads) ──────────────────────


class TestTelemetry:
    def test_durable_totals_survive_manager_restart(self, tmp_path: Path) -> None:
        mgr = MemoryManager(_settings(tmp_path, feedback=True))
        mgr._embedder = MagicMock()
        mgr._embedder.embed.return_value = [0.1] * 384
        m = _add(mgr, "captured before restart")
        assert (
            mgr.report_search_feedback([m.id], kind="used", project=PROJECT, event_id="r1")[
                "captured"
            ]
            == 1
        )
        mgr.close()

        # Fresh process over the same DB: the durable counters persist.
        mgr2 = MemoryManager(_settings(tmp_path, feedback=True))
        stats = mgr2.feedback_capture_stats()
        assert stats["enabled"] is True
        assert stats["events_total"] == 1
        assert stats["captured_used_total"] == 1
        assert stats["captured_rejected_total"] == 0
        assert stats["since_restart"]["reports_total"] == 0  # window resets
        mgr2.close()

    def test_dashboard_carries_feedback_block(self, manager_on: MemoryManager) -> None:
        m = _add(manager_on, "counted by the dashboard")
        manager_on.report_search_feedback([m.id], kind="rejected", project=PROJECT)
        block = manager_on.dashboard_stats()["feedback_capture"]
        assert block == {
            "enabled": True,
            "events_total": 1,
            "captured_used_total": 0,
            "captured_rejected_total": 1,
        }


# ── Integration: the search-reading path end to end ──────────────────────────


class TestSearchIntegration:
    def test_search_citations_report_used_lands_with_scope(
        self, manager_on: MemoryManager
    ) -> None:
        """The canonical flow: search → citation ids ride the response →
        the harness reports the consumed subset with a retry-stable
        event id → one row per citation, scoped, and the duplicate
        retry is ignored. 'used' is NEVER auto-inferred: a search
        WITHOUT a report captures nothing."""
        hit = _add(manager_on, "unique kyword alpha deployment notes")
        _add(manager_on, "different topic entirely windmills")

        results = manager_on.search("kyword alpha", limit=5, project=PROJECT)
        assert any(r.memory.id == hit.id for r in results)
        # No report yet — the search call itself captured nothing.
        assert len(_rows(manager_on)) == 0

        used_ids = [r.memory.id for r in results[:2]]
        first = manager_on.report_search_feedback(
            used_ids, kind="used", project=PROJECT, agent=AGENT, event_id="turn-42"
        )
        assert first["captured"] == len(used_ids)
        retry = manager_on.report_search_feedback(
            used_ids, kind="used", project=PROJECT, agent=AGENT, event_id="turn-42"
        )
        assert retry["duplicates"] == len(used_ids)

        rows = _rows(manager_on)
        assert len(rows) == len(used_ids)
        for row in rows:
            assert (row["kind"], row["project"], row["agent"]) == ("used", PROJECT, AGENT)
            assert row["memory_id"] in used_ids

    def test_zero_behavior_change_flag_off(
        self, manager: MemoryManager
    ) -> None:
        """Flag off: search + a stray report change nothing — zero rows,
        zero ranking influence (this slice has none by construction),
        search results identical to a manager that never heard of
        edge_stats."""
        _add(manager, "flag off corpus row one")
        _add(manager, "flag off corpus row two")
        results = manager.search("corpus", limit=10, project=PROJECT)
        assert results
        manager.report_search_feedback(
            [r.memory.id for r in results], kind="used", project=PROJECT, event_id="r1"
        )
        assert len(_rows(manager)) == 0
        assert manager.feedback_capture_stats()["events_total"] == 0
