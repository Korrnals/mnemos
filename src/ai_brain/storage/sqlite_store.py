"""SQLite metadata storage with FTS5 full-text search."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ai_brain.models import Memory, MemorySource, MemoryType


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    title TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'manual',
    source_url TEXT,
    memory_type TEXT NOT NULL DEFAULT 'note',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    file_path TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    id UNINDEXED,
    title,
    content,
    tags,
    content=memories,
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, id, title, content, tags)
    VALUES (new.rowid, new.id, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, id, title, content, tags)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, id, title, content, tags)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.tags);
    INSERT INTO memories_fts(rowid, id, title, content, tags)
    VALUES (new.rowid, new.id, new.title, new.content, new.tags);
END;

CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
"""


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(DB_SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
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
        )

    def save(self, memory: Memory) -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO memories
            (id, content, title, tags, source, source_url, memory_type,
             created_at, updated_at, metadata, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        conn.commit()

    def get(self, memory_id: str) -> Memory | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_memory(row) if row else None

    def delete(self, memory_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_all(
        self,
        limit: int = 50,
        offset: int = 0,
        source: MemorySource | None = None,
        memory_type: MemoryType | None = None,
        tags: list[str] | None = None,
    ) -> list[Memory]:
        conn = self._get_conn()
        query = "SELECT * FROM memories WHERE 1=1"
        params: list = []

        if source:
            query += " AND source = ?"
            params.append(source.value)
        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type.value)
        if tags:
            for tag in tags:
                query += " AND tags LIKE ?"
                params.append(f"%{tag}%")

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def fts_search(self, query: str, limit: int = 20) -> list[tuple[Memory, float]]:
        """Full-text search using FTS5. Returns (memory, rank) pairs."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT m.*, fts.rank
            FROM memories_fts fts
            JOIN memories m ON m.id = fts.id
            WHERE memories_fts MATCH ?
            ORDER BY fts.rank
            LIMIT ?""",
            (query, limit),
        ).fetchall()
        results = []
        for row in rows:
            memory = self._row_to_memory(row)
            rank = float(row["rank"])
            score = 1.0 / (1.0 + abs(rank))
            results.append((memory, score))
        return results

    def get_all_tags(self) -> dict[str, int]:
        """Return all tags with their counts."""
        conn = self._get_conn()
        rows = conn.execute("SELECT tags FROM memories").fetchall()
        tag_counts: dict[str, int] = {}
        for row in rows:
            for tag in json.loads(row["tags"]):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        return dict(sorted(tag_counts.items(), key=lambda x: -x[1]))

    def count(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()
        return int(row["cnt"]) if row else 0

    def get_by_file_path(self, file_path: str) -> Memory | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memories WHERE file_path = ?", (file_path,)
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def get_by_source_file(self, source_path: str) -> Memory | None:
        """Find a memory whose metadata.file_path matches the original source path."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memories WHERE json_extract(metadata, '$.file_path') = ?",
            (source_path,),
        ).fetchone()
        return self._row_to_memory(row) if row else None
