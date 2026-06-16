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

# Result types live in mnemos.models (shared between the pipeline package
# and the rest of the app). The pipeline workers import them from there
# too; we re-export at the package level so `from mnemos.pipeline import
# ClusterResult` works without forcing callers to know the inner layout.
from mnemos.models import (
    ClusterResult,
    PublishResult,
    QualityResult,
    SynthesisResult,
)

__all__ = [
    "ClusterResult",
    "PublishResult",
    "QualityResult",
    "SynthesisResult",
]
