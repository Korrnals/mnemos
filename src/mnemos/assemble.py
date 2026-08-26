"""ADR-0017 D1 / ADR-0018 — ``assemble_context`` provider contract (mnemos #125).

One API assembles the model-facing context block:

    assemble_context(session, project, file?, budget, mode) -> ContextBlock

Fixed pipeline, in order (ADR-0017 D1 with the ADR-0018 CCR-stage amendment):

  1. recall     — hybrid RRF via ``MemoryManager.search`` (FTS5 + vector
                  legs, ``CONTEXT_ADMISSIBLE_STATUSES`` gate by default —
                  no ``status``/``include_raw`` is passed, so only
                  ``published``/``processed`` surface).
  2. ccr        — OPTIONAL (``expand_ccr=True``): expand inline
                  ``[compressed: <hash> | …]`` markers found in recalled
                  content via ``retrieve_content`` (project-scoped, already
                  issuance-scanned), budget-aware: an original that would
                  not fit the caller's budget is left compressed (the
                  marker stays; the model can retrieve on demand).
  3. filter     — the 5-stage context filter (``filter/pipeline``) per
                  block, auto-detected profile. MANDATORY.
  4. scan       — issuance secret scan (``scan_issuance``) per block.
                  MANDATORY: nothing enters the assembled output unscanned;
                  refuse mode drops the block (fail-closed). Redactions
                  are counted per block.
  5. align      — CacheAligner per block (dynamic spans relocated to the
                  BLOCK tail — see the note below on provenance ordering).
  6. budget     — greedy rank-ordered inclusion of whole provenance-wrapped
                  blocks under the caller's token budget.

Entry invariant (ADR-0018): every LTM → context path passes secret scan,
provenance wrapper, and status gate. This module is that path for
pre-LLM-call injection.

Design decisions (flagged for ArchCom ratification in the #125 report):

* **Provenance format** — one prefix line per injected block, exact shape
  ``[mnemos:<memory-id> project=<slug> status=<status> retrieved=<iso>]``.
* **Provenance vs CacheAligner order** — the aligner relocates ISO
  timestamps to the tail, which would gut the ``retrieved=<iso>`` field of
  a provenance line if alignment ran after wrapping. Blocks are therefore
  aligned FIRST and wrapped AFTER: the block *content* gets its dynamic
  tail, the provenance line stays parseable.
* **Budget partitioning (addendum 2, MAY — NOT implemented)** — ``budget``
  stays monolithic. An active-state line reserved before recall allocation
  waits for the D5 baseline corridor (the ~500-token figure has no
  evidence basis).
* **Whole-block budget fill** — blocks that do not fit are SKIPPED, not
  mid-block truncated: a truncated entry whose provenance promises a
  memory the model only half-sees is worse than a clean skip (the model
  can fetch the full entry by id). ``stats.budget.skipped`` counts them.
* **contentType partition** — ``mode=code`` keeps candidates whose
  ``detect_profile`` result at ingest was ``code``; ``mode=prose`` keeps
  the rest (binary partition over log/terminal/docs/web/default — the
  addendum names no finer split; flagged for ratification).
* **Legacy rows** — memories ingested before the ingest-side capture have
  no stored ``content_type``; the filter falls back to on-the-fly
  ``detect_profile`` (same canonical function) and counts the fallback in
  ``stats.recall.content_type_fallbacks``.

Reported defects (found by this slice, escalated — NOT worked around
silently):

* ``MemoryManager.search`` passes ``project`` to the FTS leg only; the
  vector leg (``VectorStore.search``) has no project filter, so a
  project-scoped search can surface other projects' rows via the vector
  resolve path. Pre-existing behavior, blast radius = every search
  caller; fixing it changes shared ranking semantics (D5 baseline
  implications), so this module enforces project scoping at its own
  recall boundary (``stats.recall.project_scoped_out``) and the systemic
  fix goes to the ArchCom queue as its own ticket.

``mode`` semantics (single parameter, two axes):

* delivery: ``sync`` (default) or ``async``. ``async`` runs the same
  pipeline, stores the result in a bounded per-manager registry, and
  returns only a handle envelope; the full result is fetched on a later
  call by passing ``async_handle=<id>``. Deliberately minimal — no worker
  threads, no persistence.
* contentType (addendum 1): ``code`` / ``prose`` filter recall candidates
  by stored content type (delivery defaults to sync).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from mnemos.ccr import parse_marker
from mnemos.filter.pipeline import detect_profile, estimate_tokens
from mnemos.models import Memory

if TYPE_CHECKING:
    from mnemos.manager import MemoryManager

logger = logging.getLogger(__name__)

#: Valid ``mode`` values — delivery (sync/async) + contentType (code/prose).
VALID_MODES: Final[frozenset[str]] = frozenset({"sync", "async", "code", "prose"})

#: Delivery+contentType values (the two contentType modes deliver synchronously).
_CONTENT_TYPE_MODES: Final[frozenset[str]] = frozenset({"code", "prose"})

#: Default token budget for the assembled block.
DEFAULT_BUDGET: int = 2048

#: Recall depth — hybrid-search limit before contentType filtering.
RECALL_DEPTH: int = 10

#: Async result registry bound (evicts oldest; single-tenant, in-memory).
ASYNC_REGISTRY_CAP: int = 32

#: The fixed pipeline stage order (recorded verbatim in ``stats.stages``).
STAGE_ORDER: Final[tuple[str, ...]] = (
    "recall",
    "ccr",
    "filter",
    "scan",
    "align",
    "budget",
)


# ── Internal candidate model ───────────────────────────────────────────────────


@dataclass(slots=True)
class _Candidate:
    """One recalled memory flowing through the pipeline stages."""

    memory: Memory
    score: float
    search_type: str
    content_type: str
    # Block content, mutated stage by stage.
    content: str
    # CCR stage bookkeeping.
    ccr_expanded: bool = False
    ccr_redactions: int = 0
    ccr_patterns: dict[str, int] = field(default_factory=dict)
    # Scan stage bookkeeping.
    redactions: int = 0
    redacted_patterns: dict[str, int] = field(default_factory=dict)
    # Filter/align stage bookkeeping.
    filter_profile: str | None = None
    align_moved_chars: int = 0


# ── Provenance ─────────────────────────────────────────────────────────────────


def build_provenance(memory_id: str, project: str, status: str, retrieved_iso: str) -> str:
    """Build the one-line provenance prefix every injected block carries.

    Exact format (ADR-0017 D1 "injected entries carry provenance"; the
    shape is this module's contract, tested verbatim):

    ``[mnemos:<memory-id> project=<slug> status=<status> retrieved=<iso>]``
    """
    return f"[mnemos:{memory_id} project={project} status={status} retrieved={retrieved_iso}]"


# ── Validation ─────────────────────────────────────────────────────────────────


def _validate(session: str, project: str, budget: int, mode: str) -> None:
    """Boundary validation — raises ``ValueError`` with actionable messages."""
    if not session or not session.strip():
        raise ValueError("session is required (non-empty string)")
    if not project or not project.strip():
        raise ValueError("project is required (non-empty string)")
    if mode not in VALID_MODES:
        valid = ", ".join(sorted(VALID_MODES))
        raise ValueError(f"invalid mode {mode!r}; valid values: {valid}")
    if budget < 1:
        raise ValueError(f"budget must be >= 1 token, got {budget}")


# ── Stage 1: recall ────────────────────────────────────────────────────────────


def _content_type_of(memory: Memory, *, fallbacks: list[int]) -> str:
    """Stored content type with an on-the-fly fallback for legacy rows.

    The ingest-side capture (``MemoryManager.add``) persists
    ``metadata["content_type"]``; rows written before that capture (or when
    the capture failed non-fatally) are classified on the fly with the same
    canonical ``detect_profile`` function, and the fallback is counted for
    observability.
    """
    stored = memory.metadata.get("content_type")
    if isinstance(stored, str) and stored in ("code", "prose"):
        return stored
    fallbacks[0] += 1
    return "code" if detect_profile(memory.effective_content()) == "code" else "prose"


def _matches_apply_to(memory: Memory, file: str) -> bool:
    """True when a rule memory's ``applyTo:<glob>`` tag matches ``file``.

    M8 tag shape: ``applyTo:src/**`` embedded in the tags list. Matched
    against the file string as given and its basename (the recall caller
    may pass either form); ``fnmatch`` is not path-separator aware, so
    ``**`` behaves as "anything under" only relative to the prefix — the
    same leniency the M8 rule docs describe.
    """
    base = Path(file).name
    for tag in memory.tags:
        if not tag.startswith("applyTo:"):
            continue
        glob = tag[len("applyTo:") :]
        if fnmatch(file, glob) or fnmatch(base, glob):
            return True
    return False


def _recall_stage(
    mgr: MemoryManager,
    *,
    project: str,
    file: str | None,
    content_type: str | None,
) -> tuple[list[_Candidate], dict[str, Any]]:
    """Hybrid RRF recall (status-gated) + contentType filter + applyTo pinning.

    Derived query: the file stem when ``file`` is given, else the project
    slug. ``fts_search`` wraps the whole query in one FTS5 phrase, so a
    multi-term "project stem" join would (almost) never match lexically —
    a single most-content-likely term is the honest derived query, and the
    vector leg carries semantic recall in production.

    Defence at this channel's boundary: candidates whose ``project``
    differs from the requested one are dropped. The vector leg of
    ``MemoryManager.search`` is not project-filtered (pre-existing gap —
    see the module docstring "Reported defects" note), and an assembled
    block asserting ``project=<slug>`` must never inject another project's
    entry.
    """
    query = Path(file).stem if file else project

    results = mgr.search(query=query, project=project, limit=RECALL_DEPTH)

    fallbacks = [0]
    candidates: list[_Candidate] = []
    type_filtered = 0
    scoped_out = 0
    for r in results:
        if (r.memory.project or "") != project:
            scoped_out += 1
            continue
        ct = _content_type_of(r.memory, fallbacks=fallbacks)
        if content_type is not None and ct != content_type:
            type_filtered += 1
            continue
        candidates.append(
            _Candidate(
                memory=r.memory,
                score=r.score,
                search_type=r.search_type,
                content_type=ct,
                content=r.memory.effective_content(),
            )
        )

    pinned = 0
    if file:
        # Stable partition — applyTo-matching rules float to the top of the
        # candidate list (M8 semantics) preserving rank order within groups.
        matched = [c for c in candidates if _matches_apply_to(c.memory, file)]
        unmatched = [c for c in candidates if not _matches_apply_to(c.memory, file)]
        candidates = matched + unmatched
        pinned = len(matched)

    stats: dict[str, Any] = {
        "query": query,
        "candidates": len(results),
        "admissible": len(results),
        "project_scoped_out": scoped_out,
        "content_type_filtered": type_filtered,
        "content_type_fallbacks": fallbacks[0],
        "applyto_pinned": pinned,
    }
    return candidates, stats


# ── Stage 2: CCR expansion (optional) ─────────────────────────────────────────


def _ccr_stage(
    mgr: MemoryManager,
    candidates: list[_Candidate],
    *,
    project: str,
    budget: int,
    expand: bool,
) -> dict[str, Any]:
    """Expand inline CCR markers via project-scoped retrieval, budget-aware.

    ``retrieve_content`` already runs the issuance scan (ADR-0018 P0), so
    the returned original is redacted-or-refused there; the assembled-block
    scan stage re-scans it regardless (patterns evolve, belt-and-suspenders
    is cheap). An expansion is adopted only when the resulting block stays
    within the caller's budget — otherwise the compressed form (with the
    marker intact) is kept so the model retains the on-demand handle.
    """
    markers_found = 0
    expanded = 0
    skipped_missing = 0
    skipped_budget = 0

    if expand:
        for cand in candidates:
            marker = parse_marker(cand.content)
            if marker is None:
                continue
            markers_found += 1
            result = mgr.retrieve_content(str(marker["hash"]), project=project)
            original = result.get("original") if result.get("found") else None
            if not result.get("found") or result.get("refused") or not isinstance(original, str):
                skipped_missing += 1
                continue
            start, end = marker["span"]
            expanded_content = cand.content[:start] + original + cand.content[end:]
            if estimate_tokens(expanded_content) > budget:
                skipped_budget += 1
                continue
            cand.content = expanded_content
            cand.ccr_expanded = True
            cand.ccr_redactions = int(result.get("redactions", 0))
            patterns = result.get("redacted_patterns")
            if isinstance(patterns, dict):
                cand.ccr_patterns = {str(k): int(v) for k, v in patterns.items()}
            expanded += 1
    else:
        markers_found = sum(1 for c in candidates if parse_marker(c.content) is not None)

    return {
        "enabled": expand,
        "markers_found": markers_found,
        "expanded": expanded,
        "skipped_missing": skipped_missing,
        "skipped_budget": skipped_budget,
    }


# ── Stage 3: context filter ────────────────────────────────────────────────────


def _filter_stage(candidates: list[_Candidate]) -> dict[str, Any]:
    """Run the 5-stage context filter per block (auto-detected profile)."""
    from mnemos.filter.pipeline import apply_filter

    profiles: dict[str, int] = {}
    for cand in candidates:
        filtered = apply_filter(cand.content, profile=None, budget=None)
        cand.content = str(filtered["clean_content"])
        cand.filter_profile = str(filtered["profile"])
        profiles[cand.filter_profile] = profiles.get(cand.filter_profile, 0) + 1
    return {"profiles": profiles}


# ── Stage 4: secret scan (mandatory) ───────────────────────────────────────────


def _scan_stage(mgr: MemoryManager, candidates: list[_Candidate]) -> dict[str, Any]:
    """Scan every block at the issuance boundary; refuse mode drops the block.

    Merges the CCR retrieval redaction counts (the rehydrate channel's own
    scan) with the assembled-block scan counts so per-block ``redactions``
    is the total number of redacted spans attributable to that block.
    """
    survivors: list[_Candidate] = []
    refused = 0
    for cand in candidates:
        scan = mgr.scan_issuance(cand.content, context=f"assemble:context:{cand.memory.id}")
        if scan.refused:
            refused += 1
            continue
        cand.content = scan.text
        cand.redactions = scan.redactions + cand.ccr_redactions
        merged = dict(cand.ccr_patterns)
        for name, count in scan.redacted_patterns.items():
            merged[name] = merged.get(name, 0) + count
        cand.redacted_patterns = merged
        survivors.append(cand)
    candidates[:] = survivors
    return {
        "blocks_scanned": len(candidates) + refused,
        "blocks_refused": refused,
    }


# ── Stage 5: CacheAligner ──────────────────────────────────────────────────────


def _align_stage(mgr: MemoryManager, candidates: list[_Candidate]) -> dict[str, Any]:
    """Relocate dynamic content to each block's tail (prefix stability).

    Runs BEFORE provenance wrapping — see the module docstring note on
    provenance ordering (the aligner would otherwise strip the
    ``retrieved=<iso>`` timestamp out of every provenance line).
    """
    aligned_blocks = 0
    moved_chars = 0
    for cand in candidates:
        result = mgr.align_prefix(cand.content, profile=cand.filter_profile)
        cand.content = str(result["aligned_text"])
        chars = int(result["moved_chars"])
        cand.align_moved_chars = chars
        moved_chars += chars
        if bool(result["prefix_stabilized"]):
            aligned_blocks += 1
    return {"blocks_aligned": aligned_blocks, "moved_chars": moved_chars}


# ── Stage 6: token budget ──────────────────────────────────────────────────────


def _budget_stage(
    candidates: list[_Candidate],
    *,
    budget: int,
    project: str,
    retrieved_iso: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Greedy rank-ordered inclusion of whole provenance-wrapped blocks."""
    included: list[dict[str, Any]] = []
    texts: list[str] = []
    skipped = 0
    remaining = budget

    for cand in candidates:
        mem = cand.memory
        provenance = build_provenance(
            mem.id, mem.project or project, mem.status.value, retrieved_iso
        )
        block_text = f"{provenance}\n{cand.content}"
        tokens = estimate_tokens(block_text)
        if tokens > remaining:
            skipped += 1
            continue
        remaining -= tokens
        block: dict[str, Any] = {
            "memory_id": mem.id,
            "project": mem.project or project,
            "status": mem.status.value,
            "score": cand.score,
            "search_type": cand.search_type,
            "content_type": cand.content_type,
            "provenance": provenance,
            "content": cand.content,
            "tokens": tokens,
            "redactions": cand.redactions,
            "ccr_expanded": cand.ccr_expanded,
        }
        if cand.redactions:
            block["redacted_patterns"] = cand.redacted_patterns
        included.append(block)
        texts.append(block_text)

    stats = {
        "budget": budget,
        "estimated_tokens": budget - remaining,
        "blocks_included": len(included),
        "blocks_skipped": skipped,
    }
    return included, texts, stats


# ── Async result registry (per-manager, bounded) ───────────────────────────────


def _store_async_result(mgr: MemoryManager, handle: str, result: dict[str, Any]) -> None:
    """Store an async result, evicting the oldest entry past the cap."""
    with mgr._assemble_async_lock:
        registry: dict[str, dict[str, Any]] = mgr._assemble_async
        registry[handle] = result
        while len(registry) > ASYNC_REGISTRY_CAP:
            oldest = next(iter(registry))
            del registry[oldest]


def _fetch_async_result(mgr: MemoryManager, handle: str) -> dict[str, Any]:
    """Pop a stored async result — unknown handles raise ``ValueError``."""
    with mgr._assemble_async_lock:
        result = mgr._assemble_async.pop(handle, None)
    if result is None:
        raise ValueError(f"unknown or already-fetched async_handle: {handle!r}")
    return result


# ── Public pipeline entry ──────────────────────────────────────────────────────


def assemble_context(
    mgr: MemoryManager,
    *,
    session: str,
    project: str,
    file: str | None = None,
    budget: int = DEFAULT_BUDGET,
    mode: str = "sync",
    expand_ccr: bool = False,
    async_handle: str | None = None,
) -> dict[str, Any]:
    """Assemble the model-facing context block (ADR-0017 D1 contract).

    Args:
        mgr: The owning ``MemoryManager`` (stages reuse its search /
            retrieve / scan / align primitives — no parallel retrieval path).
        session: Caller's session identifier (echoed in the result; used
            for assembly-level provenance, not per-block provenance).
        project: Project slug scoping recall and CCR redemption.
        file: Optional file path — contributes recall query terms and pins
            applyTo-scoped rule memories to the top of the candidates.
        budget: Token budget for the assembled block (monolithic — see the
            module docstring for the partitioning decision).
        mode: ``sync`` (default) / ``async`` / ``code`` / ``prose`` — see
            the module docstring for the two-axis semantics.
        expand_ccr: Enable the optional CCR stage (default off).
        async_handle: When given, fetch (and pop) a previously stored
            async result instead of running a new pipeline.

    Returns:
        The ContextBlock dict: ``text`` (provenance-wrapped blocks joined
        by blank lines), per-``blocks`` detail with provenance + redaction
        counts, ``tokens`` stats, and per-``stats`` stage telemetry. For
        ``mode="async"`` only a handle envelope is returned; the full
        result comes back on the next call with ``async_handle``.

    Raises:
        ValueError: Invalid ``session`` / ``project`` / ``mode`` / ``budget``
            or an unknown ``async_handle`` (boundary validation).
    """
    _validate(session, project, budget, mode)

    if async_handle is not None:
        fetched = _fetch_async_result(mgr, async_handle)
        fetched["async_handle"] = async_handle
        logger.info(
            "assemble_context: fetched async handle=%s session=%s project=%s",
            async_handle,
            session,
            project,
        )
        return fetched

    delivery = "async" if mode == "async" else "sync"
    content_type: str | None = mode if mode in _CONTENT_TYPE_MODES else None
    retrieved_iso = datetime.now(UTC).isoformat()

    # ── Fixed stage order (D1; recorded verbatim in stats) ────────────────
    candidates, recall_stats = _recall_stage(
        mgr, project=project, file=file, content_type=content_type
    )
    ccr_stats = _ccr_stage(mgr, candidates, project=project, budget=budget, expand=expand_ccr)
    filter_stats = _filter_stage(candidates)
    scan_stats = _scan_stage(mgr, candidates)
    align_stats = _align_stage(mgr, candidates)
    blocks, texts, budget_stats = _budget_stage(
        candidates, budget=budget, project=project, retrieved_iso=retrieved_iso
    )

    result: dict[str, Any] = {
        "session": session,
        "project": project,
        "file": file,
        "mode": delivery,
        "content_type": content_type,
        "text": "\n\n".join(texts),
        "blocks": blocks,
        "tokens": {"budget": budget, "estimated": budget_stats["estimated_tokens"]},
        "stats": {
            "stages": list(STAGE_ORDER),
            "recall": recall_stats,
            "ccr": ccr_stats,
            "filter": filter_stats,
            "scan": scan_stats,
            "align": align_stats,
            "budget": budget_stats,
        },
    }

    logger.info(
        "assemble_context: session=%s project=%s file=%s mode=%s content_type=%s "
        "candidates=%d blocks=%d refused=%d tokens=%d/%d redactions=%d",
        session,
        project,
        file,
        delivery,
        content_type,
        recall_stats["candidates"],
        len(blocks),
        scan_stats["blocks_refused"],
        budget_stats["estimated_tokens"],
        budget,
        sum(b["redactions"] for b in blocks),
    )

    if delivery == "async":
        handle = uuid.uuid4().hex
        _store_async_result(mgr, handle, result)
        logger.info("assemble_context: stored async handle=%s", handle)
        return {
            "mode": "async",
            "handle": handle,
            "status": "ready",
            "note": (
                "result stored; call assemble_context(async_handle=<handle>) "
                "to fetch the assembled block"
            ),
        }

    return result
