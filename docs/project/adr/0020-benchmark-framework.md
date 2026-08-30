# ADR 0020: Memory Benchmark Framework

**Status:** Accepted (Architectural Committee, 2026-08-30) — parallel
non-blocking track per owner directive (mnemos `818613c8`); amends ADR-0019 §5
(retraction render)
**Amended by:** owner directive 2026-08-30 (recorded 2026-08-31) — baseline
storage moves from `tests/golden/baselines/` to a root-level `benchmarks/`
directory with its own nesting, excluded from the wheel; see *Canonical
baselines and versioning*
**Deciders:** Tech Lead (chair), Analytics Lead (author), Product Architect,
Senior System Engineer, Senior QA Engineer, Senior Security Engineer
**Scope:** stands S1–S4, metric registry by owner family F1–F7 (gate and
informational labels), gate policy, canonical baselines and versioning,
event-driven re-baseline triggers, ADR-0019 §5 retraction render amendment

## Context

The owner directed (mnemos `818613c8`) that without benchmarks the memory's
work cannot be honestly compared or evaluated; the Tech Lead convened the
committee on that directive (queue question mnemos `abd037f0`). Existing
assets cover part of the ground: the W4 golden corpus (81 entries, 48 rated
queries, fully deterministic) and the ADR-0019 Phase C timing stand with its
corridor formula and benchmarks-only thresholds (mnemos `06b68002`).

The Analytics Lead proposed extending those assets rather than building from
scratch: four stands, ~30 metrics in the owner's seven families, anti-tasks
(self-comparison only, percentiles only, no wall-clock outside S2). The
committee accepted it with position amendments from every reviewing senior;
their additions and corrections are folded into the Decision below.

One conflict — §5's class-bearing retraction render (agent transparency)
versus Security's reason-neutrality requirement — was resolved by a chair
compromise, confirmed by both sides, and recorded below as an amendment.

## Decision

Adopt a four-stand benchmark framework: a versioned metric registry (~32
metrics, each labelled gate or informational), canonical JSON baselines,
event-driven re-baseline triggers, a determinism-first gate policy, and
thresholds derived from measurements only. The framework is a parallel
track and does not block the ADR-0019 main line.

### Stands

| Stand | Domain | Determinism | Run / budget | Gate role |
|---|---|---|---|---|
| S1 golden-extended | issuance quality and safety | full (BLAKE2b embedder, no wall-clock, no RNG) | local, < 30 s in `make verify` | gate: corridors + invariants |
| S2 timing | wall-clock latencies (the only such domain) | noisy; the noise band is itself measured | local smoke R=1 (informational); full nightly on a quiet isolated machine | nightly gate only |
| S3 long-lived session | coherence / usefulness | seeded simulation, logical time, 100–500 turns | nightly, < 5 min | gate: corridors |
| S4 availability probe | availability and correctness of the whole memory at any moment | idempotent read-only probes at logical barriers | isolated store copy (SQLite backup API), 5–10 min | gate while within budget |

Budgets — S1 < 30 s local, nightly pack S2+S3+S4 < 20 min, S3 100–500
turns — are **provisional-until-measured**. S4 probes are strictly
read-only; write waves run only on the copy; every run is audit-marked
(`actor=benchmark`, stand_version, run_id). A runtime canary is out of
scope (separate contour, config flag off by default, owner request only).

### Metric registry (v1, ~32 metrics by owner family)

