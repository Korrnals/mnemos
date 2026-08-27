"""Golden evaluation suite (ADR-0017 D5, #125 Phase 1 Wave 4).

Deterministic golden-set measurement harness: retrieval quality
(precision@k / recall@k), injection-acceptance (planted-secret leak rate),
the A9 vector-leg predicate before/after comparison, and the ADR-0018
rewrite metric pair (replace-hit-rate / replace-regret-rate).

Run the marked suite:

    pytest tests/golden/ -v            # just the golden suite
    pytest -m golden                   # same, by marker
    pytest -m "not golden"             # everything else

The suite is deterministic (no network, no wall-clock dependence, seeded
feature-hashing embeddings) and is part of the default CI run.
"""
