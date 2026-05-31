"""Storage layer for Mnemos.

Submodules:
  - sqlite_store: SQLite FTS5 for raw/processing/processed/traces
  - vector_store: ChromaDB for published knowledge units only
  - vault: Obsidian-compatible markdown mirror

TODO (M2/M3): Implement SQLiteStore and VectorStore backed by ai-brain's
storage layer, renamed and extended with pipeline status filtering.
"""
