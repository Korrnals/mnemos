"""Synthesize worker — M4: LLM draft synthesis for a cluster.

Takes a cluster of raw/processing memories and produces a single
synthesized article (status=processed).  Idempotency is keyed on
hash(cluster_id, prompt_version, model_version) — repeats return cached
result without calling the LLM again.

Security: only rationale_summary (≤200 chars) is stored in Trace.
Raw chain-of-thought is NEVER logged or persisted.

Sync / async
------------
``synthesize_cluster_async`` is the real implementation: it awaits
``mgr.llm.complete(...)``. ``synthesize_cluster`` is a thin sync wrapper
that drives the async variant via :func:`asyncio.run`. The sync wrapper
is intended for CLI / policy-engine call sites that do **not** already
have a running event loop. Calling it from inside an async context will
raise ``RuntimeError`` (from ``asyncio.run``) — use the async variant
there instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

from mnemos.models import Memory, MemorySource, MemoryStatus, MemoryType, SynthesisResult, Trace
from mnemos.traces import TraceRecorder

if TYPE_CHECKING:
    from mnemos.manager import MemoryManager

logger = logging.getLogger(__name__)

# System prompt for the synthesis LLM call. Kept module-level so prompt
# versioning is centralised and tests can inspect it.
_SYSTEM_PROMPT: str = (
    "You are a knowledge synthesis engine. Read the provided notes and "
    "produce a concise, well-structured Markdown article that captures "
    "the key insights, decisions, and open questions. Preserve factual "
    "accuracy. Do not hallucinate."
)


def _synthesis_cache_key(cluster_id: str, prompt_version: str, model_version: str) -> str:
    payload = f"{cluster_id}:{prompt_version}:{model_version}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _build_prompt(memories: list[Memory]) -> str:
    """Assemble a synthesis prompt from cluster members.

    The prompt is split into two sections by the ``--- Synthesis ---``
    delimiter. Everything **before** the delimiter is the *context*
    (the raw notes); everything **after** is the *query* (the synthesis
    instruction). This matches the ``RLMProvider`` context/query split
    convention so RLM dispatch can decompose the notes without extra
    parsing.
    """
    parts: list[str] = []
    for i, mem in enumerate(memories, start=1):
        parts.append(f"Note {i} ({mem.source}):")
        parts.append(mem.effective_content())
        parts.append("")
    parts.append("--- Synthesis ---")
    parts.append("")
    parts.append(
        "Synthesise the above notes into a concise, well-structured "
        "Markdown article. Group related points, preserve key insights "
        "and decisions, and list open questions at the end. Do not "
        "hallucinate."
    )
    return "\n".join(parts)


async def synthesize_cluster_async(
    mgr: MemoryManager,
    cluster_id: str,
    *,
    prompt_version: str = "v1",
    force: bool = False,
    force_rlm: bool = False,
    force_standard: bool = False,
) -> SynthesisResult | None:
    """Synthesize a cluster into a draft article (async implementation).

    Args:
        mgr: MemoryManager instance.
        cluster_id: The cluster to synthesize.
        prompt_version: Bumps the cache key when prompt text changes.
        force: Bypass cache and re-synthesize.
        force_rlm: Bypass the router threshold and always use RLM.
        force_standard: Bypass the router threshold and always use the
            standard provider. Mutually exclusive with ``force_rlm``.

    Returns:
        SynthesisResult on success, None if cluster not found or empty.
    """
    if force_rlm and force_standard:
        raise ValueError(
            "force_rlm and force_standard are mutually exclusive"
        )

    # 1. Load cluster members
    members = mgr.sqlite.list_by_cluster(cluster_id)
    if not members:
        logger.warning("synthesize: cluster %s not found or empty", cluster_id[:8])
        return None

    model = mgr.settings.llm.model
    cache_key = _synthesis_cache_key(cluster_id, prompt_version, model)

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

    # 3. Build prompt and call LLM via the router
    prompt = _build_prompt(members)
    recorder = TraceRecorder(store=mgr.sqlite)

    content = ""
    fallback_used = False
    model_used = model
    tokens_in = 0
    tokens_out = 0
    t0 = time.monotonic()

    try:
        with recorder.record(
            "synthesize",
            members[0].project,
            "llm_call",
            item_id=members[0].id,
        ) as trace:
            response = await mgr.llm.complete(
                prompt,
                system=_SYSTEM_PROMPT,
                force_rlm=force_rlm,
                force_standard=force_standard,
            )
            content = response.text
            tokens_in = response.tokens_in
            tokens_out = response.tokens_out
            fallback_used = response.fallback_used
            model_used = response.model

            trace.llm_called = True
            trace.llm_done = True
            trace.tokens_in = tokens_in
            trace.tokens_out = tokens_out
            trace.fallback_used = fallback_used
            trace.rationale_summary = _build_rationale(
                cluster_id,
                len(members),
                fallback_used,
                mgr.llm,
            )
    except Exception as exc:
        logger.error(
            "synthesize: LLM call failed for %s: %s", cluster_id[:8], exc
        )
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

    # 4. Derive a title from the first member if the LLM did not provide one
    title: str | None = _extract_title(content) or (
        f"Synthesis: {members[0].title or members[0].content[:40]}"
    )

    # 5. Build result
    result = SynthesisResult(
        cluster_id=cluster_id,
        content=content,
        title=title,
        quality_score=0.0,  # set by quality_gate
        confidence=0.0,  # set by quality_gate
        source_coverage=len(members),
        model_used=model_used,
        prompt_version=prompt_version,
        cache_hit=False,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )

    # 6. Create processed memory
    processed = Memory(
        content=result.content,
        title=result.title,
        tags=[*members[0].tags, "gcw:synthesized"],
        source=MemorySource.SYNTHESIZED,
        memory_type=MemoryType.NOTE,
        project=members[0].project,
        agent=members[0].agent,
        status=MemoryStatus.PROCESSED,
        cluster_id=cluster_id,
        derived_from=[m.id for m in members],
        quality_score=0.0,
        confidence=0.0,
        source_coverage=len(members),
        metadata={
            "synthesis_cache_key": cache_key,
            "synthesis_cached_result": result.model_dump(mode="json"),
            "model_used": model_used,
            "prompt_version": prompt_version,
            "fallback_used": fallback_used,
        },
    )
    mgr.sqlite.save(processed)
    result.draft_id = processed.id

    # 7. Trace — draft created
    mgr.sqlite.save_trace(
        Trace(
            task_label="synthesize",
            project=processed.project,
            step="draft_created",
            item_id=processed.id,
            llm_called=True,
            llm_done=True,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            fallback_used=fallback_used,
            rationale_summary=(
                f"Draft {processed.id[:8]} from cluster {cluster_id[:8]} "
                f"({len(members)} sources, fallback={fallback_used})"
            )[:200],
        )
    )

    logger.info(
        "synthesize: draft %s from cluster %s (%s sources, %s ms, fallback=%s)",
        processed.id[:8],
        cluster_id[:8],
        len(members),
        latency_ms,
        fallback_used,
    )
    return result


def synthesize_cluster(
    mgr: MemoryManager,
    cluster_id: str,
    *,
    prompt_version: str = "v1",
    force: bool = False,
    force_rlm: bool = False,
    force_standard: bool = False,
) -> SynthesisResult | None:
    """Sync wrapper around :func:`synthesize_cluster_async`.

    Drives the async implementation via :func:`asyncio.run`. Intended for
    CLI / policy-engine call sites that do **not** have a running event
    loop. Calling this from inside an async context raises
    ``RuntimeError`` — use ``await synthesize_cluster_async(...)`` there
    instead.
    """
    return asyncio.run(
        synthesize_cluster_async(
            mgr,
            cluster_id,
            prompt_version=prompt_version,
            force=force,
            force_rlm=force_rlm,
            force_standard=force_standard,
        )
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _extract_title(content: str) -> str | None:
    """Extract a title from the first Markdown H1 heading in ``content``.

    Returns ``None`` when no H1 heading is present so the caller can fall
    back to a derived default title.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _build_rationale(
    cluster_id: str,
    source_count: int,
    fallback_used: bool,
    router: object,
) -> str:
    """Build a ≤200-char trace rationale, enriching with RLM metrics.

    When the router exposes ``rlm_metrics`` (RLM was dispatched), the
    rationale includes iteration / subcall / cost / trace-id metrics.
    Otherwise it records a plain summary with the fallback flag. Raw
    chain-of-thought is never included (security policy).
    """
    base = (
        f"Synthesised {source_count} sources from cluster {cluster_id[:8]}"
        f", fallback={fallback_used}"
    )
    metrics = getattr(router, "rlm_metrics", None)
    if not metrics:
        return base[:200]
    iters = metrics.get("iterations", 0)
    subcalls = metrics.get("subcall_count", 0)
    cost = float(metrics.get("total_cost", 0.0))
    trace_id = metrics.get("trace_id")
    rlm_part = (
        f" | RLM iters={iters} subcalls={subcalls} "
        f"cost=${cost:.4f}"
    )
    if trace_id:
        rlm_part += f" trace={trace_id}"
    return (base + rlm_part)[:200]
