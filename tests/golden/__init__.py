"""Golden evaluation suite (ADR-0017 D5 + ADR-0020 S1, BF-1).

The corpus and measurement harness migrated to the root-level
``benchmarks/`` catalog (owner directive 2026-08-30, ADR-0020 §4):
``benchmarks/corpus`` and ``benchmarks/stands/s1_quality``. This
package keeps the pytest surface — the regression tripwires that pin
the stand's numbers between re-baselines — and the stand runner
(``make bench-s1``) reuses the same harness for the merge gate.

Run the marked suite:

    pytest tests/golden/ -v            # just the golden suite
    pytest -m golden                   # same, by marker
    pytest -m "not golden"             # everything else

The suite is deterministic (no network, no wall-clock dependence, seeded
feature-hashing embeddings) and is part of the default CI run.
"""
