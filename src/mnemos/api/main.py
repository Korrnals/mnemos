"""FastAPI HTTP API for Mnemos.

Mirrors MCP tools as REST endpoints.
Loopback-bound by default (127.0.0.1) — do not expose externally without auth.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from mnemos import __version__
from mnemos.config import load_settings
from mnemos.manager import MemoryManager
from mnemos.models import (
    AgentRecallQuery,
    FilterRequest,
    Memory,
    MemoryCreate,
    RuleIngestRequest,
    RuleRemoveRequest,
    SearchQuery,
)
from mnemos.sessions import SessionStore
from mnemos.sessions.api import router as sessions_router

_manager: MemoryManager | None = None


def get_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        _manager = MemoryManager(load_settings())
    return _manager


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    mgr = get_manager()  # warm up (also runs the DDL on the shared db file)
    # M16: own SessionStore lives on app.state so the A2A router can
    # pick it up.  Re-uses the same db_path as MemoryManager so the
    # schema and WAL are shared.
    settings = mgr.settings
    store = SessionStore(settings.db_path)
    application.state.sessions_store = store
    try:
        yield
    finally:
        store.close()
        if _manager is not None:
            _manager.close()


app = FastAPI(
    title="Mnemos",
    description="Standalone memory & knowledge server for GCW agents.",
    version=__version__,
    lifespan=lifespan,
    # Bind only to loopback by default; controlled by uvicorn host arg
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    """Prometheus-style metrics endpoint (M5 — observability)."""
    # TODO (M5): expose mnemos_pipeline_processed_total etc.
    return get_manager().stats()


# ── Memories CRUD ──────────────────────────────────────────────────────────────


@app.post("/memories", response_model=Memory, status_code=201)
async def create_memory(data: MemoryCreate) -> Memory:
    mgr = get_manager()
    settings = mgr.settings
    from mnemos.models import validate_tag_contract

    tags = validate_tag_contract(data.tags, strict=settings.mnemos.strict_tag_contract)
    data.tags = tags
    project = next((t[len("project:") :] for t in tags if t.startswith("project:")), "")
    agent = next((t[len("agent:") :] for t in tags if t.startswith("agent:")), "")
    return mgr.add(data, project=project, agent=agent)


@app.get("/memories/{memory_id}", response_model=Memory)
async def get_memory(memory_id: str, include_raw: bool = False) -> Memory:
    mgr = get_manager()
    memory = mgr.get(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")
    if not include_raw:
        memory = memory.model_copy(update={"raw_content": None})
    return memory


@app.get("/memories", response_model=list[Memory])
async def list_memories(
    status: str | None = None,
    project: str | None = None,
    limit: int = Query(default=20, le=500),
) -> list[Memory]:
    mgr = get_manager()
    return mgr.list_recent(limit=limit, project=project)


# ── Search ─────────────────────────────────────────────────────────────────────


@app.post("/search")
async def search(query: SearchQuery) -> list[dict[str, Any]]:
    mgr = get_manager()
    results = mgr.search(
        query=query.query,
        tags=query.tags,
        project=query.project,
        limit=query.limit,
    )
    out = []
    for r in results:
        mem = r.memory
        content = mem.effective_content()
        if query.include_raw and mem.raw_content:
            content = mem.raw_content
        out.append(
            {
                "id": mem.id,
                "title": mem.auto_title(),
                "content": content,
                "tags": mem.tags,
                "score": r.score,
                "search_type": r.search_type,
            }
        )
    return out


# ── Per-agent recall (M3) ──────────────────────────────────────────────────────


@app.get("/recall/agent/{name}")
async def agent_recall(
    name: str,
    project: str | None = None,
    q: str | None = None,
    limit: int = Query(default=20, le=100),
) -> list[dict[str, Any]]:
    mgr = get_manager()
    query = AgentRecallQuery(agent=name, project=project, query=q, limit=limit)
    results = mgr.agent_recall(query)
    return [
        {
            "id": r.memory.id,
            "title": r.memory.auto_title(),
            "content": r.memory.effective_content(),
            "tags": r.memory.tags,
            "created_at": r.memory.created_at.isoformat(),
        }
        for r in results
    ]


# ── Pipeline endpoints (M4) ────────────────────────────────────────────────────


@app.post("/process")
async def trigger_process(
    project: str | None = None,
    agent: str | None = None,
    limit: int = Query(default=100, le=500),
) -> dict[str, Any]:
    """Trigger end-to-end pipeline: cluster → synthesize → quality_gate → publish."""
    mgr = get_manager()
    summary = mgr.run_pipeline(project=project, agent=agent, limit=limit)
    return {"status": "ok", **summary}


@app.post("/synthesize")
async def trigger_synthesize(cluster_id: str) -> dict[str, Any]:
    """Trigger LLM synthesis for a cluster."""
    mgr = get_manager()
    result = mgr.synthesize(cluster_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found or empty")
    return {
        "status": "ok",
        "draft_id": result.draft_id,
        "cluster_id": result.cluster_id,
        "source_coverage": result.source_coverage,
        "model_used": result.model_used,
    }


@app.post("/publish/{memory_id}")
async def publish_memory_endpoint(memory_id: str) -> dict[str, Any]:
    """Publish a processed memory to the vector index."""
    mgr = get_manager()
    result = mgr.publish(memory_id)
    if not result.published:
        raise HTTPException(
            status_code=400,
            detail=f"Publish failed for {memory_id} (status={result.previous_status})",
        )
    return {
        "status": "published",
        "memory_id": result.memory_id,
        "vector_indexed": result.vector_indexed,
    }


# ── DLQ (M5) ─────────────────────────────────────────────────────────────────


@app.get("/dlq")
async def list_dlq(
    task_label: str | None = None,
    ready_only: bool = False,
    limit: int = Query(default=50, le=500),
) -> list[dict[str, Any]]:
    """List Dead-Letter Queue entries."""
    mgr = get_manager()
    return mgr.dlq_list(task_label=task_label, ready_only=ready_only, limit=limit)


@app.post("/dlq/{dlq_id}/retry")
async def retry_dlq(dlq_id: str) -> dict[str, Any]:
    """Increment retry attempt for a DLQ entry."""
    mgr = get_manager()
    result = mgr.dlq_retry(dlq_id)
    return {"status": "retry_scheduled", "entry": result}


@app.delete("/dlq/{dlq_id}")
async def discard_dlq(dlq_id: str) -> dict[str, str]:
    """Permanently discard a DLQ entry."""
    mgr = get_manager()
    ok = mgr.dlq_discard(dlq_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"DLQ entry {dlq_id} not found")
    return {"status": "discarded", "dlq_id": dlq_id}


# ── Context Filter (M10) ─────────────────────────────────────────────────────


@app.post("/filter/{memory_id}")
async def apply_filter(memory_id: str, data: FilterRequest) -> dict[str, Any]:
    """Run the 5-stage context filter on a memory's raw_content."""
    mgr = get_manager()
    result = mgr.apply_context_filter(
        memory_id,
        profile=data.profile,
        budget=data.budget,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Tags aggregate (T-TAGS) ─────────────────────────────────────────────────


class TagCount(BaseModel):
    tag: str
    count: int


@app.get("/tags", response_model=list[TagCount])
async def list_tags() -> list[TagCount]:
    """Return all tags with their memory counts, sorted by count descending."""
    mgr = get_manager()
    raw: dict[str, int] = mgr.list_tags()
    # Sort explicitly here rather than relying on the storage-layer ORDER BY
    # surviving the dict round-trip / cache: count descending, then tag
    # ascending as a stable, deterministic tie-breaker.
    ordered = sorted(raw.items(), key=lambda kv: (-kv[1], kv[0]))
    return [TagCount(tag=t, count=c) for t, c in ordered]


# ── Traces (M6) ────────────────────────────────────────────────────────────────


@app.get("/traces")
async def list_traces(
    task_label: str | None = None,
    limit: int = Query(default=50, le=500),
) -> list[dict[str, Any]]:
    """Return pipeline trace records."""
    mgr = get_manager()
    rows = mgr.sqlite.list_traces(task_label=task_label, limit=limit)
    return [r.model_dump(mode="json") for r in rows]


# ── Path-scoped rules ingest (M8) ────────────────────────────────────────────


@app.post("/rules/ingest")
async def ingest_rules(data: RuleIngestRequest) -> dict[str, Any]:
    """Scan a directory for `*.instructions.md` files and ingest them as published memories."""
    mgr = get_manager()
    results = mgr.ingest_path_scoped_rules(
        data.rules_dir,
        project=data.project,
        agent=data.agent,
        pattern=data.pattern,
    )
    return {"status": "ok", "processed": len(results), "results": results}


@app.delete("/rules/ingest")
async def remove_rule(data: RuleRemoveRequest) -> dict[str, Any]:
    """Remove the Memory associated with a rule file."""
    mgr = get_manager()
    result = mgr.remove_path_scoped_rule(data.file_path)
    if not result["removed"]:
        raise HTTPException(status_code=404, detail=f"Rule for {data.file_path} not found")
    return {"status": "removed", **result}


# ── A2A Sessions API (M16) ──────────────────────────────────────────────────
# Mounted under ``/v1`` so the existing ``/memories``, ``/recall/*`` and
# ``/search`` routes are untouched.  The router reads its
# ``SessionStore`` from ``app.state.sessions_store`` (set in ``lifespan``)
# and falls back to a default ``load_settings()`` store when called
# outside the standard app (e.g. in a unit test that builds its own
# TestClient).
app.include_router(sessions_router, prefix="/v1")
