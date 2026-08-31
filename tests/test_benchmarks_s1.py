"""S1 stand smoke tests (ADR-0020 BF-1, epic #169).

Guards the stand itself, on top of the golden tripwires
(``tests/golden``):

* the canonical baseline JSON is schema-valid and its fingerprint
  matches the corpus as it exists on disk;
* a fresh measurement is deterministic (two complete passes agree
  exactly) and PASSES the gate against the recorded baseline;
* ``BASELINE.md`` on disk is exactly what the generator produces from
  the JSON (no hand-edits, no staleness);
* the render-neutrality invariant checker CATCHES a leak — the
  mutation test breaks ``render_retraction`` into carrying the
  quarantine reason and asserts the invariant fails (the checker is
  itself under test, so a silent pass-through cannot ship).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from benchmarks.baselines.generate_md import render_baseline_md
from benchmarks.stands.s1_quality import run as s1_run
from benchmarks.stands.s1_quality.scenarios import (
    check_render_neutrality,
    scenario_refuse_render,
)

_BASELINE_KEYS = {
    "baseline_version",
    "stand_version",
    "corpus_fingerprint",
    "created",
    "metrics",
    "environment",
}


def _load_baseline() -> dict:
    assert s1_run.BASELINE_PATH.exists(), (
        "benchmarks/baselines/s1.json is missing — run `make bench-s1-record`"
    )
    return json.loads(s1_run.BASELINE_PATH.read_text())


def test_baseline_json_schema_and_fingerprint() -> None:
    baseline = _load_baseline()
    assert set(baseline) >= _BASELINE_KEYS
    assert baseline["baseline_version"] == 1
    assert baseline["stand_version"] == s1_run.STAND_VERSION
    fingerprint = baseline["corpus_fingerprint"]
    assert isinstance(fingerprint, str) and len(fingerprint) == 64
    int(fingerprint, 16)  # sha256 hex
    assert baseline["environment"]["deterministic_embedder"] is True
    assert baseline["metrics"], "baseline carries no metrics"
    # the fingerprint pins the corpus as it exists on disk right now —
    # a corpus edit without a re-record fails here (and in the gate)
    assert fingerprint == s1_run.corpus_fingerprint()


def test_measurement_deterministic_and_gate_green() -> None:
    first = s1_run.run_measurement()
    second = s1_run.run_measurement()
    assert first == second, (
        f"S1 measurement is not deterministic across fresh runs:\n{first!r}\n{second!r}"
    )
    verdict = s1_run.gate_check(first, _load_baseline())
    assert verdict["pass"], f"S1 gate failed against the recorded baseline: {verdict['failures']}"


def test_baseline_md_is_generated_not_stale() -> None:
    baseline = _load_baseline()
    assert s1_run.MARKDOWN_PATH.exists(), "BASELINE.md missing next to s1.json"
    assert s1_run.MARKDOWN_PATH.read_text() == render_baseline_md(baseline), (
        "BASELINE.md does not match s1.json — regenerate with "
        "`make bench-s1-record`; never hand-edit the generated summary"
    )


def test_render_neutrality_invariant_catches_reason_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: a reason-bearing retraction render must fail the invariant.

    Patches the render the way a would-be "transparency improvement"
    would leak it (reason embedded in the render) and asserts the S1
    checker flags it — the ADR-0019 §5 compromise is enforced by a
    checker that demonstrably catches the defect class.
    """
    import mnemos.manager as manager_mod

    def leaky_render(memory: object) -> str:
        from mnemos.models import render_retraction

        reason = getattr(memory, "quarantine_reason", "secret")
        return f"{render_retraction(memory)[:-1]} reason={reason}]"  # type: ignore[arg-type]

    monkeypatch.setattr(manager_mod, "render_retraction", leaky_render)
    with tempfile.TemporaryDirectory() as tmp:
        refuse = scenario_refuse_render(Path(tmp))
    renders = refuse.pop("_retraction_renders")
    reasons = refuse.pop("_quarantine_reasons")
    result = check_render_neutrality({"none": []}, renders, reasons)
    assert not result["ok"], "reason-leaking retraction render passed the sweep"
    assert any(v["kind"] in {"reason-leak", "format", "class-token"} for v in result["violations"])
