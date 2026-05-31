"""SQLite metadata storage with FTS5 full-text search.

Forked from ai-brain's sqlite_store.py.
Key additions for Mnemos:
  - Extended schema: project, agent (denormalised from tags)
  - Pipeline fields: quality_score, confidence, source_coverage,
    cluster_id, derived_from, embedding_id
  - Context Filter fields: raw_content, clean_content, filter_profile,
    filter_stats, filter_version
  - Trace table (M6 — explainability)
  - FTS also indexes project + agent for fast per-agent recall (M3)
  - Per-agent / per-project query helpers (M3)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from sys import getsizeof
from typing import Any

from mnemos.models import (
    Memory,
    MemorySource,
    MemoryStatus,
    MemoryType,
    Project,
    Trace,
)

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
        except Exception:
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
    -- GCW tag contract denormalisations (M2)
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
    filter_version   TEXT
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
"""

_MIGRATIONS: list[tuple[str, str]] = [
    ("project",         "ALTER TABLE memories ADD COLUMN project TEXT NOT NULL DEFAULT ''"),
    ("agent",           "ALTER TABLE memories ADD COLUMN agent TEXT NOT NULL DEFAULT ''"),
    ("quality_score",   "ALTER TABLE memories ADD COLUMN quality_score REAL"),
    ("confidence",      "ALTER TABLE memories ADD COLUMN confidence REAL"),
    ("source_coverage", "ALTER TABLE memories ADD COLUMN source_coverage INTEGER"),
    ("cluster_id",      "ALTER TABLE memories ADD COLUMN cluster_id TEXT"),
    ("derived_from",    "ALTER TABLE memories ADD COLUMN derived_from TEXT NOT NULL DEFAULT '[]'"),
    ("embedding_id",    "ALTER TABLE memories ADD COLUMN embedding_id TEXT"),
    ("raw_content",     "ALTER TABLE memories ADD COLUMN raw_content TEXT"),
    ("clean_content",   "ALTER TABLE memories ADD COLUMN clean_content TEXT"),
    ("filter_profile",  "ALTER TABLE memories ADD COLUMN filter_profile TEXT"),
    ("filter_stats",    "ALTER TABLE memories ADD COLUMN filter_stats TEXT"),
    ("filter_version",  "ALTER TABLE memories ADD COLUMN filter_version TEXT"),
    ("category",        "ALTER TABLE memories ADD COLUMN category TEXT"),
]


# ── SQLiteStore ───────────────────────────────────────────────────────────────


