"""Data models for AI-Brain memory system."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    NOTE = "note"
    FACT = "fact"
    SNIPPET = "snippet"
    BOOKMARK = "bookmark"
    CONVERSATION = "conversation"
    SESSION_CONTEXT = "session_context"


class MemorySource(str, Enum):
    MANUAL = "manual"
    TELEGRAM = "telegram"
    WEB = "web"
    FILE = "file"
    MCP = "mcp"
    OBSIDIAN = "obsidian"
    CLI = "cli"


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = MemorySource.MANUAL
    source_url: str | None = None
    memory_type: MemoryType = MemoryType.NOTE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = Field(default_factory=dict)
    file_path: str | None = None

    def auto_title(self) -> str:
        """Generate title from first line of content if not set."""
        if self.title:
            return self.title
        first_line = self.content.strip().split("\n")[0][:100]
        first_line = first_line.lstrip("# ").strip()
        return first_line or "Untitled"


class MemoryCreate(BaseModel):
    content: str
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = MemorySource.MANUAL
    source_url: str | None = None
    memory_type: MemoryType = MemoryType.NOTE
    metadata: dict = Field(default_factory=dict)


class MemoryUpdate(BaseModel):
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    memory_type: MemoryType | None = None
    metadata: dict | None = None


class SearchQuery(BaseModel):
    query: str
    tags: list[str] | None = None
    source: MemorySource | None = None
    memory_type: MemoryType | None = None
    limit: int = 20
    hybrid_alpha: float | None = None  # override config default


class SearchResult(BaseModel):
    memory: Memory
    score: float
    search_type: str  # "semantic", "fts", "hybrid"
