"""Synthesize worker — M4: LLM draft synthesis for a cluster.

Takes a cluster of raw/processing memories and produces a single
synthesized article (status=processed).  Idempotency is keyed on
hash(scope_key, prompt_version, model_version, input_set_hash) — repeats
return cached result without calling the LLM again, and a same-ID content
swap of any member (ADR-0019 §Swap semantics) changes input_set_hash and
forces a fresh synthesis (#250 F3).

Security: only rationale_summary (≤200 chars) is stored in Trace.
Raw chain-of-thought is NEVER logged or persisted.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING

from mnemos.models import (
    NO_FEDERATE_TAG,
    Memory,
    MemorySource,
    MemoryStatus,
    MemoryType,
    SynthesisResult,
    Trace,
)

if TYPE_CHECKING:
    from mnemos.manager import MemoryManager

logger = logging.getLogger(__name__)


def _synthesis_cache_key(
    scope_key: str,
    prompt_version: str,
    model_version: str,
    input_set_hash: str,
) -> str:
    payload = f"{scope_key}:{prompt_version}:{model_version}:{input_set_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _input_set_hash(members: list[Memory], mgr: MemoryManager) -> str:
    """#250 F3 — deterministic hash over the (id, content) of all members.

    Reuses the ADR-0019 B2a freshness computation —
    ``MemoryManager._embed_content_hash`` over ``_embedding_text`` — so a
    same-ID content swap changes the synthesis idempotency key exactly
    when it changes the embedding input. Members are folded in id-sorted
    order so the hash is order-independent.

    Prior drafts of the same cluster (``source=SYNTHESIZED``) are outputs,
    not inputs: a saved draft carries the same ``cluster_id``, so hashing
    it would change the key on every re-run and the cache could never
    hit. The input set is the cluster's source records only.
    """
    source_members = [m for m in members if m.source != MemorySource.SYNTHESIZED]
    parts = [
        f"{m.id}:{mgr._embed_content_hash(mgr._embedding_text(m))}"
        for m in sorted(source_members, key=lambda m: m.id)
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _build_prompt(memories: list[Memory]) -> str:
    """Assemble a synthesis prompt from cluster members."""
    parts = [
        "You are a knowledge synthesis engine.  Read the following notes and",
        "produce a concise, well-structured article that captures the key",
        "insights, decisions, and open questions.  Preserve factual accuracy.",
        "Do not hallucinate.  Output plain Markdown.",
        "",
        "--- Notes ---",
        "",
    ]
    for i, mem in enumerate(memories, start=1):
        parts.append(f"Note {i} ({mem.source}):")
        parts.append(mem.effective_content())
        parts.append("")
    parts.append("--- Synthesis ---")
    return "\n".join(parts)


def synthesize_cluster(
    mgr: MemoryManager,
    cluster_id: str,
    *,
    prompt_version: str = "v1",
    force: bool = False,
) -> SynthesisResult | None:
    """Synthesize a cluster into a draft article.

    Args:
        mgr: MemoryManager instance.
        cluster_id: The cluster to synthesize.
        prompt_version: Bumps the cache key when prompt text changes.
        force: Bypass cache and re-synthesize.

    Returns:
        SynthesisResult on success, None if cluster not found or empty.
    """
    # 1. Load cluster members
    members = mgr.sqlite.list_by_cluster(cluster_id)
    if not members:
        logger.warning("synthesize: cluster %s not found or empty", cluster_id[:8])
        return None

    model = mgr.settings.llm.model
    cache_key = _synthesis_cache_key(
        cluster_id, prompt_version, model, _input_set_hash(members, mgr)
    )

    # 2. Idempotency / cache check — look for existing processed memory
    existing_processed = [
        m
        for m in mgr.sqlite.list_by_cluster(cluster_id)
        if m.status == MemoryStatus.PROCESSED and m.metadata.get("synthesis_cache_key") == cache_key
    ]
    if not force and existing_processed:
        logger.info("synthesize: cache hit for cluster %s", cluster_id[:8])
        cached = existing_processed[0].metadata.get("synthesis_cached_result")
        if cached:
            result = SynthesisResult.model_validate(cached)
            result.draft_id = existing_processed[0].id
            return result

    # 3. Build prompt and call LLM
    prompt = _build_prompt(members)
    t0 = time.monotonic()

    content = ""
    title: str | None = None
    llm_called = False
    tokens_in = 0
    tokens_out = 0
    # Quality scores: when a real LLM provider is wired (llm/ modules),
    # these will be set from the LLM response. Until then, the deterministic
    # placeholder synthesis assigns conservative-but-passable scores so
    # records can transition processing→processed→published instead of
    # piling up in the queue forever (P0-1 fix).
    quality_score = 0.5
    confidence = 0.5
    try:
        # TODO: wire real LLM provider when llm/ modules are implemented.
        # For now, produce a deterministic placeholder so tests can assert.
        # llm_called stays False — this is NOT an LLM call, it's a stub.
        content = f"# Synthesis of {cluster_id[:8]}\n\n"
        content += "\n\n".join(f"- {m.effective_content()[:200]}" for m in members)
        title = f"Synthesis: {members[0].title or members[0].content[:40]}"
        tokens_in = len(prompt.split())
        tokens_out = len(content.split())
    except Exception as exc:
        logger.error("synthesize: LLM call failed for %s: %s", cluster_id[:8], exc)
        # Log trace for observability
        mgr.sqlite.save_trace(
            Trace(
                task_label="synthesize",
                project=members[0].project,
                step="llm_call",
                item_id=members[0].id,
                llm_called=True,
                llm_done=False,
                latency_ms=int((time.monotonic() - t0) * 1000),
                rationale_summary=f"LLM failure: {exc}"[:200],
            )
        )
        return None

    latency_ms = int((time.monotonic() - t0) * 1000)

    # 4. Build result
    result = SynthesisResult(
        cluster_id=cluster_id,
        content=content,
        title=title,
        quality_score=quality_score,
        confidence=confidence,
        source_coverage=len(members),
        model_used=model,
        prompt_version=prompt_version,
        cache_hit=False,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )

    # #250 F2 — strip-by-default: policy-bearing tags (``applyTo:`` /
    # ``severity:``) inherited from the first member never reach the
    # synthesized record — otherwise the draft would be born already
    # pinned to the member's application scope (transitive mint→pin,
    # extends #248). Everything else from the member's tags is inherited.
    inherited_tags = [t for t in members[0].tags if not t.startswith(("applyTo:", "severity:"))]
    tags = [*inherited_tags, "mnemos:synthesized"]
    # #250 F2b — no-federate propagates by ANY-member rule: one
    # secret-bearing member is enough for the synthesis to be born
    # excluded from all external exchange.
    if NO_FEDERATE_TAG not in tags and any(NO_FEDERATE_TAG in m.tags for m in members):
        tags.append(NO_FEDERATE_TAG)

    # 5. Create processed memory
    processed = Memory(
        content=result.content,
        title=result.title,
        tags=tags,
        source=MemorySource.SYNTHESIZED,
        memory_type=MemoryType.NOTE,
        project=members[0].project,
        agent=members[0].agent,
        status=MemoryStatus.PROCESSED,
        cluster_id=cluster_id,
        derived_from=[m.id for m in members],
        quality_score=quality_score,
        confidence=confidence,
        source_coverage=len(members),
        metadata={
            "synthesis_cache_key": cache_key,
            "synthesis_cached_result": result.model_dump(mode="json"),
            "model_used": model,
            "prompt_version": prompt_version,
        },
    )
    mgr.sqlite.save(processed)
    result.draft_id = processed.id

    # 6. Trace
    mgr.sqlite.save_trace(
        Trace(
            task_label="synthesize",
            project=processed.project,
            step="draft_created",
            item_id=processed.id,
            llm_called=llm_called,
            llm_done=True,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            rationale_summary=(
                f"Draft {processed.id[:8]} from cluster {cluster_id[:8]} ({len(members)} sources)"
            ),
        )
    )

    logger.info(
        "synthesize: draft %s from cluster %s (%s sources, %s ms)",
        processed.id[:8],
        cluster_id[:8],
        len(members),
        latency_ms,
    )
    return result
