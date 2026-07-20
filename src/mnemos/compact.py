"""Compact exchange format for mnemos federation (Phase 0, issue #85 part 2a).

Implements the ``mnemos.federation.v1`` compact record format (ArchCom
2026-07-17 federation contract §2.3). The compact format is a list of
*published summaries* — not raw JSON dumps of memory content. Each
record carries the essence of a memory (≤500-char summary, key points,
tags, provenance) so the receiving instance can ingest it without
re-reading the full content. Token savings ~10x vs raw content.

The builder runs each memory through the moderation pipeline
(:func:`mnemos.moderation.moderate`, #85 Part 1) *first*:

* ``refuse`` → the record is excluded (function returns ``None`` / the
  record is skipped at the payload level).
* ``redact`` → the sanitized content (secrets redacted, PII anonymized
  to RFC-reserved values) is used as the source of ``summary`` and
  ``key_points``.
* ``allow`` → the original content is used as-is.

This is Layer 3 of the federation defence-in-depth (contract §2.2.1):

1. Layer 1 — write-path ``mnemos:no-federate`` auto-tag (#86).
2. Layer 2 — background scanner (#89, future).
3. Layer 3 — moderation pipeline on export (this module, #85 Part 2a).

Public API:

* :data:`COMPACT_SCHEMA` — schema version string (``mnemos.federation.v1``).
* :class:`CompactRecord` — one compact exchange record.
* :func:`build_compact_record` — build a single record from one
  :class:`~mnemos.models.Memory` (runs moderation first; returns
  ``None`` when moderation refuses).
* :func:`build_compact_payload` — aggregate builder: run moderation on
  a list of memories, skip refused, return
  ``{"schema": ..., "records": [...], "stats": {...}}``.
* :func:`derive_record_type` — map a memory's ``mnemos:<subtype>`` tag
  to the compact ``type`` field.
* :func:`summarize_content` — truncate content to ≤500 chars at a word
  boundary with ``...``.
* :func:`extract_key_points` — heuristic bullet/numbered list
  extraction from content.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from mnemos.models import NO_FEDERATE_TAG, Memory
from mnemos.moderation import ModerationResult, ModerationVerdict, moderate

logger = logging.getLogger(__name__)

__all__ = [
    "COMPACT_SCHEMA",
    "MAX_KEY_POINTS",
    "MAX_SUMMARY_LEN",
    "MAX_TITLE_LEN",
    "CompactRecord",
    "build_compact_payload",
    "build_compact_record",
    "derive_record_type",
    "extract_key_points",
    "summarize_content",
]

#: Compact format schema version (forward-compat marker, contract §2.3).
COMPACT_SCHEMA: str = "mnemos.federation.v1"

#: Maximum ``title`` length (chars). Contract §2.3 — short headline.
MAX_TITLE_LEN: int = 256

#: Maximum ``summary`` length (chars). Contract §2.3: "≤500 chars".
MAX_SUMMARY_LEN: int = 500

#: Maximum number of ``key_points`` per record (contract §2.3 — "Max 5").
MAX_KEY_POINTS: int = 5

# ── Type mapping ──────────────────────────────────────────────────────────────
#
# Map a memory's ``mnemos:<subtype>`` tag to the compact record ``type``
# field. The compact format's type vocabulary (contract §2.3) is a
# subset of the mnemos tag subtypes. ``mnemos:legacy`` and
# ``mnemos:synthesized`` (pipeline artefacts) fall back to ``session``
# (sensible default — they are not categorical decisions). The
# ``mnemos:no-federate`` tag is NOT mapped — records with that tag are
# refused by moderation and never reach the builder.

_TYPE_MAP: dict[str, str] = {
    "decision": "decision",
    "learning": "learning",
    "bug-pattern": "bug-pattern",
    "rule": "rule",
    "open-question": "open-question",
    "checkpoint": "checkpoint",
    "session": "session",
    # Pipeline artefacts → session (sensible default, contract §2.3).
    "legacy": "session",
    "synthesized": "session",
}

#: Default record type when no known ``mnemos:`` subtype tag is present.
_DEFAULT_TYPE: str = "session"

# ── Heuristics for summary + key points ──────────────────────────────────────

#: Bullet line regex — matches ``- `` / ``* `` / ``• `` at line start.
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)$", re.MULTILINE)

#: Numbered list line regex — matches ``1. `` / ``2. `` at line start.
_NUMBERED_RE = re.compile(r"^\s*\d+\.\s+(.+)$", re.MULTILINE)


# ── Compact record ────────────────────────────────────────────────────────────


class CompactRecord(BaseModel):
    """One compact exchange record (contract §2.3).

    Fields:
        id: ``fed:<source_agent>:<local_uuid>`` — globally unique and
            idempotent on import (the receiving side keys on this id).
        type: Compact format type vocabulary — derived from the
            memory's ``mnemos:<subtype>`` tag (see :data:`_TYPE_MAP`).
        title: Short headline (≤ :data:`MAX_TITLE_LEN` chars).
        summary: ≤ :data:`MAX_SUMMARY_LEN` chars — the essence of the
            record, not raw content.
        key_points: List of strings the reader should take away
            (≤ :data:`MAX_KEY_POINTS` entries, may be empty).
        tags: Copy of the memory's tags minus ``mnemos:no-federate``.
        source_agent: Slug of the agent that authored the memory
            (from the ``agent:<slug>`` tag, prefix stripped).
        timestamp: ISO 8601 UTC string of ``memory.created_at``.
    """

    id: str
    type: str
    title: str
    summary: str = Field(default="")
    key_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_agent: str = Field(default="")
    timestamp: str = Field(default="")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _truncate_at_word_boundary(text: str, max_len: int) -> str:
    """Truncate ``text`` to ≤ ``max_len`` chars at a word boundary.

    If the text fits, return it unchanged. Otherwise cut at the last
    whitespace boundary within ``max_len - 3`` chars and append ``...``.
    If there is no whitespace boundary (a single very long word),
    hard-cut at ``max_len - 3`` and append ``...``. The returned string
    is always ≤ ``max_len`` chars.
    """
    if len(text) <= max_len:
        return text
    cut = max_len - 3
    if cut <= 0:
        return "..."
    # Find the last whitespace boundary within the cut window.
    boundary = text.rfind(" ", 0, cut)
    if boundary <= 0:
        # No whitespace in the window → hard cut.
        return text[:cut] + "..."
    return text[:boundary] + "..."


def summarize_content(content: str, *, max_len: int = MAX_SUMMARY_LEN) -> str:
    """Truncate ``content`` to ≤ ``max_len`` chars at a word boundary.

    If the content fits, return it unchanged. Otherwise cut at the last
    whitespace boundary ≤ ``max_len - 3`` chars and append ``...``.
    """
    return _truncate_at_word_boundary(content, max_len)


def _truncate_title(text: str) -> str:
    """Truncate ``text`` to ≤ :data:`MAX_TITLE_LEN` chars at a word boundary."""
    return _truncate_at_word_boundary(text, MAX_TITLE_LEN)


def extract_key_points(content: str, *, max_points: int = MAX_KEY_POINTS) -> list[str]:
    """Heuristically extract key points from ``content``.

    Strategy (contract §2.3 — "simple heuristic, don't over-engineer"):

    1. If the content has bullet (``- `` / ``* `` / ``• ``) lines,
       collect their text (stripped).
    2. Else if the content has numbered (``1. `` / ``2. ``) lines,
       collect their text (stripped).
    3. Else fall back to the first 1-2 sentences (split on ``. ``).

    Returns at most ``max_points`` entries. Each entry is a single
    string. Empty / whitespace-only entries are dropped.
    """
    if not content:
        return []
    points: list[str] = []
    # Bullets first.
    for m in _BULLET_RE.finditer(content):
        text = m.group(1).strip()
        if text:
            points.append(text)
        if len(points) >= max_points:
            return points
    if points:
        return points
    # Numbered lines.
    for m in _NUMBERED_RE.finditer(content):
        text = m.group(1).strip()
        if text:
            points.append(text)
        if len(points) >= max_points:
            return points
    if points:
        return points
    # Fallback: first 1-2 sentences (split on ". ").
    sentences = [s.strip() for s in content.split(". ") if s.strip()]
    if not sentences:
        return []
    out: list[str] = []
    for s in sentences[:2]:
        # The split drops the trailing period; re-add for readability
        # unless the sentence already ends with terminal punctuation.
        out.append(s if s.endswith((".", "!", "?")) else s + ".")
    return out


def derive_record_type(tags: list[str]) -> str:
    """Map a memory's ``mnemos:<subtype>`` tag to the compact record type.

    Scans ``tags`` for a ``mnemos:`` prefixed tag whose suffix is a known
    subtype (see :data:`_TYPE_MAP`). The first matching subtype wins.
    ``mnemos:no-federate`` is explicitly skipped (records with that tag
    are refused by moderation before reaching the builder). Returns
    :data:`_DEFAULT_TYPE` (``session``) when no known ``mnemos:`` subtype
    tag is present.
    """
    for tag in tags:
        if not tag.startswith("mnemos:"):
            continue
        suffix = tag[len("mnemos:") :]
        if suffix == "no-federate":
            continue
        if suffix in _TYPE_MAP:
            return _TYPE_MAP[suffix]
    return _DEFAULT_TYPE


def _memory_timestamp(created_at: datetime) -> str:
    """Return ``created_at`` as an ISO 8601 UTC string.

    Naive datetimes are assumed to be UTC (defensive — :class:`Memory`
    defaults to ``datetime.now(UTC)``). The ``Z`` suffix is used when the
    offset is exactly UTC for readability (contract §2.3 example uses
    ``2026-06-20T14:30:00Z``).
    """
    ts = created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    ts_utc = ts.astimezone(UTC)
    iso = ts_utc.isoformat()
    # Normalise ``+00:00`` to ``Z`` for the canonical UTC form.
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso


def _derive_title(memory: Memory, *, content_source: str) -> str:
    """Pick the compact record title.

    Preference (contract §2.3):

    1. ``memory.title`` if set (non-empty).
    2. First line of the (sanitized) content, stripped.
    3. ``memory.auto_title()`` (which itself falls back to "Untitled").

    The result is truncated to :data:`MAX_TITLE_LEN` chars at a word
    boundary with ``...``.
    """
    if memory.title:
        return _truncate_title(memory.title)
    first_line = content_source.strip().split("\n", 1)[0].lstrip("# ").strip()
    if first_line:
        return _truncate_title(first_line)
    return _truncate_title(memory.auto_title())


# ── Per-record builder ────────────────────────────────────────────────────────


def build_compact_record(
    memory: Memory,
    *,
    source_agent: str,
    refuse_threshold: float = 0.8,
    moderation_result: ModerationResult | None = None,
) -> CompactRecord | None:
    """Build a compact exchange record from a :class:`Memory`.

    Runs :func:`mnemos.moderation.moderate` on ``memory.content`` first
    (Layer 3 defence-in-depth, #85 Part 1):

    * ``refuse`` → return ``None`` (caller skips this record).
    * ``redact`` → use ``result.sanitized_content`` as the source for
      ``summary`` and ``key_points``.
    * ``allow`` → use ``memory.content`` as-is.

    Args:
        memory: The source memory to compact.
        source_agent: Slug of the agent that authored the memory. Used
            as the ``source_agent`` field and as the prefix of the
            record ``id`` (``fed:<source_agent>:<memory.id>``). Per
            contract §2.3 this is the provenance — it should come from
            the memory's ``agent:<slug>`` tag, but the caller passes it
            explicitly so the receiving side can verify it against the
            tag (defence against a forged tag).
        refuse_threshold: Fraction of content that must be redacted/
            anonymized to trigger ``refuse`` (default 0.8 = 80%).
            Forwarded to :func:`moderate` when ``moderation_result`` is
            ``None``; ignored otherwise.
        moderation_result: Optional pre-computed moderation result. When
            provided, moderation is NOT re-run — this lets
            :func:`build_compact_payload` moderate once per memory and
            thread the result through to both the record builder and
            the stats aggregator (avoids a wasteful double moderation
            call). When ``None``, moderation is run inside this call.

    Returns:
        A :class:`CompactRecord`, or ``None`` when moderation refuses
        the record (the caller should skip ``None`` results).
    """
    result = moderation_result or moderate(
        memory.content, tags=memory.tags, refuse_threshold=refuse_threshold
    )

    if result.verdict == ModerationVerdict.REFUSE:
        logger.info(
            "compact: refused record id=%s (moderation verdict=refuse) — secrets=%d, pii=%d",
            memory.id,
            result.stats.get("secrets_redacted", 0),
            result.stats.get("pii_anonymized", 0),
        )
        return None

    # ``redact`` → use sanitized content; ``allow`` → use original.
    content_source = (
        result.sanitized_content if result.verdict == ModerationVerdict.REDACT else memory.content
    )

    summary = summarize_content(content_source)
    key_points = extract_key_points(content_source)
    record_type = derive_record_type(memory.tags)

    # Tags: keep project/agent/mnemos tags, defensively drop
    # ``mnemos:no-federate`` (moderation should have refused such records
    # already, but strip anyway — belt and braces).
    tags = [t for t in memory.tags if t != NO_FEDERATE_TAG]

    timestamp = _memory_timestamp(memory.created_at)
    title = _derive_title(memory, content_source=content_source)

    return CompactRecord(
        id=f"fed:{source_agent}:{memory.id}",
        type=record_type,
        title=title,
        summary=summary,
        key_points=key_points,
        tags=tags,
        source_agent=source_agent,
        timestamp=timestamp,
    )


# ── Payload builder ──────────────────────────────────────────────────────────


def build_compact_payload(
    memories: list[Memory],
    *,
    source_agent: str,
    refuse_threshold: float = 0.8,
) -> dict[str, Any]:
    """Build the full compact exchange payload from a list of memories.

    Runs :func:`moderate` on each memory, skips refused records, and
    returns the ``mnemos.federation.v1`` payload:

    .. code-block:: json

        {
          "schema": "mnemos.federation.v1",
          "records": [ {CompactRecord.dict()}, ... ],
          "stats": {
            "total": <int>,
            "exported": <int>,
            "refused": <int>,
            "secrets_redacted": <int>,
            "pii_anonymized": <int>
          }
        }

    ``stats`` carries counters only — no raw values, no mappings (the
    per-run mapping table is a leak surface, see
    :mod:`mnemos.moderation`).

    Args:
        memories: The source memories to compact.
        source_agent: Slug of the agent that authored the memories
            (forwarded to :func:`build_compact_record`).
        refuse_threshold: Fraction of content that must be redacted to
            trigger ``refuse`` (default 0.8, forwarded to
            :func:`moderate`).

    Returns:
        The compact payload dict, ready for JSON serialisation.
    """
    records: list[dict[str, Any]] = []
    total = len(memories)
    refused = 0
    secrets_redacted = 0
    pii_anonymized = 0

    for memory in memories:
        # Moderate once per memory and thread the result through to both
        # the record builder and the stats aggregator. This avoids the
        # wasteful double-moderation call (moderation is deterministic
        # but not free — secrets detector + PII scrubber + mapping table).
        result = moderate(
            memory.content,
            tags=memory.tags,
            refuse_threshold=refuse_threshold,
        )
        record = build_compact_record(
            memory,
            source_agent=source_agent,
            refuse_threshold=refuse_threshold,
            moderation_result=result,
        )
        if record is None:
            refused += 1
            continue
        secrets_redacted += result.stats.get("secrets_redacted", 0)
        pii_anonymized += result.stats.get("pii_anonymized", 0)
        records.append(record.model_dump())

    exported = len(records)
    return {
        "schema": COMPACT_SCHEMA,
        "records": records,
        "stats": {
            "total": total,
            "exported": exported,
            "refused": refused,
            "secrets_redacted": secrets_redacted,
            "pii_anonymized": pii_anonymized,
        },
    }
