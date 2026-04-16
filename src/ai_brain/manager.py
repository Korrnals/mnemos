"""MemoryManager — core orchestrator for CRUD and search operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from ai_brain.config import Settings
from ai_brain.embedding import EmbeddingProvider, create_embedding_provider
from ai_brain.models import (
    Memory,
    MemoryCreate,
    MemorySource,
    MemoryType,
    MemoryUpdate,
    SearchQuery,
    SearchResult,
)
from ai_brain.storage.sqlite_store import SQLiteStore
from ai_brain.storage.vault import VaultManager
from ai_brain.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


class MemoryManager:
    """Central coordinator for all memory operations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sqlite = SQLiteStore(settings.brain.data_dir / "brain.db")
        self.vault = VaultManager(settings.brain.vault_path)
        self.vectors = VectorStore(settings.brain.data_dir)
        self._embedder: EmbeddingProvider | None = None

    @property
    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            self._embedder = create_embedding_provider(self.settings.embedding)
        return self._embedder

    def close(self) -> None:
        self.sqlite.close()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add(self, data: MemoryCreate) -> Memory:
        """Create a new memory entry."""
        memory = Memory(
            content=data.content,
            title=data.title,
            tags=data.tags,
            source=data.source,
            source_url=data.source_url,
            memory_type=data.memory_type,
            metadata=data.metadata,
        )

        # Generate embedding
        embedding = self.embedder.embed(self._embedding_text(memory))

        # Write to Obsidian vault
        file_path = self.vault.memory_to_file(memory)
        memory.file_path = str(file_path)

        # Save to SQLite
        self.sqlite.save(memory)

        # Save embedding to ChromaDB
        self.vectors.upsert(
            memory.id,
            embedding,
            {"source": memory.source.value, "memory_type": memory.memory_type.value},
        )

        logger.info("Added memory %s: %s", memory.id[:8], memory.auto_title())
        return memory

    def get(self, memory_id: str) -> Memory | None:
        return self.sqlite.get(memory_id)

    def update(self, memory_id: str, data: MemoryUpdate) -> Memory | None:
        memory = self.sqlite.get(memory_id)
        if not memory:
            return None

        if data.content is not None:
            memory.content = data.content
        if data.title is not None:
            memory.title = data.title
        if data.tags is not None:
            memory.tags = data.tags
        if data.memory_type is not None:
            memory.memory_type = data.memory_type
        if data.metadata is not None:
            memory.metadata = data.metadata

        memory.updated_at = datetime.now(timezone.utc)

        # Re-embed
        embedding = self.embedder.embed(self._embedding_text(memory))

        # Update vault file
        file_path = self.vault.memory_to_file(memory)
        memory.file_path = str(file_path)

        # Update stores
        self.sqlite.save(memory)
        self.vectors.upsert(
            memory.id,
            embedding,
            {"source": memory.source.value, "memory_type": memory.memory_type.value},
        )

        return memory

    def delete(self, memory_id: str) -> bool:
        memory = self.sqlite.get(memory_id)
        if not memory:
            return False

        if memory.file_path:
            self.vault.delete_file(memory.file_path)

        self.vectors.delete(memory_id)
        self.sqlite.delete(memory_id)
        logger.info("Deleted memory %s", memory_id[:8])
        return True

    def list_memories(
        self,
        limit: int = 50,
        offset: int = 0,
        source: MemorySource | None = None,
        memory_type: MemoryType | None = None,
        tags: list[str] | None = None,
    ) -> list[Memory]:
        return self.sqlite.list_all(limit, offset, source, memory_type, tags)

    # ── SEARCH ────────────────────────────────────────────────────────────

    def search(self, query: SearchQuery) -> list[SearchResult]:
        """Hybrid search: combines semantic (vector) and full-text search."""
        alpha = query.hybrid_alpha or self.settings.search.hybrid_alpha
        limit = query.limit or self.settings.search.default_limit

        # Semantic search
        query_embedding = self.embedder.embed(query.query)
        where_filter = self._build_chroma_filter(query)
        vector_results = self.vectors.search(query_embedding, limit=limit * 2, where=where_filter)

        # Full-text search
        fts_results = self.sqlite.fts_search(query.query, limit=limit * 2)

        # Combine via Reciprocal Rank Fusion (RRF)
        return self._rrf_merge(vector_results, fts_results, alpha, limit, query)

    def semantic_search(self, text: str, limit: int = 20) -> list[SearchResult]:
        """Pure semantic search."""
        query_embedding = self.embedder.embed(text)
        vector_results = self.vectors.search(query_embedding, limit=limit)

        results = []
        for memory_id, score in vector_results:
            memory = self.sqlite.get(memory_id)
            if memory:
                results.append(SearchResult(memory=memory, score=score, search_type="semantic"))
        return results

    # ── SYNC ──────────────────────────────────────────────────────────────

    def sync_vault(self) -> dict[str, int]:
        """Full sync: scan Obsidian vault and index all files."""
        memories = self.vault.scan_vault()
        added = 0
        updated = 0

        for memory in memories:
            existing = self.sqlite.get(memory.id)
            if existing is None:
                # Also check by file path
                existing = self.sqlite.get_by_file_path(memory.file_path or "")

            embedding = self.embedder.embed(self._embedding_text(memory))

            if existing:
                memory.id = existing.id
                updated += 1
            else:
                added += 1

            self.sqlite.save(memory)
            self.vectors.upsert(
                memory.id,
                embedding,
                {"source": memory.source.value, "memory_type": memory.memory_type.value},
            )

        logger.info("Vault sync complete: %d added, %d updated", added, updated)
        return {"added": added, "updated": updated, "total": len(memories)}

    # ── TAGS ──────────────────────────────────────────────────────────────

    def get_tags(self) -> dict[str, int]:
        return self.sqlite.get_all_tags()

    # ── STATS ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_memories": self.sqlite.count(),
            "total_embeddings": self.vectors.count(),
            "vault_path": str(self.settings.brain.vault_path),
            "data_dir": str(self.settings.brain.data_dir),
        }

    # ── INTERNAL ──────────────────────────────────────────────────────────

    def _embedding_text(self, memory: Memory) -> str:
        """Prepare text for embedding: title + tags + content."""
        parts = []
        title = memory.auto_title()
        if title:
            parts.append(title)
        if memory.tags:
            parts.append(" ".join(f"#{t}" for t in memory.tags))
        parts.append(memory.content)
        return "\n".join(parts)

    def _build_chroma_filter(self, query: SearchQuery) -> dict | None:
        conditions = []
        if query.source:
            conditions.append({"source": {"$eq": query.source.value}})
        if query.memory_type:
            conditions.append({"memory_type": {"$eq": query.memory_type.value}})
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _rrf_merge(
        self,
        vector_results: list[tuple[str, float]],
        fts_results: list[tuple[Memory, float]],
        alpha: float,
        limit: int,
        query: SearchQuery,
    ) -> list[SearchResult]:
        """Reciprocal Rank Fusion merging of semantic and FTS results."""
        k = 60  # RRF constant

        scores: dict[str, float] = {}
        search_types: dict[str, str] = {}

        # Semantic scores
        for rank, (memory_id, sim_score) in enumerate(vector_results):
            rrf_score = alpha / (k + rank + 1)
            scores[memory_id] = scores.get(memory_id, 0) + rrf_score
            search_types[memory_id] = "semantic"

        # FTS scores
        for rank, (memory, fts_score) in enumerate(fts_results):
            rrf_score = (1 - alpha) / (k + rank + 1)
            scores[memory.id] = scores.get(memory.id, 0) + rrf_score
            if memory.id in search_types:
                search_types[memory.id] = "hybrid"
            else:
                search_types[memory.id] = "fts"

        # Sort by combined score
        sorted_ids = sorted(scores.keys(), key=lambda x: -scores[x])[:limit]

        # Tag filtering (post-filter if needed)
        results = []
        for memory_id in sorted_ids:
            memory = self.sqlite.get(memory_id)
            if not memory:
                continue
            if query.tags and not set(query.tags).intersection(set(memory.tags)):
                continue
            results.append(
                SearchResult(
                    memory=memory,
                    score=scores[memory_id],
                    search_type=search_types.get(memory_id, "hybrid"),
                )
            )

        return results[:limit]
