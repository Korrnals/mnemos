"""FastAPI REST API for AI-Brain."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai_brain.config import get_settings
from ai_brain.ingestion import IngestionPipeline
from ai_brain.manager import MemoryManager
from ai_brain.models import (
    Memory,
    MemoryCreate,
    MemorySource,
    MemoryType,
    MemoryUpdate,
    SearchQuery,
    SearchResult,
)

manager: MemoryManager | None = None
ingestion = IngestionPipeline()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    manager = MemoryManager(get_settings())
    yield
    if manager:
        manager.close()


app = FastAPI(
    title="AI-Brain",
    description="Hybrid long-term memory system",
    version="0.1.0",
    lifespan=lifespan,
)


def _mgr() -> MemoryManager:
    if manager is None:
        raise RuntimeError("MemoryManager not initialized")
    return manager


# ── Memories CRUD ─────────────────────────────────────────────────────────


@app.post("/api/v1/memories", response_model=Memory, status_code=201)
async def create_memory(data: MemoryCreate) -> Memory:
    return _mgr().add(data)


@app.get("/api/v1/memories", response_model=list[Memory])
async def list_memories(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: MemorySource | None = None,
    memory_type: MemoryType | None = None,
    tag: list[str] | None = Query(None),
) -> list[Memory]:
    return _mgr().list_memories(limit, offset, source, memory_type, tag)


@app.get("/api/v1/memories/{memory_id}", response_model=Memory)
async def get_memory(memory_id: str) -> Memory:
    memory = _mgr().get(memory_id)
    if not memory:
        raise HTTPException(404, "Memory not found")
    return memory


@app.put("/api/v1/memories/{memory_id}", response_model=Memory)
async def update_memory(memory_id: str, data: MemoryUpdate) -> Memory:
    memory = _mgr().update(memory_id, data)
    if not memory:
        raise HTTPException(404, "Memory not found")
    return memory


@app.delete("/api/v1/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: str) -> None:
    if not _mgr().delete(memory_id):
        raise HTTPException(404, "Memory not found")


# ── Search ────────────────────────────────────────────────────────────────


@app.post("/api/v1/search", response_model=list[SearchResult])
async def search_memories(query: SearchQuery) -> list[SearchResult]:
    return _mgr().search(query)


# ── Ingestion ─────────────────────────────────────────────────────────────


class IngestURLRequest(MemoryCreate):
    pass


@app.post("/api/v1/ingest/url", response_model=Memory, status_code=201)
async def ingest_url(url: str, tags: list[str] | None = Query(None)) -> Memory:
    data = ingestion.from_url(url, tags)
    return _mgr().add(data)


# ── Tags & Stats ──────────────────────────────────────────────────────────


@app.get("/api/v1/tags")
async def get_tags() -> dict[str, int]:
    return _mgr().get_tags()


@app.get("/api/v1/stats")
async def get_stats() -> dict:
    return _mgr().stats()


@app.post("/api/v1/sync")
async def sync_vault() -> dict:
    return _mgr().sync_vault()


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Web UI ────────────────────────────────────────────────────────────────

_WEB_STATIC = Path(__file__).parent / "web" / "static"

app.mount("/static", StaticFiles(directory=_WEB_STATIC), name="static")


@app.get("/")
async def serve_ui():
    return FileResponse(_WEB_STATIC / "index.html")
