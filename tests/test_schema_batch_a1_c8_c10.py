"""Schema-поезд (ArchCom 2026-08-27) — A1 + C8 + C10 acceptance tests.

A1 — ``ccr_cache`` composite PK ``(project, hash)``:

* legacy hash-PK databases are rebuilt first-writer-wins (one row per
  hash — the earliest rowid survives; duplicates, only constructible by
  raw writes bypassing the legacy PK, are dropped: the cache is derived
  and recompressible);
* the same hash stored by two projects is TWO rows from now on (the
  first-writer-squatting cross-project DoS edge dissolves); same-project
  re-store still refreshes the scan verdict (UPSERT on the composite key);
* ``ccr_get`` scoped lookups hit the caller's row; the unscoped legacy
  read resolves to the first-stored copy;
* ``ccr_touch`` / ``ccr_evict_lru`` / ``ccr_search`` are row-precise
  under duplicate hashes.

C8 — ``turns_fts`` + the ``turns_ai/ad/au`` triggers are dropped
(idempotently, on every connect); turn writes are unaffected.

C10 — denormalised ``memories.rewrite_source`` / ``rewrite_session``:

* legacy rows are backfilled from the metadata JSON once;
* ``count_recent_context_rewrites`` counts via the columns (equal to the
  old ``json_extract`` count on the same fixture) and is index-backed;
* the write quota becomes two-level: the PRIMARY per-(project, session)
  limiter as before, plus the SECONDARY per-project aggregate ceiling
  (``context_rewrite_project_rate_limit_per_minute``, 0=off) with the
  distinct-session count as the noisy-neighbor signal; NULL-session
  events are their own bucket under both knobs.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mnemos.config import Settings
from mnemos.context_rewrite import (
    SOURCE_CONTEXT_REWRITE,
    ContextRewriteRateLimitError,
    context_rewrite,
)
from mnemos.manager import MemoryManager
from mnemos.models import MemoryCreate, MemorySource
from mnemos.storage.sqlite_store import SQLiteStore

# ── Legacy DDL (the schema this migration window must consume) ────────────────

_LEGACY_MEMORIES_DDL = """
CREATE TABLE memories (
    id               TEXT PRIMARY KEY,
    content          TEXT NOT NULL,
    title            TEXT,
    tags             TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'manual',
    source_url       TEXT,
    memory_type      TEXT NOT NULL DEFAULT 'note',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    metadata         TEXT NOT NULL DEFAULT '{}',
    file_path        TEXT,
    category         TEXT,
    project          TEXT NOT NULL DEFAULT '',
    agent            TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'raw',
    quality_score    REAL,
    confidence       REAL,
    source_coverage  INTEGER,
    cluster_id       TEXT,
    derived_from     TEXT NOT NULL DEFAULT '[]',
    embedding_id     TEXT,
    raw_content      TEXT,
    clean_content    TEXT,
    filter_profile   TEXT,
    filter_stats     TEXT,
    filter_version   TEXT,
    workflow_status  TEXT,
    locked_by        TEXT,
    locked_at        TEXT
)
"""

# The legacy FTS/triggers (verbatim shape from the pre-batch schema) —
# created BEFORE row inserts so the external-content index stays in sync
# exactly like a healthy production database.
_LEGACY_MEMORIES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    id UNINDEXED, title, content, tags, project UNINDEXED, agent UNINDEXED,
    content=memories, content_rowid=rowid, tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, id, title, content, tags, project, agent)
    VALUES (new.rowid, new.id, new.title, new.content, new.tags,
            new.project, new.agent);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, id, title, content, tags,
                             project, agent)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.tags,
            old.project, old.agent);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, id, title, content, tags,
                             project, agent)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.tags,
            old.project, old.agent);
    INSERT INTO memories_fts(rowid, id, title, content, tags, project, agent)
    VALUES (new.rowid, new.id, new.title, new.content, new.tags,
            new.project, new.agent);
END;
"""

_LEGACY_TURNS_DDL = """
CREATE TABLE turns (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    turn_id         TEXT NOT NULL,
    step_number     INTEGER NOT NULL,
    role            TEXT NOT NULL,
    from_agent      TEXT,
    to_agent        TEXT,
    message_id      TEXT,
    content         TEXT NOT NULL,
    summary         TEXT,
    key_decisions   TEXT NOT NULL DEFAULT '[]',
    outcome         TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    context_pointer TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(session_id, turn_id),
    UNIQUE(message_id)
)
"""

