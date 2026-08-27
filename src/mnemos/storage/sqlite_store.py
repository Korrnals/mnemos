"""SQLite metadata storage with FTS5 full-text search.

Extended schema: project, agent (denormalised from tags), pipeline fields
(quality_score, confidence, source_coverage, cluster_id, derived_from,
embedding_id), Context Filter fields (raw_content, clean_content,
filter_profile, filter_stats, filter_version), and a trace table (M6 —
explainability). FTS indexes project + agent for fast per-agent recall (M3).
Per-agent / per-project query helpers (M3).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from sys import getsizeof
from typing import Any, Final, cast

from mnemos.models import (
    Memory,
    MemorySource,
    MemoryStatus,
    MemoryType,
    Project,
    Trace,
)

logger = logging.getLogger(__name__)

# ADR-0018 P1-b (m2) — FTS5 snippet highlight markers used by
# ``ccr_search``. Module-level so the issuance-side scanner
# (``MemoryManager.retrieve_content``) strips EXACTLY these markers
# before scanning a snippet: the markers wrap query-matched tokens and
# split multi-token secrets (e.g. a JWT whose payload segment matched
# the query), which makes the raw marked snippet text evade
# ``detect_secrets``.
FTS_SNIPPET_START_MARK: Final[str] = ">>>"
FTS_SNIPPET_END_MARK: Final[str] = "<<<"
FTS_SNIPPET_ELLIPSIS: Final[str] = " ... "

# M15.2 — Whitelisted dispatch for dynamic UPDATE setters.
# Maps public field name -> literal "column=?" SQL fragment. Bandit B608
# requires no user-controlled identifier be interpolated into SQL; this
# constant dict is the ONLY source of column names that `update_fields`
# will accept. Future column additions must be added here AND in the
# memories schema, not by widening the runtime allowlist.
_FIELD_UPDATERS: dict[str, str] = {
    "status": "status=?",
    "quality_score": "quality_score=?",
    "confidence": "confidence=?",
    "source_coverage": "source_coverage=?",
    "cluster_id": "cluster_id=?",
    "derived_from": "derived_from=?",
    "embedding_id": "embedding_id=?",
    "clean_content": "clean_content=?",
    "filter_profile": "filter_profile=?",
    "filter_stats": "filter_stats=?",
    "filter_version": "filter_version=?",
    "title": "title=?",
    "content": "content=?",
    "tags": "tags=?",
    "category": "category=?",
    "file_path": "file_path=?",
    # Denormalised columns derived from tags (project:/agent: slugs).
    # Whitelisted so `tags normalize` can update them via `update_fields`
    # alongside the tags JSON without falling back to `save()` (which
    # uses INSERT OR REPLACE and can desync the FTS5 external content
    # table — see fix in cli/main.py `tags normalize`).
    "project": "project=?",
    "agent": "agent=?",
}

# FTS5 query-syntax special chars. Stripping them and wrapping the rest in
# double quotes disables FTS5 prefix/NEAR/column-syntax and turns the input
# into a literal phrase. This is the recommended hardening pattern from
# https://www.sqlite.org/fts5.html#fts5_strings — see `_build_fts_query`.
_FTS5_SPECIAL_CHARS = re.compile(r'["\'\*\(\):]')

# ADR-0018 Phase 1 — the memory_edges table supports exactly one edge
# kind. Expanding this set requires updating the SQL CHECK constraint on
# memory_edges (schema migration), this whitelist, and the manager
# wrappers — by design the surface stays minimal until on_context_rewrite
# (#125) arrives with Phase 2.
_EDGE_KINDS: Final[set[str]] = {"supersedes"}

# ── TTL in-memory cache ───────────────────────────────────────────────────────


class _TTLCache:
    """Thread-safe dict with per-key TTL expiry."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _ns(key: str) -> str:
        if ":" in key:
            return key.split(":", 1)[0]
        if key.startswith("graph_"):
            return "graph"
        if key in {"tags", "projects_counts", "data_health", "stats"}:
            return "aggregates"
        return "default"

    @staticmethod
    def _size(value: Any) -> int:
        try:
            return len(json.dumps(value, ensure_ascii=False, default=str).encode())
        except (TypeError, ValueError, OverflowError):
            return getsizeof(value)

    def get(self, key: str, ttl: float) -> tuple[bool, Any]:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return False, None
            if time.monotonic() - entry["ts"] < ttl:
                entry["hits"] += 1
                return True, entry["value"]
            self._data.pop(key, None)
            return False, None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = {
                "ts": time.monotonic(),
                "hits": 0,
                "value": value,
                "size": self._size(value),
            }

    def invalidate(self, *keys: str) -> int:
        with self._lock:
            return sum(1 for k in keys if self._data.pop(k, None) is not None)

    def invalidate_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._data),
                "hits": sum(e["hits"] for e in self._data.values()),
                "size_bytes": sum(e["size"] for e in self._data.values()),
            }


# ── Schema ────────────────────────────────────────────────────────────────────

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
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
    -- Mnemos tag contract denormalisations (M2)
    project          TEXT NOT NULL DEFAULT '',
    agent            TEXT NOT NULL DEFAULT '',
    -- Knowledge pipeline (M4)
    status           TEXT NOT NULL DEFAULT 'raw',
    quality_score    REAL,
    confidence       REAL,
    source_coverage  INTEGER,
    cluster_id       TEXT,
    derived_from     TEXT NOT NULL DEFAULT '[]',
    embedding_id     TEXT,
    -- Context Filter (M10 — fields present from day 1)
    raw_content      TEXT,
    clean_content    TEXT,
    filter_profile   TEXT,
    filter_stats     TEXT,
    filter_version   TEXT,
    -- Workflow lifecycle (mnemos #96). Managed EXCLUSIVELY by the
    -- set_workflow_status method — never by save()/update_fields() — so the
    -- state machine in MemoryManager.workflow_set cannot be bypassed.
    workflow_status  TEXT,
    locked_by        TEXT,
    locked_at        TEXT,
    -- C10 (ArchCom 2026-08-27): denormalised rewrite-event provenance from
    -- metadata JSON. rewrite_source = metadata["source"] (the ingestion
    -- channel discriminator, e.g. 'context-rewrite' — NOT the MemorySource
    -- enum in the `source` column, which stays 'mcp' for rewrite events);
    -- rewrite_session = metadata["rewrite_session"]. Derived in save() and
    -- backfilled by _run_migrations; they exist so the rewrite quota counts
    -- are index-backed instead of json_extract full-scans.
    rewrite_source   TEXT,
    rewrite_session  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    id UNINDEXED,
    title,
    content,
    tags,
    project UNINDEXED,
    agent UNINDEXED,
    content=memories,
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, id, title, content, tags, project, agent)
    VALUES (new.rowid, new.id, new.title, new.content, new.tags, new.project, new.agent);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, id, title, content, tags, project, agent)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.tags, old.project, old.agent);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, id, title, content, tags, project, agent)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.tags, old.project, old.agent);
    INSERT INTO memories_fts(rowid, id, title, content, tags, project, agent)
    VALUES (new.rowid, new.id, new.title, new.content, new.tags, new.project, new.agent);
END;

