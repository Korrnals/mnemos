# D5 Golden-Set Baseline — First Recording (W4)

**ADR:** 0017 D5 (metric gates), 0018 (rewrite metric pair)
**Issue:** #125 Phase 1, Wave 4 · **Recorded at:** branch `feat/125-w4-golden-d5`
**Status:** NON-NORMATIVE — corridors are recommendations until the owner
ratifies them (see *Ratification items*).

This is the pre-change baseline ADR-0017 D5 requires: every later phase
(graph expansion, confidence decay, storage compression, retrieval
intelligence) exits only with *no regression against this record* plus a
measured improvement on its own target axis.

---

## 1. Harness design (decisions, not accidents)

| Decision | Choice | Why |
|---|---|---|
| Corpus | 81 entries, 4 projects (`aurora-api`, `vault-ui`, `mnemos-core`, `atlas-pipeline`), code/prose/logs; 70 `published`, 6 `processed`, 5 `raw` | Small enough to judge honestly, large enough to force ranking decisions; statuses exercise the ADR-0018 entry-invariant gate |
| Judgments | 48 queries (47 judged + 1 status-gate probe), 1–4 expected slugs each | "Honest and small": an entry is expected only if it genuinely answers the query |
| Embeddings | **Deterministic lexical feature-hashing** (BLAKE2b unigram+bigram buckets, 256-dim, L2-normalised) — `deterministic_embedder.py` | The production ONNX MiniLM needs an ~80 MB download (unavailable offline, not bit-reproducible across model versions). The baseline therefore measures the **retrieval pipeline** (RRF fusion, A9 predicate, status gating), NOT MiniLM quality. A MiniLM-pinned recording is a separate exercise. |
| Vector leg | Real `VectorStore` + real RRF fusion, deterministic embedder swapped in before ingest | Exercises both legs end-to-end (48/48 queries ran hybrid) |
| A9 hook | Scoped patches: `VectorStore.search` project kwarg dropped (pre-A9 store behaviour) × `VECTOR_LEG_OVERFETCH_FACTOR` 4→2 | Zero edits to `src/`; the manager-side resolve guard stays active in the "before" leg (leak-free by construction — we measure recall, not re-introduce the leak) |
| Injection channel | Real `scan_issuance_item` per result (the `mnemos_search` / REST `/search` semantics) + full `assemble_context` probe per planted entry | Measures the actual issuance paths, not a reimplementation |
| Rewrite pair | 24 real `context_rewrite` events (3 supersedes chains), 16 follow-up `retrieve_content` (10 snippet-mode "detail", 6 full-mode "whole"), 6 never-rewritten CCR controls | Both rates flow through the real idempotency / pipeline / marker / scan machinery |

Metric conventions: `precision@k = |top-k ∩ expected| / k` (strict
denominator), `recall@k = |top-k ∩ expected| / |expected|`, macro-averaged
over judged queries. `injection-acceptance = 1 − leak-rate` over every
planted-secret entry that surfaced in a result set.

### First-run findings (harness self-validation)

- The injection metric **caught a fixture bug on first run**: the planted
  GitHub token was 32 alnum after `ghp_` (catalogue needs 36), so the
  detector rightly ignored it — surfaced as 6 leaks. All planted literals
  now provably match their detector pattern.
- A scenario block below `ccr.min_size_chars` minted no marker and
  silently degraded to a mechanism miss; the harness now **fails loudly**
  on any uncached rewrite-event block (fixture-integrity guard in
  `measure.py`).

## 2. Measured baseline (deterministic; 3 consecutive runs identical)

### 2.1 Retrieval quality — `MemoryManager.search`, predicate ON, ×4 (current code)

| Metric | Value |
|---|---|
| precision@5 | **0.2979** |
| precision@10 | **0.1489** |
| recall@5 | **0.9858** |
| recall@10 | **0.9858** |
| queries ran hybrid (vector leg contributed) | 48 / 48 |
| raw / non-admissible entries surfaced | 0 (hard invariant, held) |
| out-of-project rows in scoped searches | 0 (A9 purity, held) |

Reading: recall is high because the corpus is small and the queries
distinctive — the honest caveat is that this recall corridor will
tighten when the corpus grows; the number is a floor to defend, not a
quality plateau. Precision is dominated by the strict `hits/k`
denominator (a top-10 on a ~20-entry project carries ~1.5 expected
answers); it is the axis W5+ improvements should move.

### 2.2 Injection-acceptance

| Channel | Result |
|---|---|
| search issuance (`scan_issuance_item`), 63 planted-entry appearances across all queries | **0 leaks → acceptance = 1.000** |
| `assemble_context` D1 path, 8/8 planted entries surfaced in assembled blocks | **0 leaks → acceptance = 1.000** |
| rewrite full-redemption channel (planted originals) | **0 leaks** |

Injection-acceptance is a **hard invariant, not a corridor**: any leak is
a security defect (the suite fails red), never a metric dip.