| Family | Stand(s) | Metrics |
|---|---|---|
| F1 Latencies | S2 | swap-latency, embed-heal-lag, search-latency, assemble-latency, LTV, LTS — gating in nightly; informational as local smoke |
| F2 Accuracy / quality | S1, S3 | recall@k, precision@k; **invariant** injection-acceptance = 1.000; detector-quarantine-fp (conditional corridor, below); McNemar delta (deferred, see Statistics); sufficiency@task (gate) |
| F3 Token economy | S1, S3 | ccr-reduction-ratio, assemble-budget-utilization, context-growth-factor (stop-signal for new composition algorithms) — gates; marker-overhead — informational |
| F4 Composition cleanliness | S1 | stage-discard-profile, assembled-noise-share, retraction-pollution — gates; **invariant** duplicate-rate@k = 0 |
| F5 Session coherence | S3 | fact-retention@N,k, recall-drift-over-session, checkpoint-return-integrity, replace-hit / replace-regret rate — gates |
| F6 Availability | S4 | probe-pass-rate, memory-completeness (formula below), stuck-raw, swap-coverage, embed-staleness, mixed-window-hit-rate — carried by the S4 in-budget gate |
| F7 Extensibility | S1 re-runs | scale-sensitivity (slope over 1×, 2×), embedder-swap-delta — informational; **invariants** cross-principal-leak = 0, render-neutrality |

First baselines come from measurements: S1 — the extended golden corpus on
main; S2–S4 — the first stand runs. Instrumentation stays in the harness:
latency wrappers (no `src/` changes), stage profiles from assemble-stage
telemetry, swap/embed events from audit lines `swap_committed` /
`embed_upserted`.

### New invariants

- **quarantine-exclusion = 1.000** — a quarantined entry is excluded from
  issuance yet retrievable by ID; the exclusion is a correct outcome.
- **cross-principal-leak = 0** — no issuance crosses a principal boundary.
- **render-neutrality** — a detector class appearing in any issuance render
  is a defect.

### Corrected memory-completeness formula

`memory-completeness = |retrievable admissible| / |admissible|`, admissible
= committed ∧ not quarantined ∧ not retracted ∧ status-gate passes. The
proposal's draft denominator (all committed entries) would register a
working quarantine as incompleteness; the F6 gate applies only while the
paired invariant quarantine-exclusion = 1.000 holds.

### Conditional detector-quarantine-fp corridor

The corridor applies only while injection-acceptance = 1.000 and
quarantine-exclusion = 1.000: the FP rate must never be "improved" by
weakening detectors. Corpus danger labelling is independent of the
detectors and includes legitimate tech patterns (e.g. `system:` matching
`filesystem:`) so false positives are observable.

### Canonical baselines and versioning

Benchmark artefacts live under a root-level `benchmarks/` directory (owner
directive 2026-08-30: benchmarks sit at the project root with their own
nesting, and the directory is excluded from the wheel):
`benchmarks/corpus/` (the migrated and extended golden corpus),
`benchmarks/stands/{s1_quality,s2_timing,s3_session,s4_availability}/`,
`benchmarks/baselines/`, and `benchmarks/reports/`.
`benchmarks/baselines/<stand>.json` is the source of truth (schema
unchanged: `baseline_version`, `stand_version`, `corpus_fingerprint`,
`metrics{}`, `environment{}`); `BASELINE.md` becomes a generated
human-readable summary. The original placement
(`tests/golden/baselines/<stand>.json`) is superseded; S1 stays in the local
merge gate (`make verify`) unchanged. Metrics are added additively; a format
change bumps `baseline_version` with a migration link. Stand axes
(principals, projects, embedders) are stand parameters, not separate
branches.

### Re-baseline triggers (event-driven)

Corpus growth ×2; embedder change; processing-model change;
composition-algorithm or issuance-path change (a deployment affecting
context-growth-factor must spawn a new S3 wave). No calendar cadence:
releases that do not touch the data path do not re-baseline.

### Gate policy

1. Invariants (= 1.000 / = 0) are always blocking, never carried over a
   re-baseline.
2. Deterministic S1/S3 corridors block in the local pre-merge gate
   (`make verify` includes S1).
3. S2 never blocks locally (R=1 smoke is informational); full S2 runs
   nightly on a quiet machine — a noise band wider than the corridor
   yields status NOISE, de-escalated to report + ticket, not a block.
4. S4 blocks while it fits its gate budget.
5. The owner report is one page: traffic light per family F1–F7,
   percentiles with CIs and deltas to baseline, invariants as separate
   lines, trend arrows between waves.

### Corridors, thresholds, statistics

