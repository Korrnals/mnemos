"""E2 G-gov ground truth — blind adjudication artifacts (E0 §4.3, §4.4).

E0 §4.4 locks the applicability rubric BEFORE seeding; this module
builds the artifact structure that rubric assumes:

* **Ground-truth keys live separately from the corpus** — this module
  (not ``records``) owns the qid → record mapping, the blind worksheet
  and the replacement ledger.
* **Blind worksheet (E0 §4.3)** — the judge sees ``(query, candidate
  record)`` pairs only: opaque pair ids, query text, candidate title +
  content. NEVER exposed in a pair: the leg a candidate came from, run
  ids, leg-revealing provenance, mnemos class tags, project/agent tags,
  or any gold flag. The intended-gold marks live in the SEPARATE answer
  key used only after adjudication to score. Deterministic distractors
  (next records of the same project) give the non-adjacency criterion
  something to bite on.
* **Replacement ledger (E0 §3.1, §4.4)** — adjudication rejects are
  replaced from the surplus pool, logged append-only; the analyzed
  denominator stays 96 no matter how many replacements fire.

The committed ``adjudication_ledger.json`` starts empty (no run, no
adjudication has occurred — E0's window). Ledger mutation happens at
E3 adjudication time via :func:`record_rejection`, which returns a NEW
ledger (this module never mutates state in place).

The rubric itself (three conjunctive criteria, verbatim from E0 §4.4)
rides with the worksheet so judges score against the registered text,
not folklore.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.strata.e2_gov.queries import (
    ANALYZED_GOV_QUERIES,
    GOV_QUERIES,
)
from benchmarks.strata.e2_gov.records import (
    ANALYZED_GOV_SLUGS,
    GOV_RECORDS,
    REPLACEMENT_POOL_SLUGS,
    gov_record_by_slug,
)

#: E0 §4.4 — a (query, record) pair is gold iff ALL THREE hold.
#: Verbatim from the registered document; the worksheet carries it.
ADJUDICATION_RUBRIC: tuple[str, str, str] = (
    "Topical match — the query asks about the norm or prior decision the record encodes.",
    "Self-sufficiency — the record alone answers the query (with standard terminology), "
    "without requiring another record.",
    "Non-adjacency — the record is not merely lexically co-occurrent (same "
    "project/artifact name) while answering a different question.",
)

#: E0 §3.1: the locked analyzed denominator. Replacements never change it.
ANALYZED_DENOMINATOR = 96

#: Double-annotation subsample share (E0 §4.3: 20%, kappa >= 0.6 floor).
DOUBLE_ANNOTATION_SHARE = 0.2
KAPPA_FLOOR = 0.6

LEDGER_PATH = Path(__file__).resolve().parent / "adjudication_ledger.json"


# ── ground-truth mapping (keys only — no record content here) ─────────────────


def gold_slug_for_qid(qid: str) -> str:
    """The seeded record a G-gov query was generated from (its gold)."""
    for query in GOV_QUERIES:
        if query.qid == qid:
            return query.record_slug
    raise KeyError(f"unknown G-gov qid: {qid!r}")


#: qid → gold record slug, for every DEFINED G-gov query (analyzed +
#: dormant pool queries — replacements activate the latter).
GOLD_BY_QID: dict[str, str] = {q.qid: q.record_slug for q in GOV_QUERIES}


# ── blind adjudication worksheet (E0 §4.3) ────────────────────────────────────


@dataclass(frozen=True)
class WorksheetPair:
    """One leg-stripped (query, candidate) pair, as the judge sees it."""

    pair_id: str
    query_text: str
    candidate_title: str
    candidate_content: str


@dataclass(frozen=True)
class WorksheetAnswerKeyRow:
    """Answer-key row — kept OUT of the worksheet the judge sees."""

    pair_id: str
    qid: str
    candidate_slug: str
    intended_gold: bool


def _pair_id(qid: str, slug: str) -> str:
    digest = hashlib.sha256(f"{qid}\x00{slug}".encode()).hexdigest()[:16]
    return f"p-{digest}"


def _project_groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for record in GOV_RECORDS:
        groups.setdefault(record.project, []).append(record.slug)
    return groups


def _distractors_for(slug: str, groups: dict[str, list[str]]) -> list[str]:
    """Deterministic distractors: the next two records of the same project."""
    group = groups[gov_record_by_slug(slug).project]
    position = group.index(slug)
    size = len(group)
    return [group[(position + step) % size] for step in (1, 2)]


def build_adjudication_worksheet() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the blind worksheet + the separate answer key.

    For every analyzed query (the current analyzed set), the worksheet
    carries the intended gold record plus two deterministic same-project
    distractors — shuffled deterministically by pair id so gold position
    carries no signal. The worksheet dict contains ONLY judge-visible
    fields plus the rubric header; every gold/provenance mark lives in
    the answer key dict.
    """
    groups = _project_groups()
    pairs: list[dict[str, str]] = []
    key_rows: list[dict[str, Any]] = []
    for query in ANALYZED_GOV_QUERIES:
        gold = query.record_slug
        candidates = [gold, *_distractors_for(gold, groups)]
        for slug in candidates:
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
    # Deterministic leg-neutral ordering: pair-id sort keeps position
    # meaningless while staying byte-reproducible for the fingerprint.
    pairs.sort(key=lambda p: p["pair_id"])
    key_rows.sort(key=lambda r: r["pair_id"])
    worksheet = {
        "rubric": list(ADJUDICATION_RUBRIC),
        "instructions": (
            "Mark each pair gold ONLY if all three rubric criteria hold. "
            "Judge the pair on its own text; no other context is authorized."
        ),
        "double_annotation_share": DOUBLE_ANNOTATION_SHARE,
        "kappa_floor": KAPPA_FLOOR,
        "pairs": pairs,
    }
    answer_key = {
        "note": "separate from the worksheet — opened only after adjudication",
        "rows": key_rows,
    }
    return worksheet, answer_key