class SQLiteStore:
    """Thread-safe SQLite store with FTS5, pipeline status, and per-agent recall."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._cache = _TTLCache()

    def _get_conn(self) -> sqlite3.Connection:
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
        conn.commit()

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
        )

    def _invalidate_caches(self) -> None:
        self._cache.invalidate("tags", "projects_counts", "data_health", "stats")
        self._cache.invalidate_prefix("graph_")
        self._cache.invalidate_prefix("agent_")
        self._cache.invalidate_prefix("project_")

    # ── CRUD ──────────────────────────────────────────────────────────────

    def save(self, memory: Memory) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, content, title, tags, source, source_url, memory_type,
                created_at, updated_at, metadata, file_path, category,
                project, agent, status, quality_score, confidence,
                source_coverage, cluster_id, derived_from, embedding_id,
                raw_content, clean_content, filter_profile, filter_stats, filter_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                memory.id,
                memory.content,
                memory.auto_title(),
                json.dumps(memory.tags, ensure_ascii=False),
                memory.source.value,
                memory.source_url,
                memory.memory_type.value,
                memory.created_at.isoformat(),
                memory.updated_at.isoformat(),
                json.dumps(memory.metadata, ensure_ascii=False),
                memory.file_path,
                memory.category,
                memory.project,
                memory.agent,
                memory.status.value,
                memory.quality_score,
                memory.confidence,
                memory.source_coverage,
                memory.cluster_id,
                json.dumps(memory.derived_from, ensure_ascii=False),
                memory.embedding_id,
                memory.raw_content,
                memory.clean_content,
                memory.filter_profile,
                json.dumps(memory.filter_stats, ensure_ascii=False)
                if memory.filter_stats
                else None,
                memory.filter_version,
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
        """Update arbitrary fields on a memory row."""
        if not kwargs:
            return False
        allowed = {
            "status", "quality_score", "confidence", "source_coverage",
            "cluster_id", "derived_from", "embedding_id", "clean_content",
            "filter_profile", "filter_stats", "filter_version",
            "title", "content", "tags", "category", "file_path",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        # Serialise JSON fields
        for field in ("derived_from", "tags", "filter_stats"):
            if field in updates and not isinstance(updates[field], str):
                updates[field] = json.dumps(updates[field], ensure_ascii=False)
        updates["updated_at"] = datetime.now(UTC).isoformat()
        setters = ", ".join(f"{k}=?" for k in updates)
        values = [*updates.values(), memory_id]
        conn = self._get_conn()
        cur = conn.execute(f"UPDATE memories SET {setters} WHERE id=?", values)
        conn.commit()
        self._invalidate_caches()
        return cur.rowcount > 0

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
                q += " AND tags LIKE ?"
                params.append(f'%"{tag}"%')
        if category is not None:
            if category == "__uncategorized":
                q += " AND category IS NULL"
            else:
                q += " AND (category=? OR category LIKE ?)"
                params.extend([category, f"{category}/%"])
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
        """FTS5 full-text search with optional project/agent/status filters."""
        conn = self._get_conn()
        # Build a SQL query that joins FTS results with the memories table for filters
        extra_where = ""
        params: list[Any] = [query]
        if project:
            extra_where += " AND m.project=?"
            params.append(project)
        if agent:
            extra_where += " AND m.agent=?"
            params.append(agent)
        if status:
            extra_where += " AND m.status=?"
            params.append(status.value)
        params.append(limit)
        sql = f"""
            SELECT m.*, fts.rank
            FROM memories_fts fts
            JOIN memories m ON m.id = fts.id
            WHERE memories_fts MATCH ?{extra_where}
            ORDER BY fts.rank
            LIMIT ?
        """
        rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            memory = self._row_to_memory(row)
            score = 1.0 / (1.0 + abs(float(row["rank"])))
            results.append((memory, score))
        return results

    # ── Aggregates ────────────────────────────────────────────────────────

    def get_all_tags(self) -> dict[str, int]:
        hit, val = self._cache.get("tags", 60)
        if hit:
            return val
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT j.value AS tag, COUNT(*) AS cnt "
            "FROM memories, json_each(memories.tags) AS j "
            "GROUP BY j.value ORDER BY cnt DESC"
        ).fetchall()
        result = {r[0]: r[1] for r in rows}
        self._cache.set("tags", result)
        return result

    def count(self) -> int:
        conn = self._get_conn()
        r = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return int(r[0]) if r else 0

    def count_by_status(self) -> dict[str, int]:
        hit, val = self._cache.get("stats", 60)
        if hit:
            return val
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT COALESCE(status,'raw') AS s, COUNT(*) AS c FROM memories GROUP BY s"
        ).fetchall()
        result = {r[0]: r[1] for r in rows}
        self._cache.set("stats", result)
        return result

    def get_project_memory_counts(self) -> dict[str, int]:
        hit, val = self._cache.get("projects_counts", 60)
        if hit:
            return val
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT project, COUNT(*) AS cnt FROM memories WHERE project != '' GROUP BY project"
        ).fetchall()
        result = {r[0]: r[1] for r in rows}
        self._cache.set("projects_counts", result)
        return result

    def get_by_file_path(self, file_path: str) -> Memory | None:
        conn = self._get_conn()
        r = conn.execute("SELECT * FROM memories WHERE file_path=?", (file_path,)).fetchone()
        return self._row_to_memory(r) if r else None

    def get_by_source_url(self, source_url: str) -> Memory | None:
        conn = self._get_conn()
        r = conn.execute(
            "SELECT * FROM memories WHERE source_url=?", (source_url,)
        ).fetchone()
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
        return [self._row_to_project(r) for r in conn.execute(
            "SELECT * FROM projects ORDER BY name"
        ).fetchall()]

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            paths=json.loads(row["paths"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
