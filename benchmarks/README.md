# benchmarks — the ADR-0020 benchmark framework catalog

Benchmarks live at the repository root with their own nesting (owner
directive 2026-08-30, recorded as the ADR-0020 §4 amendment) and are
**excluded from the wheel and sdist** (`pyproject.toml`). Language:
English, matching the ADR canon this catalog implements.

## Layout

| Path | Contents |
| --- | --- |
| `corpus/` | The benchmark corpus — migrated byte-exact from `tests/golden` (entries, judged queries, planted FAKE secrets, rewrite scenario, deterministic BLAKE2b embedder) plus the BF-1 additions: the legitimate tech-pattern class (`tech_patterns.py`) and the detector-independent danger labelling (`danger_labels.py`). |
| `stands/s1_quality/` | Stand S1 (issuance quality and safety): `harness.py` reuses the W4 golden measurement logic, `scenarios.py` adds the ADR-0019 §5 quarantine/retraction scenarios, detector-quarantine-fp, the render-neutrality invariant and the interim McNemar sign-test jig; `run.py` is the single-command entry point; `model_contour.py` is the S1m production-embedder contour (ADR-0021 NM-0 — see below). |
| `stands/s2_timing/` | Stand S2 (timing smoke, BF-2): wall-clock wrappers over add/search/assemble/refine on a fixed ~1e3-op load; R=1 informational, reports only — never baselines (the timing baseline belongs to the nightly quiet machine). |
| `stands/s3_session/` | Stand S3 (long-lived session, BF-3): `scenario.py` is the seeded session-as-data generator (fixed turns, logical time, unique fact markers), `run.py` replays it against one long-lived manager — fact-retention@N,k, recall-drift-over-session, checkpoint-return-integrity (invariant = 1.000), sufficiency@task, context-growth-factor, stage-discard-profile. |
| `stands/s4_availability/` | Stand S4 (availability, BF-2): fixture populations, SQLite-backup isolated store copy, strictly read-only probes with a content-checksum read-only invariant. |
| `baselines/` | Canonical `s1.json` (the source of truth: `baseline_version`, `stand_version`, `corpus_fingerprint`, `model_fingerprint`, `metrics`, `environment`) and the GENERATED `BASELINE.md` summary (`generate_md.py` — never hand-edit). |
| `reports/` | Per-run reports; gitignored, not committed. |

## Running

```bash
make bench-s1            # gate mode — corridors + invariants vs baselines/s1.json
make bench-s1-record     # re-record the baseline + regenerate BASELINE.md
make bench-s3            # S3 session stand — nightly class, NOT in make verify
make bench-s3-record     # write / re-record baselines/s3.json
make bench-s4            # S4 availability stand — nightly class, NOT in make verify
make bench-s2-smoke      # S2 timing smoke — informational, reports only
# or directly:
python benchmarks/stands/s1_quality/run.py [--record]
```

Determinism contract (ADR-0020): S1 uses the BLAKE2b lexical embedder
(no ONNX download), fixed corpus order, scoped patches instead of
`src/` edits, and measures **no wall-clock values** — the only
timestamps are run metadata (`created`), never a metric. The S1 budget
is < 30 s in the local merge gate (`make verify` runs `bench-s1`).

The golden pytest suite still exists (`pytest -m golden`) and imports
the corpus from here — a transition is a state machine, an issuance is
a golden measurement (ADR-0020 unit/golden split rule).

## S3 — the long-lived-session stand (ADR-0020 BF-3)

S3 answers the coherence question: does memory keep serving an agent
that has been talking to it for hundreds of turns? `scenario.py` builds
the whole session as DATA from a seed (`random.Random`; same seed →
byte-identical turn list; logical time only — no wall-clock value
enters any metric). One long-lived manager then replays the turns:
fact writes with unique markers, past-fact searches (exact phrase and
paraphrase), budgeted `assemble_context` (the `pre_llm_call` shape),
periodic `on_context_rewrite` events, and checkpoint → new session →
`recall_context` round-trips.

- **Metrics** (families F5 + the F3/F4 cuts ADR-0020 assigns to S3):
  `fact-retention@N,k` (histogram N ∈ {10, 50, 100, 200}, k = 5),
  `recall-drift-over-session` (the same early-fact sample probed at ~1/3
  and at the end), `checkpoint-return-integrity` (binary invariant =
  1.000 — after every round-trip every fact so far is re-probed),
  `sufficiency@task` (a task's required facts must land in the
  ASSEMBLED block, not merely in search), `context-growth-factor`
  (fixed budget + saturating anchor query at ~10% vs the final turn —
  the F3 stop-signal for composition regressions) and the
  `stage-discard-profile` (informational in baseline v1).
