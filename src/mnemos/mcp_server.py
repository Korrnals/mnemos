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
from mnemos.context_rewrite import ContextRewriteRateLimitError
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


# ── P1-7 Output token reduction: verbosity steering + effort routing ────────
# Inspired by headroom's output token reduction work. Original implementation.
# These are *hints* injected into tool result framing and passed through to
# the caller — they are not model config changes. New params are optional with
# defaults that preserve the exact pre-P1-7 behaviour (backward compatible).

_VERBOSITY_GUIDANCE: dict[str, str] = {
    "default": "",
    "terse": (
        "\n\n---\n*Output style: terse. Be brief. No preambles, no restated "
        "context, no ceremony. Lead with the result. Omit explanations the "
        "caller already has.*"
    ),
    "minimal": (
        "\n\n---\n*Output style: minimal. Facts only. No prose, no "
        "preambles, no framing. Return the data.*"
    ),
}

_EFFORT_GUIDANCE: dict[str, str] = {
    "low": "\n*Effort: low — routine step, minimal reasoning.*",
    "medium": "",
    "high": "\n*Effort: high — deliberate reasoning, verify before answering.*",
}

# Allowed value sets for validation. Kept in sync with the guidance dicts
# above. Used by _resolve_verbosity / _resolve_effort to detect caller typos
# (e.g. "verbose", "turbo") and fall back gracefully instead of silently
# coercing to an empty hint via a missing dict key.
_VALID_VERBOSITY: frozenset[str] = frozenset(_VERBOSITY_GUIDANCE.keys())
_VALID_EFFORT: frozenset[str] = frozenset(_EFFORT_GUIDANCE.keys())


def _resolve_verbosity(args: dict[str, Any], settings: Any) -> str:
    """Resolve the effective verbosity from args or config default.

    Invalid values (not in ``_VALID_VERBOSITY``) are logged at WARNING and
    fall back to the config default — graceful degradation, never raises.
    This prevents a caller typo (e.g. ``"verbose"``) from silently disabling
    steering via a missing dict key.
    """
    if not settings.output_style.enabled:
        return "default"
    raw = args.get("verbosity")
    if isinstance(raw, str):
        if raw in _VALID_VERBOSITY:
            return raw
        logger.warning(
            "Invalid verbosity %r (valid: %s); falling back to default.",
            raw,
            sorted(_VALID_VERBOSITY),
        )
        return str(settings.output_style.default_verbosity)
    return str(settings.output_style.default_verbosity)


def _resolve_effort(args: dict[str, Any], settings: Any) -> str:
    """Resolve the effective effort hint from args or config default.

    Invalid values (not in ``_VALID_EFFORT``) are logged at WARNING and
    fall back to the config default — graceful degradation, never raises.
    """
    if not settings.output_style.enabled:
        return "medium"
    raw = args.get("effort")
    if isinstance(raw, str):
        if raw in _VALID_EFFORT:
            return raw
        logger.warning(
            "Invalid effort %r (valid: %s); falling back to medium.",
            raw,
            sorted(_VALID_EFFORT),
        )
        return str(settings.output_style.default_effort)
    return str(settings.output_style.default_effort)


