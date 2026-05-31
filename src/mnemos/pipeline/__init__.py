"""Knowledge pipeline for Mnemos.

Stages: raw → [cluster] → processing → [synthesize + quality_gate]
        → processed → [publish] → published

Submodules:
  cluster      — M4: group raw entries by embedding similarity
  synthesize   — M4: LLM draft synthesis for a cluster
  quality_gate — M4: score / confidence / source_coverage thresholds
  publish      — M4: status=processed→published + ChromaDB upsert

Key invariant: only status="published" ever enters the ChromaDB vector index.
"""
