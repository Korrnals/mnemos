"""FastAPI HTTP API for Mnemos.

Mirrors MCP tools as REST endpoints.
Loopback-bound by default (127.0.0.1) — do not expose externally without auth.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from starlette.middleware.cors import CORSMiddleware

from mnemos import __version__
from mnemos.config import Settings, load_settings
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

_logger = logging.getLogger(__name__)
_manager: MemoryManager | None = None


def _setup_cors(application: FastAPI, settings: Settings) -> None:
    """Add CORSMiddleware when CORS is enabled and origins are configured.

    Strict default: if cors_enabled=False or cors_allow_origins is empty,
    no middleware is added and no cross-origin request is permitted.

    Security invariant: allow_origins=["*"] combined with
    allow_credentials=True is forbidden by the Fetch/CORS specification
    (a credential-bearing wildcard response is rejected by all compliant
    browsers and signals a misconfiguration).  This combination raises
    ValueError at startup rather than silently shipping a broken config.
    """
    cfg = settings.api
    if not cfg.cors_enabled or not cfg.cors_allow_origins:
        return
    if "*" in cfg.cors_allow_origins and cfg.cors_allow_credentials:
        raise ValueError(
            "CORS misconfiguration: cors_allow_origins=['*'] combined with "
            "cors_allow_credentials=True is forbidden by the CORS spec. "
            "Either restrict cors_allow_origins to explicit origins or set "
            "cors_allow_credentials=False."
        )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_allow_origins,
        allow_credentials=cfg.cors_allow_credentials,
        allow_methods=cfg.cors_allow_methods,
        allow_headers=cfg.cors_allow_headers,
    )
    _logger.info("CORS enabled for %d origin(s)", len(cfg.cors_allow_origins))


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

# Apply CORS middleware based on current settings.
# Middleware must be registered before the first request (i.e., here at module
# load time).  Starlette raises RuntimeError if add_middleware is called after
# the app has started.  Tests that need custom CORS settings must call
# _setup_cors(test_app, settings) on their own test_app before TestClient.
_setup_cors(app, load_settings())
