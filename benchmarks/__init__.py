"""Root-level benchmark catalog (ADR-0020, owner directive 2026-08-30).

``benchmarks/corpus`` — the migrated and extended golden corpus;
``benchmarks/stands/{s1_quality,...}`` — the stands S1-S4;
``benchmarks/baselines`` — canonical JSON baselines (source of truth)
plus the generated ``BASELINE.md``; ``benchmarks/reports`` — per-run
reports (not committed).

Excluded from the wheel and sdist (see ``pyproject.toml``).
"""
