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

## Canonical wave reports + retention (round 3)

`generate_report.py` renders the CANONICAL wave report — markdown
analysis (Russian) + PNG charts (bar chart student/teacher/BM25,
epoch dynamics from `training/runs/*/metrics.jsonl`, recall@5 across
eval rounds, cosine distribution when measured) — into
`canonical/<timestamp>-report.md` (+ `*.png`). Sources: the freshest
`s1-*.json` / `nm1b-*.json` run reports here + `baselines/s1.json`;
F1–F7 traffic lights reuse the BF-4 evaluators. matplotlib lives in
`training/requirements.txt` (ADR-0021 anti-scope — never pyproject).

RETENTION POLICY (owner directive): after a canonical report is
written, the intermediate run `*.json` files in THIS directory are
deleted — only `canonical/` (plus breadcrumbs) remains:

```bash
python3 benchmarks/reports/generate_report.py [--label <name>]   # prune ON
python3 benchmarks/reports/generate_report.py --no-prune         # keep all
python3 benchmarks/reports/generate_report.py --keep-last 10     # more breadcrumbs
```

- `--keep-last N` (default 5) keeps the N newest run JSONs;
- `latest-prev.json` is NEVER pruned (it feeds the `latest.md` trend
  arrows, it is not a run report);
- `canonical/` is generated output (timestamped, gitignored).