_LEGACY_TURNS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    id UNINDEXED, session_id UNINDEXED, content, summary, tags,
    from_agent UNINDEXED, to_agent UNINDEXED,
    content=turns, content_rowid=rowid, tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, id, session_id, content, summary, tags,
                          from_agent, to_agent)
    VALUES (new.rowid, new.id, new.session_id, new.content, new.summary,
            new.tags, new.from_agent, new.to_agent);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, id, session_id, content, summary,
                          tags, from_agent, to_agent)
    VALUES ('delete', old.rowid, old.id, old.session_id, old.content,
            old.summary, old.tags, old.from_agent, old.to_agent);
END;
CREATE TRIGGER IF NOT EXISTS turns_au AFTER UPDATE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, id, session_id, content, summary,
                          tags, from_agent, to_agent)
    VALUES ('delete', old.rowid, old.id, old.session_id, old.content,
            old.summary, old.tags, old.from_agent, old.to_agent);
    INSERT INTO turns_fts(rowid, id, session_id, content, summary, tags,
                          from_agent, to_agent)
    VALUES (new.rowid, new.id, new.session_id, new.content, new.summary,
            new.tags, new.from_agent, new.to_agent);
END;
"""

# Legacy ccr_cache: hash PRIMARY KEY (the A1 predecessor).
_LEGACY_CCR_DDL = """
CREATE TABLE ccr_cache (
    hash             TEXT PRIMARY KEY,
    original         TEXT NOT NULL,
    project          TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    retrieval_count  INTEGER NOT NULL DEFAULT 0,
    last_retrieved_at TEXT,
    secret_scan_verdict TEXT,
    secret_scan_at      TEXT
)
"""

_LEGACY_CCR_FTS = """
CREATE INDEX IF NOT EXISTS idx_ccr_cache_project   ON ccr_cache(project);
CREATE INDEX IF NOT EXISTS idx_ccr_cache_created   ON ccr_cache(created_at);
CREATE INDEX IF NOT EXISTS idx_ccr_cache_retrieval ON ccr_cache(retrieval_count);
CREATE VIRTUAL TABLE IF NOT EXISTS ccr_cache_fts USING fts5(
    hash UNINDEXED, original, content=ccr_cache, content_rowid=rowid,
    tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS ccr_cache_ai AFTER INSERT ON ccr_cache BEGIN
    INSERT INTO ccr_cache_fts(rowid, hash, original)
    VALUES (new.rowid, new.hash, new.original);
END;
CREATE TRIGGER IF NOT EXISTS ccr_cache_ad AFTER DELETE ON ccr_cache BEGIN
    INSERT INTO ccr_cache_fts(ccr_cache_fts, rowid, hash, original)
    VALUES ('delete', old.rowid, old.hash, old.original);
END;
CREATE TRIGGER IF NOT EXISTS ccr_cache_au AFTER UPDATE ON ccr_cache BEGIN
    INSERT INTO ccr_cache_fts(ccr_cache_fts, rowid, hash, original)
    VALUES ('delete', old.rowid, old.hash, old.original);
    INSERT INTO ccr_cache_fts(rowid, hash, original)
    VALUES (new.rowid, new.hash, new.original);
END;
"""


def _build_legacy_db(
    path: Path,
    *,
    ccr_ddl: str = _LEGACY_CCR_DDL,
    ccr_rows: tuple[tuple[str, str, str, int], ...] = (),
) -> None:
    """Hand-build a legacy (pre-batch) database.

    ``ccr_ddl`` allows the no-PK defensive variant (duplicate hashes were
    impossible under the production legacy PK — the migration's dedup is
    exercised through raw writes that bypass it). ``ccr_rows`` are
    ``(hash, original, project, retrieval_count)`` inserted in list order
    (rowid order = insertion order = first-writer order).
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_LEGACY_MEMORIES_DDL)
        conn.executescript(_LEGACY_MEMORIES_FTS)
        now = "2026-08-26T10:00:00+00:00"
        legacy_memories = [
            (
                "m1",
                "rewrite one",
                "p1",
                {"source": "context-rewrite", "rewrite_session": "s1", "rewrite_event_key": "k1"},
            ),
            ("m2", "rewrite two", "p1", {"source": "context-rewrite"}),
            ("m3", "normal row", "p1", {}),
        ]
        for mid, content, project, metadata in legacy_memories:
            conn.execute(
                "INSERT INTO memories (id, content, project, agent, created_at,"
                " updated_at, metadata) VALUES (?,?,?,?,?,?,?)",
                (mid, content, project, "a1", now, now, json.dumps(metadata)),
            )
        conn.execute(_LEGACY_TURNS_DDL)
        conn.executescript(_LEGACY_TURNS_FTS)
        conn.execute(
            "INSERT INTO turns (id, session_id, turn_id, step_number, role,"
            " content, context_pointer, created_at)"
            " VALUES ('t1','s1','turn-1',1,'user','legacy turn','ptr', ?)",
            (now,),
        )
        conn.execute(ccr_ddl)
        conn.executescript(_LEGACY_CCR_FTS)
        for i, (h, original, project, retrieval_count) in enumerate(ccr_rows):
            conn.execute(
                "INSERT INTO ccr_cache (hash, original, project, created_at,"
                " size_bytes, retrieval_count, secret_scan_verdict,"
                " secret_scan_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    h,
                    original,
                    project,
                    f"2026-08-2{i}T10:00:00+00:00",
                    len(original),
                    retrieval_count,
                    "clean",
                    f"2026-08-2{i}T10:00:00+00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _pk_positions(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    return {str(r[1]): int(r[5]) for r in conn.execute(f"PRAGMA table_info({table})")}


def _object_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row is not None


# ── A1: composite PK migration ────────────────────────────────────────────────


class TestA1CompositePkMigration:
    def test_legacy_db_migrated_onto_composite_pk(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _build_legacy_db(db, ccr_rows=(("h1", "original one", "projA", 3),))
        store = SQLiteStore(db)
        conn = store._get_conn()
        pk = _pk_positions(conn, "ccr_cache")
        assert pk.get("project") == 1 and pk.get("hash") == 2, pk
        # First-writer row survives intact (project, counter, verdict).
        row = conn.execute(
            "SELECT project, retrieval_count, secret_scan_verdict, original"
            " FROM ccr_cache WHERE hash='h1'"
        ).fetchone()
        assert tuple(row) == ("projA", 3, "clean", "original one")
        # Triggers and indexes are restored on the rebuilt table, FTS in sync.
        assert _object_exists(conn, "ccr_cache_ai")
        assert _object_exists(conn, "ccr_cache_ad")
        assert _object_exists(conn, "ccr_cache_au")
        assert _object_exists(conn, "idx_ccr_cache_project")
        fts_n = conn.execute("SELECT COUNT(*) FROM ccr_cache_fts").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM ccr_cache").fetchone()[0]
        assert fts_n == n
        store.close()

    def test_duplicate_hashes_first_writer_wins(self, tmp_path: Path) -> None:
        """Dup hashes across projects → the FIRST stored row survives.

        Only constructible by raw writes bypassing the legacy PK (the
        production legacy schema made duplicates impossible); the
        migration dedups by hash keeping the lowest rowid regardless.
        """
        db = tmp_path / "duplegacy.db"
        no_pk_ddl = _LEGACY_CCR_DDL.replace(
            "hash             TEXT PRIMARY KEY,", "hash             TEXT NOT NULL,"
        )
        _build_legacy_db(
            db,
            ccr_ddl=no_pk_ddl,
            ccr_rows=(
                ("h1", "original one", "projA", 3),  # first writer — survives
                ("h1", "original one", "projB", 5),  # dup hash — dropped
                ("h1", "original one", "projA", 9),  # dup (projA, h1) — dropped
                ("h2", "other content", "projA", 1),  # unrelated — survives
            ),
        )
        store = SQLiteStore(db)
        conn = store._get_conn()
        rows = [
            tuple(r)
            for r in conn.execute(
                "SELECT rowid, hash, project, retrieval_count FROM ccr_cache ORDER BY rowid"
            )
        ]
        assert rows == [(1, "h1", "projA", 3), (4, "h2", "projA", 1)]
        # PK invariant: one row per (project, hash).
        dup = conn.execute(
            "SELECT COUNT(*) FROM (SELECT project, hash FROM ccr_cache"
            " GROUP BY project, hash HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        assert dup == 0
        store.close()

    def test_migration_is_idempotent_on_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _build_legacy_db(db, ccr_rows=(("h1", "original one", "projA", 3),))
        store = SQLiteStore(db)
        store.close()
        store = SQLiteStore(db)  # reopen: no second rebuild, no data loss
        conn = store._get_conn()
        assert store.ccr_count() == 1
        assert _pk_positions(conn, "ccr_cache").get("project") == 1
        store.close()

    def test_fresh_db_has_composite_pk_from_day_one(self, tmp_path: Path) -> None:
        store = SQLiteStore(tmp_path / "fresh.db")
        conn = store._get_conn()
        pk = _pk_positions(conn, "ccr_cache")
        assert pk.get("project") == 1 and pk.get("hash") == 2
        store.close()


class TestA1StoreSemantics:
    """Every composite-key call site under duplicate hashes."""

    @pytest.fixture()
    def store(self, tmp_path: Path) -> Iterator[SQLiteStore]:
        s = SQLiteStore(tmp_path / "s.db")
        yield s
        s.close()

    def test_same_hash_two_projects_is_two_rows(self, store: SQLiteStore) -> None:
        store.ccr_store(hash="a" * 64, original="shared original", project="pA")
        store.ccr_store(hash="a" * 64, original="shared original", project="pB")
        assert store.ccr_count() == 2
        # The DoS edge dissolves: pB redeems its own marker.
        assert store.ccr_get("a" * 64, project="pB", bump=False) is not None
        assert store.ccr_get("a" * 64, project="pA", bump=False) is not None

    def test_same_project_re_store_refreshes_verdict_no_new_row(self, store: SQLiteStore) -> None:
        store.ccr_store(hash="b" * 64, original="content", project="pA")
        store.ccr_store(hash="b" * 64, original="content", project="pA")
        assert store.ccr_count() == 1
        entry = store.ccr_get("b" * 64, project="pA", bump=False)
        assert entry is not None and entry["secret_scan_verdict"] == "clean"

    def test_ccr_get_unscoped_reads_first_writer(self, store: SQLiteStore) -> None:
        store.ccr_store(hash="c" * 64, original="content", project="pA")
        store.ccr_store(hash="c" * 64, original="content", project="pB")
        entry = store.ccr_get("c" * 64, bump=False)  # legacy global read
        assert entry is not None and entry["project"] == "pA"

    def test_ccr_get_bump_hits_exactly_the_row_read(self, store: SQLiteStore) -> None:
        store.ccr_store(hash="d" * 64, original="content", project="pA")
        store.ccr_store(hash="d" * 64, original="content", project="pB")
        entry = store.ccr_get("d" * 64, project="pB")  # scoped read + bump
        assert entry is not None
        assert entry["retrieval_count"] == 1
        assert store.ccr_get("d" * 64, project="pA", bump=False)["retrieval_count"] == 0

    def test_ccr_touch_is_row_precise(self, store: SQLiteStore) -> None:
        store.ccr_store(hash="e" * 64, original="content", project="pA")
        store.ccr_store(hash="e" * 64, original="content", project="pB")
        store.ccr_touch("e" * 64, project="pA")
        assert store.ccr_get("e" * 64, project="pA", bump=False)["retrieval_count"] == 1
        assert store.ccr_get("e" * 64, project="pB", bump=False)["retrieval_count"] == 0

    def test_ccr_evict_lru_removes_exactly_excess_rows(self, store: SQLiteStore) -> None:
        for i in range(4):
            store.ccr_store(hash=f"{i:x}" * 64, original=f"content {i}", project="p")
        # One more row sharing a hash with row 0 but another project.
        store.ccr_store(hash="0" * 64, original="content 0", project="other")
        evicted = store.ccr_evict_lru(3)
        assert evicted == 2
        assert store.ccr_count() == 3

    def test_ccr_search_unscoped_no_duplicate_snippets(self, store: SQLiteStore) -> None:
        original = "alpha needle beta needle gamma needle delta"
        store.ccr_store(hash="f" * 64, original=original, project="pA")
        store.ccr_store(hash="f" * 64, original=original, project="pB")
        snippets = store.ccr_search("f" * 64, "needle", limit=5)
        # Identical copies must not flood the limit: one copy's hits only.
        assert 1 <= len(snippets) <= 3
        texts = [s["snippet"] for s in snippets]
        assert len(texts) == len(set(texts))

    def test_ccr_search_scoped_finds_only_owner_project_copy(self, store: SQLiteStore) -> None:
        store.ccr_store(hash="1" * 64, original=" searchable text ", project="pA")
        store.ccr_store(hash="1" * 64, original=" searchable text ", project="pB")
        assert store.ccr_search("1" * 64, "searchable", project="pA")
        assert store.ccr_search("1" * 64, "searchable", project="pB")
        assert store.ccr_search("1" * 64, "searchable", project="pX") == []


# ── C8: turns_fts drop ────────────────────────────────────────────────────────


class TestC8DropTurnsFts:
    def test_legacy_fts_and_triggers_dropped(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _build_legacy_db(db)
        store = SQLiteStore(db)
        conn = store._get_conn()
        assert not _object_exists(conn, "turns_fts")
        assert not _object_exists(conn, "turns_ai")
        assert not _object_exists(conn, "turns_ad")
        assert not _object_exists(conn, "turns_au")
        store.close()

    def test_turn_writes_unaffected(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _build_legacy_db(db)
        store = SQLiteStore(db)
        conn = store._get_conn()
        now = "2026-08-26T11:00:00+00:00"
        # INSERT / UPDATE / DELETE on turns run without the removed triggers.
        conn.execute(
            "INSERT INTO turns (id, session_id, turn_id, step_number, role,"
            " content, context_pointer, created_at)"
            " VALUES ('t2','s1','turn-2',2,'assistant','fine','ptr', ?)",
            (now,),
        )
        conn.execute("UPDATE turns SET summary='sum' WHERE id='t2'")
        conn.execute("DELETE FROM turns WHERE id='t1'")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
        store.close()

    def test_fresh_db_never_gets_turns_fts(self, tmp_path: Path) -> None:
        store = SQLiteStore(tmp_path / "fresh.db")
        conn = store._get_conn()
        assert not _object_exists(conn, "turns_fts")
        store.close()

    def test_drop_is_idempotent_on_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _build_legacy_db(db)
        store = SQLiteStore(db)
        store.close()
        store = SQLiteStore(db)
        conn = store._get_conn()
        assert not _object_exists(conn, "turns_fts")
        store.close()


# ── C10: denormalised columns + two-level quota ───────────────────────────────


def _settings(
    tmp: Path,
    *,
    mnemos_extra: dict[str, object] | None = None,
) -> Settings:
    mnemos_cfg: dict[str, object] = {
        "vault_path": str(tmp / "vault"),
        "data_dir": str(tmp / "data"),
        "db_name": "test.db",
    }
    mnemos_cfg.update(mnemos_extra or {})
    settings = Settings(
        mnemos=mnemos_cfg,  # type: ignore[arg-type]
        scanner={"enabled": False},
    )
    settings.resolve_paths()
    return settings


def _manager(settings: Settings) -> MemoryManager:
    mgr = MemoryManager(settings)
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 384
    mgr._embedder = mock_embedder
    return mgr


PROJECT = "sb-project"
AGENT = "sb-agent"


def _rewrite_event(mgr: MemoryManager, *, session: str | None, n: int) -> dict:
    """One stored rewrite event with unique content (event-key uniqueness)."""
    return context_rewrite(
        mgr,
        content=f"replaced block #{n} with unique body {n:04d} for the schema batch",
        project=PROJECT,
        agent=AGENT,
        session=session,
    )


class TestC10ColumnsAndBackfill:
    def test_legacy_rows_backfilled_from_metadata(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _build_legacy_db(db)
        store = SQLiteStore(db)
        conn = store._get_conn()
        rows = {
            r["id"]: (r["rewrite_source"], r["rewrite_session"])
            for r in conn.execute("SELECT id, rewrite_source, rewrite_session FROM memories")
        }
        assert rows["m1"] == (SOURCE_CONTEXT_REWRITE, "s1")
        assert rows["m2"] == (SOURCE_CONTEXT_REWRITE, None)
        assert rows["m3"] == (None, None)
        # Backfill flag set — the UPDATE does not re-run on reconnect.
        assert conn.execute(
            "SELECT 1 FROM meta WHERE key='schema_backfill_rewrite_cols_v1'"
        ).fetchone()
        store.close()

    def test_save_derives_columns_on_write(self, tmp_path: Path) -> None:
        mgr = _manager(_settings(tmp_path))
        receipt = _rewrite_event(mgr, session="sess-1", n=1)
        conn = mgr.sqlite._get_conn()
        row = conn.execute(
            "SELECT rewrite_source, rewrite_session FROM memories WHERE id=?",
            (receipt["memory_id"],),
        ).fetchone()
        assert tuple(row) == (SOURCE_CONTEXT_REWRITE, "sess-1")
        # A normal (non-rewrite) memory stores NULLs.
        normal = mgr.add(
            MemoryCreate(content="plain", source=MemorySource.MCP),
            project=PROJECT,
            agent=AGENT,
        )
        row = conn.execute(
            "SELECT rewrite_source, rewrite_session FROM memories WHERE id=?",
            (normal.id,),
        ).fetchone()
        assert tuple(row) == (None, None)
        # The C10 composite index exists and covers the count filter.
        assert _object_exists(conn, "idx_memories_project_rewrite_source_created")
        mgr.close()

    def test_count_via_columns_equals_old_json_extract_count(self, tmp_path: Path) -> None:
        mgr = _manager(_settings(tmp_path))
        # Mixed fixture: sessions s1/s2 and NULL, plus foreign-project rows.
        for n in range(3):
            _rewrite_event(mgr, session="s1", n=n)
        for n in range(2):
            _rewrite_event(mgr, session="s2", n=10 + n)
        _rewrite_event(mgr, session=None, n=20)
        context_rewrite(  # another project's event — must not count
            mgr,
            content="other project event",
            project="other-project",
            agent=AGENT,
            session="s1",
        )
        conn = mgr.sqlite._get_conn()
        # The pre-C10 implementation's formula, verbatim.
        for session in ("s1", "s2", None, "sX"):
            legacy = conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE project = ? AND created_at >= '2000-01-01' "
                "AND json_extract(metadata, '$.source') = 'context-rewrite' "
                "AND json_extract(metadata, '$.rewrite_session') IS ?",
                (PROJECT, session),
            ).fetchone()[0]
            via_columns = mgr.sqlite.count_recent_context_rewrites(PROJECT, session, "2000-01-01")
            assert via_columns == legacy, session
        assert mgr.sqlite.count_recent_context_rewrites(PROJECT, "s1", "2000-01-01") == 3
        mgr.close()

    def test_aggregate_count_reports_rows_and_distinct_sessions(self, tmp_path: Path) -> None:
        mgr = _manager(_settings(tmp_path))
        for n in range(2):
            _rewrite_event(mgr, session="s1", n=n)
        _rewrite_event(mgr, session="s2", n=10)
        _rewrite_event(mgr, session=None, n=20)  # NULL = own bucket
        rows, sessions = mgr.sqlite.count_recent_context_rewrites_by_project(PROJECT, "2000-01-01")
        assert rows == 4
        assert sessions == 3  # s1 + s2 + the NULL bucket
        mgr.close()


class TestC10TwoLevelQuota:
    """Aggregate ceiling: primary per-session first, then per-project."""

    def _mgr(self, tmp_path: Path, session_limit: int, project_limit: int):
        return _manager(
            _settings(
                tmp_path,
                mnemos_extra={
                    "context_rewrite_rate_limit_per_minute": session_limit,
                    "context_rewrite_project_rate_limit_per_minute": project_limit,
                },
            )
        )

    def test_session_cap_fires_first_under_project_cap(self, tmp_path: Path) -> None:
        # 30 sessions x few events stay far under the 300 project cap; the
        # per-session limiter is the one that fires (3/min here).
        mgr = self._mgr(tmp_path, session_limit=3, project_limit=300)
        for n in range(3):
            _rewrite_event(mgr, session="burner", n=n)
        with pytest.raises(ContextRewriteRateLimitError) as excinfo:
            _rewrite_event(mgr, session="burner", n=99)
        assert "context_rewrite_rate_limit_per_minute" in str(excinfo.value)
        # Stored rows stay under the project aggregate.
        rows, _ = mgr.sqlite.count_recent_context_rewrites_by_project(PROJECT, "2000-01-01")
        assert rows == 3
        mgr.close()

    def test_project_cap_fires_even_under_session_caps(self, tmp_path: Path) -> None:
        # Session limiter loose (10000/min), project ceiling tight (5/min):
        # the 6th event from a FRESH session still trips the aggregate.
        mgr = self._mgr(tmp_path, session_limit=10_000, project_limit=5)
        for n in range(5):
            _rewrite_event(mgr, session=f"s{n}", n=n)
        with pytest.raises(ContextRewriteRateLimitError) as excinfo:
            _rewrite_event(mgr, session="s-fresh", n=99)
        msg = str(excinfo.value)
        assert "project rate limit" in msg
        assert "context_rewrite_project_rate_limit_per_minute" in msg
        assert "session(s)" in msg  # distinct-session signal rides along
        mgr.close()

    def test_null_session_is_own_bucket_under_both_knobs(self, tmp_path: Path) -> None:
        mgr = self._mgr(tmp_path, session_limit=3, project_limit=5)
        for n in range(3):
            _rewrite_event(mgr, session=None, n=n)
        with pytest.raises(ContextRewriteRateLimitError) as excinfo:
            _rewrite_event(mgr, session=None, n=99)
        # 3 rows < project cap 5 → the per-(project, NULL) bucket fired.
        assert "context_rewrite_rate_limit_per_minute" in str(excinfo.value)
        mgr.close()

    def test_null_bucket_counts_into_project_aggregate(self, tmp_path: Path) -> None:
        mgr = self._mgr(tmp_path, session_limit=10_000, project_limit=4)
        for n in range(3):
            _rewrite_event(mgr, session=None, n=n)
        _rewrite_event(mgr, session="s1", n=10)
        with pytest.raises(ContextRewriteRateLimitError) as excinfo:
            _rewrite_event(mgr, session="s2", n=99)
        assert "project rate limit" in str(excinfo.value)
        mgr.close()

    def test_project_cap_zero_disables_aggregate(self, tmp_path: Path) -> None:
        mgr = self._mgr(tmp_path, session_limit=3, project_limit=0)
        # Fan-out across sessions is unbounded by the aggregate ceiling…
        for n in range(8):
            receipt = _rewrite_event(mgr, session=f"s{n}", n=n)
            assert receipt["status"] == "stored"
        # …while the per-session limiter still applies.
        for n in range(3):
            _rewrite_event(mgr, session="burner", n=20 + n)
        with pytest.raises(ContextRewriteRateLimitError) as excinfo:
            _rewrite_event(mgr, session="burner", n=99)
        assert "project rate limit" not in str(excinfo.value)
        mgr.close()

    def test_deduplicated_redelivery_consumes_no_quota(self, tmp_path: Path) -> None:
        mgr = self._mgr(tmp_path, session_limit=1, project_limit=1)
        first = _rewrite_event(mgr, session="s1", n=1)
        again = context_rewrite(  # identical event — dedup, no quota burn
            mgr,
            content="replaced block #1 with unique body 0001 for the schema batch",
            project=PROJECT,
            agent=AGENT,
            session="s1",
        )
        assert first["status"] == "stored"
        assert again["status"] == "deduplicated"


# ── Manager integration: the unscoped-issuance call site ──────────────────────


class TestManagerIssuanceCallSite:
    def test_retrieve_content_bumps_exactly_the_issued_row(self, tmp_path: Path) -> None:
        mgr = _manager(_settings(tmp_path))
        h = "9" * 64
        mgr.sqlite.ccr_store(hash=h, original="issued original", project="pA")
        mgr.sqlite.ccr_store(hash=h, original="issued original", project="pB")
        result = mgr.retrieve_content(h, project="pB")
        assert result["found"] is True and result["retrieval_count"] == 1
        assert mgr.sqlite.ccr_get(h, project="pA", bump=False)["retrieval_count"] == 0
        assert mgr.sqlite.ccr_get(h, project="pB", bump=False)["retrieval_count"] == 1
        mgr.close()

    def test_unscoped_retrieval_bumps_first_writer_row_only(self, tmp_path: Path) -> None:
        mgr = _manager(_settings(tmp_path))
        h = "8" * 64
        mgr.sqlite.ccr_store(hash=h, original="issued original", project="pA")
        mgr.sqlite.ccr_store(hash=h, original="issued original", project="pB")
        result = mgr.retrieve_content(h)  # legacy global read (project=None)
        assert result["found"] is True
        assert mgr.sqlite.ccr_get(h, project="pA", bump=False)["retrieval_count"] == 1
        assert mgr.sqlite.ccr_get(h, project="pB", bump=False)["retrieval_count"] == 0
        mgr.close()
