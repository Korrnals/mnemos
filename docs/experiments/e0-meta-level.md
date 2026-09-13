# E0 — Pre-Registered Experiment: Memory Meta-Level (Lanes B/B0/A · Collapse Cascade C · Awareness D)

| Field | Value |
|---|---|
| Status | **PRE-REGISTERED — committed before the first run** |
| Date | 2026-09-13 |
| Commit window | **Open.** Per ADR-0025 the E0 window is open as of this commit: no benchmark run of any leg (A/B/B0, C0–C3, D) has occurred. This file precedes all runs (issue [#252](https://github.com/Korrnals/mnemos/issues/252), acceptance clause 1). |
| Owner | Analytics Lead (experiment design); G-gov ground-truth criterion co-owned with a Security observer for provenance (R1 open point 3, resolved here in §4.4) |
| Anti-HARKing clause | **No metric, threshold, stratum, or hypothesis may be added or altered after the first run.** Deviations must be logged in a dated amendment section (§8). Single-look analysis; no interim peeking (§6.6). |
| Language | English (canonical, ADR-style) |

**Sources (authoritative, cited inline as shown):**

1. [ADR-0025 — Memory Meta-Level Lanes](../project/adr/0025-memory-meta-level-lanes.md) — `[ADR-0025 §…]`
2. Committee protocol R1 (2026-09-08) `2026-09-08-memory-meta-level-lanes.md` and its contract — `[R1 §…]` — team-local (`~/.gcw/architectural-committee/`), not part of this repository
3. Committee protocol R2 (2026-09-09) `2026-09-09-memory-segments-collapse-cascade.md` — `[R2 §…]` — team-local
4. Committee protocol R3 (2026-09-09) `2026-09-09-awareness-presence-delta.md` — `[R3 §…]` — team-local
5. [ADR-0020 — Benchmark Framework](../project/adr/0020-benchmark-framework.md) — `[ADR-0020 §…]` (stands S1m/S2/S3, McNemar pairing policy, corridor rule `baseline − max(0.02; CI95)`, event-driven re-baseline triggers)
6. [ADR-0026 — Memory Value Observability](../project/adr/0026-memory-value-observability.md) — `[ADR-0026 §…]` (invariant / corridor / verdict taxonomy; the `docs/experiments/` pre-registration canon)
7. [ArchCom meta-level cycle report (2026-09-09)](../project/reports/2026-09-09-archcom-meta-level-cycle-report.md) and [dev-plan §4a](../project/dev-plan.md) — run ordering, slice gates, dependency status

**Interpretation boundary (binding, [ADR-0025 §Decision]):** a positive result validates the retrieval-side structure (lanes mechanics, collapse cascade, awareness composition) — not a "brain zeroth layer" and not "memory organized as a brain". The metaphor enters no schema, API, or metric wording.

---

## 0. Traceability — issue #252 scope item → document section

| Issue #252 scope item (checklist) | Section(s) |
|---|---|
| Legs A/B/B0 defined; primary metric governance-recall@5 on G-gov; falsifier "B does not beat B0 → theory NOT confirmed" | §1.1, §2.3, §5.1 |
| Hypotheses H1–H4 (latency, KV-cache, governance-recall, guardrail) with exact thresholds | §2.1, §2.2, §2.3, §2.4 |
| G-gov stratum (~96 queries, ~60–100 seeded records, 2 queries/record) + G-neg (~24) + distribution-matched corpus profile (58% checkpoints) | §3.1, §3.2, §3.3 |
| Equal-budget as primary mode; pin-cost curve 5/15/30% | §4.1, §4.2 |
| H5: retention-or-report = 1.000 invariant on seeded markers; cross-session answerability vs C0 (mandatory trivial leg) | §2.5, §5.3 |
| collapse-precision ≥ 0.95 (anti-hallucination) | §2.5, §5.3 |
| C1 → C2 → C3 sequential gates; 2×2 factorial B×C1 only after individual passes | §1.2, §1.4, §5.3 |
| Multi-session stratum (~80–100 scenarios, 160–200 paired probes) in E2 | §3.4 |
| G-poison adversarial stratum; ANY trace in C-outputs = FAIL | §3.5, §5.3 |
| C1 blocked until mnema-refine (#223) smoke-validation | §1.4 |
| D1 (primary): intrusion on type-2 −20pp, McNemar p<0.05, MDE 20pp | §2.6, §5.4 |
| D4 (co-equal primary): over-deferral ≤ 0.05 on 40 stale-claims | §2.9, §5.4 |
| D2: t_eligible p95 ≤ 60s; stale-action share ≤ 0.05 | §2.7 |
| D3: ≤ 300 tokens/call, ≤ 5% budget; KV-guardrail LCP ≥ 512 | §2.8 |
| Type-1 sanity floor ≥ 90% (else harness broken → NOISE) | §2.10, §5.4 |
| Strata: 80 conflict pairs (≥40 type-2) + 40 stale-claims + 200 canaries (false-drop ≤ 0.01) + adversarial-peer | §3.6, §5.4 |
| PR #224 replay as permanent scenario | §3.7 |
| Acceptance: file committed before the first benchmark run | Header (status + commit window) |
| Acceptance: all falsifiers pre-registered; no metric added after first run | §5, §6.4, §8 + anti-HARKing clause |

---

## 1. Experiment legs overview

### 1.1 Lanes legs (R1 → ADR-0025)

| Leg | Role | Treatment | Runs in |
|---|---|---|---|
| **A** | control | current `assemble_context` pipeline as-is (`LanesConfig.enabled=false`, code path identical) | E3 |
| **B** | treatment | lanes (rules/decisions/knowledge) + governance pinned to top + stable lane ordering (`lane → (score desc)` in the budget stage) | E3 |
| **B0** | trivial (anti-confounding) | type-boost of rules/decisions at recall — one ranking line, zero meta-level | E3 |

B0 exists because any pinning mechanics lifts drowned governance; without the trivial leg a positive result is confirmation bias [ADR-0025 §Alternatives]. B's only structural contribution B0 cannot give is the byte-stable pinned prefix (lane ordering) — that is the KV-cache argument (H2) [R1 §System Engineer].

The v0 experiment carries **no manifest lane at all**: metrics live on rules, decisions, knowledge, and checkpoints over existing records; pinning goes through the operator CLI only [ADR-0025 §Binding security controls].

### 1.2 Cascade legs (R2)

| Leg | Role | Treatment | Gate to next level |
|---|---|---|---|
| **C0** | trivial (mandatory) | mechanical compressor of a token budget equal to the C1 output budget — no LLM | establishes the H5 comparison baseline |
| **C1** | treatment, session level | session-scoped collapse via mnema-refine (`scope_key='s1:<session_id>@v<n>'`) | full H5 pass at session level |
| **C2** | treatment, project level | project-scoped collapse (incremental via meta cursors) | full H5 pass at project level |
| **C3** | treatment, cross-project | cross-project collapse — **first multi-principal trigger** | single-operator threat-model revision BEFORE any C3 run + operator decision per run [R2 §Security] (timing flagged §5.3) |

"C0 without the trivial leg any collapse looks like a victory — the lesson of B0" [R2 §Decision]. C0 also supplies the superseded pointers that D2 depends on (§1.4).

### 1.3 Awareness leg (R3)

| Leg | Role | Treatment | Gate |
|---|---|---|---|
| **D** | treatment (vs control = same scenarios, awareness off) | awareness v0: presence + delta + conflict hints as a hooks composition (`include_awareness` in `pre_llm_call`, presence section in `on_session_start`, MCP tool `mnemos_awareness`) — NOT a seventh assemble stage | **D1 AND D4, co-equal** (§5.4) |

### 1.4 Ordering, blockers, dependencies

- **No lanes/cascade/awareness run precedes this file** [dev-plan §4a rule 1]. This commit satisfies that precondition.
- **P0 collapse-bug batch #250** (quarantine intake, strip-by-default, ANY-member no-federate, input_set_hash idempotency) precedes any C-leg run — a first run over an unfixed pipeline would canonize an unprotected baseline [R2 §Decision]. Status: merged 2026-09-13 (PR #260) — recorded, not re-litigated.
- **C1 is blocked until mnema-refine (#223) smoke-validation**: `synthesize.py` is a deterministic stub (`llm_called=False`); C1 over the stub measures a 200-character concatenation, not collapse [R2 §Decision, §System Engineer]. Issue [#223](https://github.com/Korrnals/mnemos/issues/223).
- **D-leg prerequisites**: D0 blocker #251 (agent hardcode) resolved 2026-09-13 (PR #263); `origin=` provenance (P0) merged 2026-09-13 (PR #262). **D2 additionally depends on superseded pointers from the C-leg lineage** — a dependency, not new construction [R3 §Analytics].
- **Factorials**: 2×2 B×C1 and 2×2 B×D run only after both constituent legs pass individually; the synergy claim ("segments in synergy") requires a non-negative interaction term [R1 §criticism conflict 3; R2 §Analytics; dev-plan §4a slices 4+].
- **E2 corpus build triggers an event-driven re-baseline in the same PR** [ADR-0020 §Re-baseline triggers].

---

## 2. Hypotheses and operational definitions

Every metric is classified at registration per the ADR-0026 taxonomy: **invariant** (= 1.000 / = 0, blocking, never carried across a re-baseline), **corridor** (blocking for rollout), **verdict** (PASS / FAIL / NO-DATA, never blocking, NO-DATA renders explicitly).

### 2.1 H1 — latency (secondary confirmation; verdict) [ADR-0025 §H1]

| Item | Registration |
|---|---|
| Metric | p95 of `assemble_context` wall-clock latency under governance-heavy load (the G-gov query stream) |
| Operational definition | per-leg p95 over the same query stream — E0 opdef (flagged): "governance-heavy load" is operationalized as the G-gov query stream; the sources leave the load mix unspecified; ≥ 3 repeats on the S2 stand, quiet machine; noise band = max − min across repeats |
| Threshold | p95(B) ≤ **0.9 × p95(A)** |
| Noise semantics | if the noise band is wider than the corridor (the 10% improvement), the comparison renders **NOISE** — de-escalated to report + ticket, never a block [ADR-0020 §Gate policy] |
| Role | independent secondary confirmation; non-gating |

### 2.2 H2 — KV-cache (secondary confirmation; verdict) [ADR-0025 §H2]

| Item | Registration |
|---|---|
| Metric (a) | **LCP** — longest common token prefix between consecutive assemblies within a session (deterministic proxy, no wall-clock) |
| Threshold (a) | LCP ≥ **512 tokens** in **≥ 80%** of intra-session transitions |
| Metric (b) | **TTFT improvement** on a stable prefix, live measurement (vLLM / llama.cpp prefix cache), **≥ 30 paired runs** (same session replayed with and without lane ordering) |
| Threshold (b) | TTFT improvement ≥ **20%** |
| Role | independent secondary confirmation; non-gating. If only H2 holds, the surviving argument of leg B is KV stability alone [ADR-0025 §Decision rule] |

### 2.3 H3 — governance flooding (PRIMARY, lanes; verdict) [ADR-0025 §H3]

| Item | Registration |
|---|---|
| Metric | **governance-recall@5 on the G-gov stratum**: share of G-gov queries for which the target governance record (the seeded rule/decision the query was generated from) appears in the top-5 blocks of the assembled context, adjudicated blind (§4.3, §4.4) |
| Pairing | per-query McNemar pairs across legs on the identical corpus build (§6.1); n = 96 |
| Confirmatory condition | **B ≥ B0 + 10 pp AND B ≥ A + 10 pp (absolute)**, each comparison exact McNemar, two-sided, **p < 0.05** |
| MDE | +10 pp absolute [R1 §Analytics] |
| Power note | registered limitation, §6.5 |

### 2.4 H4 — no-harm guardrail (BLOCKING for lanes rollout) [ADR-0025 §H4]

| Item | Registration |
|---|---|
| Metric (a) | knowledge recall@5 on the **existing 192-query corpus** (S1m, baseline 0.8745) |
| Threshold (a) | **≥ 0.832** (= 0.8745 − CI95 0.043, the ADR-0020 corridor rule) — evaluated for leg B (A and B0 reported for symmetry) |
| Metric (b) | **governance-noise-rate on G-neg**: share of G-neg queries with ≥ 1 governance-class block in the top-5 (all such insertions are false by construction, §3.2) |
| Threshold (b) | **≤ 0.15**; falsifier **> 0.30** [ADR-0025 §H4]. Band resolution registered here: (0.15, 0.30] = erosion zone — verdict does not confirm H4-clean, escalation to ArchCom with pin-cost-curve data (§4.2); not an automatic fail |
| Consequence | H4 failure **blocks rollout regardless of H3** (§5.2) |

### 2.5 H5 — cascaded collapse (PRIMARY, cascade) [R2 §Analytics; issue #252]

Confirmatory = the **four conjunctive conditions** enumerated in §5.3 (retention-or-report invariant; retention floor; precision floor; C0-superiority); each failure mode is a falsifier.

| Item | Registration |
|---|---|
| Invariant | **retention-or-report = 1.000** on seeded critical-fact markers: every seeded critical fact at a collapse boundary is either retained in the collapsed output at its level or its loss is explicitly visible in the collapse-report. Any silent loss = FAIL (invariant, blocking) — "zero silent losses", the honest operationalization of "unconditional" [R2 §Decision] |
| Primary comparison | **cross-session-answerability@5** on the multi-session stratum (§3.4): C1 > C0, exact McNemar two-sided p < 0.05 on paired probes. The committee fixed no MDE for H5; E0 registers MDE reference **+20 pp** absolute (power note §6.5) — flagged as an E0-filled gap, not a committee number |
| Precision floor | **collapse-precision ≥ 0.95**: share of collapsed-output claims corroborated by ≥ 1 source record, blind-adjudicated. Adjudication scope registered here: 100% of claims touching seeded critical markers + a random 10% of remaining claims (minimum 50 per level, or all if fewer). **Any invented critical fact = falsifier = FAIL** (anti-hallucination) [R2 §Analytics] |
| Retention floor | **collapse-fact-retention@level ≥ baseline − max(0.02; CI95)** per level — S3 fact-retention semantics, the ADR-0019/0020 corridor rule [R2 §Analytics; ADR-0020 §Corridors] |
| Cost curve | **collapse-cost curve** at 2× / 5× / 10× compression targets, from traces — descriptive (verdict), informs the C2 budget choice, never gates [R2 §System Engineer] |
| C0 definition | mechanical compressor (deterministic truncation/concatenation, no LLM) with a **token budget equal to the C1 realized output budget** — the equal-budget rule of the cascade (§4.1) |

### 2.6 D1 — intrusion on intent conflicts (PRIMARY, awareness; verdict) [R3 §Analytics; §Decision]

| Item | Registration |
|---|---|
| Metric | intrusion rate on **type-2 (intent-conflict)** scenarios: share of paired scenarios where the acting agent intrudes into the peer's work zone — writes to the component/task another active session has claimed — as determined by the scenario's deterministic collision oracle |
| Pairing | per-scenario McNemar pairs (treatment arm vs control arm, same scenario); confirmatory set = all seeded type-2 pairs, **n ≥ 40** (target 80; §3.6) |
| Confirmatory condition | intrusion(D arm) ≤ intrusion(control) **− 20 pp absolute**, exact McNemar two-sided **p < 0.05** |
| MDE | 20 pp [issue #252; R3] |

### 2.7 D2 — freshness (corridor) [R3 §Analytics]

| Item | Registration |
|---|---|
| Metric (a) | **t_eligible**: elapsed time from a peer write event to that write being eligible for inclusion in a `project_delta` response to another agent in the same project (committed + inside the recency window + past trivial-reject). Operational definition fixed here; R3 names the metric and the bound |
| Threshold (a) | p95 ≤ **60 s** |
| Metric (b) | **stale-action share**: share of scripted agent actions taken against state already superseded by a peer write, detectable via superseded pointers + `origin=` provenance |
| Threshold (b) | ≤ **0.05** |
| Dependency | superseded pointers (C-leg lineage) + origin= (P0, merged) — dependency, not new construction [R3 §Analytics] |

### 2.8 D3 — awareness price (corridor) [R3 §Analytics]

| Item | Registration |
|---|---|
| Metric | awareness block cost per call; share of assembled budget |
| Thresholds | ≤ **300 tokens/call** AND ≤ **5%** of the context budget |
| KV guardrail | the H2 proxy must hold with awareness enabled: **LCP ≥ 512 tokens in ≥ 80%** of intra-session transitions (awareness block placed last, outside the pinned prefix) |

### 2.9 D4 — over-deferral (CO-EQUAL PRIMARY; verdict) [R3 §Decision; §Product]

| Item | Registration |
|---|---|
| Metric | **over-deferral rate on the 40 seeded stale-claims**: share of stale-claim scenarios where the agent defers or abstains from work that is actually safe to do (the presence/goal claim is stale or superseded; no real conflict) |
| Threshold | ≤ **0.05** — operational rule: ≤ 2 of 40 deferrals (point estimate); Clopper-Pearson upper 95% bound reported alongside (registered caveat: at 2/40 the upper bound = 0.169; the threshold is a product-risk call, not a power-derived one — §6.5) |
| Rationale | paralysis of agents = product failure, symmetric to intrusion; D-leg passes only if D1 AND D4 both hold |

### 2.10 Type-1 sanity floor (harness validity, not a hypothesis; corridor) [R3 §Analytics]

| Item | Registration |
|---|---|
| Metric | control-arm (awareness off) intrusion-free rate on **type-1 (file-visible conflict)** scenarios: the conflict is visible in the files themselves, so a competent agent reading current files already avoids it |
| Threshold | ≥ **90%** |
| Reading registered here | R3 states the floor as "else harness broken → NOISE". E0 fixes the reading: the floor is measured on the **control arm** — if even file-visible conflicts are not handled at ≥ 90% without awareness, the scenario scripting / harness is miscalibrated and **all D1 results of the batch render NOISE** (not a verdict on the leg). Repair ticket, re-run as a new registered run (§6.6) |

---

## 3. Strata and corpus

### 3.1 G-gov — governance stratum (~96 queries) [ADR-0025 §Mandatory design elements; R1 §Analytics]

- The governance class is **absent from the S1m corpus** — the stratum is invented for this experiment.
- Seeded records: rules and decisions generated per the `tech_patterns.py` pattern, **~60–100 records** permitted, **2 queries per record** for McNemar power.
- **Numeric tension in sources, resolved here (flagged, not silent):** the sources carry both "~96 queries" and "~60–100 records × 2 queries" (= 120–200). E0 locks: **analyzed stratum = 96 queries = 48 records × 2 queries**; the seeding budget up to 100 records forms a **replacement pool** for adjudication rejects (§4.4); replacements are logged, the analyzed denominator stays 96.
- Ground-truth protocol and applicability criterion: §4.4 — fixed in this document **before seeding**.

### 3.2 G-neg — negative control (~24 queries) [ADR-0025 §Mandatory design elements]

- Queries with **no applicable governance record by construction**: knowledge questions about the same projects whose governance records exist, so lexical pull toward governance exists but the correct answer is knowledge content.
- Measures the **price of pinning**: false governance insertions. Any governance-class block in the top-5 of a G-neg query is a false insertion by construction; governance-noise-rate = insertions / 24 (§2.4).

### 3.3 Corpus profile — distribution-matched (58% checkpoints) [ADR-0025 §Context; issue #252]

- The E2 corpus extension mirrors the live-store class distribution that produces the measured drowning (1457 entries: 839 checkpoints = 58%, 30 rules, 217 decisions): **checkpoints 58% ± 2 pp** (tolerance is an E0 fill — the sources fix the 58% point, not a band), rules/decisions per live ratio.
- The same distribution matching applies to the multi-session scenario stores (§3.4) so the drowning condition is reproduced, not sanitized.
- Corpus fingerprint (seed, version, class counts) committed with E2; any corpus or issuance-path change → event-driven re-baseline in the same PR [ADR-0020 §Re-baseline triggers].

### 3.4 Multi-session stratum (~80–100 scenarios, 160–200 paired probes) [R2 §Analytics; issue #252]

- Multi-session project histories with seeded events; each scenario runs in both arms (treatment vs control) — one **paired probe** per arm, i.e. 160–200 probe runs forming 80–100 McNemar pairs.
- Built in **E2** per the issue and dev-plan; probes target cross-session-answerability@5 (H5).
- Scenario stores distribution-matched (§3.3).

### 3.5 G-poison — adversarial stratum (cascade) [R2 §Security; issue #252]

- Seeded poisoned entries in collapse inputs, mirroring the four P0 bug classes: quarantined RAW rows entering clusters (F1), synthesis tag inheritance including `applyTo:**` (F2), secret-bearing non-first members (F2b), source-swap cache invalidation (F3).
- **Falsifier: ANY trace of poisoned content in ANY C-leg output** (synthesized record, collapse report, sampled claim) = FAIL of the C leg — blocking invariant, regardless of every other metric.

### 3.6 Awareness strata [R3 §Analytics, §Security; issue #252]

| Stratum | Size | Measures |
|---|---|---|
| Conflict pairs | **80 pairs**, **≥ 40 type-2** (intent-conflict), remainder type-1 (file-visible) | D1 confirmatory on the type-2 subset; type-1 carries the sanity floor (§2.10). Type-2 may be raised by **adding** pairs — the stratum total then exceeds 80, n reported — under three binding rules: the type-1 count never drops (zeroing type-1 would kill the sanity floor), the raise decision is taken **before any treatment-vs-control comparison is computed**, and the added pairs follow the same generator and seeding protocol as the base set |
| Stale-claims | **40** seeded stale/superseded presence or goal claims | D4 over-deferral |
| Noisy canaries | **200** noisy-but-legitimate facts at the write boundary | **false-drop ≤ 0.01**; quarantine-not-delete: drop-receipt without content, TTL 90 days (configurable), un-drop path. An LLM write-filter is prohibited on the write path (hot-path latency, false positives, contradicts retention-or-report = 1.000) [R3 §Decision] |
| Adversarial-peer | scripted hostile peer emitting spoofed presence / goal claims | security falsifier (§5.4): spoofed presence must not move agent behavior |

### 3.7 PR #224 replay — permanent scenario [R3 §Decision; §Analytics]

- The PR #224 incident (a release PR closed by a parallel session during the v4.0.0 release) becomes a **permanent controlled scenario** in every D-leg batch: parallel sessions over one project, release in flight, the peer's checkpoint ~3 minutes old inside the delta recency window — the delta surfaces it at the top where recall would drown it among 839 checkpoints.
- Reported separately in every batch, indefinitely. Evidence ladder: Tier C anecdote → **Tier A controlled scenario** (this experiment) → Tier B production telemetry (future).
- **Classification and pass criterion (E0):** diagnostic verdict (ADR-0026 taxonomy), non-gating, never enters a confirmatory set (§6.4). Pass = the peer's ~3-minute-old checkpoint appears in the delta top slot AND the acting agent does not intrude into the peer's release task. A FAIL is reported with every D verdict and is a standing ArchCom escalation item.

---

## 4. Modes and protocols

### 4.1 Equal-budget — primary mode [R1 §Analytics; ADR-0025 §Mandatory design elements]

- All cross-leg comparisons run at **identical assembled-context token budgets**.
- A **B-inflated run** (leg B given extra budget) may be executed **report-only, never a basis for conclusions**: "governance no longer drowns" must not be bought with extra tokens.
- Cascade equal-budget: C0's budget = C1's **realized output budget** (§2.5) — a mechanical compressor of the same token budget.

### 4.2 Pin-cost curve — 5 / 15 / 30% [R1 §Mandatory design elements]

- Pinned-governance prefix share of the budget swept at **5%, 15%, 30%**; measured at each point: governance-recall@5 (G-gov), knowledge-recall@5 (192-corpus), governance-noise-rate (G-neg).
- The curve is **descriptive** — the optimum is a design parameter, not a constant [R1 contract §5 risk table]; it feeds the R-phase configuration choice.
- The H4 floor (§2.4) binds at **every** point of the curve.
- Informational: the 10–15% manifest-lane cap (Security invariant, R1 §Security) constrains the future P1 manifest lane, not this experiment — v0 has no manifest lane.

### 4.3 Blind adjudication [R1 §Mandatory design elements; R1 contract §4a]

- The judge sees (query, candidate record/content) pairs only — **never** the leg an issuance came from, never run ids or leg-revealing provenance; adjudication artifacts are leg-stripped.
- Double annotation on a 20% subsample; Cohen's κ reported; **κ < 0.6 → adjudication recalibrated before scoring** (calibration floor per the ADR-0026 experiments canon).

### 4.4 Ground-truth protocol for G-gov (locked here, before seeding) [ADR-0025 §Mandatory design elements; R1 contract open point 3]

- **Generator**: `tech_patterns.py` pattern; 60–100 seeded rules/decisions; 48 analyzed records × 2 queries = 96 (§3.1); surplus records = replacement pool.
- **Applicability criterion (fixed before seeding — this is the E0 act):** a (query, record) pair is a gold pair iff all three hold:
  1. **Topical match** — the query asks about the norm or prior decision the record encodes.
  2. **Self-sufficiency** — the record alone answers the query (with standard terminology), without requiring another record.
  3. **Non-adjacency** — the record is not merely lexically co-occurrent (same project/artifact name) while answering a different question.
- Rejected pairs are replaced from the pool; replacements are logged; the analyzed denominator stays 96.
- **Seed hygiene** (Security observer): declarative prose only, no second-person imperatives, no `applyTo`, no severity tags; seeds must pass the injection screen; provenance from server columns only.
- Honest residual, accepted at registration: the ground truth is only as strong as this criterion [ADR-0025 §Consequences]; it is reported with every result.

### 4.5 Security invariants for all runs [ADR-0025 §Binding security controls; R3 §Security]

- No mint→pin path without operator approval (two-key rule); v0 pinning only via operator CLI over existing records.
- `origin=` provenance from server columns, never client tags/metadata.
- Awareness/delta blocks are **never pinnable**; presence derives from server-observed hook facts, not self-reported checkpoints; dedup keys include the issuer.
- A run that violates a security invariant is **void** — it is not a data point and is logged as such (§6.6).

---

## 5. Falsifiers and decision rules

### 5.1 Lanes decision rule [ADR-0025 §Decision rule; R1]

| Outcome | Condition (governance-recall@5, G-gov) | Consequence |
|---|---|---|
| **CONFIRMED** | B − B0 ≥ +10 pp AND B − A ≥ +10 pp, both exact McNemar p < 0.05, AND H4 holds | theory confirmed; R-phase (full lanes implementation) becomes an owner decision |
| **NOT CONFIRMED (falsifier zone)** | **B − B0 < 5 pp OR p(B vs B0) ≥ 0.05** | theory NOT confirmed; at most a cheap type-boost survives; **recommendation reverts to B0**; the meta-level is not built. Scope note (flagged): ADR-0025 writes the falsifier on the B-vs-B0 comparison only ("If B does not beat B0, difference < 5 pp or p ≥ 0.05"); E0 registers it verbatim on that scope. The initial draft of this document extended the falsifier to "either comparison p ≥ 0.05" — an unflagged E0 extension, corrected here before any run |
| **INDETERMINATE (residual zone)** | any other non-confirmation outcome: B − B0 ≥ 5 pp with p(B vs B0) < 0.05, while B − B0 < 10 pp, OR B − A < +10 pp, OR p(B vs A) ≥ 0.05 | not confirmation; no rollout. Re-opening requires a NEW pre-registered experiment; re-thresholding or re-zoneing the same data is prohibited (HARKing). Closes the outcome space with the falsifier row: every possible (delta, p) combination maps to exactly one row, so no zone can be picked after seeing data |
| **H3 met, H4 failed** | all four H3 conditions satisfied while any H4 guardrail breaks | rollout blocked (§5.2); not CONFIRMED; remediation via pin-cost curve / ArchCom — the lanes win cannot ship on a broken guardrail |
| Only H2 holds | H3 fails, H2 passes | the surviving argument of leg B is KV stability alone |
| Only H1 holds | H3 fails, H1 passes | latency argument alone — insufficient for the structure |

Multiple-comparison rule (registered): each H3 comparison is tested at exact McNemar two-sided α = 0.05; **no familywise correction is applied** — the ADR binds p < 0.05 per comparison, and choosing a correction after seeing data would be post hoc. Flagged E0 choice (per the §3.1 standard): the committee sources say only "McNemar p < 0.05"; the exact (binomial on discordant pairs), two-sided form and the no-correction rule are fixed here to foreclose post-hoc selection.

### 5.2 Guardrail block rule [ADR-0025 §H4]

- Leg B breaking the **0.832 floor** on the 192-query corpus blocks rollout **regardless of H3**.
- governance-noise-rate: ≤ 0.15 pass; (0.15, 0.30] erosion zone → ArchCom escalation with pin-cost data; **> 0.30 falsifier → H4 FAIL → rollout blocked**.

### 5.3 Cascade decision rules [R2; issue #252]

| Rule | Registration |
|---|---|
| H5 full pass (definition) | **conjunctive, all four**: (1) retention-or-report = 1.000 (invariant, zero silent losses); (2) collapse-fact-retention@level ≥ baseline − max(0.02; CI95); (3) collapse-precision ≥ 0.95 AND no invented critical fact; (4) cross-session-answerability@5: C_L > C0, exact McNemar p < 0.05. "Pass at level L" always means all four |
| Zero-silent-loss | condition (1): retention-or-report = 1.000 at every level; **any silent loss of a seeded critical marker = FAIL** (invariant) |
| Anti-hallucination | condition (3): collapse-precision ≥ 0.95; **any invented critical fact = FAIL** (falsifier) |
| C0-superiority | condition (4): cross-session-answerability@5: C_L > C0, exact McNemar p < 0.05 |
| Sequential gates | **C1 full pass (all four conditions) → C2 allowed; C2 full pass → C3 allowed.** Running a level without the prior level's full pass stacks attribution errors [R2 §Alternatives] |
| C3 additional gate | single-operator threat-model revision (multi-principal trigger, `manager.py:3459`) BEFORE any C3 run; each C3 run is an operator decision [R2 §Security]. **Flagged resolution (per the §3.1 standard):** R2's decision summary reads "threat model revised BEFORE C2/C3" — the stricter reading, revision precedes C2 as well — while R2's alternatives row ties the multi-principal trigger to C3 alone; E0 registers the C3-scoped reading. If the committee intends the stricter reading, that is an amendment, not an interpretation |
| Factorial | 2×2 B×C1 only after B and C1 pass individually; synergy claim requires a non-negative interaction term |
| G-poison | **ANY trace of poisoned content in ANY C-output = FAIL** of the C leg, blocking, regardless of other metrics |
| C1 blocker | no C1 run before mnema-refine #223 smoke-validation (§1.4) |

### 5.4 Awareness decision rules [R3; issue #252]

| Rule | Registration |
|---|---|
| Co-equal gates | **PASS = D1 AND D4.** The legs are not extended past D1–D4 — HARKing excluded [R3 §Decision] |
| Awareness-theater falsifier | if the D-arm improvement concentrates on **type-1 (file-visible)** scenarios while **type-2 (intent-conflict)** — the target stratum — shows no improvement, the mechanism delivers only what ordinary file context already delivers: **theater → FAIL**. Operationalization (E0, registered): theater is declared when the delta (D arm − control) on type-2 scenarios is **< +5 pp** while the delta on type-1 scenarios is **≥ +10 pp**. Related R3 signal: seen-but-ignored ≥ 30% → iterate placement, not data (a placement re-run requires an amendment entry, §8) |
| Over-deferral | D4 > 0.05 (> 2 of 40) → **FAIL** — agent paralysis is a product failure symmetric to intrusion |
| Adversarial-peer | spoofed presence/goal claims must **not** move agent behavior: no abstention from work based on unverified self-reported presence without operator coordination (the fixed frame is part of the treatment). **Any scripted deferral or decision change attributable to the spoofed block = security-contour FAIL regardless of D1/D4** [R3 §Security] |
| Harness sanity | type-1 control floor < 90% → **NOISE for the whole D batch** (§2.10); repair + new registered run — NOISE is not a leg verdict |
| Corridors | D2/D3 failure blocks production rollout of awareness v0 (fix-first), does not falsify D1/D4 |

---

## 6. Analysis plan

### 6.1 Pairing and tests (McNemar policy per ADR-0020 / ADR-0025)

- Pairing unit = the **identical probe under two legs**: same corpus build, same seeds, deterministic stands. G-gov query (n = 96) for H3 pairs; multi-session scenario (n = 80–100) for H5; type-2 scenario (n ≥ 40) for D1; C0/C1 paired probes for H5 at each level.
- All McNemar tests: **exact** (binomial on discordant pairs), **two-sided**, α = 0.05, per comparison (§5.1) — an E0 choice, flagged there; the sources say only "McNemar p < 0.05".
- D4: fixed-threshold count rule (≤ 2 of 40), exact binomial CI reported (§2.9).
- Non-paired thresholds (H1, H2a, D2, D3, canary false-drop, collapse-precision) evaluated against their registered bounds with intervals; no additional tests invented later.

### 6.2 Interval reporting

- Wilson score CI95 for every proportion; latency percentiles with CI95 across repeats; floors derived only via the corridor rule `baseline − max(0.02; CI95)` [ADR-0020 §Corridors]. Percentiles, never means [ADR-0020 §Alternatives].

### 6.3 Verdict taxonomy mapping [ADR-0026 §5]

| Class | Metrics in this experiment |
|---|---|
| invariant (blocking) | retention-or-report = 1.000; G-poison trace = 0; seed injection-acceptance = 1.000 (§4.4 hygiene); security invariants (§4.5) |
| corridor (blocking for rollout/production/level progression) | recall@5 ≥ 0.832; governance-noise ≤ 0.15; collapse-precision ≥ 0.95; collapse-fact-retention@level floor (baseline − max(0.02; CI95)); canary false-drop ≤ 0.01; D2 both bounds; D3 all bounds; type-1 sanity floor ≥ 0.90 |
| verdict (PASS / FAIL / NO-DATA, never blocking) | H3 comparisons; H5 answerability delta; D1; D4 count; H1; H2a/b; collapse-cost curve; #224-replay diagnostic (§3.7) |

**NO-DATA handling:** missing probes, voided runs (§4.5), NOISE (§2.1, §2.10) render **explicitly** — never as zero, never silently dropped [ADR-0026 §5]. A FAIL verdict is a reportable result; null and negative results are results.

### 6.4 Subgroup discipline

- Confirmatory and gating analyses are restricted to the registered strata, each with its taxonomy class (§6.3): G-gov (H3), G-neg (H4b), 192-corpus (H4a), multi-session (H5), the type-2 subset (D1), stale-claims (D4), canaries (corridor), G-poison (invariant). The #224-replay is diagnostic (§3.7): reported with every D batch, never enters a gate or a confirmatory set.
- **No unregistered subgroup analysis** (record type, query length, session, ordering, time) may support any decision; any such cut is exploratory, labeled, non-gating.
- All comparator legs are reported symmetrically; no leg is removed post hoc [ADR-0026 §8].

### 6.5 Power notes (registered limitations, stated before any run)

- **H3 / G-gov (n = 96 pairs):** 80% power (exact McNemar, α = 0.05) requires ≈ 90 discordant pairs resolving a 65/35 split — an ≈ +28 pp marginal effect at ~94% discordance. At the registered +10 pp MDE the power is **0.13–0.32** across plausible discordance (40–96%); a true +10 pp effect can therefore land in the falsifier zone (observed < 5 pp or p(B vs B0) ≥ 0.05). The design is powered for large effects consistent with the drowning premise (A near-floor vs B high); the limited power at the +10 pp threshold is a registered limitation stated before the run. Realized discordance is reported with the result.
- **H5 (n = 80–100 scenario pairs):** at ~50% discordance the +20 pp MDE reference yields power ≈ 0.70 at n = 80 and ≈ 0.78 at n = 100 — it does not reach 0.80 anywhere in the registered range. 80% power requires ≈ +25 pp at n = 80 and ≈ +22 pp at n = 100, or a larger stratum. The committee fixed no H5 MDE; the +20 pp reference is an E0 registration (§2.5, flagged).
- **D1 (n = 40–80 type-2 pairs):** at ~50% discordance, a +25 pp effect has only ≈ 62% power at n = 40; the 80%-power MDE at n = 40 is ≈ +30 pp. Even at the stratum target n = 80, the registered +20 pp MDE yields ≈ 0.70 power. Raising type-2 by adding pairs toward and beyond 80 (§3.6) is the power plan, not decoration: a confirmatory D1 claim at the registered MDE is supportable only at the top of the range.
- **D4 (n = 40):** point-estimate rule; at 2/40 the Clopper-Pearson upper bound = 0.169. The 0.05 threshold is a product-risk call registered by the committee, not a power-derived one.

### 6.6 Single-look, run ledger, re-baseline

- **Single-look:** the registered analysis runs once per leg after data collection completes; no interim look at leg comparisons; no sequential peeking.
- **Run ledger (append-only, below §8):** every run logged with run id, date, legs, corpus fingerprint, flag state; voided runs logged as void.
- **Re-baseline:** corpus × 2 growth, embedder or processing-model change, composition-algorithm or issuance-path change → event-driven re-baseline **in the same PR** [ADR-0020 §Re-baseline triggers]. Invariants never carry across a re-baseline [ADR-0026 §5].

---

## 7. Registered exploratory measures (reported, never gating) [R1 contract §4a; R3 §Analytics]

| Measure | Description | Source |
|---|---|---|
| Behavioral obedience of pinned rules | scripted agent solves ~20 tasks; share of obeyed pinned rules (governance that is recalled but not obeyed is not a benefit) | R1 |
| Longitudinal governance-decay | wallpaper-effect probe over time; confirmed decay zeroes the value of a future manifest lane | R1 (deferred probe) |
| checkpoint-share@k | share of checkpoints in issuance on non-bootstrap queries; enters the ADR-0020 metric registry, informational | R1 |
| Graft rate | share of agent-A decisions using a fact from another session's write — mechanical proxy of the "organism" claim | R3 |
| Counterfactual shadow-replay | replay of scenarios with the awareness block suppressed, post hoc | R3 |
| Abstention credit attribution | abstention → fact-ID provenance chain (also a binding security control; exploratory as a metric) | R3 |

---

## 8. Amendment log

**2026-09-13 — registered. No post-run amendments.**

**2026-09-13 (same day, later) — pre-run revision 1.** Independent review of the initial registration returned REQUEST-CHANGES (1×P1, 4×P2, 4×P3). The E0 window was and remains open — no run has occurred — so the revision is honest pre-run editing, not HARKing. Changes: §5.1 outcome space closed (INDETERMINATE extended to the full residual zone; falsifier re-scoped to the ADR's B-vs-B0 wording, with the initial draft's "either comparison" extension flagged; H3-met/H4-failed row added); §5.3 H5 pass enumerated as four conjunctive conditions, sequential gates tied to "full pass"; §6.3 corridors extended (canary false-drop, collapse-fact-retention floor, collapse-precision moved from verdict to corridor as it gates level progression); §6.4 confirmatory strata re-enumerated with classes, #224-replay made diagnostic (§3.7, with a pass criterion); §3.6 type-2 raise rule made additive with pre-comparison timing; §5.3 C3 threat-model timing flagged as an E0 resolution of an R2 internal ambiguity; §6.5 power figures corrected to conservative exact-binomial values; §5.4 theater falsifier operationalized (type-2 delta < +5 pp while type-1 delta ≥ +10 pp); E0 fills flagged (58% ±2 pp tolerance, exact two-sided McNemar, governance-heavy load opdef). The anti-HARKing clause applies unchanged from this revision onward.

Standing rules for this section: after the first run, any deviation — a changed metric, threshold, stratum, hypothesis, or analysis choice — requires a dated entry stating what changed, why, and which run prompted it. **A logged deviation is a report of what happened, never an authorization: the registered analysis stands and is reported as registered; any analysis under changed rules is reported alongside as exploratory (§6.4), never as the confirmatory result, and never replaces the registered verdict.** Run-ledger entries (§6.6) are appended below as runs occur.
