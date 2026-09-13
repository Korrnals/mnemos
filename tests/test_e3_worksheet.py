"""Ledger-aware adjudication worksheet — #277 pre-run registrations.

Pins (issue #277 → E0 §4.3/§4.4):

* **bootstrap byte-identity** — the ledger-aware builder over the
  pristine ledger reproduces the CURRENT (static) artifact byte-for-byte
  on every shared field, and the answer key in full;
* **ledger awareness** — replacement records' pairs reach the worksheet
  (the static builder's P2 bug: after the first rejection the swap never
  reached the artifact);
* **the frozen double-annotation rule** — a pure function of the
  pair-id set (rank by sha256(pair_id), top ceil(20%)), stamped into the
  worksheet before adjudication — no post-hoc selection freedom;
* **the distractor-score semantics line** rides with the worksheet;
* blindness preserved on the additive fields (E0 §4.3).
"""

from __future__ import annotations

import json
import math

import pytest
from benchmarks.strata.e2_gov import ground_truth as gt
from benchmarks.strata.e2_gov import worksheet as ws_mod
from benchmarks.strata.e2_gov.queries import ANALYZED_GOV_QIDS, gov_queries_for_record

#: The shared fields the static builder emits — the ledger-aware builder
#: must reproduce each byte-identically over the pristine ledger.
_SHARED_FIELDS = ("rubric", "instructions", "double_annotation_share", "kappa_floor", "pairs")

#: The pre-run registrations riding as ADDITIVE fields (issue #277).
_ADDITIVE_FIELDS = (
    "distractor_scoring_semantics",
    "double_annotation_rule",
    "double_annotation_pair_ids",
)


def _dumps(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, indent=2)


@pytest.fixture(scope="module")
def bootstrap_artifacts() -> tuple[dict, dict]:
    return ws_mod.build_ledger_worksheet(gt.initial_ledger())


@pytest.fixture(scope="module")
def static_artifacts() -> tuple[dict, dict]:
    return gt.build_adjudication_worksheet()


# ── bootstrap byte-identity (the pin) ─────────────────────────────────────────


@pytest.mark.parametrize("field", _SHARED_FIELDS)
def test_bootstrap_reproduces_static_artifact_bytes(
    bootstrap_artifacts: tuple[dict, dict],
    static_artifacts: tuple[dict, dict],
    field: str,
) -> None:
    """Issue #277 P2: parameterizing with the ledger must NOT change the
    bootstrap artifact — every shared field byte-identical to the static
    builder's output."""
    ws, _ = bootstrap_artifacts
    ref_ws, _ = static_artifacts
    assert _dumps(ws[field]) == _dumps(ref_ws[field]), field


def test_bootstrap_answer_key_identical(
    bootstrap_artifacts: tuple[dict, dict], static_artifacts: tuple[dict, dict]
) -> None:
    _, key = bootstrap_artifacts
    _, ref_key = static_artifacts
    assert _dumps(key) == _dumps(ref_key)


def test_committed_ledger_reproduces_bootstrap(
    bootstrap_artifacts: tuple[dict, dict],
) -> None:
    """The COMMITTED adjudication_ledger.json is the pristine state — the
    builder over it equals the builder over initial_ledger()."""
    ws, key = ws_mod.build_ledger_worksheet(gt.load_ledger())
    ref_ws, ref_key = bootstrap_artifacts
    assert _dumps(ws) == _dumps(ref_ws)
    assert _dumps(key) == _dumps(ref_key)


def test_worksheet_field_set_is_static_plus_registrations(
    bootstrap_artifacts: tuple[dict, dict],
) -> None:
    ws, _ = bootstrap_artifacts
    assert set(ws) == set(_SHARED_FIELDS) | set(_ADDITIVE_FIELDS)


# ── the pre-run registrations ride with every worksheet ──────────────────────


def test_distractor_semantics_line_rides_with_worksheet(
    bootstrap_artifacts: tuple[dict, dict],
) -> None:
    """#277 P3: the semantics line is a worksheet instruction — a judge
    marking a distractor gold reports an ambiguous pair; it never enters
    scoring."""
    ws, _ = bootstrap_artifacts
    line = ws["distractor_scoring_semantics"]
    assert "corpus-hygiene" in line and "never enter scoring" in line


def test_double_annotation_rule_text_frozen() -> None:
    """The rule names its own mechanics (sha256 rank, ceil(20%), pure
    function) so the artifact is self-describing."""
    text = ws_mod.DOUBLE_ANNOTATION_RULE
    assert "sha256(pair_id)" in text
    assert "ceil(20%)" in text
    assert "pure function" in text


# ── the frozen selection function ────────────────────────────────────────────


