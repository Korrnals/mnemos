"""Benchmark corpus (ADR-0020) — migrated from ``tests/golden`` byte-exact.

Modules:

* ``corpus``               — fixture entries + planted FAKE secrets;
* ``queries``              — judged queries + relevance judgments;
* ``rewrite_scenario``     — the scripted ADR-0018 rewrite events;
* ``deterministic_embedder`` — the BLAKE2b lexical embedder (no RNG);
* ``tech_patterns``        — legitimate tech-pattern entries (the
  detector-quarantine-fp observability class, BF-1 addition);
* ``danger_labels``        — detector-independent danger labelling.

The first four keep their pre-migration content (only package import
paths changed); the corpus fingerprint in ``benchmarks/baselines`` pins
the exact bytes.
"""
