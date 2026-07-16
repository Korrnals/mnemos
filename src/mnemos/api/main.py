"""FastAPI HTTP API for Mnemos.

Mirrors MCP tools as REST endpoints.
Loopback-bound by default (127.0.0.1) — do not expose externally without auth.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.cors import CORSMiddleware

from mnemos import __version__
from mnemos.api.auth import router as auth_router
from mnemos.api.auth_store import AuthStore
from mnemos.api.middleware import AuthMiddleware
from mnemos.api.rate_limit import limiter
from mnemos.config import ApiConfig, Settings, load_settings
from mnemos.manager import MemoryManager
from mnemos.models import (
    AgentRecallQuery,
    FilterRequest,
    Memory,
    MemoryCreate,
    MemorySource,
    MemoryStatus,
    MemoryType,
    RuleIngestRequest,
    RuleRemoveRequest,
    SearchQuery,
    validate_tag_contract,
)
from mnemos.sessions import SessionStore
from mnemos.sessions.api import router as sessions_router

logger = logging.getLogger(__name__)
_logger = logger  # backward-compat alias used by CORS/tags code paths
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


# ── Startup guard (ADR-0014 §Trust zones) ─────────────────────────────────────


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_non_loopback_auth(api_cfg: ApiConfig) -> None:
    """Exit non-zero if a non-loopback bind is attempted without required auth config.

    Enforced at startup so a misconfigured ``auth_enabled: false`` server never
    becomes reachable from the network without credentials.
    """
    if _is_loopback_host(api_cfg.host):
        return

    missing: list[str] = []
    if not api_cfg.auth_enabled:
        missing.append("api.auth_enabled=true")
    if not api_cfg.totp_enabled:
        missing.append("api.totp_enabled=true")
    if not api_cfg.behind_tls_proxy:
        missing.append("api.behind_tls_proxy=true")

    if missing:
        print(
            f"FATAL: non-loopback bind ({api_cfg.host!r}) requires: "
            + ", ".join(missing)
            + ".  See docs/security.md for the remote setup guide.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # TOTP enabled but master key missing → refuse to start
    if api_cfg.totp_enabled and not api_cfg.totp_master_key.get_secret_value():
        print(
            "FATAL: api.totp_enabled=true but MNEMOS_API__TOTP_MASTER_KEY is not set.",
            file=sys.stderr,
        )
        raise SystemExit(1)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    mgr = get_manager()  # warm up (also runs the DDL on the shared db file)
    settings = mgr.settings

    # T-AUTH: startup guard — must run before any state is exposed.
    _check_non_loopback_auth(settings.api)

    # Expose API config on app.state so AuthMiddleware can read it without
    # calling load_settings() on every request.
    application.state.api_config = settings.api

    # M16: own SessionStore lives on app.state so the A2A router can
    # pick it up.  Re-uses the same db_path as MemoryManager so the
    # schema and WAL are shared.
    store = SessionStore(settings.db_path)
    application.state.sessions_store = store

    # T-AUTH: AuthStore on app.state for auth router and middleware.
    auth_store = AuthStore(settings.db_path)
    application.state.auth_store = auth_store

    # Start the background processor so raw memories added via the HTTP
    # API are automatically clustered → synthesized → quality-gated →
    # published + vector-indexed. Without this, POST /memories leaves
    # memories in `raw` status forever (queue grows, last_processed_at
    # stays None) — the same bug previously fixed for the MCP server
    # (see CHANGELOG [2.3.0] "Background processor not running").
    mgr.start_background_processor()

    try:
        yield
    finally:
        mgr.stop_background_processor()
        store.close()
        auth_store.close()
        if _manager is not None:
            _manager.close()


app = FastAPI(
    title="Mnemos",
    description="Standalone memory & knowledge server for AI agents.",
    version=__version__,
    lifespan=lifespan,
    # Bind only to loopback by default; controlled by uvicorn host arg
    docs_url="/docs",
    redoc_url="/redoc",
)

# T-AUTH: rate limiter state + exception handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# T-AUTH: auth middleware (runs after CORS, before routes)
app.add_middleware(AuthMiddleware)


# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Dashboard / metrics (mnemos-eyes) ─────────────────────────────────────────


@app.get("/api/v1/stats")
async def dashboard_stats() -> dict[str, Any]:
    """Structured JSON dashboard data for mnemos-eyes."""
    return get_manager().dashboard_stats()


@app.get("/api/v1/stats/timeseries")
async def stats_timeseries(
    metric: str = Query(default="memories_added"),
    range: str = Query(default="30d"),
    granularity: str = Query(default="day"),
) -> dict[str, Any]:
    """Temporal data for dashboard charts."""
    mgr = get_manager()
    # Parse range like "30d", "7d", "90d"
    days: int | None = None
    if range.endswith("d"):
        try:
            days = int(range[:-1])
        except ValueError:
            days = None
    elif range.endswith("h"):
        # Hour ranges not yet supported by the daily query; clamp to 1 day.
        days = 1
    if days is None or days <= 0:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid range '{range}'. Expected format like '30d' (positive integer + 'd').",
        )
    return mgr.timeseries(metric=metric, days=days, granularity=granularity)


def _prometheus_text(mgr: MemoryManager) -> str:
    """Render dashboard stats as Prometheus exposition text."""
    data = mgr.dashboard_stats()
    vol = data["volume"]
    filt = data["filter"]
    pipe = data["pipeline"]
    search = data["search"]
    vectors = data["vectors"]
    sessions = data["sessions"]
    lines: list[str] = []
    lines.append("# HELP mnemos_memories_total Total number of memories in storage")
    lines.append("# TYPE mnemos_memories_total gauge")
    lines.append(f"mnemos_memories_total {vol['memories_total']}")
    lines.append("# HELP mnemos_memories_by_status Memories by status")
    lines.append("# TYPE mnemos_memories_by_status gauge")
    for s, c in vol["by_status"].items():
        lines.append(f'mnemos_memories_by_status{{status="{s}"}} {c}')
    lines.append("# HELP mnemos_memories_by_project Memories by project")
    lines.append("# TYPE mnemos_memories_by_project gauge")
    for p, c in vol["by_project"].items():
        lines.append(f'mnemos_memories_by_project{{project="{p}"}} {c}')
    lines.append("# HELP mnemos_memories_by_agent Memories by agent")
    lines.append("# TYPE mnemos_memories_by_agent gauge")
    for a, c in vol["by_agent"].items():
        lines.append(f'mnemos_memories_by_agent{{agent="{a}"}} {c}')
    lines.append("# HELP mnemos_memories_by_type Memories by memory_type")
    lines.append("# TYPE mnemos_memories_by_type gauge")
    for t, c in vol["by_type"].items():
        lines.append(f'mnemos_memories_by_type{{type="{t}"}} {c}')
    lines.append("# HELP mnemos_filter_avg_reduction_pct Average filter reduction percentage")
    lines.append("# TYPE mnemos_filter_avg_reduction_pct gauge")
    lines.append(f"mnemos_filter_avg_reduction_pct {filt['avg_reduction_pct']}")
    lines.append("# HELP mnemos_filter_filtered_total Memories with clean_content populated")
    lines.append("# TYPE mnemos_filter_filtered_total gauge")
    lines.append(f"mnemos_filter_filtered_total {filt['filtered_total']}")
    lines.append("# HELP mnemos_pipeline_processed_total Total processed memories")
    lines.append("# TYPE mnemos_pipeline_processed_total counter")
    lines.append(f"mnemos_pipeline_processed_total {pipe['processed_total']}")
    lines.append("# HELP mnemos_pipeline_dlq_depth Current DLQ depth")
    lines.append("# TYPE mnemos_pipeline_dlq_depth gauge")
    lines.append(f"mnemos_pipeline_dlq_depth {pipe['dlq_depth']}")
    lines.append("# HELP mnemos_search_requests_total Total search requests since restart")
    lines.append("# TYPE mnemos_search_requests_total counter")
    lines.append(f"mnemos_search_requests_total {search['requests_total']}")
    lines.append("# HELP mnemos_search_avg_latency_ms Average search latency in ms")
    lines.append("# TYPE mnemos_search_avg_latency_ms gauge")
    lines.append(f"mnemos_search_avg_latency_ms {search['avg_latency_ms']}")
    lines.append("# HELP mnemos_vectors_indexed_total Indexed vectors")
    lines.append("# TYPE mnemos_vectors_indexed_total gauge")
    lines.append(f"mnemos_vectors_indexed_total {vectors['indexed_total']}")
    lines.append("# HELP mnemos_sessions_active Active sessions (updated within 24h)")
    lines.append("# TYPE mnemos_sessions_active gauge")
    lines.append(f"mnemos_sessions_active {sessions['active']}")
    lines.append("# HELP mnemos_sessions_total Total sessions")
    lines.append("# TYPE mnemos_sessions_total gauge")
    lines.append(f"mnemos_sessions_total {sessions['total']}")
    return "\n".join(lines) + "\n"


@app.get("/api/v1/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus text exposition format for Grafana/observability."""
    text = _prometheus_text(get_manager())
    return Response(
        content=text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    """Legacy metrics endpoint — returns stats() JSON for backward compat.

    For Prometheus text format, use ``GET /api/v1/metrics``.
    """
    return get_manager().stats()


# ── Memories CRUD ──────────────────────────────────────────────────────────────


@app.post("/memories", response_model=Memory, status_code=201)
async def create_memory(data: MemoryCreate) -> Memory:
    mgr = get_manager()
    settings = mgr.settings

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
    agent: str | None = None,
    tags: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=20, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Memory]:
    mgr = get_manager()
    status_enum: MemoryStatus | None = None
    if status:
        try:
            status_enum = MemoryStatus(status)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Valid: {[s.value for s in MemoryStatus]}",
            ) from exc
    tag_list: list[str] | None = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    return mgr.list_recent(
        limit=limit,
        offset=offset,
        project=project,
        agent=agent,
        status=status_enum,
        tags=tag_list,
        since=since,
        until=until,
    )


