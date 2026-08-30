"""S1 quality stand (ADR-0020) — issuance quality and safety.

Deterministic by construction: BLAKE2b lexical embedder, fixed corpus
order, no wall-clock, no RNG, no network. ``harness`` reuses the W4
golden measurement logic; ``scenarios`` adds the ADR-0019 §5
quarantine/retraction scenarios, the detector-quarantine-fp class, the
render-neutrality invariant and the interim McNemar sign-test jig;
``run`` is the single-command entry point.
"""
