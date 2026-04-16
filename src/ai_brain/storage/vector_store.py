"""ChromaDB vector store for semantic search."""

from __future__ import annotations

from pathlib import Path

import chromadb

from ai_brain.models import Memory


class VectorStore:
    """ChromaDB-based vector storage for memory embeddings."""

    COLLECTION_NAME = "brain_memories"

    def __init__(self, data_dir: Path) -> None:
        persist_dir = data_dir / "chroma_data"
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, memory_id: str, embedding: list[float], metadata: dict | None = None) -> None:
        """Add or update embedding for a memory."""
        self._collection.upsert(
            ids=[memory_id],
            embeddings=[embedding],
            metadatas=[metadata or {}],
        )

    def delete(self, memory_id: str) -> None:
        """Delete embedding by memory ID."""
        try:
            self._collection.delete(ids=[memory_id])
        except Exception:
            pass

    def search(
        self,
        query_embedding: list[float],
        limit: int = 20,
        where: dict | None = None,
    ) -> list[tuple[str, float]]:
        """Semantic search. Returns list of (memory_id, similarity_score)."""
        kwargs: dict = {
            "query_embeddings": [query_embedding],
            "n_results": limit,
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        pairs: list[tuple[str, float]] = []
        if results["ids"] and results["distances"]:
            for mid, dist in zip(results["ids"][0], results["distances"][0]):
                score = 1.0 - dist  # cosine distance → similarity
                pairs.append((mid, score))
        return pairs

    def count(self) -> int:
        return self._collection.count()

    def has(self, memory_id: str) -> bool:
        try:
            result = self._collection.get(ids=[memory_id])
            return len(result["ids"]) > 0
        except Exception:
            return False