def test_double_annotation_selection_is_frozen_pure_function(
    bootstrap_artifacts: tuple[dict, dict],
) -> None:
    """The selection is a pure function of the pair-id set: same ids in,
    same subsample out — regardless of input order or call count."""
    ws, _ = bootstrap_artifacts
    pair_ids = [p["pair_id"] for p in ws["pairs"]]
    first = ws_mod.double_annotation_pair_ids(pair_ids)
    assert tuple(first) == ws_mod.double_annotation_pair_ids(reversed(pair_ids))
    assert tuple(first) == ws_mod.double_annotation_pair_ids(list(pair_ids))  # deterministic
    # top ceil(20%), in rank order
    assert len(first) == math.ceil(len(pair_ids) * gt.DOUBLE_ANNOTATION_SHARE)
    assert set(first) <= set(pair_ids)
    # the stamp equals the recomputation (the artifact IS the rule)
    assert ws["double_annotation_pair_ids"] == list(first)


def test_double_annotation_share_ceiling_on_small_sets() -> None:
    """ceil semantics: 10 ids → 2 (exactly 20%), 11 ids → 3 (≥ 20%)."""
    ids10 = [f"p-{i:03d}" for i in range(10)]
    ids11 = [*ids10, "p-010"]
    assert len(ws_mod.double_annotation_pair_ids(ids10)) == 2
    assert len(ws_mod.double_annotation_pair_ids(ids11)) == 3


def test_selection_rank_is_sha256_order(bootstrap_artifacts: tuple[dict, dict]) -> None:
    import hashlib

    ws, _ = bootstrap_artifacts
    stamped = ws["double_annotation_pair_ids"]
    ranked = sorted(
        (p["pair_id"] for p in ws["pairs"]),
        key=lambda pid: hashlib.sha256(pid.encode()).hexdigest(),
    )
    assert stamped == ranked[: len(stamped)]


# ── ledger awareness (the P2 fix) ────────────────────────────────────────────


def test_replacement_pairs_reach_the_worksheet() -> None:
    """The static builder's bug: after record_rejection the replacement
    record's pairs never reached the artifact. The ledger-aware builder
    carries them; the denominator stays 96."""
    ledger = gt.initial_ledger()
    rejected = ledger["analyzed_records"][7]
    amended, replacement = gt.record_rejection(
        ledger, rejected, "self-sufficiency fail", "2026-09-13"
    )
    ws, key = ws_mod.build_ledger_worksheet(amended)
    assert len(ws["pairs"]) == 96 * 3  # 96 queries x (gold + 2 distractors)
    rows = key["rows"]
    assert len(rows) == 96 * 3
    assert len([r for r in rows if r["intended_gold"]]) == 96  # one gold per query
    replacement_qids = {query.qid for query in gov_queries_for_record(replacement)}
    key_qids = {r["qid"] for r in rows}
    assert replacement_qids <= key_qids, "replacement queries missing from the worksheet"
    rejected_qids = {query.qid for query in gov_queries_for_record(rejected)}
    assert not rejected_qids & key_qids, "rejected record's queries still present"
    assert key_qids == {q.qid for q in gt.active_analyzed_queries(amended)}
    # the stamped double-annotation set follows the AMENDED pair ids
    assert ws["double_annotation_pair_ids"] == list(
        ws_mod.double_annotation_pair_ids([p["pair_id"] for p in ws["pairs"]])
    )


def test_worksheet_after_replacement_stays_blind() -> None:
    amended, _ = gt.record_rejection(
        gt.initial_ledger(), gt.initial_ledger()["analyzed_records"][0], "r", "2026-09-13"
    )
    ws, _ = ws_mod.build_ledger_worksheet(amended)
    blob = json.dumps(ws)
    for field in ('"leg"', '"run_id"', '"qid"', '"candidate_slug"', "project:", "agent:"):
        assert field not in blob, field


def test_bootstrap_blindness_preserved_on_additive_fields(
    bootstrap_artifacts: tuple[dict, dict],
) -> None:
    """E0 §4.3 on the registered fields: no leg/run/provenance leakage —
    the only structured payload they add is judge-visible pair ids."""
    ws, _ = bootstrap_artifacts
    blob = json.dumps(ws)
    for field in ('"leg"', '"run_id"', '"qid"', '"candidate_slug"', "mnemos:"):
        assert field not in blob, field
    # the stamped ids are a subset of the judge-visible pair ids
    stamped = set(ws["double_annotation_pair_ids"])
    assert stamped <= {p["pair_id"] for p in ws["pairs"]}


def test_bootstrap_gold_coverage_matches_analyzed_set(
    bootstrap_artifacts: tuple[dict, dict],
) -> None:
    _, key = bootstrap_artifacts
    golds = {r["qid"] for r in key["rows"] if r["intended_gold"]}
    assert golds == ANALYZED_GOV_QIDS
