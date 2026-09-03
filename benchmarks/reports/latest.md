# mnemos benchmark report — one page per wave (ADR-0020 §5)

Generated 2026-09-03T18:15:39+00:00 from `benchmarks/baselines/*.json` (bytes, not memory).
Traffic light: 🟢 corridor holds / invariant meets requirement · 🟡 skip, noise, or baseline not born yet · 🔴 breach.

| Family | Domain | Light |
|---|---|---|
| F1 | Latencies (S2 nightly) | 🟡 |
| F2 | Accuracy / quality (S1 + S1m + S3) | 🟢 |
| F3 | Token economy (S3 + S1) | 🟢 |
| F4 | Composition cleanliness (S1 + S3) | 🟢 |
| F5 | Session coherence (S3) | 🟢 |
| F6 | Availability (S4) | 🟢 |
| F7 | Extensibility (S1) | 🟢 |

**Overall: 🟡 YELLOW**

## F1 — Latencies (S2 nightly) 🟡

- no S2 baseline yet — the timing baseline is a property of the nightly quiet machine (`make bench-s2-nightly` with `S2_NIGHTLY_FLAGS=--record-nightly`); local smoke is informational only (ADR-0020 §5)
- latest nightly verdict: NOISE — add (NOISE); NOISE de-escalates to report + ticket (ADR-0020 §5), REGRESSION blocks

## F2 — Accuracy / quality (S1 + S1m + S3) 🟢

- reference (BLAKE2b) recall@10 0.8944 (Δ +0.0000 vs baseline) / precision@5 0.2293 over 191 judged queries
- production embedder (S1m): recall@10 0.9424 / MRR 0.9466 — self-comparison corridor
- sufficiency@task (S3): 0.9333 (Δ +0.0000 vs baseline)
- trend vs previous wave: →

**Invariants:**
- injection_acceptance = 1.0000 (required = 1.0000) — OK
- non_admissible_surfaced = 0 (required = 0) — OK
- quarantine_exclusion = 1.0000 (required = 1.0000) — OK

## F3 — Token economy (S3 + S1) 🟢

- context-growth-factor (S3, stop-signal): 1.0000 (Δ +0.0000 vs baseline) — ceiling +0.02 over baseline
- rewrite pair (S1): hit 0.9375 (Δ +0.0000 vs baseline) / regret 0.2500
- trend vs previous wave: →

## F4 — Composition cleanliness (S1 + S3) 🟢

- assembled-noise: duplicate occurrences at k=5/10 = 0/0
- stage-discard profile (S3, informational): 40 assembles, 0 scan-refused, 0 budget-skipped
- trend vs previous wave: →

**Invariants:**
- duplicate_occurrences_at_5 = 0 (required = 0) — OK
- duplicate_occurrences_at_10 = 0 (required = 0) — OK
- rewrite_redemption_leaks = 0 (required = 0) — OK

## F5 — Session coherence (S3) 🟢

- fact-retention@N,k: 1.0000 (Δ +0.0000 vs baseline) (53/53 probes)
- recall-drift-over-session: 0.0000 (Δ +0.0000 vs baseline) (negative = degradation)
- trend vs previous wave: →

**Invariants:**
- checkpoint_return_integrity = 1.0000 (required = 1.0000) — OK

## F6 — Availability (S4) 🟢

- probe-pass-rate: 1.0000 (Δ +0.0000 vs baseline); memory-completeness: 1.0000 (Δ +0.0000 vs baseline)
- embed-staleness: 0 stale of 0 checked refined; read-only invariant ok
- trend vs previous wave: →

**Invariants:**
- quarantine_exclusion = 1.0000 (required = 1.0000) — OK

## F7 — Extensibility (S1) 🟢

- scale-sensitivity (A9 recall@10 delta current vs pre-A9): -0.0157 (floor -0.02)
- trend vs previous wave: →

**Invariants:**
- foreign_project_surfaced = 0 (required = 0) — OK
- render_neutrality = 0 (required = 0) — OK