Corridor rule (from ADR-0019): `baseline − max(0.02; 95% CI)`. Percentiles
only, never means; thresholds derive from measurements only (mnemos
`06b68002`). McNemar on paired raw-vs-refined hits is deferred until the
rated corpus grows 48 → ~192 (underpowered at 48); interim — a sign test
on discordant pairs. Scale sensitivity is a slope over two points (1×, 2×).

### Amendment to ADR-0019 §5: reason-neutral retraction render

The retraction render in **all** issuance paths (REST get, CCR retrieve,
session injection) becomes `[retracted: <iso-ts>]` — reason-neutral, no
detector class. The reason class lives in the audit trail and in the
direct-access (get-by-id) metadata, visible only to an authorized operator
(with `auth_enabled=false` — the local operator of the single deployment).
Anti-enumeration applies to the probe contour, not the main get-by-id: the
explicit tombstone remains, and UUID enumeration is impractical.
Render-neutrality is an S1 invariant; Security confirmed the compromise
conditional on the auth-gated field, satisfied here.

## Phases

| Wave | Scope | Depends on |
|---|---|---|
| BF-1 | S1 extension (quarantine / retraction / mixed-window scenarios, interim sign-test jig) + canonical JSON baselines + BASELINE.md generator | Phase C baseline rewrite (ADR-0019) |
| BF-2 | S4 stand (isolated store copy, probes, audit marking) + S2 smoke in the local gate | BF-1 |
| BF-3 | S3 stand (seeded simulation, sufficiency@task, fact-retention) — before the first deployment of a new composition algorithm | BF-1 |
| BF-4 | Full S2 nightly + one-page owner report + rated-corpus growth 48 → 192 | BF-2 / BF-3 |

Implementation owner: Tech Lead; execution in waves via delegates under the
agent-review protocol. Priority: S4 before S3 (availability is the base
promise, and the raw-before-swap window is already live); S3 must be ready
before the first composition-algorithm deployment.

## Consequences

- **Positive:** every caught regression maps to a concrete decision;
  deterministic stands gate merges while noisy timing only informs — no
  "red by noise"; security invariants become first-class gates; versioned
  baselines survive corpus, embedder, and model growth; the reason-neutral
  render closes a CWE-209 oracle.
- **Negative / costs:** harness and stand maintenance; a nightly quiet
  machine; corridors stay cold until first measurements; the unit/golden
  split rule (transitions — unit, issuance — golden) enforced in review.
- **Deferred / accepted residuals:** McNemar until the corpus reaches ~192;
  runtime canary — a separate RFC with anti-enumeration requirements, on
  owner request only; federation / multi-principal axes activate with the
  second principal.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| S4 probes on the production store | New attack vector and false-positive source; an isolated copy via the SQLite backup API is used instead |
| Detector class in the issuance render | Oracle for iteratively bypassing detectors (CWE-209) |
| Blocking S2 on a local machine | "Red by noise"; full S2 runs nightly on a quiet machine only |
| Calendar re-baseline ("per release") | Releases do not always touch the data path; re-baselining binds to a product cause |
| Moving unit-level state transitions into golden tests | Double maintenance cost; rule: a transition is a unit test, observable issuance is golden |
| External benchmarks as a gate | Self-comparison over time only; external sets are informational references |
| Hand-picked thresholds | Owner directive mnemos `06b68002` — thresholds derive from measurements only |
| Means instead of percentiles / single S2 runs | Destroys comparability determinism |
| Scale sensitivity over three or more points | Expensive; a slope over two points (1×, 2×) answers the question |

## References

- ADR-0017 — roadmap; D5 metric-gate lineage this framework extends.
- ADR-0018 — entry invariant and provenance marker contract (unchanged here).
- ADR-0019 — corridor formula, Phases A–D, D5 metrics, timing stand; §5
  (retraction render) is amended by this ADR.
- Architectural Committee session of 2026-08-30 (protocol and contract) —
  archived with the committee records, team-local, not part of this
  repository; see the mnemos entries below.
- Owner directives: mnemos `818613c8` (benchmark framework; parallel
  non-blocking track), `06b68002` (benchmarks-only thresholds).
- Queue question closed by this ADR: mnemos `abd037f0`.