# ── Search ─────────────────────────────────────────────────────────────────────


@app.post("/search")
async def search(query: SearchQuery) -> list[dict[str, Any]]:
    mgr = get_manager()
    results = mgr.search(
        query=query.query,
        tags=query.tags,
        project=query.project,
        status=query.status,
        limit=query.limit,
        include_raw=query.include_raw,
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
                "status": mem.status.value,
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


@app.post("/reindex")
async def reindex_vectors(batch_size: int = Query(default=100, le=1000)) -> dict[str, Any]:
    """Rebuild the vector index for all published memories.

    Re-embeds every published memory and upserts into the vector store.
    Use after enabling embeddings or switching embedding models.
    """
    mgr = get_manager()
    result = mgr.rebuild_vector_index(batch_size=batch_size)
    return {"status": "ok", **result}


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
async def publish_memory_endpoint(
    memory_id: str,
    skip_quality_check: bool = Query(default=False),
) -> dict[str, Any]:
    """Publish a memory to the vector index.

    When ``skip_quality_check=true``, bypasses the processed-status
    requirement so memories can be published directly from ``raw``
    status without an LLM pipeline. This enables search to work
    immediately in deployments without a configured LLM backend.
    """
    mgr = get_manager()
    result = mgr.publish(memory_id, skip_quality_check=skip_quality_check)
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


class TagsRenameRequest(BaseModel):
    """Request body for POST /tags/rename — mirrors ``mnemos_tags_rename``."""

    from_prefix: str
    to_prefix: str
    subtypes: list[str] | None = None
    dry_run: bool = True
    project: str | None = None
    agent: str | None = None
    invalid_subtypes_to_legacy: bool = False


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


@app.post("/tags/rename")
async def rename_tags(req: TagsRenameRequest) -> dict[str, Any]:
    """Bulk rename tags matching ``from_prefix:<subtype>`` → ``to_prefix:<subtype>``.

    Mirrors the ``mnemos_tags_rename`` MCP tool and the ``mnemos tags rename``
    CLI command. Safe: uses ``update_fields`` (plain UPDATE) so the FTS5
    external-content index stays consistent. ``dry_run=true`` by default —
    nothing is written unless the caller explicitly sets ``dry_run=false``.
    """
    _track_http_call()
    mgr = get_manager()
    return mgr.tags_rename(
        from_prefix=req.from_prefix,
        to_prefix=req.to_prefix,
        subtypes=req.subtypes,
        dry_run=req.dry_run,
        project=req.project,
        agent=req.agent,
        invalid_subtypes_to_legacy=req.invalid_subtypes_to_legacy,
    )


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


# ── Auto-collect tracker (HTTP-local, mirrors MCP _checkpoint_tracker) ──────────

_auto_collect_tracker = {"calls_since_save": 0, "last_save_ts": 0.0}
_auto_collect_state = {
    "enabled": os.environ.get("MNEMOS_AUTO_COLLECT", "").lower() in ("true", "1", "yes", "on"),
}
_auto_collect_lock = threading.Lock()


def _track_http_call(is_save: bool = False) -> None:
    """Track HTTP memory-work calls for the auto-collect signal vector."""
    with _auto_collect_lock:
        if is_save:
            _auto_collect_tracker["calls_since_save"] = 0
            _auto_collect_tracker["last_save_ts"] = time.monotonic()
        else:
            _auto_collect_tracker["calls_since_save"] += 1


def _http_remind_calls() -> int:
    return 6 if _auto_collect_state["enabled"] else 12


def _http_remind_secs() -> int:
    return 480 if _auto_collect_state["enabled"] else 900


# ── Session context (save/recall) ──────────────────────────────────────────────


class SaveContextRequest(BaseModel):
    """Request body for POST /context/save — mirrors ``mnemos_save_context``.

    Fields accept either a string or a list of strings. When a list is
    provided, items are joined with newlines to form the markdown section
    body. This matches the Hermes plugin schema which declares these as
    ``type: array, items: {type: string}`` and the MCP tool which accepts
    free-form strings (bullet lists).
    """

    project: str
    goals: str | list[str] | None = None
    completed: str | list[str] | None = None
    in_progress: str | list[str] | None = None
    decisions: str | list[str] | None = None
    context: str | list[str] | None = None


class RecallContextRequest(BaseModel):
    """Request body for POST /context/recall — mirrors ``mnemos_recall_context``."""

    project: str
    query: str | None = None
    limit: int = 5


@app.post("/context/save", status_code=201)
async def save_context(req: SaveContextRequest) -> dict[str, Any]:
    """Save a session checkpoint memory tagged ``mnemos:checkpoint``.

    Mirrors the ``mnemos_save_context`` MCP tool. Builds structured Markdown
    from the supplied fields and stores it as a ``SESSION_CONTEXT`` memory.
    """
    mgr = get_manager()
    parts = [f"# Session checkpoint — {datetime.now(UTC).isoformat()}\n"]
    for field in ("goals", "completed", "in_progress", "decisions", "context"):
        val = getattr(req, field)
        if val:
            # Accept both str and list[str] — join lists with newlines.
            if isinstance(val, list):
                val = "\n".join(val)
            parts.append(f"## {field.replace('_', ' ').title()}\n{val}\n")
    content = "\n".join(parts)
    tags = [f"project:{req.project}", "agent:user", "mnemos:checkpoint"]
    data = MemoryCreate(
        content=content,
        tags=tags,
        source=MemorySource.MCP,
        memory_type=MemoryType.SESSION_CONTEXT,
    )
    memory = mgr.add(data, project=req.project, agent="user")
    _track_http_call(is_save=True)
    return {"status": "saved", "id": str(memory.id), "title": memory.auto_title()}


@app.post("/context/recall")
async def recall_context(req: RecallContextRequest) -> dict[str, Any]:
    """Recall the most recent checkpoint memories for a project.

    Mirrors the ``mnemos_recall_context`` MCP tool.
    """
    _track_http_call()
    mgr = get_manager()
    memories = mgr.recall_context(project=req.project, query=req.query, limit=req.limit)
    if not memories:
        return {
            "project": req.project,
            "checkpoints": [],
            "message": "No context found. Start by saving context with POST /context/save.",
        }
    return {
        "project": req.project,
        "checkpoints": [
            {
                "id": m.id,
                "title": m.auto_title(),
                "content": m.effective_content(),
                "tags": m.tags,
                "created_at": m.created_at.isoformat(),
            }
            for m in memories
        ],
    }


# ── Reversible content compression (CCR) ───────────────────────────────────────


class CompressRequest(BaseModel):
    """Request body for POST /compress — mirrors ``mnemos_compress``."""

    text: str
    profile: str | None = None
    project: str = ""


class RetrieveRequest(BaseModel):
    """Request body for POST /retrieve — mirrors ``mnemos_retrieve``."""

    hash: str
    query: str | None = None
    snippet_count: int | None = None


@app.post("/compress")
async def compress_content(req: CompressRequest) -> dict[str, Any]:
    """Compress ``text`` via CCR and cache the original.

    Mirrors the ``mnemos_compress`` MCP tool. Returns the CCR result dict
    (compressed text, hash, sizes, reduction, marker, …).
    """
    _track_http_call()
    mgr = get_manager()
    return mgr.compress_content(req.text, profile=req.profile, project=req.project)


@app.post("/retrieve")
async def retrieve_content(req: RetrieveRequest) -> dict[str, Any]:
    """Retrieve a CCR-cached original (or FTS5 snippets when ``query`` is set).

    Mirrors the ``mnemos_retrieve`` MCP tool.
    """
    _track_http_call()
    mgr = get_manager()
    return mgr.retrieve_content(req.hash, query=req.query, snippet_count=req.snippet_count)


# ── Auto-collect signal vector ─────────────────────────────────────────────────


@app.get("/auto-collect")
async def auto_collect_status() -> dict[str, Any]:
    """Compaction signal vector — mirrors ``mnemos_auto_collect_status``.

    Returns the in-process call counter / elapsed-time signals plus
    client-populated heuristic slots. The ``recommendation`` field is
    ``"save_checkpoint"`` when either signal exceeds its threshold, else
    ``"ok"``.
    """
    with _auto_collect_lock:
        calls = _auto_collect_tracker["calls_since_save"]
        elapsed = (
            time.monotonic() - _auto_collect_tracker["last_save_ts"]
            if _auto_collect_tracker["last_save_ts"]
            else 0.0
        )
    call_threshold = _http_remind_calls()
    secs_threshold = _http_remind_secs()
    call_triggered = calls >= call_threshold
    elapsed_triggered = elapsed > secs_threshold and calls > 0
    return {
        "auto_collect_enabled": _auto_collect_state["enabled"],
        "signals": {
            "call_counter": {
                "calls_since_save": calls,
                "threshold": call_threshold,
                "triggered": call_triggered,
            },
            "elapsed_secs": {
                "value": int(elapsed),
                "threshold": secs_threshold,
                "triggered": elapsed_triggered,
            },
            "context_size_heuristic": {"value": None, "note": "populated by client"},
            "summary_marker_detected": {"value": None, "note": "populated by client"},
            "reference_drop_heuristic": {"value": None, "note": "populated by client"},
        },
        "recommendation": ("save_checkpoint" if (call_triggered or elapsed_triggered) else "ok"),
        "next_reminder_in_calls": max(0, call_threshold - calls),
    }


# ── URL ingest ─────────────────────────────────────────────────────────────────


class IngestUrlRequest(BaseModel):
    """Request body for POST /ingest-url — mirrors ``mnemos_ingest_url``."""

    url: str
    tags: list[str]


@app.post("/ingest-url", status_code=201)
async def ingest_url(req: IngestUrlRequest) -> dict[str, Any]:
    """Fetch a web page, extract main text, and save it as a RAW memory.

    Mirrors the ``mnemos_ingest_url`` MCP tool. Credentials embedded in the
    URL are stripped before storage (OWASP A02). Tags are validated through
    the project's tag contract.
    """
    _track_http_call()
    mgr = get_manager()
    settings = mgr.settings
    url_clean = re.sub(r"(https?://)([^@]*@)", r"\1", req.url)
    tags = validate_tag_contract(req.tags, strict=settings.mnemos.strict_tag_contract)
    project = next((t[len("project:") :] for t in tags if t.startswith("project:")), "")
    agent = next((t[len("agent:") :] for t in tags if t.startswith("agent:")), "")
    memory = mgr.ingest_url(url_clean, tags=tags, project=project, agent=agent)
    return {"id": str(memory.id), "title": memory.auto_title(), "url": url_clean}


# ── File watcher (M8) ─────────────────────────────────────────────────────────


class WatchStartRequest(BaseModel):
    """Request body for POST /watch/start — mirrors ``mnemos_watch_start``."""

    paths: list[str] = []
    scan: bool = True
    include_rules: bool = False


@app.post("/watch/start")
async def watch_start(req: WatchStartRequest) -> dict[str, Any]:
    """Start the background vault watcher.

    Mirrors the ``mnemos_watch_start`` MCP tool. When ``paths`` is empty the
    current working directory is watched. When ``include_rules`` is true the
    watcher also ingests ``*.instructions.md`` rule files found under the
    watched paths.
    """
    _track_http_call()
    mgr = get_manager()
    paths = req.paths or [str(Path.cwd())]
    mgr.watch_start(paths=paths, scan=req.scan, include_rules=req.include_rules)
    return {
        "status": "started",
        "paths": paths,
        "scan": req.scan,
        "include_rules": req.include_rules,
    }


@app.post("/watch/stop")
async def watch_stop() -> dict[str, str]:
    """Stop the background vault watcher.

    Mirrors the ``mnemos_watch_stop`` MCP tool. Idempotent — returns
    ``{"status": "stopped"}`` whether or not a watcher was running.
    """
    _track_http_call()
    mgr = get_manager()
    mgr.watch_stop()
    return {"status": "stopped"}


@app.get("/watch/status")
async def watch_status() -> dict[str, Any]:
    """Return the current watcher status.

    Mirrors the ``mnemos_watch_status`` MCP tool. Returns ``{"running": bool}``.
    """
    _track_http_call()
    mgr = get_manager()
    return mgr.watch_status()


# ── Export / Import (M17 — backup/restore) ────────────────────────────────────


class ExportRequest(BaseModel):
    """Request body for POST /api/v1/export."""

    format: str = "json"  # json | sqlite
    compress: str = "none"  # none | gzip | zstd
    encrypt: bool = False
    project: str | None = None
    agent: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    since: str | None = None
    until: str | None = None


@app.post("/api/v1/export")
async def api_export(
    req: ExportRequest,
    passphrase: str | None = Header(None, alias="X-Mnemos-Passphrase"),
) -> StreamingResponse:
    """Export memories and stream the resulting file as a download.

    Passphrase for encryption is read from the ``X-Mnemos-Passphrase``
    header (handled below) — kept out of the request body so it is not
    logged as a request parameter.
    """
    import io
    import json as _json

    from mnemos.cli.export import (
        CompressMode,
        ExportFilter,
        ExportFormat,
        build_json_payload,
    )
    from mnemos.models import MemoryStatus

    mgr = get_manager()

    try:
        fmt = ExportFormat(req.format)
        comp = CompressMode(req.compress)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    status_enum: MemoryStatus | None = None
    if req.status:
        try:
            status_enum = MemoryStatus(req.status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid status: {req.status}") from exc

    since_dt = _parse_iso(req.since)
    until_dt = _parse_iso(req.until)
    filt = ExportFilter(
        project=req.project,
        agent=req.agent,
        status=status_enum,
        tags=req.tags,
        since=since_dt,
        until=until_dt,
    )

    if fmt == ExportFormat.JSON:
        payload = build_json_payload(mgr, filt)
        raw = _json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        media = "application/json"
        suffix = "json"
    else:
        from mnemos.cli.export import _build_sqlite_snapshot

        raw = _build_sqlite_snapshot(mgr)
        media = "application/gzip"
        suffix = "tar.gz"

    # Compression
    from mnemos.cli.export import _compress

    payload_bytes, _warnings = _compress(raw, comp)
    if comp == CompressMode.GZIP and fmt == ExportFormat.JSON:
        media = "application/gzip"
        suffix = "json.gz"

    # Encryption
    if req.encrypt:
        if not passphrase:
            raise HTTPException(
                status_code=400,
                detail="Encryption requested but X-Mnemos-Passphrase header is missing.",
            )
        from mnemos.cli.export import _encrypt

        payload_bytes = _encrypt(payload_bytes, passphrase)
        media = "application/octet-stream"
        suffix = "enc"

    filename = f"mnemos-export.{suffix}"
    return StreamingResponse(
        io.BytesIO(payload_bytes),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 date string, returning None on None/empty."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid ISO date: {value}") from exc


@app.post("/api/v1/import")
async def api_import(
    file: UploadFile = File(...),  # noqa: B008 — FastAPI idiom for multipart upload
    mode: str = Query(default="merge"),
    overwrite: bool = Query(default=False),
    confirm: bool = Query(default=False),
    dry_run: bool = Query(default=False),
    passphrase: str | None = Header(None, alias="X-Mnemos-Passphrase"),
) -> dict[str, Any]:
    """Import an export file uploaded as multipart form data.

    Returns a summary dict with ``imported``, ``skipped``, ``updated``,
    ``errors``, and ``warnings``. Encryption passphrase is read from the
    ``X-Mnemos-Passphrase`` header (kept out of the body / logs).
    """
    from mnemos.cli.import_ import run_import

    mgr = get_manager()

    # Stream the upload into a temp file so run_import can read it.
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=f"-{file.filename or 'import'}") as tf:
        tf.write(await file.read())
        tmp_path = Path(tf.name)
    try:
        result = run_import(
            mgr,
            tmp_path,
            mode=mode,
            overwrite=overwrite,
            confirm=confirm,
            dry_run=dry_run,
            passphrase=passphrase,
        )
        return result.summary()
    finally:
        tmp_path.unlink(missing_ok=True)


# ── A2A Sessions API (M16) ──────────────────────────────────────────────────
# Mounted under ``/v1`` so the existing ``/memories``, ``/recall/*`` and
# ``/search`` routes are untouched.  The router reads its
# ``SessionStore`` from ``app.state.sessions_store`` (set in ``lifespan``)
# and falls back to a default ``load_settings()`` store when called
# outside the standard app (e.g. in a unit test that builds its own
# TestClient).
app.include_router(sessions_router, prefix="/v1")

# ── Auth API (T-AUTH, ADR-0014) ───────────────────────────────────────────────
app.include_router(auth_router)

# Apply CORS middleware based on current settings.
# Middleware must be registered before the first request (i.e., here at module
# load time).  Starlette raises RuntimeError if add_middleware is called after
# the app has started, so this CANNOT be moved into ``lifespan``.  Tests that
# need custom CORS settings must call _setup_cors(test_app, settings) on their
# own test_app before TestClient.
#
# MERGE CONTRACT with feat/api-auth (AuthMiddleware): Starlette applies
# middleware in REVERSE order of registration (last added = outermost).  CORS
# MUST be the outermost layer so that pre-flight ``OPTIONS`` requests are
# answered before AuthMiddleware can reject them as unauthenticated.
# ``app.add_middleware(AuthMiddleware)`` is registered earlier (just after app
# construction); this call stays at the bottom so CORS wraps it as outermost.
_setup_cors(app, load_settings())
