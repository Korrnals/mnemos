"""MCP server for Mnemos — exposes mnemos_* memory tools to Copilot/LLM agents.

Tools: mnemos_add (enforces Mnemos TagContract), mnemos_search, mnemos_recall,
mnemos_agent_recall (M3), mnemos_auto_collect_status (per-signal compaction
vector, M7), and others. Auto-collect driven by MNEMOS_AUTO_COLLECT env var.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mnemos.config import load_settings
from mnemos.models import (
    AgentRecallQuery,
    MemoryCreate,
    MemorySource,
    MemoryStatus,
    MemoryType,
    TagContractError,
    validate_tag_contract,
)

logger = logging.getLogger(__name__)

server = Server("mnemos")
_manager: Any = None  # MemoryManager — lazy init to avoid import-time side-effects

# ── Auto-collect mode ──────────────────────────────────────────────────────────
_auto_collect_state = {
    "enabled": os.environ.get("MNEMOS_AUTO_COLLECT", "").lower() in ("true", "1", "yes", "on"),
}

# ── Auto-checkpoint tracking ───────────────────────────────────────────────────
_checkpoint_tracker = {
    "calls_since_save": 0,
    "last_save_ts": 0.0,
}


def _remind_calls() -> int:
    return 6 if _auto_collect_state["enabled"] else 12


def _remind_secs() -> int:
    return 480 if _auto_collect_state["enabled"] else 900


def get_manager() -> Any:
    global _manager
    if _manager is None:
        from mnemos.manager import MemoryManager

        _manager = MemoryManager(load_settings())
    return _manager


def _detect_project() -> str:
    """Auto-detect project name from current working directory."""
    return Path(os.getcwd()).name


def _checkpoint_reminder() -> str | None:
    """Return a reminder string if it's time to save a checkpoint, else None."""
    calls = _checkpoint_tracker["calls_since_save"]
    elapsed = (
        time.monotonic() - _checkpoint_tracker["last_save_ts"]
        if _checkpoint_tracker["last_save_ts"]
        else 0.0
    )
    if calls >= _remind_calls() or (elapsed > _remind_secs() and calls > 0):
        return (
            f"\n\n⚠️ [mnemos] {calls} tool calls since last checkpoint "
            f"({int(elapsed)}s ago). Consider calling mnemos_save_context "
            f"to preserve your current progress."
        )
    return None


def _track_call(is_save: bool = False) -> None:
    if is_save:
        _checkpoint_tracker["calls_since_save"] = 0
        _checkpoint_tracker["last_save_ts"] = time.monotonic()
    else:
        _checkpoint_tracker["calls_since_save"] += 1


def _auto_collect_instructions(project: str) -> str:
    return (
        "\n\n---\n"
        "## 🔄 Auto-Collect Mode Active\n\n"
        "You MUST follow these rules for the entire session:\n\n"
        "1. **Session start**: You already called mnemos_recall_context (good). "
        "Review the context above and continue from where you left off.\n"
        "2. **Save checkpoints**: Call `mnemos_save_context` after completing meaningful work, "
        "before switching tasks, or when your context grows large.\n"
        "3. **Store knowledge**: Use `mnemos_add` to save any discoveries, patterns, decisions, "
        "architecture insights, gotchas, or reusable knowledge. Tag with "
        f"`project:{project}` and relevant topic tags.\n"
        "4. **Search first**: Before complex work, use `mnemos_search` to check if relevant "
        "context was stored in previous sessions.\n"
    )


# ── Tool listing ───────────────────────────────────────────────────────────────


