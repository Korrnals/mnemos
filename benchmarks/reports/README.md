# benchmarks/reports

Per-run stand reports (JSON) land here and are NOT committed — the
canonical baselines live in `benchmarks/baselines/`.

A gate-mode run writes `s1-<timestamp>.json` (full measurement plus the
gate verdict) on every invocation; `--record` runs write nothing here
(their output IS the baseline).

Two GENERATED artefacts ARE committed (BF-4, both gitignore-exempt —
never hand-edit):

- `latest.md` — the one-page owner report (`make bench-report`,
  `benchmarks/report_page.py`): traffic light per family F1–F7 from
  all baselines, invariants as separate lines, deltas, trend arrows;
- `latest-prev.json` — the previous wave's headline snapshot the trend
  arrows read from (rewritten on every generation).
