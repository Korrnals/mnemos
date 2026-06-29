"""P1-4 — CCR (Compress-Cache-Retrieve) reversible compression.

Inspired by headroom's CCR (https://github.com/headroomlabs-ai/headroom),
Apache 2.0. This is an original implementation integrated into the existing
mnemos SQLite store (one DB, one backup) rather than a separate cache.

Pipeline
--------
1. **Compress** — run the existing ``filter/pipeline.apply_filter`` over the
   input. The 5-stage filter already achieves 86-96% reduction on logs,
   JSON, and code.
2. **Cache** — store the ORIGINAL uncompressed content in ``ccr_cache``
   keyed by its SHA-256 hash. The hash is content-addressed, so
   re-compressing the same text is a no-op.
3. **Retrieve** — embed a short parseable marker in the compressed output.
   The LLM calls ``mnemos_retrieve(hash)`` to fetch the full original back
   (zero data loss) or ``mnemos_retrieve(hash, query=...)`` for FTS5-ranked
   snippets within the cached original.

Marker format
-------------
``[compressed: <hash> | <N>→<M> chars | retrieve via mnemos_retrieve]``

The marker is the *only* overhead added on top of the filtered content.
It is short, parseable, and LLM-friendly.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any

from mnemos.filter.pipeline import apply_filter

if TYPE_CHECKING:
    from mnemos.config import CCRConfig
    from mnemos.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

# Marker: [compressed: <hash> | 5000→500 chars | retrieve via mnemos_retrieve]
_MARKER_RE = re.compile(
    r"\[compressed:\s*(?P<hash>[0-9a-f]{64})\s*\|"
    r"\s*(?P<orig>\d+)→(?P<comp>\d+)\s*chars\s*\|"
    r"\s*retrieve via mnemos_retrieve\]"
)


def content_hash(text: str) -> str:
    """SHA-256 of the UTF-8 encoded text (hex, 64 chars)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_marker(h: str, original_chars: int, compressed_chars: int) -> str:
    """Build the CCR marker string."""
    return (
        f"[compressed: {h} | {original_chars}→{compressed_chars} chars | "
        f"retrieve via mnemos_retrieve]"
    )


def parse_marker(text: str) -> dict[str, Any] | None:
    """Extract the first CCR marker from ``text``.

    Returns ``{"hash","original_chars","compressed_chars","span"}`` or
    ``None`` if no marker is present.
    """
    m = _MARKER_RE.search(text)
    if m is None:
        return None
    return {
        "hash": m.group("hash"),
        "original_chars": int(m.group("orig")),
        "compressed_chars": int(m.group("comp")),
        "span": (m.start(), m.end()),
    }


def compress(
    text: str,
    *,
    store: SQLiteStore,
    config: CCRConfig,
    profile: str | None = None,
    project: str = "",
) -> dict[str, Any]:
    """Compress ``text``, cache the original, return a marker-embedded result.

    Args:
        text: Raw content to compress.
        store: SQLiteStore with the ``ccr_*`` methods.
        config: CCRConfig (ttl, max_entries, min_size_chars, ...).
        profile: Filter profile hint (auto-detected if None).
        project: Optional project slug to scope the cache entry.

    Returns:
        Dict with ``compressed_text``, ``hash``, ``original_size``,
        ``compressed_size``, ``reduction_pct``, ``marker``, ``cached``,
        ``profile``. For content below ``min_size_chars`` the text is
        returned as-is with ``cached=False`` and ``reduction_pct=0``.
    """
    original_size = len(text)

    # Tiny content — no token savings, skip caching.
    if original_size < config.min_size_chars:
        return {
            "compressed_text": text,
            "hash": "",
            "original_size": original_size,
            "compressed_size": original_size,
            "reduction_pct": 0.0,
            "marker": "",
            "cached": False,
            "profile": "skipped",
        }

    # 1. Compress via the existing filter pipeline.
    filtered = apply_filter(
        text,
        profile=profile,
        budget=config.filter_budget,
    )
    compressed_body = filtered["clean_content"]

    # 2. Cache the original (content-addressed, idempotent).
    h = content_hash(text)
    store.ccr_store(hash=h, original=text, project=project)

    # 3. Build marker + embed at the head of the compressed output.
    compressed_size = len(compressed_body)
    marker = build_marker(h, original_size, compressed_size)
    compressed_text = f"{marker}\n{compressed_body}"

    reduction_pct = round((1.0 - compressed_size / max(original_size, 1)) * 100.0, 2)

    # Opportunistic housekeeping: evict LRU if over capacity.
    # TTL cleanup is invoked explicitly by a scheduler / CLI to avoid
    # paying the scan cost on every compress call.
    try:
        store.ccr_evict_lru(config.max_entries)
    except Exception as exc:  # non-fatal — compression still succeeded
        logger.warning("ccr LRU eviction failed (non-fatal): %s", exc)

    return {
        "compressed_text": compressed_text,
        "hash": h,
        "original_size": original_size,
        "compressed_size": compressed_size,
        "reduction_pct": reduction_pct,
        "marker": marker,
        "cached": True,
        "profile": filtered["profile"],
    }


def retrieve(
    h: str,
    *,
    store: SQLiteStore,
    config: CCRConfig,
    query: str | None = None,
    snippet_count: int | None = None,
) -> dict[str, Any]:
    """Retrieve the original content for ``h``, or FTS5 snippets if ``query``.

    Args:
        h: SHA-256 hash from a CCR marker.
        store: SQLiteStore with the ``ccr_*`` methods.
        config: CCRConfig (for default snippet_count).
        query: Optional search query. If provided, returns ranked snippets
            from within the cached original instead of the full text.
        snippet_count: Override the config default snippet count.

    Returns:
        Dict with ``hash``, ``found``, and either ``original`` (full) or
        ``snippets`` (ranked list). ``found=False`` if the hash is absent.
    """
    entry = store.ccr_get(h)
    if entry is None:
        return {"hash": h, "found": False, "reason": "hash not in cache"}

    if query is None:
        return {
            "hash": h,
            "found": True,
            "original": entry["original"],
            "size_bytes": entry["size_bytes"],
            "retrieval_count": entry["retrieval_count"],
        }

    count = snippet_count if snippet_count is not None else config.snippet_count
    snippets = store.ccr_search(h, query, limit=count)
    return {
        "hash": h,
        "found": True,
        "query": query,
        "snippets": snippets,
        "retrieval_count": entry["retrieval_count"],
    }


def cleanup(
    *,
    store: SQLiteStore,
    config: CCRConfig,
) -> dict[str, int]:
    """Run TTL expiry + LRU eviction. Returns counts of what was removed."""
    ttl_deleted = store.ccr_cleanup_ttl(config.ttl_days)
    lru_evicted = store.ccr_evict_lru(config.max_entries)
    return {"ttl_deleted": ttl_deleted, "lru_evicted": lru_evicted}
