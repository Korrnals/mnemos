"""SQLite + numpy vector store for semantic search.

Forked from ai-brain's vector_store.py.
All data stored in WAL-mode SQLite (vectors.db).
Similarity computed with numpy cosine — fast on CPU, no Rust deps.

For Mnemos: vectors are gated on status=published (MemoryManager ensures this).
The collection name is "mnemos_memories" to avoid collisions with ai-brain data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

import numpy as np

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS embeddings (
    id       TEXT PRIMARY KEY,
    vector   BLOB NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
"""
_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_embeddings_id ON embeddings(id);"


class VectorStore:
    """SQLite + numpy vector store — no external Rust-based vector DB required."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = str(data_dir / "vectors.db")
        self._local = threading.local()
        conn = self._conn()
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        # `getattr(..., default=None)` returns `Any`; the truthy check is
        # the runtime guard. After the guard the attribute is the cached
        # Connection. We cast back to sqlite3.Connection so the declared
        # return type holds — mypy --strict enforces this.
        if not getattr(self._local, "conn", None):
            self._local.conn = sqlite3.connect(self._db_path, check_same_thread=False)
        return cast(sqlite3.Connection, self._local.conn)

    # ── pack / unpack ─────────────────────────────────────────────────────

    @staticmethod
    def _pack(embedding: list[float]) -> bytes:
        return np.asarray(embedding, dtype=np.float32).tobytes()

    @staticmethod
    def _unpack(blob: bytes) -> np.ndarray:
        return np.frombuffer(blob, dtype=np.float32)

    # ── write ─────────────────────────────────────────────────────────────

    def upsert(
        self,
        memory_id: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO embeddings(id, vector, metadata) VALUES (?,?,?)",
            (memory_id, self._pack(embedding), json.dumps(metadata or {})),
        )
        conn.commit()

    def batch_upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        batch_size: int = 500,
    ) -> None:
        metas: list[dict[str, Any]] = metadatas or [{} for _ in ids]
        rows = [
            (i, self._pack(e), json.dumps(m))
            for i, e, m in zip(ids, embeddings, metas, strict=True)
        ]
        conn = self._conn()
        for start in range(0, len(rows), batch_size):
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings(id, vector, metadata) VALUES (?,?,?)",
                rows[start: start + batch_size],
            )
        conn.commit()

    def delete(self, memory_id: str) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM embeddings WHERE id=?", (memory_id,))
        conn.commit()

    # ── search ────────────────────────────────────────────────────────────

    def search(
        self,
        query_embedding: list[float],
        limit: int = 20,
    ) -> list[tuple[str, float]]:
        """Cosine similarity search. Returns (memory_id, score) sorted descending."""
        conn = self._conn()
        rows = conn.execute("SELECT id, vector FROM embeddings").fetchall()
        if not rows:
            return []

        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q = q / q_norm

        ids = [r[0] for r in rows]
        mat = np.stack([self._unpack(r[1]) for r in rows])  # (N, D)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        mat = mat / norms
        scores: np.ndarray = mat @ q  # (N,)

        top_k = min(limit, len(scores))
        idx = np.argpartition(scores, -top_k)[-top_k:]
        idx = idx[np.argsort(scores[idx])[::-1]]
        return [(ids[i], float(scores[i])) for i in idx]

    # ── read helpers ──────────────────────────────────────────────────────

    def has(self, memory_id: str) -> bool:
        conn = self._conn()
        return bool(
            conn.execute(
                "SELECT 1 FROM embeddings WHERE id=? LIMIT 1", (memory_id,)
            ).fetchone()
        )

    def count(self) -> int:
        # `fetchone()[0]` is `Any` (sqlite3.Row); COUNT(*) is always int.
        row = self._conn().execute("SELECT COUNT(*) FROM embeddings").fetchone()
        return int(row[0]) if row else 0

    def get_embeddings(self, ids: list[str]) -> dict[str, list[float]]:
        if not ids:
            return {}
        conn = self._conn()
        # placeholders is a static join of literal "?" characters; ids are bound
        # via parameter substitution, so no user input reaches the SQL string.
        placeholders = ",".join(["?"] * len(ids))
        sql = (
            "SELECT id, vector FROM embeddings WHERE id IN (" + placeholders + ")"  # nosec B608
        )
        rows = conn.execute(sql, ids).fetchall()
        return {r[0]: self._unpack(r[1]).tolist() for r in rows}
