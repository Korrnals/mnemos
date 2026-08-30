# benchmarks — the ADR-0020 benchmark framework catalog

Benchmarks live at the repository root with their own nesting (owner
directive 2026-08-30, recorded as the ADR-0020 §4 amendment) and are
**excluded from the wheel and sdist** (`pyproject.toml`). Language:
English, matching the ADR canon this catalog implements.

## Layout

| Path | Contents |
| --- | --- |
| `corpus/` | The benchmark corpus — migrated byte-exact from `tests/golden` (entries, judged queries, planted FAKE secrets, rewrite scenario, deterministic BLAKE2b embedder) plus the BF-1 additions: the legitimate tech-pattern class (`tech_patterns.py`) and the detector-independent danger labelling (`danger_labels.py`). |
| `stands/s1_quality/` | Stand S1 (issuance quality and safety): `harness.py` reuses the W4 golden measurement logic, `scenarios.py` adds the ADR-0019 §5 quarantine/retraction scenarios, detector-quarantine-fp, the render-neutrality invariant and the interim McNemar sign-test jig; `run.py` is the single-command entry point. Stands S2–S4 land in waves BF-2+. |
| `baselines/` | Canonical `s1.json` (the source of truth: `baseline_version`, `stand_version`, `corpus_fingerprint`, `metrics`, `environment`) and the GENERATED `BASELINE.md` summary (`generate_md.py` — never hand-edit). |
| `reports/` | Per-run reports; gitignored, not committed. |

## Running

```bash
make bench-s1            # gate mode — corridors + invariants vs baselines/s1.json
make bench-s1-record     # re-record the baseline + regenerate BASELINE.md
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
3. S2 (timing) never blocks locally; S4 blocks while within budget —
   both are BF-2+ concerns.
4. detector-quarantine-fp is informational with a conditional corridor
   (only while injection-acceptance and quarantine-exclusion hold):
   the FP rate must never be "improved" by weakening detectors.

## Re-baseline triggers

Corpus growth ×2; embedder change; processing-model change;
composition-algorithm or issuance-path change. Releases that do not
touch the data path do not re-baseline. To re-record:
`make bench-s1-record` in the same PR as the change that caused it.
