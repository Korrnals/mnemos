# benchmarks/reports

Per-run stand reports (JSON) land here and are NOT committed — the
canonical baselines live in `benchmarks/baselines/`.

A gate-mode run writes `s1-<timestamp>.json` (full measurement plus the
gate verdict) on every invocation; `--record` runs write nothing here
(their output IS the baseline).
