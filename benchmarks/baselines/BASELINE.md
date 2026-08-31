# S1 Quality Baseline — generated summary (ADR-0020 BF-1)

> GENERATED from `benchmarks/baselines/s1.json` — the JSON is the
> source of truth; regenerate with `make bench-s1-record` (or
> `python benchmarks/stands/s1_quality/run.py --record`). Do not edit.

- **baseline_version:** 1
- **stand_version:** s1-1
- **corpus_fingerprint:** `492fb39b2e98d92037438137a08419266946aacf7062ed7370b07f9d3fb41f59`
- **created:** 2026-08-31T20:41:12+00:00
- **model_fingerprint (production embedder):** `chromadb all-MiniLM-L6-v2 sha256:4f148ba8ae9c…`
  - full weights sha256: `4f148ba8ae9c2c7fbee4af2b132db8d06c6a6545b47fc83bbb98c3d22b8393e6`
- **environment:** python 3.12.3, deterministic_embedder=True (BLAKE2b lexical — pins the retrieval PIPELINE, not MiniLM)

## 1. Retrieval quality (judged golden queries)

| Metric | Value | 95% CI (half-width) |
| --- | ---: | ---: |
| precision@5 | 0.2979 | 0.0411 |
| precision@10 | 0.1489 | 0.0205 |
| recall@5 | 0.9858 | 0.0194 |
| recall@10 | 0.9858 | 0.0194 |
| queries (judged / probes / hybrid) | 47 / 1 / 48 | — |

## 2. Invariants (hard — any deviation is a defect, not a dip)

| Invariant | Value | Expect | Status |
| --- | ---: | ---: | --- |
| injection_acceptance | 1.0000 | 1.0 | OK |
| non_admissible_surfaced | 0 | 0 | OK |
| foreign_project_surfaced | 0 | 0 | OK |
| duplicate_occurrences_at_5 | 0 | 0 | OK |
| duplicate_occurrences_at_10 | 0 | 0 | OK |
| quarantine_exclusion | 1.0000 | 1.0 | OK |
| render_neutrality | 0 | 0 | OK |
| rewrite_redemption_leaks | 0 | 0 | OK |

## 3. Injection-acceptance detail

- planted appearances across all queries: **53**, leaks: **0** (search channel)
- assemble_context probes: leaks **[]**, all planted surfaced: **True**

## 4. A9 before/after (vector-leg predicate x over-fetch)

| Variant | recall@5 | recall@10 | precision@5 | precision@10 | hybrid | planted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| a9-on x4 (current) | 0.9858 | 0.9858 | 0.2979 | 0.1489 | 48 | 53 |
| a9-off x4 | 0.9769 | 0.9929 | 0.2936 | 0.1511 | 48 | 51 |
| a9-off x2 (pre-A9) | 0.9823 | 0.9929 | 0.2979 | 0.1511 | 47 | 35 |
| a9-on x2 | 0.9858 | 0.9858 | 0.2979 | 0.1489 | 48 | 52 |

Delta (current - pre-A9) recall@10: **-0.0071**

## 5. ADR-0018 rewrite pair

| Metric | Value |
| --- | ---: |
| replace-hit-rate | 0.9375 (15/16) |
| replace-regret-rate | 0.2500 (6/24) |
| control channel | 6/6 |

## 6. ADR-0019 S1-S3 scenarios

- **write-find** — PASS
- **supersede-refind** — PASS
- **refuse-render** — PASS
  - supersede: id unchanged True; served projection regenerated True; old projection gone from the lexical leg True
  - observation (informational): filter projection stale right after a content edit — False — false since #193 (the projection is reset in the same transaction as the content write)
  - refusal reasons are detector class codes: True (secret, prompt-injection)
  - retraction render format: True; titles withheld: True
  - quarantine exclusion from issuance: True; retrievable by id: True
  - CCR cached original retracted: True

## 7. detector-quarantine-fp (informational; conditional corridor)

