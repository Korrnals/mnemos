"""S3 stand smoke tests (ADR-0020 BF-3, epic #169).

Guards the stand itself:

* the canonical baseline JSON is schema-valid and its fingerprint
  matches the scenario generator as it exists on disk;
* a compact (60-turn) session runs, produces every metric family, and is
  byte-deterministic across two fresh passes (serialized baseline minus
  the ``created`` metadata is equal);
* the metric math is correct on a synthetic easy scenario: all-exact
  probes over short unique facts → fact-retention = 1.0, the
  checkpoint invariant = 1.000, and no drift degradation on a short
  distance;
* the gate RED-detects each defect class it exists to catch: a broken
  checkpoint invariant, a retention corridor breach, an age-bucket
  starvation, a context-growth ceiling breach, and a scenario-axes
  substitution (corridors must only compare identical sessions);
* the Makefile wiring keeps S3 nightly-class: ``bench-s3`` exists and
  ``verify`` does NOT include it (ADR-0020 gate policy).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.stands.s3_session import run as s3_run
from benchmarks.stands.s3_session.scenario import (
    Scenario,
    ScenarioConfig,
    WriteFact,
    build_scenario,
)

ROOT = Path(s3_run.__file__).resolve().parents[3]

_BASELINE_KEYS = {
    "baseline_version",
    "stand_version",
    "corpus_fingerprint",
    "model_fingerprint",
    "created",
    "metrics",
    "environment",
}

#: A compact but complete session for the suite smoke (fast, < 2 s).
_SMOKE = ScenarioConfig(turns=60, ages=(5, 10, 30))
#: The easy scenario: exact-phrase probes only, every fact short and
#: unique → the deterministic pipeline must retrieve every one.
_EASY = ScenarioConfig(turns=60, ages=(5, 10, 30), paraphrase_share=0.0, drift_sample=8)


def _load_baseline() -> dict:
    assert s3_run.BASELINE_PATH.exists(), (
        "benchmarks/baselines/s3.json is missing — run `make bench-s3-record`"
    )
    return json.loads(s3_run.BASELINE_PATH.read_text())


def _metrics_copy() -> dict:
    return json.loads(json.dumps(_load_baseline()["metrics"]))


def _recorded() -> dict:
    return _load_baseline()


# ── baseline artefact ─────────────────────────────────────────────────────────


def test_baseline_json_schema_and_fingerprint() -> None:
    baseline = _load_baseline()
    assert set(baseline) >= _BASELINE_KEYS
    assert baseline["baseline_version"] == 1
    assert baseline["stand_version"] == s3_run.STAND_VERSION
    fingerprint = baseline["corpus_fingerprint"]
    assert isinstance(fingerprint, str) and len(fingerprint) == 64
    int(fingerprint, 16)  # sha256 hex
    assert baseline["environment"]["deterministic_embedder"] is True
    assert baseline["metrics"], "baseline carries no metrics"
    # the fingerprint pins the scenario generator as it exists on disk —
    # a scenario edit without a re-record fails here (and in the gate)
    assert fingerprint == s3_run.corpus_fingerprint()


def test_baseline_records_every_metric_family() -> None:
    metrics = _load_baseline()["metrics"]
    for family in (
        "scenario",
        "fact_retention",
        "recall_drift",
        "checkpoint_return_integrity",
        "sufficiency_at_task",
        "context_growth",
        "stage_discard_profile",
        "rewrite_events",
    ):
        assert family in metrics, f"baseline misses the {family} family"
    scenario = metrics["scenario"]
    assert 100 <= scenario["turns"] <= 500, "the recorded nightly run is 100-500 turns (ADR-0020)"
    by_age = metrics["fact_retention"]["by_age"]
    for age in scenario["ages"]:
        bucket = by_age[str(age)]
        assert bucket["probes"] > 0, f"recorded run starved the age-{age} bucket"
        assert bucket["rate"] is not None


# ── smoke run + determinism ───────────────────────────────────────────────────


def test_smoke_run_produces_all_metric_families() -> None:
    metrics = s3_run.run_measurement(_SMOKE)
    assert metrics["scenario"]["turns"] == _SMOKE.turns
    assert metrics["fact_retention"]["overall"]["probes"] > 0
    assert metrics["recall_drift"]["sample"] > 0
    inv = metrics["checkpoint_return_integrity"]
    assert inv["checkpoints"] == _SMOKE.checkpoints
    assert inv["ok"] and inv["value"] == 1.0
    assert metrics["sufficiency_at_task"]["tasks"] > 0
    assert metrics["context_growth"]["factor"] is not None
    stage = metrics["stage_discard_profile"]
    assert stage["assembles"] > 0
    assert stage["recall_candidates_total"] > 0


def test_run_is_deterministic_across_fresh_passes() -> None:
    first = s3_run.run_measurement(_SMOKE)
    second = s3_run.run_measurement(_SMOKE)
    assert first == second, (
        f"S3 measurement is not deterministic across fresh runs:\n{first!r}\n{second!r}"
    )
    # byte-level: the serialized baseline differs ONLY in ``created``
    # (run metadata — never a metric)
    b1, b2 = s3_run.build_baseline(first), s3_run.build_baseline(second)
    b1.pop("created"), b2.pop("created")
    assert json.dumps(b1, sort_keys=True) == json.dumps(b2, sort_keys=True)


def test_gate_green_when_current_equals_recorded() -> None:
    """The gate logic passes when current == recorded (hermetic level)."""
    same = _metrics_copy()
    assert s3_run.gate_check(same, _recorded())["pass"]


# ── metric correctness on the synthetic easy scenario ────────────────────────


def test_easy_scenario_retention_is_one() -> None:
    """All facts short and unique, exact-phrase probes → retention 1.0.

    The exact phrase occurs verbatim in exactly one fact, so the FTS leg
    ranks it first — anything below 1.0 is a pipeline defect, not noise.
    """
    metrics = s3_run.run_measurement(_EASY)
    overall = metrics["fact_retention"]["overall"]
    assert overall["probes"] >= 10, "easy scenario starved of probes"
    assert overall["rate"] == 1.0, (
        f"exact-phrase retention {overall['rate']} < 1.0 on unique facts — "
        "the deterministic pipeline lost a uniquely-phrased fact"
    )


def test_easy_scenario_no_short_distance_drift() -> None:
    """Early vs late probes of the same facts: no degradation expected."""
    metrics = s3_run.run_measurement(_EASY)
    drift = metrics["recall_drift"]
    assert drift["sample"] >= 5
    assert drift["early"]["rate"] == 1.0
    assert drift["late"]["rate"] == 1.0
    assert drift["delta"] == 0.0


def test_easy_scenario_checkpoint_integrity_holds() -> None:
    metrics = s3_run.run_measurement(_EASY)
    inv = metrics["checkpoint_return_integrity"]
    assert inv["ok"] is True
    assert inv["value"] == 1.0
    assert inv["misses"] == 0
    assert inv["facts_probed"] > 0


def test_scenario_is_pure_data_and_marker_complete() -> None:
    """Every fact carries a unique marker and a matching write op."""
    scenario: Scenario = build_scenario(ScenarioConfig(turns=60, ages=(5, 10)))
    markers = [f.marker for f in scenario.facts]
    assert len(markers) == len(set(markers)), "fact markers must be unique"
    written = {op.fact.marker for op in scenario.ops if isinstance(op, WriteFact)}
    assert written == set(markers), (
        "every fact must have exactly one write op — a registry-only fact "
        "silently pollutes every downstream metric pool"
    )
    for fact in scenario.facts:
        assert fact.marker in fact.content
        assert fact.exact_query in fact.content, "the exact query must occur verbatim"


def test_same_seed_same_scenario_different_seed_differs() -> None:
    a = build_scenario(ScenarioConfig(turns=60, ages=(5, 10), seed=7))
    b = build_scenario(ScenarioConfig(turns=60, ages=(5, 10), seed=7))
    c = build_scenario(ScenarioConfig(turns=60, ages=(5, 10), seed=8))
    assert a == b
    assert a != c


# ── gate mutation tests — every defect class must go RED ─────────────────────


def test_gate_red_on_checkpoint_invariant_breach() -> None:
    metrics = _metrics_copy()
    metrics["checkpoint_return_integrity"] = {
        **metrics["checkpoint_return_integrity"],
        "value": 0.0,
        "ok": False,
        "misses": 2,
    }
    verdict = s3_run.gate_check(metrics, _recorded())
    assert not verdict["pass"]
    assert any("checkpoint-return-integrity" in f for f in verdict["failures"])


def test_gate_red_on_retention_corridor_breach() -> None:
    metrics = _metrics_copy()
    metrics["fact_retention"]["overall"]["rate"] = 0.5
    verdict = s3_run.gate_check(metrics, _recorded())
    assert not verdict["pass"]
    assert any("fact-retention@N,k" in f and "below corridor" in f for f in verdict["failures"])


def test_gate_red_on_retention_bucket_starvation() -> None:
    """A drained age bucket is a scenario regression, not a green pass."""
    metrics = _metrics_copy()
    starved = metrics["fact_retention"]["by_age"]["200"]
    metrics["fact_retention"]["by_age"]["200"] = {**starved, "probes": 0, "rate": None}
    verdict = s3_run.gate_check(metrics, _recorded())
    assert not verdict["pass"]
    assert any("bucket 200 starved" in f for f in verdict["failures"])


def test_gate_red_on_context_growth_ceiling_breach() -> None:
    metrics = _metrics_copy()
    metrics["context_growth"]["factor"] = (
        float(_recorded()["metrics"]["context_growth"]["factor"]) + 0.5
    )
    verdict = s3_run.gate_check(metrics, _recorded())
    assert not verdict["pass"]
    assert any("context-growth-factor" in f and "above ceiling" in f for f in verdict["failures"])


def test_gate_red_on_scenario_axis_substitution() -> None:
    """Corridors only compare IDENTICAL sessions — a different seed or
    length must demand a re-record, never ride the recorded corridors."""
    metrics = _metrics_copy()
    metrics["scenario"] = {**metrics["scenario"], "seed": 4242}
    verdict = s3_run.gate_check(metrics, _recorded())
    assert not verdict["pass"]
    assert any("scenario axis seed differs" in f for f in verdict["failures"])


def test_gate_red_on_fingerprint_mismatch() -> None:
    recorded = _recorded()
    recorded["corpus_fingerprint"] = "0" * 64
    verdict = s3_run.gate_check(_metrics_copy(), recorded)
    assert not verdict["pass"]
    assert any("fingerprint differs" in f for f in verdict["failures"])


# ── make wiring: nightly class, not the local merge gate ──────────────────────


def test_makefile_wiring_nightly_class() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "bench-s3:" in makefile, "the bench-s3 make target is missing"
    assert "bench-s3-record:" in makefile, "the bench-s3-record target is missing"
    verify_line = next(line for line in makefile.splitlines() if line.startswith("verify:"))
    assert "bench-s3" not in verify_line, (
        "ADR-0020 gate policy: S3 is nightly-class and must NOT ride "
        "`make verify` (S1 is the only stand in the local merge gate)"
    )


def test_rewrite_rate_limits_disabled_rationale_pinned() -> None:
    """The stand's settings disable the wall-clock rewrite quotas.

    Source pin: the rationale (a logical multi-hour session compressed
    into seconds vs a per-REAL-minute quota) lives in run.py; without
    the override the nightly run trips 429-shaped failures mid-scenario.
    """
    source = (ROOT / "benchmarks" / "stands" / "s3_session" / "run.py").read_text(encoding="utf-8")
    assert '"context_rewrite_rate_limit_per_minute": 0' in source
    assert '"context_rewrite_project_rate_limit_per_minute": 0' in source


@pytest.mark.parametrize("turns", [20, 40])
def test_tiny_runs_stay_valid(turns: int) -> None:
    """Short sessions (the suite smoke shape) still yield sane metrics."""
    metrics = s3_run.run_measurement(
        ScenarioConfig(turns=turns, ages=(5,), checkpoints=1, tasks=2, drift_sample=4)
    )
    assert metrics["checkpoint_return_integrity"]["ok"]
    assert metrics["scenario"]["operations"]["CheckpointTurn"] == 1
    # empty far buckets are recorded as null, never as fake rates
    for entry in metrics["fact_retention"]["by_age"].values():
        if entry["probes"] == 0:
            assert entry["rate"] is None
