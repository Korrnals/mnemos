"""Ledger-aware adjudication worksheet — the E3 build path (issue #277).

Why this module exists (and why NOT inside ``ground_truth.py``):
``ground_truth`` is INSIDE the e2-gov ``corpus_fingerprint`` module set
(``profile._stratum_modules`` — records, checkpoints, queries,
ground_truth), so post-run edits there break the fingerprint pin and
count as a corpus change per E0 §3.3/§6.6. The adjudication worksheet,
however, MUST track the REPLACEMENT LEDGER: ``record_rejection`` swaps
records in, and the static ``ground_truth.build_adjudication_worksheet``
iterates ``ANALYZED_GOV_QUERIES`` — after the first rejection the
replacement record's pairs never reach the artifact (issue #277, P2).
The ledger-aware builder therefore lives HERE, outside the fingerprint
set: it can evolve with the adjudication workflow without touching the
pinned corpus bytes.

Contracts pinned by test (``tests/test_e3_worksheet.py``):

* **Bootstrap byte-identity** — over the pristine ledger
  (``ground_truth.initial_ledger()`` / the committed
  ``adjudication_ledger.json``) this builder reproduces the CURRENT
  artifact byte-identically on every field the static builder emits
  (rubric, instructions, share, kappa floor, pairs) and the answer key
  in full. The pre-run registrations below ride as ADDITIVE fields —
  the judge-visible payload of the shared fields is unchanged.
* **Ledger awareness** — over an amended ledger the worksheet carries
  the replacement records' pairs; the analyzed denominator stays 96.
* **Blindness preserved** — the additive fields carry no leg, run id,
  qid, slug, or provenance marks (E0 §4.3).

Pre-run registrations riding with every worksheet (issue #277, the E0
window is open — registered in code BEFORE any adjudication):

* the **deterministic double-annotation rule** — WHICH pairs get
  double-annotated is fixed as a pure function of the pair ids (rank by
  ``sha256(pair_id)``, top ceil(20%)), removing the post-hoc selection
  freedom that could launder kappa past the 0.6 floor;
* the **distractor-score semantics line** — distractor gold-marks are
  corpus-hygiene signal, never enter scoring.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from benchmarks.strata.e2_gov import ground_truth as gt

# ground_truth is byte-frozen (inside the corpus_fingerprint set), so its
# module-private helpers (_pair_id, _project_groups, _distractors_for) are
# the STABLE single construction sites for pair ids and distractors —
# importing them (rather than duplicating the logic) is what makes
# bootstrap byte-identity structural, not coincidental.
from benchmarks.strata.e2_gov.ground_truth import (
    _distractors_for,
    _pair_id,
    _project_groups,
)
from benchmarks.strata.e2_gov.records import gov_record_by_slug

#: E0 §4.3 — the P3 semantics line (issue #277): a judge MAY mark a
#: distractor gold when it genuinely satisfies the rubric; that mark is
#: a NON-ADJACENCY / hygiene signal about the seed corpus (a distractor
#: answering the query as well as the intended gold means the pair is
#: ambiguous), never a scoring input.
DISTRACTOR_SCORING_SEMANTICS: str = (
    "Distractor gold-marks are corpus-hygiene signal, never enter scoring: "
    "marking a distractor gold reports an ambiguous pair, it does not "
    "contribute to any metric."
)

#: The frozen selection rule, verbatim as registered (issue #277, P2 —
#: frozen pre-run; a post-hoc change would be a selection freedom able
#: to launder kappa past the 0.6 floor).
DOUBLE_ANNOTATION_RULE: str = (
    "Deterministic double-annotation subsample (frozen pre-run, issue #277): "
    "rank ALL pair_ids by sha256(pair_id) hex ascending and double-annotate "
    "the top ceil(20%) of the ranked list. The selection is a pure function "
    "of the pair_id set — no discretionary choice at adjudication time."
)


def double_annotation_pair_ids(pair_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """The frozen 20% double-annotation subsample (E0 §4.3, issue #277).

    Rank every pair id by ``sha256(pair_id)`` hex ascending, take the
    top ``ceil(len * DOUBLE_ANNOTATION_SHARE)`` — ceil guarantees at
    least the registered 20% share on any stratum size. Pure function
    of the pair-id set: same ids in, same subsample out, so the
    selection cannot be re-rolled after seeing annotations. Return
    order IS the rank order (stable, ids unique by construction).
    """
    ranked = sorted(set(pair_ids), key=lambda pid: hashlib.sha256(pid.encode()).hexdigest())
    take = math.ceil(len(ranked) * gt.DOUBLE_ANNOTATION_SHARE)
    return tuple(ranked[:take])


def build_ledger_worksheet(
    ledger: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the blind worksheet + separate answer key under a LEDGER state.

    The analyzed query set is ``ground_truth.active_analyzed_queries``
    (the ledger's analyzed records, their two queries each — exactly 96
    after any number of replacements, E0 §3.1). Over the pristine
    ledger the output is byte-identical to the static
    ``ground_truth.build_adjudication_worksheet`` artifact on every
    shared field (pinned by test); the pre-run registrations of this
    module ride as additive worksheet fields.

    Distractors are drawn from ALL 100 seeded records (same rule as the
    static builder: the next two records of the gold's project), so a
    replacement record pulled from the pool gets real same-project
    distractors, not leftovers of the record it replaced.
    """
    queries = gt.active_analyzed_queries(ledger)
    groups = _project_groups()

    pairs: list[dict[str, str]] = []
    key_rows: list[dict[str, Any]] = []
    for query in queries:
        gold = query.record_slug
        for slug in [gold, *_distractors_for(gold, groups)]:
            record = gov_record_by_slug(slug)
            pid = _pair_id(query.qid, slug)
            pairs.append(
                {
                    "pair_id": pid,
                    "query_text": query.text,
                    "candidate_title": record.title,
                    "candidate_content": record.content,
                }
            )
            key_rows.append(
                {
                    "pair_id": pid,
                    "qid": query.qid,
                    "candidate_slug": slug,
                    "intended_gold": slug == gold,
                }
            )
    # Deterministic leg-neutral ordering — identical discipline to the
    # static builder (pair-id sort carries no gold-position signal).
    pairs.sort(key=lambda p: p["pair_id"])
    key_rows.sort(key=lambda r: r["pair_id"])

    worksheet: dict[str, Any] = {
        # Shared fields — VERBATIM from the static builder; byte-identity
        # over the pristine ledger is pinned by test.
        "rubric": list(gt.ADJUDICATION_RUBRIC),
        "instructions": (
            "Mark each pair gold ONLY if all three rubric criteria hold. "
            "Judge the pair on its own text; no other context is authorized."
        ),
        "double_annotation_share": gt.DOUBLE_ANNOTATION_SHARE,
        "kappa_floor": gt.KAPPA_FLOOR,
        "pairs": pairs,
        # Pre-run registrations (issue #277) — additive fields; the
        # shared judge-visible payload above is unchanged.
        "distractor_scoring_semantics": DISTRACTOR_SCORING_SEMANTICS,
        "double_annotation_rule": DOUBLE_ANNOTATION_RULE,
        "double_annotation_pair_ids": list(
            double_annotation_pair_ids([p["pair_id"] for p in pairs])
        ),
    }
    answer_key: dict[str, Any] = {
        "note": "separate from the worksheet — opened only after adjudication",
        "rows": key_rows,
    }
    return worksheet, answer_key