CREATE INDEX IF NOT EXISTS idx_memories_source   ON memories(source);
CREATE INDEX IF NOT EXISTS idx_memories_type     ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_created  ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_status   ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_project  ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_agent    ON memories(agent);
CREATE INDEX IF NOT EXISTS idx_memories_cluster  ON memories(cluster_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
-- NOTE (C10): idx_memories_project_rewrite_source_created is created in
-- _run_migrations, NOT here — the rewrite_source/rewrite_session columns
-- reach legacy DBs via ALTER TABLE in that same routine, and this script
-- runs BEFORE it (an index over a missing column would abort the connect).

-- mnemos #96: workflow lifecycle audit log. Every state transition is
-- recorded here (actor, from->to, reason, force_used). The workflow_status /
-- locked_by / locked_at columns on `memories` are the *current* projection;
-- this table is the immutable history that makes the audit + rate-limit
-- guardrails work.
CREATE TABLE IF NOT EXISTS memory_workflow_history (
    id          TEXT PRIMARY KEY,
    memory_id   TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    force_used  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wf_history_memory ON memory_workflow_history(memory_id);
CREATE INDEX IF NOT EXISTS idx_wf_history_created ON memory_workflow_history(created_at);

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    paths       TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    id                TEXT PRIMARY KEY,
    task_label        TEXT NOT NULL,
    project           TEXT NOT NULL DEFAULT '',
    step              TEXT NOT NULL,
    item_id           TEXT,
    llm_called        INTEGER NOT NULL DEFAULT 0,
    llm_done          INTEGER NOT NULL DEFAULT 0,
    cache_hit         INTEGER NOT NULL DEFAULT 0,
    fallback_used     INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    tokens_in         INTEGER NOT NULL DEFAULT 0,
    tokens_out        INTEGER NOT NULL DEFAULT 0,
    tokens_per_sec    REAL NOT NULL DEFAULT 0.0,
    rationale_summary TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_project ON traces(project);
CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at);

-- M5: Dead-Letter Queue for failed synthesis / publish
CREATE TABLE IF NOT EXISTS dlq (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL,
    cluster_id      TEXT,
    task_label      TEXT NOT NULL DEFAULT 'synthesize',
    error_message   TEXT NOT NULL DEFAULT '',
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    next_retry_at   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dlq_memory    ON dlq(memory_id);
CREATE INDEX IF NOT EXISTS idx_dlq_cluster  ON dlq(cluster_id);
CREATE INDEX IF NOT EXISTS idx_dlq_retry      ON dlq(next_retry_at);

-- M16: A2A Sessions (persistent backend for A2A routing)
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}',
    ttl_expires_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created  ON sessions(created_at);

CREATE TABLE IF NOT EXISTS turns (
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
);

CREATE INDEX IF NOT EXISTS idx_turns_session_step ON turns(session_id, step_number);
CREATE INDEX IF NOT EXISTS idx_turns_message_id   ON turns(message_id);
CREATE INDEX IF NOT EXISTS idx_turns_created       ON turns(created_at);

-- C8 (ArchCom 2026-08-27): turns_fts + the turns_ai/ad/au triggers were
-- REMOVED — dead index (zero readers in src) and a second plaintext copy
-- of every turn at rest plus write amplification on the hot turn path.
-- Legacy DBs have the table+triggers dropped idempotently in
-- _run_migrations. Turn-level search is not a feature; the DDL lives in
-- VCS history if a /v1/search consumer ever materialises.

-- T-AUTH: bearer tokens, session tokens, TOTP challenges (ADR-0014)
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_id              TEXT PRIMARY KEY,
    token_sha256          TEXT NOT NULL UNIQUE,
    name                  TEXT,
    totp_secret_encrypted BLOB,
    created_at            TEXT NOT NULL,
    expires_at            TEXT,
    disabled_at           TEXT,
    failure_count         INTEGER NOT NULL DEFAULT 0,
    totp_failure_count    INTEGER NOT NULL DEFAULT 0,
    totp_last_step        INTEGER,
    revoked               INTEGER NOT NULL DEFAULT 0,
    totp_required         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    session_sha256 TEXT PRIMARY KEY,
    token_id       TEXT NOT NULL REFERENCES auth_tokens(token_id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    client_ip      TEXT
);

CREATE TABLE IF NOT EXISTS auth_challenges (
    challenge_id TEXT PRIMARY KEY,
    token_id     TEXT NOT NULL REFERENCES auth_tokens(token_id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_sha256      ON auth_tokens(token_sha256);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires   ON auth_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_challenges_expires ON auth_challenges(expires_at);

-- Generic key-value metadata store for cross-run state (e.g. pipeline
-- last-run timestamp). Uses UPSERT so concurrent writers don't clobber
-- each other's rows.
CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- P1-4: CCR (Compress-Cache-Retrieve) reversible compression cache.
-- Stores the ORIGINAL uncompressed content keyed by its SHA-256 hash.
-- The compressed representation embeds a marker referencing this hash;
-- mnemos_retrieve fetches the original back with zero data loss.
-- Inspired by headroom's CCR (https://github.com/headroomlabs-ai/headroom),
-- Apache 2.0 — we integrate into the existing mnemos store (one DB).
-- A1 (ArchCom 2026-08-27): composite PK (project, hash) — the same content
-- hash cached by two projects is TWO rows (the first-writer-squatting
-- cross-project DoS edge of the hash-only PK dissolves). Legacy DBs are
-- rebuilt onto this shape by _run_migrations (first-writer-wins dedup).
CREATE TABLE IF NOT EXISTS ccr_cache (
    hash             TEXT NOT NULL,
    original         TEXT NOT NULL,
    project          TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL DEFAULT 0,
    retrieval_count  INTEGER NOT NULL DEFAULT 0,
    last_retrieved_at TEXT,
    -- ADR-0018 P1-a: verdict of the detect_secrets scan run at store
    -- time. 'clean' | 'hit' | 'unknown' (scanner error). NULL on legacy
    -- rows written before the migration — treated as unscanned. The
    -- stored original is ALWAYS verbatim (zero-loss, committee
    -- decision); this flag is observability only — issuance keeps
    -- scanning unconditionally (patterns evolve; a store-time verdict
    -- alone would go stale).
    secret_scan_verdict TEXT,
    secret_scan_at      TEXT,
    -- A2 (ArchCom 2026-08-27) — issuer ledger: the agent/session that
    -- FIRST stored this (project, hash) row (the UPSERT does not rewrite
    -- them — first-writer owns, the same rule A1 applied to the PK).
    -- NULL on rows stored without caller identity (legacy migrations,
    -- identity-less compress callers): marker provenance for those rows
    -- is unverifiable and strict-mode validation refuses them.
    issuer_agent    TEXT,
    issuer_session  TEXT,
    PRIMARY KEY (project, hash)
);

CREATE INDEX IF NOT EXISTS idx_ccr_cache_project   ON ccr_cache(project);
CREATE INDEX IF NOT EXISTS idx_ccr_cache_created   ON ccr_cache(created_at);
CREATE INDEX IF NOT EXISTS idx_ccr_cache_retrieval ON ccr_cache(retrieval_count);

-- P1-4: FTS5 over cached originals so retrieve(query=...) can rank
-- snippets without a separate DB. External-content table over ccr_cache.
CREATE VIRTUAL TABLE IF NOT EXISTS ccr_cache_fts USING fts5(
    hash UNINDEXED,
    original,
    content=ccr_cache,
    content_rowid=rowid,
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

-- ADR-0018 Phase 1 groundwork: minimal memory graph edges. Only
-- kind='supersedes' exists in Phase 1 (no expansion, no MCP surface —
-- on_context_rewrite arrives with mnemos #125). PK (from, to, kind)
-- makes add_edge idempotent (INSERT OR IGNORE). Self-edges are rejected
-- both here (CHECK) and in add_memory_edge (friendly ValueError).
-- ON DELETE CASCADE keeps edges consistent when memories are deleted.
CREATE TABLE IF NOT EXISTS memory_edges (
    from_memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    to_memory_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('supersedes')),
    created_at     TEXT NOT NULL,
    PRIMARY KEY (from_memory_id, to_memory_id, kind),
    CHECK (from_memory_id <> to_memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_edges_from ON memory_edges(from_memory_id, kind);
CREATE INDEX IF NOT EXISTS idx_memory_edges_to   ON memory_edges(to_memory_id, kind);
"""

_MIGRATIONS: list[tuple[str, str]] = [
    ("project", "ALTER TABLE memories ADD COLUMN project TEXT NOT NULL DEFAULT ''"),
    ("agent", "ALTER TABLE memories ADD COLUMN agent TEXT NOT NULL DEFAULT ''"),
    ("quality_score", "ALTER TABLE memories ADD COLUMN quality_score REAL"),
    ("confidence", "ALTER TABLE memories ADD COLUMN confidence REAL"),
    ("source_coverage", "ALTER TABLE memories ADD COLUMN source_coverage INTEGER"),
    ("cluster_id", "ALTER TABLE memories ADD COLUMN cluster_id TEXT"),
    ("derived_from", "ALTER TABLE memories ADD COLUMN derived_from TEXT NOT NULL DEFAULT '[]'"),
    ("embedding_id", "ALTER TABLE memories ADD COLUMN embedding_id TEXT"),
    ("raw_content", "ALTER TABLE memories ADD COLUMN raw_content TEXT"),
    ("clean_content", "ALTER TABLE memories ADD COLUMN clean_content TEXT"),
    ("filter_profile", "ALTER TABLE memories ADD COLUMN filter_profile TEXT"),
    ("filter_stats", "ALTER TABLE memories ADD COLUMN filter_stats TEXT"),
    ("filter_version", "ALTER TABLE memories ADD COLUMN filter_version TEXT"),
    ("category", "ALTER TABLE memories ADD COLUMN category TEXT"),
    # mnemos #96 — workflow lifecycle columns. Added via ALTER so existing
    # DBs gain the columns on next connect; fresh DBs get them from
    # _DB_SCHEMA. The history table is CREATE TABLE IF NOT EXISTS in
    # _DB_SCHEMA so it does not need an entry here.
    ("workflow_status", "ALTER TABLE memories ADD COLUMN workflow_status TEXT"),
    ("locked_by", "ALTER TABLE memories ADD COLUMN locked_by TEXT"),
    ("locked_at", "ALTER TABLE memories ADD COLUMN locked_at TEXT"),
    # C10 (ArchCom 2026-08-27) — denormalised rewrite-event provenance
    # columns (metadata.source / metadata.rewrite_session). Nullable:
    # non-rewrite memories carry NULL. Existing rows are BACKFILLED once
    # from the metadata JSON by _run_migrations (meta-table flag
    # ``schema_backfill_rewrite_cols_v1``); new rows derive the columns in
    # save(). NOT named `source` — that column is the MemorySource enum.
    ("rewrite_source", "ALTER TABLE memories ADD COLUMN rewrite_source TEXT"),
    ("rewrite_session", "ALTER TABLE memories ADD COLUMN rewrite_session TEXT"),
]

# ADR-0018 P1-a — scan-at-store verdict columns on ccr_cache. Existing
# rows keep NULL (treated as unscanned; issuance keeps scanning them as
# today). Fresh DBs get the columns from _DB_SCHEMA; the ALTER only runs
# on legacy databases. Two columns (verdict + scan timestamp) so the
# flag's freshness is auditable.
_CCR_MIGRATIONS: list[tuple[str, str]] = [
    (
        "secret_scan_verdict",
        "ALTER TABLE ccr_cache ADD COLUMN secret_scan_verdict TEXT",
    ),
    (
        "secret_scan_at",
        "ALTER TABLE ccr_cache ADD COLUMN secret_scan_at TEXT",
    ),
    # A2 (ArchCom 2026-08-27) — issuer-ledger columns. Existing rows keep
    # NULL (provenance unverifiable → strict marker validation refuses
    # them with the distinct "unverifiable legacy marker" reason). Fresh
    # DBs get the columns from _DB_SCHEMA.
    (
        "issuer_agent",
        "ALTER TABLE ccr_cache ADD COLUMN issuer_agent TEXT",
    ),
    (
        "issuer_session",
        "ALTER TABLE ccr_cache ADD COLUMN issuer_session TEXT",
    ),
]

# meta-table flag gating the one-time C10 backfill of the denormalised
# rewrite columns (set inside the same transaction as the backfill).
_BACKFILL_REWRITE_COLS_FLAG: Final[str] = "schema_backfill_rewrite_cols_v1"

# C8 (ArchCom 2026-08-27) — legacy turn FTS objects dropped idempotently on
# every connect (IF EXISTS no-ops after the first run).
_C8_DROP_SQL: Final[tuple[str, ...]] = (
    "DROP TRIGGER IF EXISTS turns_ai",
    "DROP TRIGGER IF EXISTS turns_ad",
    "DROP TRIGGER IF EXISTS turns_au",
    "DROP TABLE IF EXISTS turns_fts",
)

# A1 (ArchCom 2026-08-27) — rebuild target for the ccr_cache composite-PK
# migration. Shape mirrors the ccr_cache DDL in _DB_SCHEMA exactly.
_CCR_REBUILD_DDL: Final[str] = """
CREATE TABLE ccr_cache_a1_rebuild (
    hash               TEXT NOT NULL,
    original           TEXT NOT NULL,
    project            TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    size_bytes         INTEGER NOT NULL DEFAULT 0,
    retrieval_count    INTEGER NOT NULL DEFAULT 0,
    last_retrieved_at  TEXT,
    secret_scan_verdict TEXT,
    secret_scan_at      TEXT,
    issuer_agent        TEXT,
    issuer_session      TEXT,
    PRIMARY KEY (project, hash)
)
"""


# ── SQLiteStore ───────────────────────────────────────────────────────────────


class SQLiteStore:
    """Thread-safe SQLite store with FTS5, pipeline status, and per-agent recall."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._cache = _TTLCache()
        # Serialises CONNECTION BOOTSTRAP (schema script + migrations)
        # across this store's threads. The per-thread connections
        # themselves run unlocked as before — only the first connect of
        # each thread takes the lock. Without it, a manager's background
        # threads (scanner loop, CCR cleanup) racing the main thread's
        # first connect interleave multi-statement DDL: the A1 rebuild's
        # DROP/RENAME can collide with a concurrent fresh-CREATE from
        # the schema script ("database disk image is malformed" /
        # "database is locked"). Cross-PROCESS first-connects are
        # serialized at the SQLite level instead (BEGIN IMMEDIATE +
        # busy_timeout + IF EXISTS convergence).
        self._bootstrap_lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            with self._bootstrap_lock:
                conn = getattr(self._local, "conn", None)
                if conn is None:
                    conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA busy_timeout=5000")
                    conn.executescript(_DB_SCHEMA)
                    self._run_migrations(conn)
                    self._local.conn = conn
        return conn

    @staticmethod
    def _run_migrations(conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        for col, sql in _MIGRATIONS:
            if col not in existing:
                conn.execute(sql)
        # C10 index — created here (not in _DB_SCHEMA) because legacy DBs
        # gain the rewrite columns from the ALTERs above, while _DB_SCHEMA
        # runs before this routine on every connect. IF NOT EXISTS no-ops
        # after the first connect. Backs the per-(project, session) and
        # per-project rewrite quota counts without json_extract scans.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_project_rewrite_source_created "
            "ON memories(project, rewrite_source, created_at)"
        )
        ccr_cols = {row[1] for row in conn.execute("PRAGMA table_info(ccr_cache)").fetchall()}
        for col, sql in _CCR_MIGRATIONS:
            if col not in ccr_cols:
                conn.execute(sql)
        conn.commit()
        # ── Schema-поезд (ArchCom 2026-08-27): A1 + C8 + C10 backfill ──
        SQLiteStore._migrate_c8_drop_turns_fts(conn)
        SQLiteStore._migrate_c10_backfill_rewrite_cols(conn)
        SQLiteStore._migrate_a1_ccr_composite_pk(conn)

    @staticmethod
    def _migrate_c8_drop_turns_fts(conn: sqlite3.Connection) -> None:
        """C8 — drop the dead ``turns_fts`` index and its three triggers.

        Idempotent (IF EXISTS on every connect): after the first run the
        statements are no-ops. Turn writes never touched the FTS table
        directly (only via the triggers), so nothing else changes.
        """
        for sql in _C8_DROP_SQL:
            conn.execute(sql)
        conn.commit()

    @staticmethod
    def _migrate_c10_backfill_rewrite_cols(conn: sqlite3.Connection) -> None:
        """C10 — one-time backfill of ``rewrite_source``/``rewrite_session``.

        Existing rows get the values extracted from the metadata JSON (the
        pre-C10 write path stored them there only). The meta-table flag is
        set in the same commit so an interrupted backfill re-runs on the
        next connect. Rows whose metadata is not valid JSON are skipped
        defensively (json_extract would raise); ``save()`` re-derives both
        columns on the next write of such a row anyway.

        CONCURRENT-CONNECT SAFE (review round): ``_run_migrations`` runs on
        every THREAD-LOCAL connection, and a manager's background threads
        (scanner loop, CCR cleanup) can open their first connection while
        the main thread is still inside this routine. Both connections then
        observe "flag absent" and both run the backfill. The UPDATE is
        idempotent (converges to the same values) and the flag INSERT is
        ``INSERT OR IGNORE`` — the losing racer no-ops instead of raising
        ``UNIQUE constraint failed: meta.key`` (the plain INSERT made
        random concurrent first-connects crash the store and flaked the
        REST suite).
        """
        flag = conn.execute(
            "SELECT 1 FROM meta WHERE key = ?", (_BACKFILL_REWRITE_COLS_FLAG,)
        ).fetchone()
        if flag is not None:
            return
        cur = conn.execute(
            "UPDATE memories SET "
            "rewrite_source = json_extract(metadata, '$.source'), "
            "rewrite_session = json_extract(metadata, '$.rewrite_session') "
            "WHERE json_valid(metadata) "
            "AND (json_extract(metadata, '$.source') IS NOT NULL "
            "     OR json_extract(metadata, '$.rewrite_session') IS NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value, updated_at) VALUES (?,?,?)",
            (
                _BACKFILL_REWRITE_COLS_FLAG,
                "1",
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
        backfilled = int(cur.rowcount or 0)
        if backfilled:
            logger.info("C10 backfill: denormalised %d memory rows", backfilled)

    @staticmethod
    def _migrate_a1_ccr_composite_pk(conn: sqlite3.Connection) -> None:
        """A1 — rebuild ``ccr_cache`` onto the composite PK ``(project, hash)``.

        Detection is by PK shape (PRAGMA table_info pk positions), so fresh
        DBs (already created with the composite PK by _DB_SCHEMA) and
        already-migrated DBs skip this. Legacy hash-PK tables are rebuilt:
        create the new table, copy FIRST-WRITER-WINS (one row per hash —
        the lowest rowid, i.e. the earliest stored copy; the cache is
        derived data and recompressible, so dropped duplicates are
        acceptable by committee decision), drop the old table, rename.
        Rowids are preserved through the copy so the external-content
        ``ccr_cache_fts`` stays addressable; the FTS index is rebuilt
        afterwards regardless (also repairs any pre-existing desync).
        ``_DB_SCHEMA`` is re-executed after the rename to restore the
        ccr_cache indexes and triggers in one place (all IF NOT EXISTS —
        the rest of the script no-ops).

        Review round — CRASH SAFETY: create+copy+drop+rename run inside
        ONE explicit transaction (``BEGIN IMMEDIATE … COMMIT`` via
        executescript; SQLite DDL is transactional). The pre-fix sequence
        of autocommitted statements had a crash wedge: a process death
        between CREATE ``ccr_cache_a1_rebuild`` and the RENAME left an
        orphan rebuild table, and the reopen re-ran the plain CREATE →
        OperationalError from ``_get_conn`` → the store became PERMANENTLY
        unopenable. Now a crash before COMMIT rolls back to the intact
        legacy table (reopen re-runs the migration cleanly), and the
        leading ``DROP TABLE IF EXISTS`` converges an orphan left by any
        pre-fix crash. The post-rename schema re-exec and the FTS 'rebuild'
        stay OUTSIDE the transaction (idempotent, self-healing on the next
        connect) as before.
        """
        info = conn.execute("PRAGMA table_info(ccr_cache)").fetchall()
        pk_positions = {str(row[1]): int(row[5]) for row in info}
        already_composite = pk_positions.get("project") == 1 and pk_positions.get("hash") == 2
        if already_composite or not pk_positions:
            # Already composite (fresh/migrated) or no ccr_cache table —
            # nothing to rebuild. Still converge a possible orphan from a
            # PRE-transactional crash: in the worst window the legacy table
            # was dropped, the rename never ran, and the schema script has
            # just recreated an empty composite cache — the half-built
            # ``ccr_cache_a1_rebuild`` would otherwise linger forever.
            conn.execute("DROP TABLE IF EXISTS ccr_cache_a1_rebuild")
            conn.commit()
            return
        total = int(conn.execute("SELECT COUNT(*) FROM ccr_cache").fetchone()[0])
        try:
            # B608: the script is composed EXCLUSIVELY of static module
            # constants and literals (transaction keywords + the DDL
            # constant + a fully-static copy statement) — no user-controlled
            # fragment ever enters it. One transaction so DDL is atomic.
            conn.executescript(
                "BEGIN IMMEDIATE;\n"  # nosec B608 - static literal
                # Converge an orphaned rebuild table from a pre-fix crash.
                "DROP TABLE IF EXISTS ccr_cache_a1_rebuild;\n"
                + _CCR_REBUILD_DDL.rstrip()
                + ";\n"  # nosec B608 - static literal
                + "INSERT INTO ccr_cache_a1_rebuild "  # nosec B608 - static copy stmt
                "(rowid, hash, original, project, created_at, size_bytes, "
                " retrieval_count, last_retrieved_at, secret_scan_verdict, "
                " secret_scan_at, issuer_agent, issuer_session) "
                "SELECT rowid, hash, original, project, created_at, size_bytes, "
                "       retrieval_count, last_retrieved_at, secret_scan_verdict, "
                "       secret_scan_at, issuer_agent, issuer_session "
                "FROM ccr_cache "
                "WHERE rowid IN (SELECT MIN(rowid) FROM ccr_cache GROUP BY hash);\n"
                "DROP TABLE ccr_cache;\n"
                "ALTER TABLE ccr_cache_a1_rebuild RENAME TO ccr_cache;\n"
                "COMMIT;"
            )
        except Exception:
            # Roll the half-run script back so the next connect sees the
            # intact legacy table and re-runs cleanly; re-raise as-is.
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        # Restore indexes + triggers (and re-assert the rest of the schema).
        conn.executescript(_DB_SCHEMA)
        # Re-sync the external-content FTS index with the rebuilt table.
        conn.execute("INSERT INTO ccr_cache_fts(ccr_cache_fts) VALUES('rebuild')")
        conn.commit()
        kept = int(conn.execute("SELECT COUNT(*) FROM ccr_cache").fetchone()[0])
        logger.info(
            "A1 migration: ccr_cache rebuilt onto composite PK (project, hash): "
            "%d rows kept, %d duplicate-hash rows dropped (first-writer-wins)",
            kept,
            total - kept,
        )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

    # ── Row conversion ────────────────────────────────────────────────────

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
        keys = set(row.keys())

        def _get(k: str, default: Any = None) -> Any:
            return row[k] if k in keys else default

        return Memory(
            id=row["id"],
            content=row["content"],
            title=row["title"],
            tags=json.loads(row["tags"]),
            source=MemorySource(row["source"]),
            source_url=row["source_url"],
            memory_type=MemoryType(row["memory_type"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata"]),
            file_path=row["file_path"],
            category=_get("category"),
            project=_get("project", ""),
            agent=_get("agent", ""),
            status=MemoryStatus(_get("status", "raw")),
            quality_score=_get("quality_score"),
            confidence=_get("confidence"),
            source_coverage=_get("source_coverage"),
            cluster_id=_get("cluster_id"),
            derived_from=json.loads(_get("derived_from") or "[]"),
            embedding_id=_get("embedding_id"),
            raw_content=_get("raw_content"),
            clean_content=_get("clean_content"),
            filter_profile=_get("filter_profile"),
            filter_stats=json.loads(_get("filter_stats")) if _get("filter_stats") else None,
            filter_version=_get("filter_version"),
            # mnemos #96 — workflow projection (read-only here; writes go
            # through set_workflow_status so the state machine cannot be
            # bypassed). NULL on legacy rows created before the migration.
            workflow_status=_get("workflow_status"),
            locked_by=_get("locked_by"),
            locked_at=_get("locked_at"),
        )

    def rebuild_fts_index(self) -> int:
        """Rebuild the FTS5 index from the memories table.

        Use when the FTS5 external-content table is desynced from
        ``memories`` (e.g. after INSERT OR REPLACE corruption or manual
        row deletion).  Returns the number of rows indexed.
        """
        conn = self._get_conn()
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        conn.commit()
        count = int(conn.execute("SELECT count(*) FROM memories_fts").fetchone()[0])
        self._invalidate_caches()
        return count

    def _invalidate_caches(self) -> None:
        self._cache.invalidate(
            "tags",
            "projects_counts",
            "agents_counts",
            "types_counts",
            "data_health",
            "stats",
        )
        self._cache.invalidate_prefix("graph_")
        self._cache.invalidate_prefix("agent_")
        self._cache.invalidate_prefix("project_")

    # ── CRUD ──────────────────────────────────────────────────────────────

    def save(self, memory: Memory, *, trusted_rewrite_provenance: bool = False) -> None:
        """Insert or update a memory, keeping the FTS5 index consistent.

        Uses UPDATE for existing rows (fires AFTER UPDATE trigger) and
        INSERT for new rows (fires AFTER INSERT trigger).  Never uses
        INSERT OR REPLACE — that fires the INSERT trigger with a new
        rowid while the FTS5 external-content table still references the
        old rowid, causing ``missing row N from content table`` errors.

        C10 review round — TRUSTED GATE on the denormalised rewrite
        provenance: ``rewrite_source`` / ``rewrite_session`` derive from
        ``memory.metadata`` ONLY when the trusted rewrite-event path
        (``context_rewrite`` → ``MemoryManager.add``) passes
        ``trusted_rewrite_provenance=True``. The generic create paths
        (REST/MCP/CLI) accept client-controlled ``metadata`` and must
        never mint rewrite quota counters — planted
        ``source='context-rewrite'`` + ``rewrite_session`` there would
        otherwise burn the rewrite channel's per-session and per-project
        quotas (429 DoS) without ever touching the rewrite API. On the
        untrusted UPDATE path the two columns are left UNTOUCHED (an
        edited legitimate event keeps counting; a forged row never had
        them derived in the first place).
        """
        conn = self._get_conn()
        existing = conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory.id,)).fetchone()
        title = memory.auto_title()
        tags_json = json.dumps(memory.tags, ensure_ascii=False)
        meta_json = json.dumps(memory.metadata, ensure_ascii=False)
        derived_json = json.dumps(memory.derived_from, ensure_ascii=False)
        filter_stats_json = (
            json.dumps(memory.filter_stats, ensure_ascii=False) if memory.filter_stats else None
        )
        # C10 — denormalised rewrite provenance (metadata is the source of
        # truth; these columns exist purely so the rewrite quota counts are
        # index-backed). Gated on the trusted caller — see the docstring.
        # Only non-empty str values are promoted; anything else (missing
        # key, JSON object, empty string) stores NULL.
        if trusted_rewrite_provenance:
            meta_src = memory.metadata.get("source")
            rewrite_source = meta_src if isinstance(meta_src, str) and meta_src else None
            meta_sess = memory.metadata.get("rewrite_session")
            rewrite_session = meta_sess if isinstance(meta_sess, str) and meta_sess else None
        else:
            rewrite_source = None
            rewrite_session = None
        if existing:
            if trusted_rewrite_provenance:
                conn.execute(
                    """UPDATE memories SET
                       content = ?, title = ?, tags = ?, source = ?, source_url = ?,
                       memory_type = ?, created_at = ?, updated_at = ?, metadata = ?,
                       file_path = ?, category = ?, project = ?, agent = ?, status = ?,
                       quality_score = ?, confidence = ?, source_coverage = ?,
                       cluster_id = ?, derived_from = ?, embedding_id = ?,
                       raw_content = ?, clean_content = ?, filter_profile = ?,
                       filter_stats = ?, filter_version = ?,
                       rewrite_source = ?, rewrite_session = ?
                       WHERE id = ?""",
                    (
                        memory.content,
                        title,
                        tags_json,
                        memory.source.value,
                        memory.source_url,
                        memory.memory_type.value,
                        memory.created_at.isoformat(),
                        memory.updated_at.isoformat(),
                        meta_json,
                        memory.file_path,
                        memory.category,
                        memory.project,
                        memory.agent,
                        memory.status.value,
                        memory.quality_score,
                        memory.confidence,
                        memory.source_coverage,
                        memory.cluster_id,
                        derived_json,
                        memory.embedding_id,
                        memory.raw_content,
                        memory.clean_content,
                        memory.filter_profile,
                        filter_stats_json,
                        memory.filter_version,
                        rewrite_source,
                        rewrite_session,
                        memory.id,
                    ),
                )
            else:
                # Untrusted UPDATE: the rewrite columns are NOT in the SET
                # list — preserved as-is (cannot be forged or erased here).
                conn.execute(
                    """UPDATE memories SET
                       content = ?, title = ?, tags = ?, source = ?, source_url = ?,
                       memory_type = ?, created_at = ?, updated_at = ?, metadata = ?,
                       file_path = ?, category = ?, project = ?, agent = ?, status = ?,
                       quality_score = ?, confidence = ?, source_coverage = ?,
                       cluster_id = ?, derived_from = ?, embedding_id = ?,
                       raw_content = ?, clean_content = ?, filter_profile = ?,
                       filter_stats = ?, filter_version = ?
                       WHERE id = ?""",
                    (
                        memory.content,
                        title,
                        tags_json,
                        memory.source.value,
                        memory.source_url,
                        memory.memory_type.value,
                        memory.created_at.isoformat(),
                        memory.updated_at.isoformat(),
                        meta_json,
                        memory.file_path,
                        memory.category,
                        memory.project,
                        memory.agent,
                        memory.status.value,
                        memory.quality_score,
                        memory.confidence,
                        memory.source_coverage,
                        memory.cluster_id,
                        derived_json,
                        memory.embedding_id,
                        memory.raw_content,
                        memory.clean_content,
                        memory.filter_profile,
                        filter_stats_json,
                        memory.filter_version,
                        memory.id,
                    ),
                )
        else:
            conn.execute(
                """INSERT INTO memories
                   (id, content, title, tags, source, source_url, memory_type,
                    created_at, updated_at, metadata, file_path, category,
                    project, agent, status, quality_score, confidence,
                    source_coverage, cluster_id, derived_from, embedding_id,
                    raw_content, clean_content, filter_profile, filter_stats,
                    filter_version, rewrite_source, rewrite_session)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    memory.id,
                    memory.content,
                    title,
                    tags_json,
                    memory.source.value,
                    memory.source_url,
                    memory.memory_type.value,
                    memory.created_at.isoformat(),
                    memory.updated_at.isoformat(),
                    meta_json,
                    memory.file_path,
                    memory.category,
                    memory.project,
                    memory.agent,
                    memory.status.value,
                    memory.quality_score,
                    memory.confidence,
                    memory.source_coverage,
                    memory.cluster_id,
                    derived_json,
                    memory.embedding_id,
                    memory.raw_content,
                    memory.clean_content,
                    memory.filter_profile,
                    filter_stats_json,
                    memory.filter_version,
                    # NULL on the generic path regardless of metadata claims.
                    rewrite_source,
                    rewrite_session,
                ),
            )
        conn.commit()
        self._invalidate_caches()

    def get(self, memory_id: str) -> Memory | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_memory(row) if row else None

    def delete(self, memory_id: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        self._invalidate_caches()
        return cur.rowcount > 0

    def update_status(self, memory_id: str, status: MemoryStatus) -> bool:
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        cur = conn.execute(
            "UPDATE memories SET status=?, updated_at=? WHERE id=?",
            (status.value, now, memory_id),
        )
        conn.commit()
        self._invalidate_caches()
        return cur.rowcount > 0

    def update_fields(self, memory_id: str, **kwargs: Any) -> bool:
        """Update arbitrary fields on a memory row.

        M15.2 security hardening: only columns present in the module-level
        `_FIELD_UPDATERS` whitelist are accepted. Column names are taken
        from the static dict (never from kwargs), so the constructed SQL
        contains no user-controlled identifiers. All values are bound
        parameters. B608 (SQL injection) is impossible by construction.

        Unknown keys are silently dropped (defence in depth — column names
        are taken from the whitelist, not from kwargs, so the SQL body is
        safe regardless of what the caller passes).
        """
        if not kwargs:
            return False
        # Drop keys not in the whitelist before they reach the SQL builder.
        # This is the only filter needed: setters are built from the dict
        # values (static SQL fragments), and values are bound parameters.
        updates: dict[str, Any] = {k: kwargs[k] for k in _FIELD_UPDATERS if k in kwargs}
        if not updates:
            return False
        # Serialise JSON fields (only str accepted per the strict whitelist).
        for field in ("derived_from", "tags", "filter_stats"):
            if field in updates and not isinstance(updates[field], str):
                updates[field] = json.dumps(updates[field], ensure_ascii=False)
        updates["updated_at"] = datetime.now(UTC).isoformat()
        # `setters` is built by joining whitelisted static fragments only —
        # no user input flows into the column names. Values are bound `?`.
        setters = ", ".join(_FIELD_UPDATERS[k] for k in updates if k != "updated_at")
        setters = setters + ", updated_at=?"
        values = [updates[k] for k in updates if k != "updated_at"]
        values.append(updates["updated_at"])
        values.append(memory_id)
        conn = self._get_conn()
        # B608: setters built from `_FIELD_UPDATERS` whitelist, not user input.
        cur = conn.execute(
            "UPDATE memories SET " + setters + " WHERE id=?",  # nosec B608
            values,
        )
        conn.commit()
        self._invalidate_caches()
        return cur.rowcount > 0

    # ── Workflow lifecycle (mnemos #96) ────────────────────────────────────
    #
    # These methods are the ONLY writers of the workflow_status / locked_by /
    # locked_at columns and the memory_workflow_history table. They are
    # intentionally NOT exposed via update_fields/_FIELD_UPDATERS so the
    # state machine in MemoryManager.workflow_set cannot be bypassed by a
    # generic field update. The SQL is static (no user-controlled column
    # names) and uses bound parameters throughout, so B608 is impossible by
    # construction.

    def get_workflow_status(self, memory_id: str) -> dict[str, Any] | None:
        """Return the current workflow projection for a memory.

        Returns ``None`` when the memory does not exist so callers can map
        that to a 404. The dict always contains the three keys (values may
        be ``None`` on legacy rows).
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT workflow_status, locked_by, locked_at FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "workflow_status": row["workflow_status"],
            "locked_by": row["locked_by"],
            "locked_at": row["locked_at"],
        }

    def set_workflow_status(
        self,
        memory_id: str,
        workflow_status: str | None,
        locked_by: str | None,
        locked_at: str | None,
    ) -> bool:
        """Write the three workflow columns. Does NOT touch history.

        Caller (MemoryManager.workflow_set) is responsible for the audit
        row and for the state-machine / lock / rate-limit guardrails. This
        method is the low-level writer only.
        """
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE memories SET workflow_status=?, locked_by=?, locked_at=?, "
            "updated_at=? WHERE id=?",
            (
                workflow_status,
                locked_by,
                locked_at,
                datetime.now(UTC).isoformat(),
                memory_id,
            ),
        )
        conn.commit()
        return cur.rowcount > 0

    def add_workflow_history(self, entry: dict[str, Any]) -> None:
        """Append an immutable audit row to memory_workflow_history."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO memory_workflow_history "
            "(id, memory_id, from_status, to_status, actor, reason, force_used, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                entry["id"],
                entry["memory_id"],
                entry["from_status"],
                entry["to_status"],
                entry["actor"],
                entry["reason"],
                entry["force_used"],
                entry["created_at"],
            ),
        )
        conn.commit()

    def get_workflow_history(self, memory_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return audit rows for a memory, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, memory_id, from_status, to_status, actor, reason, "
            "force_used, created_at "
            "FROM memory_workflow_history WHERE memory_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (memory_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "memory_id": r["memory_id"],
                "from_status": r["from_status"],
                "to_status": r["to_status"],
                "actor": r["actor"],
                "reason": r["reason"],
                "force_used": bool(r["force_used"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def count_workflow_transitions_since(self, memory_id: str, since_iso: str) -> int:
        """Count audit rows for ``memory_id`` at/after ``since_iso``.

        Backs the per-memory rate-limit guardrail (#96 guardrail 5).
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM memory_workflow_history "
            "WHERE memory_id = ? AND created_at >= ?",
            (memory_id, since_iso),
        ).fetchone()
        return int(row["n"]) if row else 0

    # ── Listing ───────────────────────────────────────────────────────────

    def list_all(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        source: MemorySource | None = None,
        memory_type: MemoryType | None = None,
        tags: list[str] | None = None,
        status: MemoryStatus | None = None,
        project: str | None = None,
        agent: str | None = None,
        category: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[Memory]:
        conn = self._get_conn()
        q = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if source:
            q += " AND source=?"
            params.append(source.value)
        if memory_type:
            q += " AND memory_type=?"
            params.append(memory_type.value)
        if status:
            q += " AND status=?"
            params.append(status.value)
        if project:
            q += " AND project=?"
            params.append(project)
        if agent:
            q += " AND agent=?"
            params.append(agent)
        if tags:
            for tag in tags:
                q += " AND EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)"
                params.append(tag)
        if category is not None:
            if category == "__uncategorized":
                q += " AND category IS NULL"
            else:
                q += " AND (category=? OR category LIKE ?)"
                params.extend([category, f"{category}/%"])
        if since:
            q += " AND created_at >= ?"
            params.append(since)
        if until:
            q += " AND created_at <= ?"
            params.append(until)
        q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [self._row_to_memory(r) for r in conn.execute(q, params).fetchall()]

    def list_recent_for_agent(
        self,
        agent: str,
        *,
        project: str | None = None,
        limit: int = 20,
    ) -> list[Memory]:
        """M3 — most recent memories for a specific agent (+ optional project)."""
        conn = self._get_conn()
        q = "SELECT * FROM memories WHERE agent=?"
        params: list[Any] = [agent]
        if project:
            q += " AND project=?"
            params.append(project)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_memory(r) for r in conn.execute(q, params).fetchall()]

    def list_by_cluster(self, cluster_id: str) -> list[Memory]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE cluster_id=? ORDER BY created_at ASC",
            (cluster_id,),
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    # ── Export / import query (M17) ───────────────────────────────────────

    def list_for_export(
        self,
        *,
        project: str | None = None,
        agent: str | None = None,
        status: MemoryStatus | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        """Return memories matching export filters, ordered oldest-first.

        ``since`` / ``until`` apply to both ``created_at`` and ``updated_at``
        (a memory is included if either timestamp falls within the window).
        When ``since`` is set, this implements incremental export: only
        memories created or updated after the boundary are returned.
        """
        conn = self._get_conn()
        q = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if project:
            q += " AND project=?"
            params.append(project)
        if agent:
            q += " AND agent=?"
            params.append(agent)
        if status:
            q += " AND status=?"
            params.append(status.value)
        if tags:
            for tag in tags:
                q += " AND EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value = ?)"
                params.append(tag)
        if since:
            q += " AND (created_at >= ? OR updated_at >= ?)"
            params.extend([since.isoformat(), since.isoformat()])
        if until:
            q += " AND (created_at <= ? OR updated_at <= ?)"
            params.extend([until.isoformat(), until.isoformat()])
        q += " ORDER BY created_at ASC"
        if limit is not None:
            q += " LIMIT ?"
            params.append(limit)
        return [self._row_to_memory(r) for r in conn.execute(q, params).fetchall()]

    def wipe_all(self) -> int:
        """Delete every memory row (and FTS shadow rows via triggers).

        Used by ``mnemos import --mode restore``. Returns the number of
        deleted memory rows. Schema, indexes, projects, traces, and DLQ
        are preserved — only the ``memories`` table is cleared.
        """
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM memories")
        conn.commit()
        self._invalidate_caches()
        return cur.rowcount

    def wipe_projects(self) -> int:
        """Delete every project row. Used by restore mode before re-import."""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM projects")
        conn.commit()
        self._invalidate_caches()
        return cur.rowcount

    # ── FTS search ────────────────────────────────────────────────────────

    def fts_search(
        self,
        query: str,
        limit: int = 20,
        *,
        project: str | None = None,
        agent: str | None = None,
        status: MemoryStatus | None = None,
    ) -> list[tuple[Memory, float]]:
        """FTS5 full-text search with optional project/agent/status filters.

        M15.2 hardening: the user-supplied `query` is escaped via
        `_build_fts_query` (FTS5 special chars stripped, rest wrapped in
        double quotes — disables FTS5 prefix/NEAR/column syntax). The
        optional filter columns are bound parameters, never interpolated.
        The SQL body is built by string-concatenating static fragments +
        `?` placeholders, so the resulting statement contains no
        user-controlled identifiers (B608-safe).
        """
        conn = self._get_conn()
        fts_query = self._build_fts_query(query)
        where_parts: list[str] = ["memories_fts MATCH ?"]
        params: list[Any] = [fts_query]
        if project:
            where_parts.append("m.project = ?")
            params.append(project)
        if agent:
            where_parts.append("m.agent = ?")
            params.append(agent)
        if status:
            where_parts.append("m.status = ?")
            params.append(status.value)
        where_clause = " AND ".join(where_parts)
        # B608: where_clause is composed of static fragments + `?` placeholders.
        # No user input is interpolated. rank column is from FTS5 itself.
        sql = (
            "SELECT m.*, f.rank "
            "FROM memories_fts f "
            "JOIN memories m ON m.id = f.id "
            "WHERE " + where_clause + " "  # nosec B608
            "ORDER BY f.rank "
            "LIMIT ?"
        )
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "missing row" in str(exc) or "content table" in str(exc):
                logger.warning("FTS5 index corrupted, auto-rebuilding: %s", exc)
                self.rebuild_fts_index()
                rows = conn.execute(sql, params).fetchall()
            else:
                raise
        results: list[tuple[Memory, float]] = []
        for row in rows:
            memory = self._row_to_memory(row)
            score = 1.0 / (1.0 + abs(float(row["rank"])))
            results.append((memory, score))
        return results

    @staticmethod
    def _build_fts_query(user_query: str) -> str:
        """Convert user input into a safe FTS5 MATCH expression.

        Strategy (per https://www.sqlite.org/fts5.html#fts5_strings):
          1. Strip FTS5 query-syntax special chars: `* " ' ( ) :`
             (the apostrophe is stripped defensively; we then wrap in
             double quotes which themselves become literal).
          2. Collapse runs of whitespace.
          3. Wrap the result in double quotes — FTS5 treats the contents
             of a double-quoted string as a literal phrase with no
             operator parsing (no prefix `*`, no NEAR, no column filters).

        If sanitization empties the input, we return a literal phrase
        containing a deliberately-unique token. FTS5's `""` is a syntax
        error, so we cannot return an empty phrase; a nonsense phrase
        yields zero rows without raising.
        """
        cleaned = _FTS5_SPECIAL_CHARS.sub(" ", user_query or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return '"__mnemos_fts5_no_match_placeholder__"'
        return '"' + cleaned + '"'

    # ── Aggregates ────────────────────────────────────────────────────────

    def get_all_tags(self) -> dict[str, int]:
        hit, val = self._cache.get("tags", 60)
        if hit:
            return cast(dict[str, int], val)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT j.value AS tag, COUNT(*) AS cnt "
            "FROM memories, json_each(memories.tags) AS j "
            "GROUP BY j.value ORDER BY cnt DESC"
        ).fetchall()
        # `r` is sqlite3.Row — index access yields `Any`. The schema
        # guarantees the tag column is text and the count is int, so the
        # explicit str/int casts make the declared dict[str, int] return
        # type hold under mypy --strict. We then `cast` the comprehension
        # so the function's return type is also explicit.
        result: dict[str, int] = {str(r[0]): int(r[1]) for r in rows}
        self._cache.set("tags", result)
        return result

    def count(self) -> int:
        conn = self._get_conn()
        r = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return int(r[0]) if r else 0

    def count_by_status(self) -> dict[str, int]:
        hit, val = self._cache.get("stats", 60)
        if hit:
            return cast(dict[str, int], val)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT COALESCE(status,'raw') AS s, COUNT(*) AS c FROM memories GROUP BY s"
        ).fetchall()
        result: dict[str, int] = {str(r[0]): int(r[1]) for r in rows}
        self._cache.set("stats", result)
        return result

    def get_project_memory_counts(self) -> dict[str, int]:
        hit, val = self._cache.get("projects_counts", 60)
        if hit:
            return cast(dict[str, int], val)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT project, COUNT(*) AS cnt FROM memories WHERE project != '' GROUP BY project"
        ).fetchall()
        result: dict[str, int] = {str(r[0]): int(r[1]) for r in rows}
        self._cache.set("projects_counts", result)
        return result

    def count_by_agent(self) -> dict[str, int]:
        """Count memories grouped by agent (non-empty only)."""
        hit, val = self._cache.get("agents_counts", 60)
        if hit:
            return cast(dict[str, int], val)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT agent, COUNT(*) AS cnt FROM memories WHERE agent != '' GROUP BY agent"
        ).fetchall()
        result: dict[str, int] = {str(r[0]): int(r[1]) for r in rows}
        self._cache.set("agents_counts", result)
        return result

    def count_by_type(self) -> dict[str, int]:
        """Count memories grouped by memory_type."""
        hit, val = self._cache.get("types_counts", 60)
        if hit:
            return cast(dict[str, int], val)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT COALESCE(memory_type,'note') AS t, COUNT(*) AS c FROM memories GROUP BY t"
        ).fetchall()
        result: dict[str, int] = {str(r[0]): int(r[1]) for r in rows}
        self._cache.set("types_counts", result)
        return result

    def count_by_date(
        self,
        *,
        days: int = 30,
        granularity: str = "day",
    ) -> list[dict[str, Any]]:
        """Return daily memory counts for the last ``days`` days.

        granularity is accepted for forward-compat (only "day" supported now).
        Returns a list of ``{"timestamp": "YYYY-MM-DD", "value": N}`` dicts
        ordered by timestamp ascending.
        """
        _ = granularity  # only "day" supported; accepted for API symmetry
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DATE(created_at) AS d, COUNT(*) AS c "
            "FROM memories "
            "WHERE created_at > datetime('now', ?) "
            "GROUP BY d ORDER BY d ASC",
            (f"-{int(days)} days",),
        ).fetchall()
        return [{"timestamp": str(r["d"]), "value": int(r["c"])} for r in rows]

    def count_sessions(self) -> dict[str, int]:
        """Return total and active session counts.

        "active" = sessions updated within the last 24h (heuristic).
        """
        conn = self._get_conn()
        total_row = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        total = int(total_row["c"]) if total_row else 0
        active_row = conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE updated_at > datetime('now', '-1 day')"
        ).fetchone()
        active = int(active_row["c"]) if active_row else 0
        return {"total": total, "active": active}

    def get_filter_stats(self) -> dict[str, Any]:
        """M10 — aggregate Context Filter coverage statistics.

        Returns:
            filtered: count of memories with clean_content populated
            unfiltered: count of memories without clean_content
            avg_reduction_pct: mean char reduction across filtered memories
            by_profile: {profile: count} for filtered memories
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT "
            "COUNT(*) FILTER (WHERE clean_content IS NOT NULL) AS filtered, "
            "COUNT(*) FILTER (WHERE clean_content IS NULL) AS unfiltered "
            "FROM memories"
        ).fetchone()
        filtered = int(row["filtered"]) if row else 0
        unfiltered = int(row["unfiltered"]) if row else 0

        # Per-profile counts (filtered memories only)
        profile_rows = conn.execute(
            "SELECT COALESCE(filter_profile,'default') AS p, COUNT(*) AS c "
            "FROM memories WHERE clean_content IS NOT NULL GROUP BY p"
        ).fetchall()
        by_profile: dict[str, int] = {str(r["p"]): int(r["c"]) for r in profile_rows}

        # Average char reduction: parse filter_stats JSON for each filtered
        # memory and compute (1 - final_chars / original_chars) * 100.
        avg_reduction_pct = 0.0
        if filtered > 0:
            stat_rows = conn.execute(
                "SELECT filter_stats FROM memories "
                "WHERE clean_content IS NOT NULL AND filter_stats IS NOT NULL"
            ).fetchall()
            reductions: list[float] = []
            for sr in stat_rows:
                raw_stats = sr["filter_stats"]
                if not raw_stats:
                    continue
                try:
                    parsed = json.loads(raw_stats)
                except (json.JSONDecodeError, TypeError):
                    continue
                reduction = parsed.get("reduction")
                if not isinstance(reduction, dict):
                    continue
                orig = reduction.get("original_chars")
                final = reduction.get("final_chars")
                if isinstance(orig, (int, float)) and isinstance(final, (int, float)) and orig > 0:
                    reductions.append((1.0 - final / orig) * 100.0)
            if reductions:
                avg_reduction_pct = sum(reductions) / len(reductions)

        return {
            "filtered": filtered,
            "unfiltered": unfiltered,
            "avg_reduction_pct": round(avg_reduction_pct, 2),
            "by_profile": by_profile,
        }

    def get_by_file_path(self, file_path: str) -> Memory | None:
        conn = self._get_conn()
        r = conn.execute("SELECT * FROM memories WHERE file_path=?", (file_path,)).fetchone()
        return self._row_to_memory(r) if r else None

    def get_by_source_url(self, source_url: str) -> Memory | None:
        conn = self._get_conn()
        r = conn.execute("SELECT * FROM memories WHERE source_url=?", (source_url,)).fetchone()
        return self._row_to_memory(r) if r else None

    # ── Traces (M6) ───────────────────────────────────────────────────────

    def save_trace(self, trace: Trace) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO traces
               (id, task_label, project, step, item_id, llm_called, llm_done,
                cache_hit, fallback_used, latency_ms, tokens_in, tokens_out,
                tokens_per_sec, rationale_summary, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trace.id,
                trace.task_label,
                trace.project,
                trace.step,
                trace.item_id,
                int(trace.llm_called),
                int(trace.llm_done),
                int(trace.cache_hit),
                int(trace.fallback_used),
                trace.latency_ms,
                trace.tokens_in,
                trace.tokens_out,
                trace.tokens_per_sec,
                trace.rationale_summary,
                trace.created_at.isoformat(),
            ),
        )
        conn.commit()

    def list_traces(
        self,
        project: str | None = None,
        task_label: str | None = None,
        limit: int = 100,
    ) -> list[Trace]:
        conn = self._get_conn()
        q = "SELECT * FROM traces WHERE 1=1"
        params: list[Any] = []
        if project:
            q += " AND project=?"
            params.append(project)
        if task_label:
            q += " AND task_label=?"
            params.append(task_label)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [Trace.model_validate(dict(r)) for r in rows]

    # ── DLQ (M5) ──────────────────────────────────────────────────────────

    def dlq_add(
        self,
        memory_id: str,
        *,
        cluster_id: str | None = None,
        task_label: str = "synthesize",
        error_message: str = "",
        max_attempts: int = 3,
    ) -> None:
        """Add a failed item to the Dead-Letter Queue."""
        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO dlq
               (id, memory_id, cluster_id, task_label, error_message,
                attempt_count, max_attempts, next_retry_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                memory_id,
                cluster_id,
                task_label,
                error_message,
                1,
                max_attempts,
                now,
                now,
                now,
            ),
        )
        conn.commit()

    def dlq_list(
        self,
        *,
        task_label: str | None = None,
        ready_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List DLQ entries, optionally filtering to retry-ready items."""
        conn = self._get_conn()
        q = "SELECT * FROM dlq WHERE 1=1"
        params: list[Any] = []
        if task_label:
            q += " AND task_label=?"
            params.append(task_label)
        if ready_only:
            q += " AND (next_retry_at IS NULL OR next_retry_at <= ?)"
            params.append(datetime.now(UTC).isoformat())
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(q, params).fetchall()]

    def dlq_increment_attempt(self, dlq_id: str, *, backoff_sec: int = 60) -> None:
        """Bump attempt_count and set next_retry_at with exponential backoff."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT attempt_count, max_attempts FROM dlq WHERE id=?", (dlq_id,)
        ).fetchone()
        if not row:
            return
        attempt = row["attempt_count"] + 1
        next_retry = datetime.now(UTC).isoformat()
        if attempt <= row["max_attempts"]:
            # Exponential backoff with jitter cap
            delay = min(backoff_sec * (2 ** (attempt - 1)), 86400)
            from datetime import timedelta

            next_retry = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        conn.execute(
            "UPDATE dlq SET attempt_count=?, next_retry_at=?, updated_at=? WHERE id=?",
            (attempt, next_retry, datetime.now(UTC).isoformat(), dlq_id),
        )
        conn.commit()

    def dlq_remove(self, dlq_id: str) -> bool:
        """Remove a DLQ entry (discard or after successful retry)."""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM dlq WHERE id=?", (dlq_id,))
        conn.commit()
        return cur.rowcount > 0

    def dlq_count(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM dlq").fetchone()
        return row["c"] if row else 0

    # ── Projects ──────────────────────────────────────────────────────────

    def save_project(self, project: Project) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO projects
               (id, name, description, paths, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (
                project.id,
                project.name,
                project.description,
                json.dumps(project.paths, ensure_ascii=False),
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
            ),
        )
        conn.commit()
        self._invalidate_caches()

    def get_project(self, project_id: str) -> Project | None:
        conn = self._get_conn()
        r = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._row_to_project(r) if r else None

    def get_project_by_name(self, name: str) -> Project | None:
        conn = self._get_conn()
        r = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
        return self._row_to_project(r) if r else None

    def list_projects(self) -> list[Project]:
        conn = self._get_conn()
        return [
            self._row_to_project(r)
            for r in conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        ]

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            paths=json.loads(row["paths"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ── Generic key-value metadata ────────────────────────────────────────

    def set_meta(self, key: str, value: str) -> None:
        """Upsert a metadata row (e.g. pipeline last-run timestamp)."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO meta (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,
                   updated_at=excluded.updated_at""",
            (key, value, datetime.now(UTC).isoformat()),
        )
        conn.commit()

    def get_meta(self, key: str) -> str | None:
        """Read a metadata row. Returns None if the key does not exist."""
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # ── CCR cache (P1-4) ──────────────────────────────────────────────────

    def ccr_store(
        self,
        *,
        hash: str,
        original: str,
        project: str = "",
        issuer_agent: str | None = None,
        issuer_session: str | None = None,
    ) -> int:
        """Insert a CCR cache entry (idempotent on ``(project, hash)``).

        Uses an UPSERT so re-compressing the same content within the SAME
        project is a no-op for the stored row (the original is already
        cached) while still refreshing the scan verdict. A1 (ArchCom
        2026-08-27): the conflict target is the composite PK — the same
        hash stored by a DIFFERENT project inserts its own row (the
        first-writer-squatting cross-project DoS edge of the hash-only
        PK is dissolved). Returns the rowid of the stored (or
        pre-existing) entry.

        ADR-0018 P1-a — scan-at-store verdict: ``detect_secrets`` runs on
        the ORIGINAL at store time and the verdict ('clean' | 'hit' |
        'unknown') is persisted in ``secret_scan_verdict``. The stored
        original itself remains verbatim (zero-loss, committee decision);
        the flag is observability only — issuance (``retrieve_content``)
        keeps scanning unconditionally because patterns evolve and a
        store-time verdict alone would go stale. On 'hit' a WARNING is
        logged with the hash and log-safe per-pattern counts only; raw
        matched values are never logged (hard rule).

        A2 (ArchCom 2026-08-27) — issuer ledger: ``issuer_agent`` /
        ``issuer_session`` record the caller identity that FIRST stored
        this ``(project, hash)`` row. The UPSERT does NOT rewrite them
        on conflict (first-writer owns, mirroring the A1 PK rule): a
        session re-compressing already-cached identical content receives
        a marker whose row stays bound to the first issuer — strict-mode
        provenance then refuses that redemption, which is fail-closed
        and harmless (the re-compressor already holds the content it
        passed in). ``None``/empty values normalise to NULL: rows stored
        without caller identity are unverifiable and strict marker
        validation refuses them (distinct reason).
        """
        from mnemos.secrets_detector import detect_secrets, findings_by_pattern

        conn = self._get_conn()
        now = datetime.now(UTC).isoformat()
        size_bytes = len(original.encode("utf-8"))
        issuer_agent_n = issuer_agent.strip() or None if issuer_agent else None
        issuer_session_n = issuer_session.strip() or None if issuer_session else None
        verdict = "unknown"
        try:
            findings = detect_secrets(original)
        except Exception as exc:  # pragma: no cover — defensive, non-fatal
            logger.warning("CCR store scan failed (verdict=unknown): hash=%s error=%s", hash, exc)
        else:
            verdict = "hit" if findings else "clean"
            if findings:
                # Log-safe: hash + pattern counts only, never matched values.
                logger.warning(
                    "CCR store scan hit: hash=%s patterns=%s — raw values not logged",
                    hash,
                    findings_by_pattern(findings),
                )
        conn.execute(
            "INSERT INTO ccr_cache "
            "(hash, original, project, created_at, size_bytes, retrieval_count, "
            " secret_scan_verdict, secret_scan_at, issuer_agent, issuer_session) "
            "VALUES (?,?,?,?,?,0,?,?,?,?) "
            "ON CONFLICT(project, hash) DO UPDATE SET "
            "  secret_scan_verdict=excluded.secret_scan_verdict, "
            "  secret_scan_at=excluded.secret_scan_at",
            (
                hash,
                original,
                project,
                now,
                size_bytes,
                verdict,
                now,
                issuer_agent_n,
                issuer_session_n,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT rowid FROM ccr_cache WHERE hash=? AND project=?", (hash, project)
        ).fetchone()
        return int(row["rowid"]) if row else 0

    def ccr_get(
        self,
        hash: str,
        *,
        project: str | None = None,
        bump: bool = True,
    ) -> dict[str, Any] | None:
        """Fetch a CCR cache entry by hash and bump its retrieval counter.

        ADR-0018 P1-a — project scoping: when ``project`` is given (a
        non-empty slug), the lookup additionally requires the entry to
        belong to that project. A hash stored under a different project
        returns ``None`` (cross-session marker redemption is denied,
        fail-closed) and the retrieval counter is NOT bumped. When
        ``project`` is ``None`` the lookup stays unscoped (legacy
        behavior preserved for callers without project context); under
        the A1 composite PK the same hash may exist in several projects,
        so the unscoped read resolves to the FIRST-STORED copy (lowest
        rowid / earliest created_at — first-writer-wins, the same rule
        the A1 migration used to dedup legacy rows).

        ADR-0018 P1-b review (F4) — ``bump=False`` skips the
        ``retrieval_count`` / ``last_retrieved_at`` UPDATE (and returns
        the CURRENT count): the issuance layer reads the entry unbumped,
        decides, and calls :meth:`ccr_touch` only when content is
        actually issued — a refused/denied issuance must not LRU-pin
        the entry (``ccr_evict_lru`` protects high-count entries) or
        inflate retrieval stats.

        Returns ``{"hash","original","project","created_at","size_bytes",
        "retrieval_count","secret_scan_verdict","secret_scan_at",
        "issuer_agent","issuer_session"}`` or ``None`` if not found /
        project mismatch. The issuer fields are ``None`` for rows stored
        without caller identity (A2 ledger, see :meth:`ccr_store`).
        """
        conn = self._get_conn()
        sql = "SELECT * FROM ccr_cache WHERE hash=?"
        params: list[Any] = [hash]
        if project:
            sql += " AND project=?"
            params.append(project)
        else:
            sql += " ORDER BY created_at ASC, rowid ASC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        if bump:
            now = datetime.now(UTC).isoformat()
            # Bump exactly the row that was read (its own project — the
            # filter param may be None while the row is project-scoped).
            conn.execute(
                "UPDATE ccr_cache SET retrieval_count=retrieval_count+1, "
                "last_retrieved_at=? WHERE hash=? AND project=?",
                (now, hash, row["project"]),
            )
            conn.commit()
        return {
            "hash": row["hash"],
            "original": row["original"],
            "project": row["project"],
            "created_at": row["created_at"],
            "size_bytes": int(row["size_bytes"]),
            "retrieval_count": int(row["retrieval_count"]) + (1 if bump else 0),
            "secret_scan_verdict": row["secret_scan_verdict"],
            "secret_scan_at": row["secret_scan_at"],
            "issuer_agent": row["issuer_agent"],
            "issuer_session": row["issuer_session"],
        }

    def ccr_touch(self, hash: str, *, project: str | None = None) -> None:
        """Bump a CCR entry's retrieval counter (ADR-0018 P1-b review F4).

        Companion to ``ccr_get(bump=False)``: the issuance layer calls
        this only AFTER deciding to issue content, so refused/denied
        issuances leave ``retrieval_count`` / ``last_retrieved_at``
        untouched. Updating a hash that no longer exists (evicted between
        the read and the decision) is a no-op.

        A1: with the composite PK the same hash may exist in several
        projects — pass the ``project`` of the row that was actually
        issued (``MemoryManager.retrieve_content`` passes the entry's
        own project). ``project=None`` is the legacy global form and
        bumps EVERY copy of the hash (they hold identical content; the
        counter is LRU metadata, not a per-project fact).
        """
        conn = self._get_conn()
        sql = (
            "UPDATE ccr_cache SET retrieval_count=retrieval_count+1, "
            "last_retrieved_at=? WHERE hash=?"
        )
        params: list[Any] = [datetime.now(UTC).isoformat(), hash]
        if project is not None:
            sql += " AND project=?"
            params.append(project)
        conn.execute(sql, params)
        conn.commit()

    def ccr_count(self) -> int:
        """Total number of cached CCR entries."""
        conn = self._get_conn()
        return int(conn.execute("SELECT count(*) FROM ccr_cache").fetchone()[0])

    def ccr_cleanup_ttl(self, ttl_days: int) -> int:
        """Delete cache entries older than ``ttl_days``. Returns count deleted."""
        conn = self._get_conn()
        cutoff = (datetime.now(UTC)).isoformat()
        # SQLite date math: subtract ttl_days from now via the modifiers.
        cur = conn.execute(
            "DELETE FROM ccr_cache WHERE date(created_at, '+' || ? || ' days') < date(?)",
            (ttl_days, cutoff),
        )
        conn.commit()
        return cur.rowcount or 0

    def ccr_evict_lru(self, max_entries: int) -> int:
        """Evict least-retrieved entries until count <= max_entries.

        Ties on retrieval_count break by created_at (oldest first).
        Returns count evicted. A1: eviction is per-ROW (rowid-based) —
        under the composite PK the same hash may exist in several
        projects and evicting "the hash" would delete every copy at
        once; exactly ``excess`` rows are removed.
        """
        conn = self._get_conn()
        total = self.ccr_count()
        if total <= max_entries:
            return 0
        excess = total - max_entries
        cur = conn.execute(
            "DELETE FROM ccr_cache WHERE rowid IN ("
            "  SELECT rowid FROM ccr_cache "
            "  ORDER BY retrieval_count ASC, created_at ASC "
            "  LIMIT ?"
            ")",
            (excess,),
        )
        conn.commit()
        return cur.rowcount or 0

    def ccr_search(
        self,
        hash: str,
        query: str,
        limit: int = 5,
        *,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5-ranked snippet search within a single cached original.

        Returns a list of ``{"snippet","rank}`` dicts ordered by relevance.
        Uses the same FTS5 query sanitisation as ``fts_search`` so the
        user-supplied query cannot inject FTS5 operator syntax.

        ADR-0018 P1-a — project scoping (same defect class as
        ``ccr_get``): when ``project`` is given, the entry's project is
        verified BEFORE the FTS query runs and an empty result is
        returned on mismatch. Defence in depth — ``ccr.retrieve`` already
        scopes the ``ccr_get`` lookup, this guard keeps the snippet
        channel from leaking other projects' entries when a caller
        invokes search directly.

        A1 — the FTS leg joins the content table and resolves to ONE
        copy of the hash: the caller's project when scoped, otherwise
        the first-stored copy (all copies of a hash hold identical
        content — content addressing — but WITHOUT this restriction the
        N project copies would each emit the same snippet and flood the
        limit).

        The snippet highlight markers are the module-level
        ``FTS_SNIPPET_*`` constants (single source of truth): the
        issuance-side scanner (ADR-0018 P1-b m2) strips exactly these
        markers before scanning a snippet, because they split
        multi-token secrets (e.g. a JWT whose payload token matched the
        query) so the raw marked snippet text evades ``detect_secrets``.
        """
        conn = self._get_conn()
        if project:
            owner = conn.execute(
                "SELECT 1 FROM ccr_cache WHERE hash=? AND project=?", (hash, project)
            ).fetchone()
            if owner is None:
                return []
        fts_query = self._build_fts_query(query)
        sql = (
            "SELECT snippet(ccr_cache_fts, 1, ?, ?, ?, 32) AS snip, "
            "f.rank AS rank "
            "FROM ccr_cache_fts f JOIN ccr_cache c ON c.rowid = f.rowid "
            "WHERE f.ccr_cache_fts MATCH ? AND f.hash=? "
        )
        params: list[Any] = [
            FTS_SNIPPET_START_MARK,
            FTS_SNIPPET_END_MARK,
            FTS_SNIPPET_ELLIPSIS,
            fts_query,
            hash,
        ]
        if project:
            sql += "AND c.project = ? "
            params.append(project)
        else:
            # Unscoped: first-stored copy only (identical content across
            # copies; one copy keeps snippets unique under the composite PK).
            sql += "AND c.rowid = (SELECT MIN(rowid) FROM ccr_cache WHERE hash=?) "
            params.append(hash)
        sql += "ORDER BY f.rank LIMIT ?"
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "missing row" in str(exc) or "content table" in str(exc):
                logger.warning("ccr_cache_fts corrupted, skipping search: %s", exc)
                return []
            raise
        return [{"snippet": str(r["snip"]), "rank": float(r["rank"])} for r in rows]

    def ccr_delete_all(self) -> int:
        """Drop every CCR cache entry. Used by tests and `mnemos ccr purge`."""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM ccr_cache")
        conn.commit()
        return cur.rowcount or 0

    # ── Memory edges (ADR-0018 Phase 1 groundwork) ─────────────────────────

    def add_memory_edge(
        self,
        from_memory_id: str,
        to_memory_id: str,
        *,
        kind: str = "supersedes",
    ) -> bool:
        """Add a directed edge between two memories (idempotent).

        Returns ``True`` when a new edge was inserted, ``False`` when an
        identical edge already existed (INSERT OR IGNORE on the
        (from, to, kind) primary key).

        Raises:
            ValueError: self-edge (``from == to``) or unknown ``kind``.
                A memory superseding itself is meaningless and signals a
                caller bug — rejected here with a friendly error; the
                SQL CHECK constraint is the defence-in-depth backstop.
            sqlite3.IntegrityError: either memory id does not exist
                (foreign key, ``PRAGMA foreign_keys=ON``).
        """
        if kind not in _EDGE_KINDS:
            raise ValueError(f"unknown edge kind {kind!r}; supported kinds: {sorted(_EDGE_KINDS)}")
        if from_memory_id == to_memory_id:
            raise ValueError("self-edges are not allowed (from_memory_id == to_memory_id)")
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT OR IGNORE INTO memory_edges "
            "(from_memory_id, to_memory_id, kind, created_at) VALUES (?,?,?,?)",
            (from_memory_id, to_memory_id, kind, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        return cur.rowcount > 0

    def get_direct_edges(
        self,
        from_memory_id: str,
        *,
        kind: str = "supersedes",
    ) -> list[dict[str, Any]]:
        """Return direct outgoing edges for ``from_memory_id`` (no expansion).

        One hop only — graph traversal/expansion is Phase 2 (ADR-0018).
        Returns ``[{"from_memory_id","to_memory_id","kind","created_at"}]``
        ordered by creation time ascending.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT from_memory_id, to_memory_id, kind, created_at "
            "FROM memory_edges WHERE from_memory_id = ? AND kind = ? "
            "ORDER BY created_at ASC",
            (from_memory_id, kind),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_memory_id_by_rewrite_event_key(self, event_key: str) -> str | None:
        """Return the memory id carrying ``metadata.rewrite_event_key``.

        Idempotency lookup for the ``on_context_rewrite`` event (ADR-0018,
        mnemos #125 Wave 2): the event handler computes a content-addressed
        key and consults this BEFORE any write, so a re-delivered event
        performs no duplicate writes. Deliberately a specific method, not a
        generic metadata query — JSON extraction is unindexed and the
        surface stays minimal (same philosophy as ``_EDGE_KINDS``).
        Returns the EARLIEST match (creation order) or ``None``.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM memories "
            "WHERE json_extract(metadata, '$.rewrite_event_key') = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (event_key,),
        ).fetchone()
        return str(row["id"]) if row is not None else None

    def count_recent_context_rewrites(
        self, project: str, session: str | None, since_iso: str
    ) -> int:
        """Count STORED rewrite-event memories for ``(project, session)`` at/after ``since_iso``.

        Backs the per-(project, session) write quota (#125 W2 review F1 —
        mirrors the #96 guardrail-5 pattern). Counts rows in ``memories``,
        i.e. stored events only: a deduplicated re-delivery performs no
        write and therefore consumes no quota. ``session=None`` matches
        rows stored without a session (null-safe ``IS`` comparison).
        C10 (ArchCom 2026-08-27): filters on the denormalised
        ``rewrite_source`` / ``rewrite_session`` columns (maintained by
        ``save()`` and the one-time backfill) so the count is served by
        ``idx_memories_project_rewrite_source_created`` instead of a
        ``json_extract`` full scan per call.
        """
        from mnemos.context_rewrite import SOURCE_CONTEXT_REWRITE

        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM memories "
            "WHERE project = ? AND created_at >= ? AND rewrite_source = ? "
            "AND rewrite_session IS ?",
            (project, since_iso, SOURCE_CONTEXT_REWRITE, session),
        ).fetchone()
        return int(row["n"]) if row else 0

    def count_recent_context_rewrites_by_project(
        self, project: str, since_iso: str
    ) -> tuple[int, int]:
        """Return ``(rows, distinct_sessions)`` for rewrite events in a project.

        C10 (ArchCom 2026-08-27) — backs the SECONDARY per-project
        aggregate write quota: the row count trips the ceiling, the
        distinct-session count is the noisy-neighbor signal carried in
        the log line and the 429 message (one session burning the whole
        project budget vs many sessions). NULL-session events count as
        rows AND as one session bucket (``COALESCE`` — session slugs are
        validated non-empty, so ``''`` cannot collide with a real one).
        Index-backed via ``idx_memories_project_rewrite_source_created``.
        """
        from mnemos.context_rewrite import SOURCE_CONTEXT_REWRITE

        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS n, "
            "COUNT(DISTINCT COALESCE(rewrite_session, '')) AS s "
            "FROM memories "
            "WHERE project = ? AND created_at >= ? AND rewrite_source = ?",
            (project, since_iso, SOURCE_CONTEXT_REWRITE),
        ).fetchone()
        if row is None:  # pragma: no cover — aggregate always returns a row
            return 0, 0
        return int(row["n"]), int(row["s"])
