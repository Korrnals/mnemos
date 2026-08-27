"""Golden D5 baseline suite (ADR-0017 D5 + ADR-0018 pair, #125 W4).

Marked ``@pytest.mark.golden``. Deterministic (see measure.py) and fast
enough to stay in the default CI run — deselect with ``-m "not golden"``
if a quick inner loop is needed.

Structure:

* ``test_golden_determinism``        — two full measurements must agree
                                        exactly (byte-level float equality);
* ``test_hard_invariants``           — status gate, A9 project purity,
                                        injection-acceptance = 1.0 on both
                                        the search channel and the
                                        assemble_context path, rewrite
                                        planted redaction;
* ``test_a9_predicate_comparison``   — the deferred ArchCom before/after:
                                        vector-leg predicate x over-fetch
                                        matrix on recall@10, with the
                                        regression guard (predicate ON,
                                        x4 must not lose to PRE-A9);
* ``test_d5_regression_floors``      — non-normative floors, one notch
                                        below the recorded baseline
                                        (BASELINE.md is the record; these
                                        constants are the tripwire);
* ``test_rewrite_metric_pair``       — replace-hit-rate /
                                        replace-regret-rate floors and
                                        control-channel health.

The measured numbers of THIS commit are recorded in tests/golden/BASELINE.md.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, TypedDict

import pytest

from tests.golden.measure import (
    K_VALUES,
    RewriteMetrics,
    SearchMetrics,
    assemble_leak_check,
    fresh_golden_manager,
    measure_rewrite,
    measure_search,
    overfetch_factor,
    vector_predicate_off,
)

pytestmark = pytest.mark.golden


# ── non-normative D5 floors (tripwire = recorded baseline - 0.02) ────────────
# Recorded from tests/golden/BASELINE.md (first baseline, W4): the
# baseline measured precision@5=0.2979, precision@10=0.1489,
# recall@5=recall@10=0.9858, hit-rate=0.9375 (15/16; the single miss is
# the designed B5 verdict-gated snippet refusal on a secret-bearing
# original), regret-rate=0.25 (6/24 scripted premature rewrites). These
# floors are REGRESSION tripwires, not targets: an intentional retrieval
# change that moves any number below its floor must re-record the
# baseline (and say so in the PR), not loosen the floor. Non-normative
# until the owner ratifies the corridors (see BASELINE.md
# "Ratification items").
FLOOR_PRECISION_5 = 0.28
FLOOR_PRECISION_10 = 0.13
FLOOR_RECALL_5 = 0.97
FLOOR_RECALL_10 = 0.97
FLOOR_HIT_RATE = 0.85
CEILING_REGRET_RATE = 0.30
FLOOR_A9_DELTA = -0.02  # predicate-ON vs PRE-A9: at most 2pp worse


class FullMeasurement(TypedDict):
    """One complete deterministic pass over every metric axis."""

    current: SearchMetrics
    a9_off_x4: SearchMetrics
    pre_a9: SearchMetrics
    a9_on_x2: SearchMetrics
    rewrite: RewriteMetrics
    assemble: dict[str, Any]


def _run_full_measurement(root: Path) -> FullMeasurement:
    """One complete deterministic pass: corpus → all metrics."""
    with fresh_golden_manager(root) as (mgr, slug_to_id):
        current = measure_search(mgr, slug_to_id, label="a9-on x4 (current)")
        with vector_predicate_off():
            off_x4 = measure_search(mgr, slug_to_id, label="a9-off x4")
        with vector_predicate_off(), overfetch_factor(2):
            pre_a9 = measure_search(mgr, slug_to_id, label="a9-off x2 (pre-A9)")
        with overfetch_factor(2):
            on_x2 = measure_search(mgr, slug_to_id, label="a9-on x2")
        rewrite = measure_rewrite(mgr)
        assemble = assemble_leak_check(mgr, slug_to_id)
    return {
        "current": current,
        "a9_off_x4": off_x4,
        "pre_a9": pre_a9,
        "a9_on_x2": on_x2,
        "rewrite": rewrite,
        "assemble": assemble,
    }


def _snapshot(result: FullMeasurement) -> str:
    """Flatten a measurement into a comparable string (floats repr-stable)."""

    def metrics_repr(m: SearchMetrics) -> str:
        parts = [m.label, f"judged={m.judged_queries}", f"probes={m.probe_queries}"]
        for k in K_VALUES:
            parts.append(f"p@{k}={m.precision[k]!r}")
        for k in K_VALUES:
            parts.append(f"r@{k}={m.recall[k]!r}")
        parts.extend(
            [
                f"nonadm={m.non_admissible_surfaced}",
                f"foreign={m.foreign_project_surfaced}",
                f"hybrid={m.hybrid_queries}",
                f"planted={m.planted_appearances}",
                f"leaks={m.planted_leaks}",
            ]
        )
        return " ".join(parts)

    rw = result["rewrite"]
    return " | ".join(
        [
            metrics_repr(result["current"]),
            metrics_repr(result["a9_off_x4"]),
            metrics_repr(result["pre_a9"]),
            metrics_repr(result["a9_on_x2"]),
            f"rw: n={rw.rewrite_events} m={rw.follow_up_retrieves} hits={rw.hits} "
            f"whole={rw.whole_redemptions} ctl={rw.control_hits}/{rw.controls} "
            f"leaks={rw.planted_redemption_leaks}",
            f"assemble: leaks={result['assemble']['leaks']!r} "
            f"surfaced={result['assemble']['all_planted_surfaced']}",
        ]
    )


def test_golden_determinism() -> None:
    """Two complete measurements must produce identical snapshots."""
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        run_a = _run_full_measurement(Path(tmp_a))
        run_b = _run_full_measurement(Path(tmp_b))
    snap_a, snap_b = _snapshot(run_a), _snapshot(run_b)
    assert snap_a == snap_b, (
        "golden measurement is not deterministic across fresh runs:\n"
        f"run1: {snap_a}\nrun2: {snap_b}"
    )


def test_hard_invariants() -> None:
    """Safety invariants that must hold exactly, every run."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_full_measurement(Path(tmp))
    for key in ("current", "a9_off_x4", "pre_a9", "a9_on_x2"):
        m = result[key]
        assert m.non_admissible_surfaced == 0, (
            f"[{m.label}] raw/non-admissible entries surfaced in search results"
        )
        assert m.foreign_project_surfaced == 0, (
            f"[{m.label}] out-of-project rows surfaced in a scoped search"
        )
        assert m.planted_leaks == 0, f"[{m.label}] planted secret leaked at issuance"
        assert m.planted_appearances >= 8, (
            f"[{m.label}] planted entries never ranked — injection metric "
            "measured nothing (probe coverage regression)"
        )
    # vector leg actually contributes (else the A9 comparison is vacuous)
    assert result["current"].hybrid_queries >= 10, (
        "vector leg contributed to almost no queries — deterministic embedder or fusion regression"
    )
    # D1 injection path: assembled context never carries a planted literal
    asm = result["assemble"]
    assert asm["leaks"] == [], f"assemble_context leaked planted secrets: {asm['leaks']}"
    assert asm["all_planted_surfaced"], (
        "some planted entries never entered an assembled block — leak check "
        "vacuously green (probe queries lost coverage)"
    )
    # rewrite channel: full redemption of planted blocks must be redacted
    assert result["rewrite"].planted_redemption_leaks == 0