# ── replacement ledger (E0 §3.1, §4.4) ────────────────────────────────────────


class PoolExhaustedError(RuntimeError):
    """The adjudication replacement pool is empty — no replacement left."""


def initial_ledger() -> dict[str, Any]:
    """The pristine ledger: analyzed 48, pool 52, no rejects, no runs."""
    return {
        "stratum": "e2-gov",
        "e0_spec": "docs/experiments/e0-meta-level.md §3.1, §4.3, §4.4",
        "denominator": ANALYZED_DENOMINATOR,
        "analyzed_records": list(ANALYZED_GOV_SLUGS),
        "replacement_pool": list(REPLACEMENT_POOL_SLUGS),
        "rejects": [],
        "replacements": [],
    }


def load_ledger(path: Path | None = None) -> dict[str, Any]:
    """Load the committed ledger (append-only artifact)."""
    ledger_path = path if path is not None else LEDGER_PATH
    ledger: dict[str, Any] = json.loads(ledger_path.read_text())
    return ledger


def save_ledger(ledger: dict[str, Any], path: Path | None = None) -> None:
    ledger_path = path if path is not None else LEDGER_PATH
    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=False) + "\n")


def record_rejection(
    ledger: dict[str, Any], slug: str, reason: str, decided_at: str
) -> tuple[dict[str, Any], str]:
    """Apply one adjudication reject; returns (new_ledger, replacement_slug).

    E0 §4.4: a rejected analyzed record is replaced by the next unused
    pool record (fixed pool order), the replacement is logged, and the
    analyzed denominator stays 96 — the swap exchanges one record (and
    its two queries) for another, atomically.

    ``decided_at`` is an ISO date string supplied by the caller: this
    module is deterministic and reads no clock.
    """
    analyzed = list(ledger["analyzed_records"])
    pool = list(ledger["replacement_pool"])
    if slug not in analyzed:
        raise ValueError(f"{slug!r} is not in the analyzed set")
    if any(r["record"] == slug for r in ledger["rejects"]):
        raise ValueError(f"{slug!r} was already rejected")
    if not pool:
        raise PoolExhaustedError(
            "replacement pool exhausted — the denominator can no longer be held at 96"
        )
    replacement = pool.pop(0)
    analyzed[analyzed.index(slug)] = replacement
    new_ledger = {
        **ledger,
        "analyzed_records": analyzed,
        "replacement_pool": pool,
        "rejects": [
            *ledger["rejects"],
            {"record": slug, "reason": reason, "decided_at": decided_at},
        ],
        "replacements": [
            *ledger["replacements"],
            {"rejected": slug, "replacement": replacement, "decided_at": decided_at},
        ],
    }
    # The denominator is structural, not stored faith: assert the swap
    # kept the analyzed set at 48 records (96 queries).
    if len(new_ledger["analyzed_records"]) != ANALYZED_DENOMINATOR // 2:
        raise AssertionError("analyzed set size drifted — denominator broken")
    return new_ledger, replacement


def active_analyzed_queries(ledger: dict[str, Any]) -> tuple[Any, ...]:
    """The analyzed query set under a ledger state (initial or amended).

    Initial state == ``ANALYZED_GOV_QUERIES``; after replacements the
    rejected records' queries are swapped for their replacements' — the
    count is always exactly 96.
    """
    from benchmarks.strata.e2_gov.queries import gov_queries_for_record

    active: list[Any] = []
    for slug in ledger["analyzed_records"]:
        active.extend(gov_queries_for_record(slug))
    if len(active) != ANALYZED_DENOMINATOR:
        raise AssertionError(
            f"active analyzed queries = {len(active)}, expected {ANALYZED_DENOMINATOR}"
        )
    return tuple(active)