- labelled entries: 89 (benign 81 / dangerous 8)
- text-level: TP 8, FP **8**, FN 0, TN 73 → fp_rate_over_benign = **0.0988**
- false-positive slugs: aurora-filesystem-mount-note, aurora-os-support-matrix, vaultui-ecosystem-plugins-note, vaultui-subsystem-focus-note, mnemos-filesystem-scan-note, mnemos-subsystem-cache-note, atlas-ecosystem-deps-note, atlas-filesystem-staging-note
- live ingest: 8/8 benign tech-pattern entries demoted to RAW by the N1 gate (observable FP cost)
- conditional corridor (ADR-0020): applies only while injection-acceptance = 1.000 and quarantine-exclusion = 1.000; the FP rate must never be 'improved' by weakening detectors

## 8. Render-neutrality sweep

- surfaces checked: **491** (assembled_context, search_issuance + retraction renders)
- violations: **0**

## 9. McNemar jig (interim)

- pair: `fts_only_vs_hybrid_rrf` over 47 judged queries
- hits: leg A 46 / leg B 45; discordant b=1, c=2; two-sided sign-test p = **1.0000**
- interim per ADR-0020 (48 judged queries are underpowered for McNemar); the same jig re-targets raw-vs-refined projections when deterministic refined projections exist

## 10. S1m — production-embedder model contour (ADR-0021 NM-0)

- the PRODUCTION embedder over the same judged corpus — self-comparison only, NEVER against the BLAKE2b reference (the reference measures retrieval mechanics, the model semantic quality)

| Metric | Value | 95% CI (half-width) |
| --- | ---: | ---: |
| precision@5 | 0.2936 | 0.0393 |
| precision@10 | 0.1489 | 0.0205 |
| recall@5 | 0.9787 | 0.0235 |
| recall@10 | 0.9858 | 0.0194 |
| mrr | 1.0000 | — |
| ndcg@5 | 0.9817 | — |
| ndcg@10 | 0.9852 | — |
| judged queries | 47 | — |
- embedder: `chromadb all-MiniLM-L6-v2 sha256:4f148ba8ae9c…`, dim 384, arch x86_64

## 11. Gate corridors (derived from THIS baseline)

| Metric | Corridor |
| --- | --- |
| precision_at_5 ≥ | +0.2568 (baseline 0.2979 - max(0.02; ci 0.0411)) |
| precision_at_10 ≥ | +0.1284 (baseline 0.1489 - max(0.02; ci 0.0205)) |
| recall_at_5 ≥ | +0.9658 (baseline 0.9858 - max(0.02; ci 0.0200)) |
| recall_at_10 ≥ | +0.9658 (baseline 0.9858 - max(0.02; ci 0.0200)) |
| replace-hit-rate ≥ | +0.9175 |
| replace-regret-rate ≤ | +0.2700 |
| A9 recall@10 delta ≥ | -0.0200 |
| invariants | exact (= 1.000 / = 0), never carried over a re-baseline |
| s1m precision_at_5 ≥ | +0.2543 (baseline 0.2936 - max(0.02; ci 0.0393)) |
| s1m precision_at_10 ≥ | +0.1284 (baseline 0.1489 - max(0.02; ci 0.0205)) |
| s1m recall_at_5 ≥ | +0.9552 (baseline 0.9787 - max(0.02; ci 0.0235)) |
| s1m recall_at_10 ≥ | +0.9658 (baseline 0.9858 - max(0.02; ci 0.0200)) |
| model_fingerprint | exact match vs this baseline — a mismatch is RED (re-baseline `--record`, same PR, per ADR-0021) |

## 12. Reproducing

```bash
make bench-s1            # gate mode (corridors + invariants vs this baseline)
make bench-s1-record     # re-record this baseline + regenerate this file
```

Runtime is wall-clock-bounded only by the harness itself (ADR-0020
budget: S1 < 30 s local); every measured value is deterministic —
no clock, no RNG, no network. Pre-BF-1 baseline history (the W4
record) lives in git history at `tests/golden/BASELINE.md`.
