"""MCP server for AI-Brain — exposes memory tools to Copilot/LLM agents."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ai_brain.config import load_settings
from ai_brain.ingestion import IngestionPipeline
from ai_brain.manager import MemoryManager
from ai_brain.models import MemoryCreate, MemorySource, MemoryType, SearchQuery
from ai_brain.watcher import BrainWatcher

logger = logging.getLogger(__name__)

server = Server("ai-brain")
_manager: MemoryManager | None = None
_watcher: BrainWatcher | None = None

# ── Auto-checkpoint tracking ─────────────────────────────────────────────────
_checkpoint_tracker = {
    "calls_since_save": 0,
    "last_save_ts": 0.0,
}
_CHECKPOINT_REMIND_CALLS = 12  # remind after N tool calls without save
_CHECKPOINT_REMIND_SECS = 900  # remind after 15 min without save


def get_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        _manager = MemoryManager(load_settings())
    return _manager


def _get_watcher() -> BrainWatcher:
    """Get or create the background watcher (lazy init)."""
    global _watcher
    if _watcher is None:
        settings = load_settings()
        _watcher = BrainWatcher(
            manager=get_manager(),
            ignore_dirs=set(settings.watcher.ignore_dirs),
            extensions={e if e.startswith(".") else f".{e}" for e in settings.watcher.extensions},
            max_file_size=settings.watcher.max_file_size_kb * 1024,
        )
    return _watcher


def _detect_project() -> str:
    """Auto-detect project name from current working directory."""
    return Path(os.getcwd()).name


def _checkpoint_reminder() -> str | None:
    """Return a reminder string if it's time to save a checkpoint, else None."""
    calls = _checkpoint_tracker["calls_since_save"]
    elapsed = time.monotonic() - _checkpoint_tracker["last_save_ts"] if _checkpoint_tracker["last_save_ts"] else 0
    if calls >= _CHECKPOINT_REMIND_CALLS or (elapsed > _CHECKPOINT_REMIND_SECS and calls > 0):
        return (
            f"\n\n⚠️ [ai-brain] {calls} tool calls since last checkpoint "
            f"({int(elapsed)}s ago). Consider calling brain_save_context "
            f"to preserve your current progress."
        )
    return None