### 2.3 A9 before/after (the comparison ArchCom deferred to W4)

| Variant | recall@5 | recall@10 | precision@5 | precision@10 | hybrid | planted surfacing |
|---|---|---|---|---|---|---|
| predicate ON, ×4 (current) | 0.9858 | 0.9858 | 0.2979 | 0.1489 | 48/48 | 63 |
| predicate OFF, ×4 | 0.9876 | 0.9876 | 0.2979 | 0.1489 | 48/48 | 46 |
| predicate OFF, ×2 (**pre-A9 emulation**) | 0.9823 | 0.9929 | 0.2979 | 0.1511 | 44/48 | 35 |
| predicate ON, ×2 | 0.9858 | 0.9858 | 0.2979 | 0.1489 | 48/48 | 63 |

**Delta (current − pre-A9): recall@5 +0.0035, recall@10 −0.0071,
precision@5 ±0, precision@10 −0.0021.**

Verdict: **no material recall regression from the A9 predicate + ×4
over-fetch** — deltas are ≤0.7pp and mixed-sign. The ×4 constant is
validated at this corpus scale: ×2 with the predicate ON measures
*identical* recall@5/@10 (the depth headroom is not load-bearing here).
Two honest caveats:

1. **Crowding stress is weak at 81 entries.** The clearest pre-A9 effect
   in the data is not on judged recall but on *planted surfacing*
   (63 → 35): unscoped global candidates displace in-project rows from
   top-10 — they just happened to displace non-expected entries in these
   judgments. A future re-record should add cross-project
   near-duplicate-content entries so global crowding threatens *expected*
   rows; then the constant's depth claim gets a real stress test.
2. Pre-A9 also lost the vector leg entirely on 4/48 queries (44 hybrid)
   — the depth consumed by foreign rows.

### 2.4 ADR-0018 rewrite pair

| Metric | Value | Numerator / denominator |
|---|---|---|
| replace-hit-rate | **0.9375** (15/16) | follow-up retrieves that redeemed a rewrite-minted marker / all scripted follow-up retrieves |
| replace-regret-rate | **0.2500** (6/24) | rewrite events whose original was later redeemed in full / all rewrite events |
| control channel | 6/6 | never-rewritten CCR entries redeemed (no false attribution, channel healthy) |

The single hit miss is the **B5 verdict-gated snippet refusal**: a
detail follow-up on a secret-bearing original is refused in snippet mode
by design (full-original retrieve stays available, redacted). This is
exactly why the committee set the corridor below 1.0 — 100% would
require un-evictable caches or weakening the security gate.

Regret is scripted ground truth (6 of 24 rewrites are reactivated tasks
needing the whole block back). The rate measures the *harness policy's*
premature-replacement fraction flowing through the real mechanism; a
mechanism failure converts a regret into a hit-miss instead — the pair
is measured together for that reason.

## 3. Recommended corridors (non-normative until ratified)

| Metric | Corridor | Basis |
|---|---|---|
| precision@5 | ≥ 0.28 | baseline − 0.02 tripwire |
| precision@10 | ≥ 0.13 | baseline − 0.02 tripwire |
| recall@5, recall@10 | ≥ 0.97 | baseline − 0.02 tripwire (re-derive on corpus growth) |
| injection-acceptance | **= 1.00 (invariant)** | any leak is a defect, not a dip |
| replace-hit-rate | ≥ 0.90 | below baseline 0.9375 with room for the designed B5 refusal; NOT 1.0 (committee) |
| replace-regret-rate | ≤ 0.30 | bounds premature replacement; NOT 0 — regret is a policy property to bound, not erase |
| A9 recall delta (ON vs pre-A9) | ≥ −0.02 | predicate must not cost material in-project recall |

Every intentional retrieval change that lands below a corridor must
re-record this file in the same PR — floors are tripwires, not targets.

## 4. Ratification items (owner decisions pending)

1. **Precision denominator**: strict `hits/k` vs `hits/min(k, |results|)`.
   Strict is recorded here (a system that returns k results should be
   judged on k slots).
2. **Rewrite-pair denominators**: hit over follow-up retrieves, regret
   over rewrite events, with scripted `detail`/`whole` ground truth as
   the operational definition of "premature replacement".
3. **Corridor values** in §3 as the D5 exit gates for Phases 2–4.
4. **Embedder scope statement**: this baseline pins the pipeline, not
   MiniLM; whether a second, machine-pinned MiniLM baseline is required
   before Phase 2 (graph expansion changes ranking) is an owner call.
5. **A9 corpus-scale caveat** (§2.3 item 1): schedule a corpus-growth
   re-record or accept the constant as validated-at-scale.

## 5. Reproducing

```bash
pytest tests/golden/ -v          # the marked suite (also in the default run)
pytest -m golden                 # by marker
pytest -m "not golden"           # everything else
```

Runtime ~1.5 s; fully deterministic (no network, no wall clock, no RNG —
three consecutive runs produced byte-identical snapshots; enforced by
`test_golden_determinism`).
