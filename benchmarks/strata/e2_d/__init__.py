"""E2 awareness-leg strata — D-strata corpus wave (E0 §3.6, §3.7).

Corpus PREPARATION ONLY: no experiment legs, no assemble calls, no
arms, no metrics. The E3 runner (future wave) materializes these
scenarios into real stores and executes them against the REAL
awareness engine (``mnemos.awareness``); until then nothing measures
them except the structural tests that pin them. Import the submodules
directly (``scenarios``, ``conflict_pairs``, ``stale_claims``,
``canaries``, ``adversarial``, ``replay224``, ``oracle``,
``ground_truth``, ``materialize``, ``profile``) — deliberately no
eager re-exports here.

E0 section → artifact map (all counts test-pinned):

* §3.6 conflict pairs — ``conflict_pairs.CONFLICT_PAIRS``: 80 pairs =
  40 type-2 (intent-conflict, the D1 confirmatory set, n at the
  registered >= 40 floor) + 40 type-1 (file-visible, the §2.10 sanity
  floor); type-1 is the type-2 world plus one evidence row
  (ceteris paribus).
* §3.6 stale-claims — ``stale_claims.STALE_CLAIMS``: 40 seeded
  stale/superseded claims = 20 window_expired (5400 s, outside the
  delta clamp) + 20 superseded_goal (latest-wins checkpoint sequence);
  D4 over-deferral over exactly this list.
* §3.6 noisy canaries — ``canaries.CANARY_FACTS``: 200 noisy-but-
  legitimate write-boundary facts (140 generic-add + 60 checkpoint
  channel); false-drop = 0 at the current boundary, pinned by design
  and by test — the <= 0.01 corridor's reproduction set.
* §3.6 adversarial-peer — ``adversarial.ADVERSARIAL_SCENARIO``: the
  spoofed-presence security falsifier (§5.4) — four hostile moves
  (spoofed presence, applyTo smuggling, forged #251 stamps,
  cross-project spoof), execution deferred to E3.
* §3.7 PR #224 replay — ``replay224.REPLAY_224``: the permanent
  diagnostic scenario — peer checkpoint 180 s old, top-slot reachable
  through the real engine, drowning mass at the live 58% ± 2 pp
  checkpoint share (57.9% at scenario scale).
* §2.6 oracle — ``oracle``: the deterministic collision oracle (exact
  target/zone intersection) + the per-scenario D1/D4 binary outcomes.
* §2.6/§2.9/§3.7 keys — ``ground_truth``: expected-outcome answer key
  kept separate from the agent-facing views (blindness discipline,
  e2_gov worksheet/answer-key pattern).
* §3.3 profile — ``profile.py`` + ``profile.json``: fingerprint (sha256
  over 8 stratum modules), counts, engine binding (hint threshold = 2,
  registered E0 §8 rev. 2), fixed parameters reported to the
  orchestrator (NOT amendments to E0).

Isolation contract (E0 §1.4, ADR-0020): the S1 stand's
``corpus_fingerprint`` hashes only ``benchmarks/corpus`` modules, and
the e2_gov profile hashes only its own four modules — this package is
invisible to both (asserted by test), so ``make bench-s1`` keeps
measuring exactly what it measured before this wave.
"""