- **Gate**: the checkpoint invariant blocks always; retention / drift /
  sufficiency carry `baseline − max(0.02; 95% CI)` floors, growth a
  +0.02 ceiling; a scenario-fingerprint or seed/turns mismatch demands
  a same-PR `--record` (corridors only compare identical sessions).
- **Nightly class**: `make bench-s3` (default 240 turns, < 1 s
  measured) is NOT part of `make verify` — the pytest suite carries a
  compact determinism smoke instead (`tests/test_benchmarks_s3.py`).
- The stand's settings disable the context-rewrite per-minute quotas:
  they are wall-clock quotas and the run compresses a multi-hour
  logical session into seconds (rationale pinned by a source test).
- S3 is the ADR-0021 NM-2 prerequisite: the nano-refiner (NM-3) ships
  only while `fact-retention@N,k` does not regress on this stand.

## S1m — the production-embedder model contour (ADR-0021 NM-0)

S1 gates the retrieval PIPELINE on the deterministic BLAKE2b reference;
until NM-0 the PRODUCTION embedder (chromadb's all-MiniLM-L6-v2 ONNX
today) was measured by nothing — a silent weights substitution passed
the gate. S1m closes that hole as a separate section of the same
deterministic run:

- **What it measures**: the same judged corpus and golden queries run
  through the production embedder (a second fresh golden manager with
  the real provider installed — the identical ingest/ranking path),
  reporting recall@k, precision@k (k ∈ {5, 10}), MRR and nDCG@k in the
  `s1m` section of `s1.json`.
- **Self-comparison only**: the S1m corridor is `its own baseline -
  max(0.02; 95% CI)` — never against the BLAKE2b numbers (the reference
  measures retrieval mechanics, the production model semantic quality;
  ADR-0021 explicitly rejected replacing one with the other).
- **`model_fingerprint`** (top-level in `s1.json`): `{provider, model,
  weights_sha256, opset}` — the sha256 is over the REAL local ONNX
  artifact when the provider keeps one (chromadb), so an on-disk
  weights swap changes it even when every provider string stays
  identical; API/lazy providers record identifier-only (`null` hash).
  `opset` is read via the optional `onnx` checker and participates only
  when both sides read it (readability is an environment property).
- **Fail-loud**: a gate run whose live fingerprint differs from the
  recorded one is RED — "production embedder changed (old=X new=Y) —
  explicit re-baseline required (--record), same PR per ADR-0021". A
  baseline without a fingerprint (pre-NM-0 format) is the documented
  migration: the first `--record` pins it, nothing recorded can
  silently diverge yet.
- **Skip semantics**: the production embedder may be unbuildable in the
  run environment (no cached weights, no network, missing optional dep).
  Then `s1m` reports `{"status": "skipped", "reason": …}` — GREEN by
  default; RED only when `MNEMOS_BENCH_S1M_REQUIRED=1` (CI nightlies) is
  set, or when the baseline pins a fingerprint (the gate cannot verify
  the embedder it is supposed to gate).
- MRR / nDCG are recorded with corridors pending a second baseline;
  per-arch baseline rows and MTEB comparisons are informational only
  (ADR-0021 determinism rules).

## Gate policy (ADR-0020 §Gate policy, condensed)

1. **Invariants block always** (= 1.000 / = 0): injection-acceptance,
   quarantine-exclusion, duplicate-rate@k, render-neutrality,
   non-admissible surfacing, project purity. Never carried over a
   re-baseline.
2. **Deterministic corridors block locally**: recall/precision@k,
   rewrite pair, A9 delta — floor = `baseline − max(0.02; 95% CI)`.
   A corpus-fingerprint mismatch fails the gate: re-baselining is
   event-driven (corpus ×2, embedder/model change, issuance-path
   change), never calendar-driven.
3. S2 (timing) never blocks locally; S3/S4 are nightly-class — `make
   bench-s3` / `make bench-s4` gate on corridors + invariants but ride
   the nightly contour, not `make verify` (the suite carries only
   fast determinism smokes; full CI wiring is BF-4).
4. detector-quarantine-fp is informational with a conditional corridor
   (only while injection-acceptance and quarantine-exclusion hold):
   the FP rate must never be "improved" by weakening detectors.

## Re-baseline triggers

Corpus growth ×2; embedder change; processing-model change;
composition-algorithm or issuance-path change. Releases that do not
touch the data path do not re-baseline. To re-record:
`make bench-s1-record` in the same PR as the change that caused it.
An embedder/weights change additionally updates `model_fingerprint`
and the `s1m` section — the gate is RED until that same-PR re-record
lands (ADR-0021 NM-0 fail-loud).