# mcp SDK uses runtime decorators (Server.list_tools / Server.call_tool) that
# are not annotated in the upstream stub. mypy --strict flags them as untyped
# decorators/calls, but ONLY when the optional `mcp` extra is installed — so an
# inline `type: ignore[...]` would be "unused" in CI (which type-checks without
# mcp) and trip `warn_unused_ignores`. The relaxation is therefore scoped to
# this module via [[tool.mypy.overrides]] in pyproject.toml instead.
@server.list_tools()
async def list_tools() -> list[Tool]:
    _ac = _auto_collect_state["enabled"]

    _recall_desc = (
        (
            "🔄 [AUTO-COLLECT] MANDATORY: Call this at the START of EVERY conversation/session. "
            "Restores project context from long-term memory. Without this, you lose continuity. "
            "Also call after context window compression."
        )
        if _ac
        else (
            "Recall the latest session context for a project from long-term memory. "
            "Use at the START of every session, after context compression, "
            "or whenever you notice gaps in project state. "
            "Returns the most recent checkpoint with goals, progress, and decisions."
        )
    )

    _save_desc = (
        (
            "🔄 [AUTO-COLLECT] MANDATORY: Call this PROACTIVELY — after meaningful work, "
            "before ending a conversation, when context is large, or before switching tasks. "
            "Captures: goals, completed work, decisions, active files, architecture notes."
        )
        if _ac
        else (
            "Save current session context/checkpoint to long-term memory. "
            "Use PROACTIVELY to preserve: current goals, completed tasks, decisions made, "
            "active file paths, architecture notes. "
            "Call after completing significant work steps or before switching major tasks."
        )
    )

    _add_desc = (
        (
            "🔄 [AUTO-COLLECT] Proactively save discoveries, patterns, decisions, gotchas, "
            "and any reusable knowledge. Tags MUST include project:<slug>, agent:<slug>, "
            "and at least one mnemos:<subtype> tag."
        )
        if _ac
        else (
            "Add a new entry to long-term memory. "
            "Tags MUST include: project:<slug>, agent:<slug>, and mnemos:<subtype>. "
            "Valid mnemos subtypes: session, bug-pattern, learning, decision, rule, "
            "open-question, checkpoint, legacy."
        )
    )

    _search_desc = (
        (
            "🔄 [AUTO-COLLECT] Search long-term memory BEFORE doing complex work — "
            "check if relevant facts, decisions, or patterns were stored previously."
        )
        if _ac
        else (
            "Search long-term memory using semantic + full-text hybrid search (RRF). "
            "Only searches 'published' knowledge units by default. "
            "Add status filter to query raw/processing/processed entries."
        )
    )

    return [
        Tool(
            name="mnemos_search",
            description=_search_desc,
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags (optional)",
                    },
                    "project": {
                        "type": "string",
                        "description": "Restrict search to a project (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default: 10)",
                        "default": 10,
                    },
                    "include_raw": {
                        "type": "boolean",
                        "description": (
                            "When true, includes raw/processing entries in results "
                            "(default: false — only published/processed)."
                        ),
                        "default": False,
                    },
                    "status": {
                        "type": "string",
                        "enum": ["raw", "processing", "processed", "published", "archived"],
                        "description": (
                            "Filter by memory status (optional). Overrides include_raw."
                        ),
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="mnemos_add",
            description=_add_desc,
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Text content to remember"},
                    "title": {
                        "type": "string",
                        "description": "Short title (auto-generated if omitted)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Tags. REQUIRED: project:<slug>, agent:<slug>, mnemos:<subtype>. "
                            "Optional: severity:, stack:, applyTo:, source: prefixes."
                        ),
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["note", "fact", "snippet", "bookmark", "conversation"],
                        "default": "note",
                    },
                    "filter_profile": {
                        "type": "string",
                        "enum": ["log", "terminal", "code", "docs", "web", "default"],
                        "description": "Context Filter profile (M10). Auto-selected if omitted.",
                    },
                },
                "required": ["content", "tags"],
            },
        ),
        Tool(
            name="mnemos_filter",
            description=(
                "Run or refresh the context filter on an existing memory. "
                "Useful when auto_filter was off, or to re-filter with a different profile."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "ID of the memory to filter",
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["log", "terminal", "code", "docs", "web", "default"],
                        "description": "Context Filter profile (auto-selected if omitted)",
                    },
                    "budget": {
                        "type": "integer",
                        "description": "Token budget for truncation (optional)",
                    },
                },
                "required": ["memory_id"],
            },
        ),
        Tool(
            name="mnemos_agent_recall",
            description=(
                "Recall memories filtered by agent identity. "
                "Returns the most recent entries for a specific agent, "
                "optionally scoped to a project and/or a query. (M3)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Agent slug (e.g. 'cr-security-reviewer')",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project scope",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional FTS/vector query within agent scope",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Max entries to return",
                    },
                },
                "required": ["agent"],
            },
        ),
        Tool(
            name="mnemos_save_context",
            description=_save_desc,
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name (auto-detected from cwd if omitted)",
                    },
                    "goals": {"type": "string", "description": "Current session goals"},
                    "completed": {"type": "string", "description": "What has been completed"},
                    "in_progress": {"type": "string", "description": "What is in progress"},
                    "decisions": {
                        "type": "string",
                        "description": "Key technical decisions and rationale",
                    },
                    "context": {
                        "type": "string",
                        "description": "Other critical context (file paths, architecture, gotchas)",
                    },
                },
            },
        ),
        Tool(
            name="mnemos_recall_context",
            description=_recall_desc,
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name (auto-detected from cwd if omitted)",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional: specific aspect to focus on",
                    },
                },
            },
        ),
        Tool(
            name="mnemos_list_recent",
            description="List the most recent memory entries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags (optional)",
                    },
                    "project": {"type": "string", "description": "Filter by project"},
                },
            },
        ),
        Tool(
            name="mnemos_list_tags",
            description="List all tags in the memory with their counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="mnemos_tags_rename",
            description=(
                "Bulk rename tags matching from_prefix:<subtype> → "
                "to_prefix:<subtype> across existing memories. Safe: uses "
                "UPDATE (FTS5 stays consistent), dry_run=true by default, "
                "idempotent. Use to migrate gcw: → mnemos: tags."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "from_prefix": {
                        "type": "string",
                        "description": "Source prefix, e.g. 'gcw:'",
                    },
                    "to_prefix": {
                        "type": "string",
                        "description": "Target prefix, e.g. 'mnemos:'",
                    },
                    "subtypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional whitelist of subtypes to rename",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": True,
                        "description": "Preview without writing (default true)",
                    },
                    "project": {
                        "type": "string",
                        "description": "Scope to a project slug (optional)",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Scope to an agent slug (optional)",
                    },
                    "invalid_subtypes_to_legacy": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Rename invalid subtypes to <to_prefix>legacy instead of skipping them"
                        ),
                    },
                },
                "required": ["from_prefix", "to_prefix"],
            },
        ),
        Tool(
            name="mnemos_ingest_url",
            description="Fetch a web page, extract its content, and save to memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch and ingest"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags (must include project:, agent:, mnemos:)",
                    },
                },
                "required": ["url", "tags"],
            },
        ),
        Tool(
            name="mnemos_watch_start",
            description=(
                "Start watching directories for file changes and auto-index into memory. "
                "Runs in background."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Directories to watch (defaults to cwd)",
                    },
                    "scan": {"type": "boolean", "default": True},
                    "include_rules": {
                        "type": "boolean",
                        "default": False,
                        "description": "Also watch .github/instructions/*.instructions.md (M8)",
                    },
                },
            },
        ),
        Tool(
            name="mnemos_watch_stop",
            description="Stop the background file watcher.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="mnemos_watch_status",
            description="Report background watcher status.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="mnemos_auto_collect_status",
            description=(
                "Report current compaction-detection signal vector. "
                "Returns per-signal values + composite recommendation. (M7)"
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="mnemos_stats",
            description="Get Mnemos health statistics and memory counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="mnemos_reprocess",
            description=(
                "Manually trigger the knowledge pipeline to process "
                "raw/processing entries into published knowledge. "
                "Use when mnemos_stats shows a large queue_depth."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "agent": {"type": "string"},
                    "limit": {"type": "integer", "default": 100},
                },
            },
        ),
        Tool(
            name="mnemos_compress",
            description=(
                "Compress large content (tool output, logs, JSON) with ZERO data "
                "loss. The original is cached in SQLite keyed by its hash; the "
                "compressed output embeds a marker so the LLM can call "
                "mnemos_retrieve to fetch the full original back. 70-90% token "
                "reduction. Inspired by headroom's CCR (Apache 2.0)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Content to compress (>=500 chars to cache)",
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["log", "terminal", "code", "docs", "web", "default"],
                        "description": "Filter profile (auto-detected if omitted)",
                    },
                    "project": {
                        "type": "string",
                        "description": "Project slug to scope the cache entry (optional)",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="mnemos_retrieve",
            description=(
                "Retrieve the original uncompressed content for a CCR marker hash. "
                "If query is omitted: returns the full original. If query is "
                "provided: returns FTS5-ranked snippets from within the cached "
                "original. Use the hash from a [compressed: <hash> | ...] marker."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hash": {
                        "type": "string",
                        "description": "SHA-256 hash from a CCR marker",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional search query for snippet retrieval",
                    },
                    "snippet_count": {
                        "type": "integer",
                        "default": 5,
                        "description": "Number of snippets when query is provided",
                    },
                },
                "required": ["hash"],
            },
        ),
    ]