def _steering_suffix(args: dict[str, Any], settings: Any) -> str:
    """Build the verbosity + effort guidance suffix appended to tool output.

    Returns "" when verbosity is "default" and effort is "medium" (the
    no-op case), preserving the exact pre-P1-7 output for callers that do
    not pass the new params.
    """
    verbosity = _resolve_verbosity(args, settings)
    effort = _resolve_effort(args, settings)
    return _VERBOSITY_GUIDANCE.get(verbosity, "") + _EFFORT_GUIDANCE.get(effort, "")


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
            name="mnemos_tags",
            description=(
                "Bulk tag operations across memories: rename a prefix, "
                "remove tags, or add tags. Action-based dispatch — the "
                "grouped pilot tool (mnemos #97). action='rename' is the "
                "same as mnemos_tags_rename; 'remove' drops exact (or, "
                "with wildcard=true, prefix-matched) tags; 'add' appends "
                "tags to memories matching a project/agent filter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["rename", "remove", "add"],
                        "description": (
                            "rename: from_prefix->to_prefix "
                            "(use from_prefix/to_prefix). "
                            "remove: drop tags matching the tags list "
                            "(exact match by default). "
                            "add: append tags to memories matching the "
                            "project/agent filter."
                        ),
                    },
                    "from_prefix": {
                        "type": "string",
                        "description": "Source prefix for rename (e.g. 'gcw:')",
                    },
                    "to_prefix": {
                        "type": "string",
                        "description": "Target prefix for rename (e.g. 'mnemos:')",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Tags to remove or add (exact match). "
                            "Required for action='remove' and 'add'."
                        ),
                    },
                    "subtypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional whitelist filter (rename only)",
                    },
                    "wildcard": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Prefix match (remove) vs exact. rename is prefix-based by design."
                        ),
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
                            "rename: rename invalid subtypes to "
                            "<to_prefix>legacy instead of skipping them"
                        ),
                    },
                },
                "required": ["action"],
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
                "original. Use the hash from a [compressed: <hash> | ...] marker. "
                "Issued content is scanned for secrets: matched spans are "
                "redacted (<REDACTED:<pattern>>) in the response, which reports "
                "the count via 'redactions' (0 when clean); the stored original "
                "is preserved unchanged."
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
                    "project": {
                        "type": "string",
                        "description": (
                            "Optional project slug: scope the lookup to this "
                            "project's entries — a hash cached under another "
                            "project is reported as not found"
                        ),
                    },
                },
                "required": ["hash"],
            },
        ),
        Tool(
            name="mnemos_align_prefix",
            description=(
                "P1-5 CacheAligner — relocate dynamic content (timestamps, UUIDs, "
                "session ids, tokens) to the end of text so the prefix stays "
                "byte-identical across requests and provider KV caches "
                "(Anthropic cache_control, OpenAI prefix caching) hit. Inspired "
                "by headroom's CacheAligner (Apache 2.0). Original implementation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "System-prompt-like text to stabilize",
                    },
                    "profile": {
                        "type": "string",
                        "enum": ["code", "docs", "default"],
                        "description": (
                            "Filter profile toggling which dynamic kinds are "
                            "extracted. 'code' skips bare tokens (avoids mangling "
                            "long identifiers). Default extracts all kinds."
                        ),
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="mnemos_assemble_context",
            description=(
                "ADR-0017 D1 provider contract — assemble the model-facing "
                "context block for a pre-LLM-call injection. Fixed pipeline: "
                "hybrid RRF recall (published/processed only, the entry-"
                "invariant status gate) → optional CCR marker expansion → "
                "context filter → MANDATORY secret scan (redacted spans are "
                "counted per block; nothing enters the output unscanned) → "
                "CacheAligner → token budget. Every injected block carries a "
                "provenance line "
                "'[mnemos:<id> project=<slug> status=<status> retrieved=<iso>]'. "
                "mode: sync (default) / async (store the result, return a "
                "handle, fetch it on a later call via async_handle) / code / "
                "prose (filter recall candidates by stored content type). "
                "Returns the assembled text, per-block provenance + redaction "
                "counts, and token stats."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session": {
                        "type": "string",
                        "description": (
                            "Caller's session identifier (echoed in the result; "
                            "identifies the assembly, not the memories)."
                        ),
                    },
                    "project": {
                        "type": "string",
                        "description": "Project slug scoping recall and CCR redemption.",
                    },
                    "file": {
                        "type": "string",
                        "description": (
                            "Optional file path: contributes recall query terms "
                            "and pins applyTo-scoped rule memories to the top."
                        ),
                    },
                    "budget": {
                        "type": "integer",
                        "default": 2048,
                        "description": "Token budget for the assembled block.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["sync", "async", "code", "prose"],
                        "default": "sync",
                        "description": (
                            "sync = return the assembled block now (default); "
                            "async = return a handle, fetch via async_handle; "
                            "code/prose = sync delivery + filter recall "
                            "candidates by stored content type."
                        ),
                    },
                    "expand_ccr": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Enable the optional CCR stage: expand inline "
                            "[compressed: <hash> | ...] markers found in "
                            "recalled content via project-scoped retrieval, "
                            "budget-aware (originals that would not fit stay "
                            "compressed)."
                        ),
                    },
                    "async_handle": {
                        "type": "string",
                        "description": (
                            "Fetch (and pop) a result stored by a previous mode='async' call."
                        ),
                    },
                },
                "required": ["session", "project"],
            },
        ),
        Tool(
            name="mnemos_context_rewrite",
            description=(
                "ADR-0018 on_context_rewrite lifecycle event — the harness "
                "reports that it REWROTE a block of its working context: the "
                "original is stored to long-term memory losslessly (normal "
                "knowledge pipeline: enters raw, context-reachable only after "
                "the pipeline advances it to processed/published; write-path "
                "secret scan auto-tags mnemos:no-federate on a hit). "
                "IDEMPOTENT: the same event re-delivered performs no "
                "duplicate writes (content-addressed event key over "
                "project/agent/session/supersedes/content; the advisory diff "
                "is excluded — it is not load-bearing). VERSION-LESS: no "
                "ordering promise, no version chains — replacement lineage "
                "is a supersedes edge (optional 'supersedes' = memory id of "
                "the replaced block). Rehydrate goes through the EXISTING "
                "scanned/gated channels (mnemos_retrieve / "
                "mnemos_assemble_context). Set include_marker=true to also "
                "get the CCR compress marker for the original to keep in the "
                "window."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "Original text of the replaced context block — "
                            "the source of truth, stored unchanged."
                        ),
                    },
                    "project": {
                        "type": "string",
                        "description": "Project slug (tag project:<slug>).",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Agent slug (tag agent:<slug>).",
                    },
                    "session": {
                        "type": "string",
                        "description": (
                            "Optional session id — provenance metadata and "
                            "part of the idempotency key."
                        ),
                    },
                    "supersedes": {
                        "type": "string",
                        "description": (
                            "Optional memory id of the block being replaced — "
                            "creates the supersedes edge new → old."
                        ),
                    },
                    "diff": {
                        "type": "string",
                        "description": (
                            "Optional advisory was→becomes diff — stored as "
                            "metadata only, never load-bearing."
                        ),
                    },
                    "include_marker": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Also return the CCR compress marker for the "
                            "original (the caller keeps it in its window)."
                        ),
                    },
                },
                "required": ["content", "project", "agent"],
            },
        ),
        Tool(
            name="mnemos_export",
            description=(
                "Export memories to a file (JSON or SQLite snapshot). Writes the "
                "result to disk and returns metadata only (path, memory_count, "
                "format, bytes) — the content is NOT returned inline (stdio "
                "transport limitation). Thin wrapper over the CLI export logic. "
                "Inherits #86 federation defence: excludes mnemos:no-federate "
                "records and redacts detected secrets in passing records. "
                "When encrypt=true the passphrase is read from the "
                "MNEMOS_EXPORT_PASSPHRASE environment variable — never pass the "
                "passphrase value in the tool arguments (it would appear in logs)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["json", "sqlite"],
                        "default": "json",
                        "description": (
                            "json = metadata-only export with filters; "
                            "sqlite = full tar.gz snapshot (filters ignored)."
                        ),
                    },
                    "compress": {
                        "type": "string",
                        "enum": ["none", "gzip"],
                        "default": "none",
                        "description": "Compression mode (zstd is CLI-only).",
                    },
                    "project": {
                        "type": "string",
                        "description": "Filter by project slug (json only).",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Filter by agent slug (json only).",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["raw", "processing", "processed", "published", "archived"],
                        "description": "Filter by memory status (json only).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags (json only).",
                    },
                    "since": {
                        "type": "string",
                        "description": (
                            "ISO-8601 timestamp — only memories created on or "
                            "after this date (json only)."
                        ),
                    },
                    "until": {
                        "type": "string",
                        "description": (
                            "ISO-8601 timestamp — only memories created before "
                            "this date (json only)."
                        ),
                    },
                    "encrypt": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "When true, encrypt the output with the passphrase "
                            "from the MNEMOS_EXPORT_PASSPHRASE env var."
                        ),
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Absolute path where the export file is written.",
                    },
                },
                "required": ["output_path"],
            },
        ),
        Tool(
            name="mnemos_import",
            description=(
                "Import memories from an export file (merge or restore mode). "
                "Thin wrapper over the CLI import logic. Inherits #86 import "
                "validation: rejects schema drift, oversized content, invalid "
                "tags; logs prompt-injection patterns at WARNING without blocking. "
                "Restore mode is destructive (wipes all existing data) and "
                "requires confirm=true. For encrypted inputs the passphrase is "
                "read from the environment variable NAMED by passphrase_env "
                "(never the value itself — passing the value in arguments would "
                "leak it into logs)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "description": "Absolute path to the export file to import.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["merge", "restore"],
                        "default": "merge",
                        "description": (
                            "merge = insert new / skip-or-overwrite existing; "
                            "restore = wipe all then import (requires confirm=true)."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "default": False,
                        "description": "Overwrite existing memories (merge mode only).",
                    },
                    "confirm": {
                        "type": "boolean",
                        "default": False,
                        "description": "Required true for restore mode (hard gate).",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Validate without writing; returns a validation report.",
                    },
                    "passphrase_env": {
                        "type": "string",
                        "description": (
                            "Name of the environment variable holding the "
                            "decryption passphrase (NOT the value)."
                        ),
                    },
                },
                "required": ["source_path"],
            },
        ),
        Tool(
            name="mnemos_workflow",
            description=(
                "Workflow lifecycle management for a memory (mnemos #96). "
                "Separates mutable workflow state (open/in-progress/blocked/"
                "resolved/done/withdrawn) from append-only tag classification. "
                "Action-based dispatch — same pattern as mnemos_tags. "
                "'set' transitions the status through a server-enforced state "
                "machine (blocked->done is forbidden; terminal states are final), "
                "acquires/releases a lock, and records every transition in an "
                "audit log. 'get' returns the current status + lock owner. "
                "'history' returns the audit trail. Guardrails: stale-lock "
                "auto-release (>24h), idempotent transitions (no-op on same "
                "status), force-unlock (requires reason), per-memory rate limit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "get", "history"],
                        "description": (
                            "set: transition status (requires memory_id, to, actor). "
                            "get: return current status + lock. "
                            "history: return the audit trail."
                        ),
                    },
                    "memory_id": {
                        "type": "string",
                        "description": "Target memory id.",
                    },
                    "to": {
                        "type": "string",
                        "enum": [
                            "open",
                            "in-progress",
                            "blocked",
                            "resolved",
                            "done",
                            "withdrawn",
                        ],
                        "description": (
                            "Target status (action='set' only). blocked->done is "
                            "forbidden — a blocked memory must resolve first."
                        ),
                    },
                    "actor": {
                        "type": "string",
                        "description": (
                            "Free-form actor id (Phase 1 weak identity — NO "
                            "authn/authz). Required for action='set'."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "default": "",
                        "description": "Human-readable reason. Required when force=true.",
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "Override a lock held by another actor (requires reason).",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Max history rows (action='history' only).",
                    },
                },
                "required": ["action", "memory_id"],
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


# ── #84 federation export/import handlers ────────────────────────────────────
#
# Thin wrappers over cli/export.py::run_export and cli/import_.py::run_import.
# Both underlying functions are already clean callables (no Typer ctx), so no
# refactoring was required. The wrappers:
#   * validate the MCP arguments,
#   * read passphrases from the environment (never from args — per
#     sensitive-data.instructions.md args appear in MCP logs),
#   * write the export to disk and return metadata only (no inline content —
#     stdio transport cannot carry binary or large JSON inline),
#   * enforce the restore-mode confirm gate.


def _handle_export(mgr: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch helper for the ``mnemos_export`` tool."""
    from mnemos.cli.export import CompressMode, ExportFilter, ExportFormat, run_export

    output_path_str = args.get("output_path")
    if not output_path_str or not isinstance(output_path_str, str):
        return {"error": "output_path is required and must be a string"}
    output_path = Path(output_path_str)
    if not output_path.is_absolute():
        return {"error": f"output_path must be absolute, got: {output_path}"}

    fmt_str = args.get("format", "json")
    try:
        fmt = ExportFormat(fmt_str)
    except ValueError as exc:
        return {"error": f"Invalid format '{fmt_str}': {exc}"}

    compress_str = args.get("compress", "none")
    try:
        compress = CompressMode(compress_str)
    except ValueError as exc:
        return {"error": f"Invalid compress '{compress_str}': {exc}"}

    # ── Filters (json only; sqlite ignores them) ──────────────────────────
    status_str = args.get("status")
    status: MemoryStatus | None = None
    if status_str:
        try:
            status = MemoryStatus(status_str)
        except ValueError as exc:
            return {"error": f"Invalid status '{status_str}': {exc}"}

    since_raw = args.get("since")
    until_raw = args.get("until")
    since_dt = _parse_iso_arg(since_raw) if since_raw else None
    until_dt = _parse_iso_arg(until_raw) if until_raw else None
    if since_raw and since_dt is None:
        return {"error": f"Invalid since timestamp: {since_raw}"}
    if until_raw and until_dt is None:
        return {"error": f"Invalid until timestamp: {until_raw}"}

    filt = ExportFilter(
        project=args.get("project"),
        agent=args.get("agent"),
        status=status,
        tags=args.get("tags"),
        since=since_dt,
        until=until_dt,
    )

    # ── Encryption: passphrase from env, never from args ───────────────────
    encrypt = bool(args.get("encrypt", False))
    passphrase: str | None = None
    if encrypt:
        passphrase = os.environ.get("MNEMOS_EXPORT_PASSPHRASE")
        if not passphrase:
            return {
                "error": (
                    "encrypt=true but MNEMOS_EXPORT_PASSPHRASE environment "
                    "variable is not set or empty. Set it before calling "
                    "mnemos_export — the passphrase value must never appear "
                    "in tool arguments."
                )
            }

    try:
        result = run_export(
            mgr,
            fmt=fmt,
            output=output_path,
            compress=compress,
            encrypt=encrypt,
            passphrase=passphrase,
            filt=filt,
        )
    except Exception as exc:  # surface a clean error to the caller
        return {"error": f"Export failed: {exc}"}

    return {
        "path": str(result.path),
        "memory_count": result.memory_count,
        "format": result.format.value,
        "compress": result.compress.value,
        "encrypted": result.encrypted,
        "bytes": result.bytes_written,
        "warnings": list(result.warnings),
    }


def _handle_import(mgr: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch helper for the ``mnemos_import`` tool."""
    from mnemos.cli.import_ import ImportMode, run_import

    source_path_str = args.get("source_path")
    if not source_path_str or not isinstance(source_path_str, str):
        return {"error": "source_path is required and must be a string"}
    source_path = Path(source_path_str)
    if not source_path.is_absolute():
        return {"error": f"source_path must be absolute, got: {source_path}"}

    mode = args.get("mode", "merge")
    if mode not in (ImportMode.MERGE, ImportMode.RESTORE):
        return {"error": f"Invalid mode '{mode}': must be 'merge' or 'restore'"}

    confirm = bool(args.get("confirm", False))
    # Hard gate: restore is destructive — refuse without explicit confirmation.
    if mode == ImportMode.RESTORE and not confirm:
        return {
            "error": (
                "Restore mode wipes all existing memories, vectors, and projects. "
                "Set confirm=true to acknowledge this and proceed."
            )
        }

    passphrase_env = args.get("passphrase_env")
    passphrase: str | None = None
    if passphrase_env:
        if not isinstance(passphrase_env, str) or not passphrase_env.isidentifier():
            return {
                "error": (
                    f"passphrase_env must be a valid environment variable name, "
                    f"got: {passphrase_env!r}"
                )
            }
        passphrase = os.environ.get(passphrase_env)
        if passphrase is None:
            return {
                "error": (
                    f"Environment variable {passphrase_env!r} (named by "
                    f"passphrase_env) is not set. The decryption passphrase "
                    f"must live in the environment, never in the tool arguments."
                )
            }

    try:
        result = run_import(
            mgr,
            source_path,
            mode=mode,
            overwrite=bool(args.get("overwrite", False)),
            confirm=confirm,
            dry_run=bool(args.get("dry_run", False)),
            passphrase=passphrase,
        )
    except Exception as exc:  # surface a clean error to the caller
        return {"error": f"Import failed: {exc}"}

    return {
        "mode": result.mode,
        "dry_run": result.dry_run,
        "imported": result.imported,
        "skipped": result.skipped,
        "updated": result.updated,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
        "format_version": result.format_version,
        "mnemos_version": result.mnemos_version,
    }


def _parse_iso_arg(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp argument into an aware datetime.

    Returns ``None`` on a parse failure so the caller can emit a clean
    error instead of raising inside the dispatch path.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


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
        result = {
            "id": memory.id,
            "title": memory.auto_title(),
            "status": memory.status,
            "filtered": filtered,
            "filter_profile": memory.filter_profile,
        }
        _suffix = _steering_suffix(args, settings)
        if _suffix:
            result["_output_style_hint"] = _suffix
        return result

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
        # ADR-0018 P1-b (M1 + review F1/F3): scan-at-issuance — BOTH echoed
        # strings (content and title; auto_title() derives from raw content)
        # are scanned/redacted per item; refuse mode drops the item
        # entirely (fail-closed); the drop log carries the memory id.
        _search_results = []
        for r in results:
            scan = mgr.scan_issuance_item(
                r.memory.effective_content(),
                title=r.memory.auto_title(),
                context=f"mcp:mnemos_search:{r.memory.id}",
            )
            if scan.refused:
                continue
            item = {
                "id": r.memory.id,
                "title": scan.title,
                "content": scan.content,
                "tags": r.memory.tags,
                "score": r.score,
                "search_type": r.search_type,
                "status": r.memory.status,
                "redactions": scan.redactions,
            }
            if scan.redactions:
                item["redacted_patterns"] = scan.redacted_patterns
            _search_results.append(item)
        _suffix = _steering_suffix(args, settings)
        if _suffix:
            return {"results": _search_results, "_output_style_hint": _suffix}
        return _search_results

    # ── mnemos_agent_recall (M3) ────────────────────────────────────────────
    if name == "mnemos_agent_recall":
        recall_query = AgentRecallQuery(
            agent=args["agent"],
            project=args.get("project"),
            query=args.get("query"),
            limit=args.get("limit", 20),
        )
        results = mgr.agent_recall(recall_query)
        # ADR-0018 P1-b (M1 + review F1/F3): scan-at-issuance on BOTH echoed
        # strings (content and title) — same policy as mnemos_search.
        recalled = []
        for r in results:
            scan = mgr.scan_issuance_item(
                r.memory.effective_content(),
                title=r.memory.auto_title(),
                context=f"mcp:mnemos_agent_recall:{r.memory.id}",
            )
            if scan.refused:
                continue
            item = {
                "id": r.memory.id,
                "title": scan.title,
                "content": scan.content,
                "tags": r.memory.tags,
                "created_at": r.memory.created_at.isoformat(),
                "status": r.memory.status,
                "redactions": scan.redactions,
            }
            if scan.redactions:
                item["redacted_patterns"] = scan.redacted_patterns
            recalled.append(item)
        return recalled

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
                + _steering_suffix(args, settings)
            )
        out = [f"# Context for project '{project}'\n"]
        for m in memories:
            # ADR-0018 P1-b (M1 + review F3): scan-at-issuance on the echoed
            # content (this channel renders no titles); refuse mode drops
            # the memory's section, logged with the memory id.
            scan = mgr.scan_issuance(
                m.effective_content(),
                context=f"mcp:mnemos_recall_context:{m.id}",
            )
            if scan.refused:
                continue
            out.append(f"---\n{scan.text}\n")
        instructions = _auto_collect_instructions(project) if _auto_collect_state["enabled"] else ""
        return "\n".join(out) + instructions + _steering_suffix(args, settings)

    # ── mnemos_list_recent ──────────────────────────────────────────────────
    if name == "mnemos_list_recent":
        memories = mgr.list_recent(
            limit=args.get("limit", 10),
            tags=args.get("tags"),
            project=args.get("project"),
        )
        # ADR-0018 P1-b review (F1): this channel echoes titles
        # (auto_title() derives from raw content) and no content — the
        # title is scanned; refuse mode drops the row.
        listed = []
        for m in memories:
            scan = mgr.scan_issuance_item(
                None, title=m.auto_title(), context=f"mcp:mnemos_list_recent:{m.id}"
            )
            if scan.refused:
                continue
            item = {
                "id": m.id,
                "title": scan.title,
                "tags": m.tags,
                "status": m.status,
                "created_at": m.created_at.isoformat(),
                "redactions": scan.redactions,
            }
            if scan.redactions:
                item["redacted_patterns"] = scan.redacted_patterns
            listed.append(item)
        return listed

    # ── mnemos_list_tags ────────────────────────────────────────────────────
    if name == "mnemos_list_tags":
        return mgr.list_tags()

    # ── mnemos_tags (grouped: rename/remove/add) — pilot (mnemos #97) ───────
    # Also serves as the backing dispatch for the legacy mnemos_tags_rename
    # tool (non-breaking alias). When the LLM calls mnemos_tags_rename we
    # inject action="rename" and fall through to the same handler.
    if name in ("mnemos_tags", "mnemos_tags_rename"):
        action = args.get("action")
        if name == "mnemos_tags_rename":
            # Alias: legacy rename tool routes to the grouped rename path.
            # Force action='rename' AFTER merging args so a stray ``action``
            # key in a legacy rename call cannot leak through to the dispatcher.
            args = dict(args)
            args["action"] = "rename"
            action = "rename"
        if action == "rename":
            if not args.get("from_prefix") or not args.get("to_prefix"):
                return {
                    "error": "action='rename' requires 'from_prefix' and 'to_prefix' "
                    "(both must end with ':', e.g. 'gcw:' -> 'mnemos:')"
                }
            return mgr.tags_rename(
                from_prefix=args["from_prefix"],
                to_prefix=args["to_prefix"],
                subtypes=args.get("subtypes"),
                dry_run=args.get("dry_run", True),
                project=args.get("project"),
                agent=args.get("agent"),
                invalid_subtypes_to_legacy=args.get("invalid_subtypes_to_legacy", False),
            )
        if action == "remove":
            return mgr.tags_remove(
                tags=args.get("tags", []),
                wildcard=args.get("wildcard", False),
                dry_run=args.get("dry_run", True),
                project=args.get("project"),
                agent=args.get("agent"),
            )
        if action == "add":
            return mgr.tags_add(
                tags=args.get("tags", []),
                dry_run=args.get("dry_run", True),
                project=args.get("project"),
                agent=args.get("agent"),
            )
        return {"error": f"unknown action {action!r}. Valid actions: 'rename', 'remove', 'add'"}

    # ── mnemos_workflow (grouped: set/get/history) — mnemos #96 ────────────
    # Thin wrapper over MemoryManager.workflow_set / workflow_get /
    # workflow_history. The state machine + 5 guardrails are enforced
    # server-side in the manager, so this dispatch only translates
    # ValueError (guardrail violation) into a clean error dict — mirroring
    # how mnemos_tags surfaces validation problems.
    if name == "mnemos_workflow":
        action = args.get("action")
        memory_id = args.get("memory_id")
        if not memory_id:
            return {"error": "memory_id is required (the target memory id)"}
        if action == "set":
            to = args.get("to")
            actor = args.get("actor")
            if not to:
                return {"error": "action='set' requires 'to' (target workflow status)"}
            if not actor:
                return {"error": "action='set' requires 'actor' (free-form actor id)"}
            try:
                return mgr.workflow_set(
                    memory_id,
                    to,
                    actor=actor,
                    reason=args.get("reason", ""),
                    force=bool(args.get("force", False)),
                )
            except ValueError as exc:
                # Guardrail / state-machine violation — surface verbatim.
                return {"error": str(exc)}
        if action == "get":
            result = mgr.workflow_get(memory_id)
            if result is None:
                return {"error": f"memory {memory_id!r} not found"}
            return result
        if action == "history":
            return {
                "memory_id": memory_id,
                "history": mgr.workflow_history(memory_id, limit=int(args.get("limit", 50))),
            }
        return {"error": f"unknown action {action!r}. Valid actions: 'set', 'get', 'history'"}

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
        # ADR-0018 P1-a: optional project scopes the cache lookup — a hash
        # cached under another project is reported as not found.
        return mgr.retrieve_content(
            args["hash"],
            query=args.get("query"),
            snippet_count=args.get("snippet_count"),
            project=args.get("project"),
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

    # ── mnemos_align_prefix (P1-5 CacheAligner) ──────────────────────────────
    if name == "mnemos_align_prefix":
        return mgr.align_prefix(args["text"], profile=args.get("profile"))

    # ── mnemos_assemble_context (ADR-0017 D1, #125) ─────────────────────────
    if name == "mnemos_assemble_context":
        # Locals are suffixed: `project` is already bound as `str` by the
        # save/recall handlers above in this long dispatch function.
        asm_session = args.get("session")
        asm_project = args.get("project")
        if not isinstance(asm_session, str) or not asm_session.strip():
            return {"error": "session is required and must be a non-empty string"}
        if not isinstance(asm_project, str) or not asm_project.strip():
            return {"error": "project is required and must be a non-empty string"}
        asm_file = args.get("file")
        if asm_file is not None and not isinstance(asm_file, str):
            # Review F3: without the guard a non-str file reaches the
            # pipeline and dies as a TypeError echoing caller input.
            return {"error": "file must be a string when provided"}
        try:
            return mgr.assemble_context(
                session=asm_session,
                project=asm_project,
                file=asm_file,
                budget=int(args.get("budget", 2048)),
                mode=str(args.get("mode", "sync")),
                expand_ccr=bool(args.get("expand_ccr", False)),
                async_handle=args.get("async_handle"),
            )
        except ValueError as exc:
            # Boundary validation (mode/budget/async_handle incl. the
            # session-bound handle check) — surface a clean error dict
            # instead of the generic exception path.
            return {"error": str(exc)}

    # ── mnemos_context_rewrite (ADR-0018, #125 Wave 2) ──────────────────────
    if name == "mnemos_context_rewrite":
        cr_content = args.get("content")
        cr_project = args.get("project")
        cr_agent = args.get("agent")
        if not isinstance(cr_content, str) or not cr_content.strip():
            return {"error": "content is required and must be a non-empty string"}
        if not isinstance(cr_project, str) or not cr_project.strip():
            return {"error": "project is required and must be a non-empty string"}
        if not isinstance(cr_agent, str) or not cr_agent.strip():
            return {"error": "agent is required and must be a non-empty string"}
        cr_session = args.get("session")
        cr_supersedes = args.get("supersedes")
        cr_diff = args.get("diff")
        optional_strs = (("session", cr_session), ("supersedes", cr_supersedes), ("diff", cr_diff))
        for label, value in optional_strs:
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return {"error": f"{label} must be a non-empty string when provided"}
        try:
            return mgr.context_rewrite(
                content=cr_content,
                project=cr_project,
                agent=cr_agent,
                session=cr_session,
                supersedes=cr_supersedes,
                diff=cr_diff,
                include_marker=bool(args.get("include_marker", False)),
            )
        except ContextRewriteRateLimitError as exc:
            # W2 review F1: backpressure, not validation — a distinct,
            # machine-checkable flag so the harness can back off.
            return {"error": str(exc), "rate_limited": True}
        except ValueError as exc:
            # Boundary validation + size caps + tag-contract violations
            # (strict mode) + supersedes not found in project — clean
            # error dict, no trace echo.
            return {"error": str(exc)}

    # ── mnemos_export (#84 federation export) ──────────────────────────────
    if name == "mnemos_export":
        return _handle_export(mgr, args)

    # ── mnemos_import (#84 federation import) ──────────────────────────────
    if name == "mnemos_import":
        return _handle_import(mgr, args)

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
    # Start the background secrets scanner (Layer 2 defence-in-depth,
    # #89). No-op when ``scanner.enabled`` is False. Runs on its own
    # daemon thread so it never blocks the MCP stdio loop.
    from mnemos.scanner_runtime import get_scanner

    scanner = get_scanner(mgr)
    scanner.start()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        scanner.stop()
        mgr.stop_background_processor()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