def test_a9_predicate_comparison() -> None:
    """The deferred ArchCom W4 comparison: predicate ON vs PRE-A9.

    Reports the recall@10 delta between the current code path
    (predicate ON, x4 over-fetch) and the pre-A9 emulation (predicate
    OFF at the store, x2 depth). A materially negative delta means the
    predicate + over-fetch constant LOSE in-project recall — a finding
    for the committee, surfaced by this assertion.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_full_measurement(Path(tmp))
    current_r10 = result["current"].recall[10]
    pre_a9_r10 = result["pre_a9"].recall[10]
    delta = current_r10 - pre_a9_r10
    assert delta >= FLOOR_A9_DELTA, (
        "A9 predicate + x4 over-fetch regresses in-project recall@10 by "
        f"{delta:+.4f} (current={current_r10:.4f}, pre-A9={pre_a9_r10:.4f}) — "
        "report to ArchCom: the over-fetch constant needs re-derivation"
    )
    # over-fetch sensitivity: x2 with the predicate ON must not collapse
    on_x2_r10 = result["a9_on_x2"].recall[10]
    assert on_x2_r10 >= current_r10 - 0.10, (
        "recall@10 collapsed when over-fetch dropped x4→x2 with the "
        f"predicate ON (x4={current_r10:.4f}, x2={on_x2_r10:.4f}) — the "
        "constant is load-bearing beyond its documented depth role"
    )


def test_d5_regression_floors() -> None:
    """Retrieval floors — tripwire one notch below the recorded baseline."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_full_measurement(Path(tmp))
    m = result["current"]
    assert m.precision[5] >= FLOOR_PRECISION_5, (
        f"precision@5={m.precision[5]:.4f} below floor {FLOOR_PRECISION_5}"
    )
    assert m.precision[10] >= FLOOR_PRECISION_10, (
        f"precision@10={m.precision[10]:.4f} below floor {FLOOR_PRECISION_10}"
    )
    assert m.recall[5] >= FLOOR_RECALL_5, f"recall@5={m.recall[5]:.4f} below floor {FLOOR_RECALL_5}"
    assert m.recall[10] >= FLOOR_RECALL_10, (
        f"recall@10={m.recall[10]:.4f} below floor {FLOOR_RECALL_10}"
    )


def test_rewrite_metric_pair() -> None:
    """ADR-0018 pair floors + control-channel health."""
    with (
        tempfile.TemporaryDirectory() as tmp,
        fresh_golden_manager(Path(tmp)) as (mgr, _slug_to_id),
    ):
        rw = measure_rewrite(mgr)
    assert rw.rewrite_events == 24
    assert rw.follow_up_retrieves == 16
    assert rw.hit_rate >= FLOOR_HIT_RATE, (
        f"replace-hit-rate={rw.hit_rate:.4f} below floor {FLOOR_HIT_RATE} "
        f"({rw.hits}/{rw.follow_up_retrieves} follow-ups redeemed)"
    )
    assert rw.regret_rate <= CEILING_REGRET_RATE, (
        f"replace-regret-rate={rw.regret_rate:.4f} above ceiling "
        f"{CEILING_REGRET_RATE} ({rw.whole_redemptions}/{rw.rewrite_events})"
    )
    assert rw.control_hits == rw.controls, (
        f"control channel degraded: {rw.control_hits}/{rw.controls} "
        "never-rewritten CCR entries redeemed"
    )