def _track_call(is_save: bool = False) -> None:
    """Track tool calls for auto-checkpoint reminders."""
    if is_save:
        _checkpoint_tracker["calls_since_save"] = 0
        _checkpoint_tracker["last_save_ts"] = time.monotonic()
    else:
        _checkpoint_tracker["calls_since_save"] += 1


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="brain_search",
            description="Search long-term memory using semantic + full-text hybrid search. "
            "Use this to recall facts, notes, snippets, bookmarks, and conversations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="brain_add",
            description="Add a new entry to long-term memory. "
            "Use for saving facts, notes, code snippets, important context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The text content to remember",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title (optional, auto-generated if omitted)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for categorization",
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["note", "fact", "snippet", "bookmark", "conversation"],
                        "description": "Type of memory entry",
                        "default": "note",
                    },
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="brain_get",
            description="Retrieve a specific memory entry by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "UUID of the memory to retrieve",
                    },
                },
                "required": ["memory_id"],
            },
        ),
        Tool(
            name="brain_list_tags",
            description="List all tags in the memory with their counts.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="brain_stats",
            description="Get memory system statistics.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="brain_ingest_url",
            description="Fetch a web page, extract its content, and save to memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch and save",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags to add",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="brain_save_context",
            description=(
                "Save current session context/checkpoint to long-term memory. "
                "Use this PROACTIVELY to preserve: current goals, completed tasks, "
                "decisions made, active file paths, architecture notes, and any critical "
                "information that must survive context compression. "
                "Call this after completing significant work steps, when context is getting large, "
                "or before switching major tasks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project/workspace name (auto-detected from cwd if omitted)",
                    },
                    "goals": {
                        "type": "string",
                        "description": "Current session goals and objectives",
                    },
                    "completed": {
                        "type": "string",
                        "description": "What has been completed so far",
                    },
                    "in_progress": {
                        "type": "string",
                        "description": "What is currently in progress",
                    },
                    "decisions": {
                        "type": "string",
                        "description": "Key technical decisions and their rationale",
                    },
                    "context": {
                        "type": "string",
                        "description": "Any other critical context (file paths, architecture, gotchas, etc)",
                    },
                },
            },
        ),
        Tool(
            name="brain_recall_context",
            description=(
                "Recall the latest session context for a project from long-term memory. "
                "Use this at the START of every session, after context compression, "
                "or whenever you notice gaps in your understanding of the project state. "
                "Returns the most recent checkpoint with goals, progress, and decisions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project/workspace name (auto-detected from cwd if omitted)",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional: specific aspect to focus on (e.g. 'architecture decisions')",
                    },
                },
            },
        ),
        Tool(
            name="brain_list_recent",
            description=(
                "List the most recent memory entries. "
                "Useful for getting an overview of what's been stored recently."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max entries to return (default: 10)",
                        "default": 10,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags (optional)",
                    },
                },
            },
        ),
        Tool(
            name="brain_watch_start",
            description=(
                "Start watching directories for file changes and auto-index them into memory. "
                "Runs in background. Optionally performs an initial scan of existing files."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Directories to watch (defaults to cwd if omitted)",
                    },
                    "scan": {
                        "type": "boolean",
                        "description": "Scan existing files before watching (default: true)",
                        "default": True,
                    },
                },
            },
        ),
        Tool(
            name="brain_watch_stop",
            description="Stop the background file watcher.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="brain_watch_status",
            description="Get current watcher status: running/stopped, watched paths, indexing stats.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    mgr = get_manager()
    result = await _handle_tool(mgr, name, arguments)

    # Track calls and append checkpoint reminder for non-save tools
    if name == "brain_save_context":
        _track_call(is_save=True)
    else:
        _track_call()
        reminder = _checkpoint_reminder()
        if reminder and result:
            result[-1] = TextContent(type="text", text=result[-1].text + reminder)

    return result


async def _handle_tool(mgr: MemoryManager, name: str, arguments: dict) -> list[TextContent]:

    if name == "brain_search":
        sq = SearchQuery(
            query=arguments["query"],
            tags=arguments.get("tags"),
            limit=arguments.get("limit", 10),
        )
        results = mgr.search(sq)

        if not results:
            return [TextContent(type="text", text="No results found.")]

        lines = []
        for i, r in enumerate(results, 1):
            m = r.memory
            lines.append(
                f"## {i}. {m.auto_title()} (score: {r.score:.3f})\n"
                f"Tags: {', '.join(m.tags) or '—'} | Source: {m.source.value} | ID: {m.id[:8]}\n\n"
                f"{m.content[:500]}{'...' if len(m.content) > 500 else ''}\n"
            )
        return [TextContent(type="text", text="\n---\n".join(lines))]

    elif name == "brain_add":
        data = MemoryCreate(
            content=arguments["content"],
            title=arguments.get("title"),
            tags=arguments.get("tags", []),
            source=MemorySource.MCP,
            memory_type=MemoryType(arguments.get("memory_type", "note")),
        )
        memory = mgr.add(data)
        return [TextContent(
            type="text",
            text=f"Memory saved: {memory.auto_title()} (ID: {memory.id[:8]})",
        )]

    elif name == "brain_get":
        memory = mgr.get(arguments["memory_id"])
        if not memory:
            return [TextContent(type="text", text="Memory not found.")]
        return [TextContent(
            type="text",
            text=(
                f"# {memory.auto_title()}\n\n"
                f"Tags: {', '.join(memory.tags) or '—'}\n"
                f"Source: {memory.source.value}\n"
                f"Created: {memory.created_at.isoformat()}\n\n"
                f"{memory.content}"
            ),
        )]

    elif name == "brain_list_tags":
        tags = mgr.get_tags()
        if not tags:
            return [TextContent(type="text", text="No tags found.")]
        lines = [f"- **{tag}**: {count}" for tag, count in tags.items()]
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "brain_stats":
        s = mgr.stats()
        return [TextContent(type="text", text=json.dumps(s, indent=2))]

    elif name == "brain_ingest_url":
        ingestion = IngestionPipeline()
        data = ingestion.from_url(arguments["url"], arguments.get("tags"))
        memory = mgr.add(data)
        return [TextContent(
            type="text",
            text=f"URL ingested: {memory.auto_title()} (ID: {memory.id[:8]})",
        )]

    elif name == "brain_save_context":
        project = arguments.get("project") or _detect_project()
        parts = [f"# Session Context: {project}"]
        parts.append(f"Saved: {datetime.now(timezone.utc).isoformat()}")
        if arguments.get("goals"):
            parts.append(f"\n## Goals\n{arguments['goals']}")
        if arguments.get("completed"):
            parts.append(f"\n## Completed\n{arguments['completed']}")
        if arguments.get("in_progress"):
            parts.append(f"\n## In Progress\n{arguments['in_progress']}")
        if arguments.get("decisions"):
            parts.append(f"\n## Decisions\n{arguments['decisions']}")
        if arguments.get("context"):
            parts.append(f"\n## Context\n{arguments['context']}")

        content = "\n".join(parts)
        data = MemoryCreate(
            content=content,
            title=f"Session context: {project}",
            tags=[f"project:{project}", "session-context", "checkpoint"],
            source=MemorySource.MCP,
            memory_type=MemoryType.SESSION_CONTEXT,
        )
        memory = mgr.add(data)
        return [TextContent(
            type="text",
            text=f"Context checkpoint saved for '{project}' (ID: {memory.id[:8]}). "
            f"This context will persist across sessions and context compression.",
        )]

    elif name == "brain_recall_context":
        project = arguments.get("project") or _detect_project()
        extra_query = arguments.get("query", "")

        # Search for session context by project tag
        sq = SearchQuery(
            query=f"session context {project} goals progress decisions {extra_query}",
            tags=[f"project:{project}"],
            limit=5,
        )
        results = mgr.search(sq)

        if not results:
            # Fallback: search without tag filter in case tags were not exact
            sq = SearchQuery(
                query=f"session context {project} {extra_query}",
                limit=5,
            )
            results = mgr.search(sq)

        if not results:
            return [TextContent(
                type="text",
                text=f"No saved context found for project '{project}'. "
                f"This appears to be a new session. Use brain_save_context to create checkpoints.",
            )]

        lines = []
        for i, r in enumerate(results, 1):
            m = r.memory
            lines.append(
                f"--- Checkpoint {i} (score: {r.score:.3f}, {m.created_at.strftime('%Y-%m-%d %H:%M')}) ---\n"
                f"{m.content}\n"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "brain_list_recent":
        limit = arguments.get("limit", 10)
        tags = arguments.get("tags")
        memories = mgr.list_memories(limit=limit, tags=tags)

        if not memories:
            return [TextContent(type="text", text="No memories found.")]

        lines = []
        for m in memories:
            lines.append(
                f"- **{m.auto_title()}** [{m.memory_type.value}] "
                f"tags: {', '.join(m.tags) or '—'} | {m.created_at.strftime('%Y-%m-%d %H:%M')} | ID: {m.id[:8]}"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "brain_watch_start":
        import threading

        watcher = _get_watcher()
        if watcher._observers:
            return [TextContent(type="text", text="Watcher is already running.")]

        raw_paths = arguments.get("paths") or [os.getcwd()]
        scan = arguments.get("scan", True)
        watch_paths = [Path(p).expanduser().resolve() for p in raw_paths]
        valid = [p for p in watch_paths if p.is_dir()]
        if not valid:
            return [TextContent(type="text", text=f"No valid directories found: {raw_paths}")]

        if scan:
            for rp in valid:
                count = watcher.scan_directory(rp)
                logger.info("Scanned %s: %d files indexed", rp.name, count)

        watcher.watch(valid)

        stats = watcher.stats
        path_list = "\n".join(f"  - {p}" for p in valid)
        return [TextContent(
            type="text",
            text=(
                f"Watcher started on {len(valid)} directories:\n{path_list}\n"
                f"Initial scan: {stats['ingested']} ingested, "
                f"{stats['updated']} updated, {stats['skipped']} skipped."
            ),
        )]

    elif name == "brain_watch_stop":
        watcher = _get_watcher()
        if not watcher._observers:
            return [TextContent(type="text", text="Watcher is not running.")]
        stats = watcher.stats
        watcher.stop()
        return [TextContent(
            type="text",
            text=(
                f"Watcher stopped. Final stats: {stats['ingested']} ingested, "
                f"{stats['updated']} updated, {stats['skipped']} skipped, "
                f"{stats['errors']} errors."
            ),
        )]

    elif name == "brain_watch_status":
        watcher = _get_watcher()
        running = bool(watcher._observers)
        stats = watcher.stats
        watched = [str(obs._watch.path) for obs in watcher._observers] if running else []
        status_text = (
            f"**Watcher status**: {'🟢 running' if running else '⚪ stopped'}\n"
            f"**Watched paths**: {len(watched)}\n"
        )
        if watched:
            status_text += "\n".join(f"  - {p}" for p in watched) + "\n"
        status_text += (
            f"**Stats**: {stats['ingested']} ingested, {stats['updated']} updated, "
            f"{stats['skipped']} skipped, {stats['errors']} errors"
        )
        return [TextContent(type="text", text=status_text)]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def run_mcp_server() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    import asyncio
    asyncio.run(run_mcp_server())


if __name__ == "__main__":
    main()
