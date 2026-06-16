"""Publish stage — M4: status=processed→published + vector index upsert.

Only status="published" ever enters the vector index.
This is the key invariant that keeps hybrid recall high-signal.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mnemos.models import MemoryStatus, PublishResult

if TYPE_CHECKING:
    from mnemos.manager import MemoryManager

logger = logging.getLogger(__name__)


def publish_memory(
    mgr: MemoryManager,
    memory_id: str,
    *,
    skip_quality_check: bool = False,
) -> PublishResult:
    """Promote a processed memory to published and index it.

    Args:
        mgr: MemoryManager instance.
        memory_id: The processed memory to publish.
        skip_quality_check: If True, bypass quality gate (use with care).

    Returns:
        PublishResult indicating success and whether vector indexing occurred.
    """
    memory = mgr.sqlite.get(memory_id)
    if memory is None:
        return PublishResult(
            memory_id=memory_id,
            published=False,
            previous_status="",
        )

    previous = memory.status.value

    if memory.status != MemoryStatus.PROCESSED and not skip_quality_check:
        logger.warning(
            "publish: %s status=%s, expected processed — skipping",
            memory_id[:8],
            memory.status.value,
        )
        return PublishResult(
            memory_id=memory_id,
            published=False,
            previous_status=previous,
        )

    # Transition status
    from datetime import UTC, datetime

    memory.status = MemoryStatus.PUBLISHED
    memory.updated_at = datetime.now(UTC)
    mgr.sqlite.save(memory)

    # Upsert to vector index
    vector_indexed = False
    try:
        emb = mgr.embedder.embed(mgr._embedding_text(memory))
        mgr.vectors.upsert(
            memory.id,
            emb,
            {"project": memory.project, "agent": memory.agent},
        )
        vector_indexed = True
    except Exception as exc:
        logger.warning("publish: vector upsert failed for %s: %s", memory_id[:8], exc)

    logger.info(
        "publish: %s → published (vector=%s)",
        memory_id[:8],
        vector_indexed,
    )
    return PublishResult(
        memory_id=memory_id,
        published=True,
        vector_indexed=vector_indexed,
        previous_status=previous,
    )