# ── Tool call handler ──────────────────────────────────────────────────────────


@server.call_tool()  # see module note on @server.list_tools / pyproject mypy override
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    _track_call(is_save=(name == "mnemos_save_context"))
    reminder = _checkpoint_reminder()

    try:
        result = await _dispatch(name, arguments)
    except TagContractError as exc:
        return [TextContent(type="text", text=f"❌ Tag contract violation:\n{exc}")]
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        return [TextContent(type="text", text=f"❌ Error: {exc}")]

    text = (
        result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
    )
    if reminder:
        text += reminder
    return [TextContent(type="text", text=text)]


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    mgr = get_manager()
    settings = mgr.settings

    # ── mnemos_add ──────────────────────────────────────────────────────────
    if name == "mnemos_add":
        raw_tags: list[str] = args.get("tags", [])
        # Enforce / patch TagContract
        tags = validate_tag_contract(
            raw_tags,
            strict=settings.mnemos.strict_tag_contract,
        )
        # Derive denormalised fields from validated tags
        project = next((t[len("project:") :] for t in tags if t.startswith("project:")), "")
        agent = next((t[len("agent:") :] for t in tags if t.startswith("agent:")), "")

        data = MemoryCreate(
            content=args["content"],
            title=args.get("title"),
            tags=tags,
            source=MemorySource.MCP,
            memory_type=MemoryType(args.get("memory_type", "note")),
            filter_profile=args.get("filter_profile"),
        )
        memory = mgr.add(data, project=project, agent=agent)
        # M10: report whether auto-filter ran and which profile was applied.
        # mgr.add() runs apply_context_filter internally when auto_filter is
        # enabled and reloads the memory, so filter_profile is populated on
        # success. On failure (non-fatal) filter_profile stays None.
        filtered = bool(
            settings.mnemos.auto_filter and memory.content and memory.filter_profile is not None
        )
        return {
            "id": memory.id,
            "title": memory.auto_title(),
            "status": memory.status,
            "filtered": filtered,
            "filter_profile": memory.filter_profile,
        }

    # ── mnemos_search ───────────────────────────────────────────────────────
    if name == "mnemos_search":
        status_str = args.get("status")
        status: MemoryStatus | None = None
        if status_str:
            try:
                status = MemoryStatus(status_str)
            except ValueError:
                valid = ", ".join(s.value for s in MemoryStatus)
                return f"❌ Invalid status '{status_str}'. Valid values: {valid}"
        results = mgr.search(
            query=args["query"],
            tags=args.get("tags"),
            project=args.get("project"),
            limit=args.get("limit", 10),
            include_raw=args.get("include_raw", False),
            status=status,
        )
        return [
            {
                "id": r.memory.id,
                "title": r.memory.auto_title(),
                "content": r.memory.effective_content(),
                "tags": r.memory.tags,
                "score": r.score,
                "search_type": r.search_type,
                "status": r.memory.status,
            }
            for r in results
        ]

    # ── mnemos_agent_recall (M3) ────────────────────────────────────────────
    if name == "mnemos_agent_recall":
        recall_query = AgentRecallQuery(
            agent=args["agent"],
            project=args.get("project"),
            query=args.get("query"),
            limit=args.get("limit", 20),
        )
        results = mgr.agent_recall(recall_query)
        return [
            {
                "id": r.memory.id,
                "title": r.memory.auto_title(),
                "content": r.memory.effective_content(),
                "tags": r.memory.tags,
                "created_at": r.memory.created_at.isoformat(),
                "status": r.memory.status,
            }
            for r in results
        ]

    # ── mnemos_save_context ─────────────────────────────────────────────────
    if name == "mnemos_save_context":
        project = args.get("project") or _detect_project()
        parts = [f"# Session checkpoint — {datetime.now(UTC).isoformat()}\n"]
        for field in ("goals", "completed", "in_progress", "decisions", "context"):
            if args.get(field):
                parts.append(f"## {field.replace('_', ' ').title()}\n{args[field]}\n")
        content = "\n".join(parts)
        tags = [f"project:{project}", "agent:user", "mnemos:checkpoint"]
        data = MemoryCreate(content=content, tags=tags, source=MemorySource.MCP)
        memory = mgr.add(data, project=project, agent="user")
        _track_call(is_save=True)
        instructions = _auto_collect_instructions(project) if _auto_collect_state["enabled"] else ""
        return f"✅ Context saved (id={memory.id}).{instructions}"

    # ── mnemos_recall_context ───────────────────────────────────────────────
    if name == "mnemos_recall_context":
        project = args.get("project") or _detect_project()
        memories = mgr.recall_context(project=project, query=args.get("query"), limit=5)
        if not memories:
            instructions = (
                _auto_collect_instructions(project) if _auto_collect_state["enabled"] else ""
            )
            return (
                f"No context found for project '{project}'. "
                f"Start by saving context with mnemos_save_context.{instructions}"
            )
        out = [f"# Context for project '{project}'\n"]
        for m in memories:
            out.append(f"---\n{m.effective_content()}\n")
        instructions = _auto_collect_instructions(project) if _auto_collect_state["enabled"] else ""
        return "\n".join(out) + instructions

    # ── mnemos_list_recent ──────────────────────────────────────────────────
    if name == "mnemos_list_recent":
        memories = mgr.list_recent(
            limit=args.get("limit", 10),
            tags=args.get("tags"),
            project=args.get("project"),
        )
        return [
            {
                "id": m.id,
                "title": m.auto_title(),
                "tags": m.tags,
                "status": m.status,
                "created_at": m.created_at.isoformat(),
            }
            for m in memories
        ]

    # ── mnemos_list_tags ────────────────────────────────────────────────────
    if name == "mnemos_list_tags":
        return mgr.list_tags()

    # ── mnemos_tags_rename ──────────────────────────────────────────────────
    if name == "mnemos_tags_rename":
        return mgr.tags_rename(
            from_prefix=args["from_prefix"],
            to_prefix=args["to_prefix"],
            subtypes=args.get("subtypes"),
            dry_run=args.get("dry_run", True),
            project=args.get("project"),
            agent=args.get("agent"),
            invalid_subtypes_to_legacy=args.get("invalid_subtypes_to_legacy", False),
        )

    # ── mnemos_stats ────────────────────────────────────────────────────────
    if name == "mnemos_stats":
        return mgr.stats()
    # ── mnemos_reprocess ─────────────────────────────────────────────────────
    if name == "mnemos_reprocess":
        _project = args.get("project")
        _agent = args.get("agent")
        _limit = int(args.get("limit", 100))
        return mgr.run_pipeline(project=_project, agent=_agent, limit=_limit)
    # ── mnemos_compress (P1-4 CCR) ───────────────────────────────────────────
    if name == "mnemos_compress":
        return mgr.compress_content(
            args["text"],
            profile=args.get("profile"),
            project=args.get("project", "") or "",
        )
    # ── mnemos_retrieve (P1-4 CCR) ───────────────────────────────────────────
    if name == "mnemos_retrieve":
        return mgr.retrieve_content(
            args["hash"],
            query=args.get("query"),
            snippet_count=args.get("snippet_count"),
        )
    # ── mnemos_filter (M10) ─────────────────────────────────────────────────
    if name == "mnemos_filter":
        memory_id = args["memory_id"]
        result = mgr.apply_context_filter(
            memory_id,
            profile=args.get("profile"),
            budget=args.get("budget"),
        )
        if result.get("status") == "error":
            return result
        return {
            "memory_id": memory_id,
            "profile": result["filter_profile"],
            "clean_content": result["clean_content"],
            "stats": result["stats"],
        }

    # ── mnemos_ingest_url ───────────────────────────────────────────────────
    if name == "mnemos_ingest_url":
        # Security: strip credentials from URL before storing (OWASP A02)
        import re as _re

        url = args["url"]
        url_clean = _re.sub(r"(https?://)([^@]*@)", r"\1", url)
        raw_tags = args.get("tags", [])
        tags = validate_tag_contract(
            raw_tags,
            strict=settings.mnemos.strict_tag_contract,
        )
        project = next((t[len("project:") :] for t in tags if t.startswith("project:")), "")
        agent = next((t[len("agent:") :] for t in tags if t.startswith("agent:")), "")
        memory = mgr.ingest_url(url_clean, tags=tags, project=project, agent=agent)
        return {"id": memory.id, "title": memory.auto_title(), "url": url_clean}

    # ── mnemos_watch_* ──────────────────────────────────────────────────────
    if name == "mnemos_watch_start":
        paths = args.get("paths") or [os.getcwd()]
        include_rules = args.get("include_rules", False)
        mgr.watch_start(paths=paths, scan=args.get("scan", True), include_rules=include_rules)
        return f"✅ Watcher started on {paths}" + (
            " (including .instructions.md rules)" if include_rules else ""
        )

    if name == "mnemos_watch_stop":
        mgr.watch_stop()
        return "✅ Watcher stopped."

    if name == "mnemos_watch_status":
        return mgr.watch_status()

    # ── mnemos_auto_collect_status (M7) ─────────────────────────────────────
    if name == "mnemos_auto_collect_status":
        calls = _checkpoint_tracker["calls_since_save"]
        elapsed = (
            time.monotonic() - _checkpoint_tracker["last_save_ts"]
            if _checkpoint_tracker["last_save_ts"]
            else 0.0
        )
        return {
            "auto_collect_enabled": _auto_collect_state["enabled"],
            "signals": {
                "call_counter": {
                    "calls_since_save": calls,
                    "threshold": _remind_calls(),
                    "triggered": calls >= _remind_calls(),
                },
                "elapsed_secs": {
                    "value": int(elapsed),
                    "threshold": _remind_secs(),
                    "triggered": elapsed > _remind_secs() and calls > 0,
                },
                # M7 additional signals (context-size, summary-marker, reference-drop)
                # are populated by the client plugin when it supplies those signals.
                "context_size_heuristic": {"value": None, "note": "populated by client (M7)"},
                "summary_marker_detected": {"value": None, "note": "populated by client (M7)"},
                "reference_drop_heuristic": {"value": None, "note": "populated by client (M7)"},
            },
            "recommendation": (
                "save_checkpoint"
                if (calls >= _remind_calls() or (elapsed > _remind_secs() and calls > 0))
                else "ok"
            ),
            "next_reminder_in_calls": max(0, _remind_calls() - calls),
        }

    return f"Unknown tool: {name}"


# ── Entry point ────────────────────────────────────────────────────────────────


async def main() -> None:
    """Run the Mnemos MCP server over stdio."""
    from mnemos.logging_setup import setup_logging

    settings = load_settings()
    setup_logging(settings)
    # Start the background processor so raw entries are automatically
    # clustered → synthesized → quality-gated → published.
    mgr = get_manager()
    mgr.start_background_processor()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        mgr.stop_background_processor()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
