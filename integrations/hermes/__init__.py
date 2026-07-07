"""Hermes Agent MemoryProvider plugin for Mnemos.

Connects Mnemos (standalone memory & knowledge server) to Hermes Agent's
pluggable memory system. The Hermes ``MemoryProvider`` ABC is the interface;
this plugin talks to Mnemos via its HTTP API (``mnemos serve``).

Installation::

    # 1. Start Mnemos server
    mnemos serve --host 127.0.0.1 --port 8000 &

    # 2. Copy this plugin into Hermes plugins dir
    cp -r integrations/hermes ~/.hermes/plugins/mnemos

    # 3. Activate via interactive wizard (recommended)
    hermes memory setup
    # Select "mnemos" from the list, configure base_url etc.

    # OR activate manually
    hermes config set memory.provider mnemos
    # /restart in gateway or restart CLI

Config (in $HERMES_HOME/config.yaml under ``memory.mnemos``):

    memory:
      provider: mnemos
      mnemos:
        base_url: "http://127.0.0.1:8000"     # Mnemos HTTP API
        api_key: ""                            # Bearer token if auth enabled
        project: "hermes"                      # default project tag
        agent: "hermes-default"                # default agent tag
        auto_sync: true                        # mirror built-in memory writes
        prefetch_limit: 5                      # max results in prefetch
        sync_interval: 10                      # sync every Nth turn

Or via env vars::

    MNEMOS_BASE_URL       — HTTP API base (default: http://127.0.0.1:8000)
    MNEMOS_API_KEY        — Bearer token (default: empty)
    MNEMOS_PROJECT        — default project slug (default: hermes)
    MNEMOS_AGENT          — default agent slug (default: hermes-default)
    MNEMOS_AUTO_SYNC      — mirror builtin writes (default: true)
    MNEMOS_PREFETCH_LIMIT — prefetch result count (default: 5)
    MNEMOS_SYNC_INTERVAL  — sync every Nth turn (default: 10)

HTTP endpoints used (confirmed from Mnemos source):

    GET  /health              → {status: ok}
    POST /memories            → create memory (201, returns full Memory object)
    GET  /memories            → list recent (query: status, project, limit)
    GET  /memories/{id}       → read one
    POST /search              → hybrid search (body: query, tags, project, limit, include_raw)
    GET  /recall/agent/{name} → agent recall (query: project, q, limit)
    GET  /tags                → list tags with counts
    GET  /metrics             → stats/metrics
    POST /process             → knowledge pipeline
    POST /publish/{id}        → publish memory
    POST /context/save        → save session checkpoint (NEW)
    POST /context/recall      → recall session context (NEW)
    POST /compress            → CCR compression (NEW)
    POST /retrieve            → CCR retrieval (NEW)
    GET  /auto-collect        → auto-collect status (NEW)
    POST /ingest-url          → fetch and save web page (NEW)
    POST /watch/start         → start file watcher (NEW)
    POST /watch/stop          → stop file watcher (NEW)
    GET  /watch/status        → watcher status (NEW)

Tools exposed (all HTTP-backed, no more simulations):
    mnemos_search             — POST /search
    mnemos_add                — POST /memories
    mnemos_recall_context     — POST /context/recall (was: simulated)
    mnemos_save_context       — POST /context/save (was: simulated)
    mnemos_agent_recall       — GET /recall/agent/{name}
    mnemos_list_recent        — GET /memories
    mnemos_list_tags          — GET /tags
    mnemos_stats              — GET /metrics
    mnemos_auto_collect_status — GET /auto-collect (was: synthetic)
    mnemos_compress           — POST /compress (NEW, was MCP-only)
    mnemos_retrieve           — POST /retrieve (NEW, was MCP-only)
    mnemos_ingest_url         — POST /ingest-url (NEW, was MCP-only)
    mnemos_watch_start        — POST /watch/start (NEW, was MCP-only)
    mnemos_watch_stop         — POST /watch/stop (NEW, was MCP-only)
    mnemos_watch_status       — GET /watch/status (NEW, was MCP-only)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ── Circuit breaker ───────────────────────────────────────────────────────────
# After this many consecutive failures, pause API calls for the cooldown
# period to avoid hammering a down or misbehaving server.
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120

# ── Significance threshold for sync_turn ──────────────────────────────────────
# Only sync turns where the user message exceeds this many characters,
# or every Nth turn (sync_interval). This honors Mnemos' "write sparingly"
# philosophy and avoids flooding memory with trivial exchanges.
_SYNC_MIN_USER_CHARS = 50


# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load config from env vars, with config.yaml ``plugins.mnemos`` overrides.

    Environment variables provide defaults; the ``plugins.mnemos`` section of
    ``config.yaml`` (if present) overrides individual keys. This avoids silent
    failures when the YAML file exists but is missing fields the user set via
    env vars or ``.env``.
    """
    config: dict[str, Any] = {
        "base_url": os.environ.get("MNEMOS_BASE_URL", "http://127.0.0.1:8000"),
        "api_key": os.environ.get("MNEMOS_API_KEY", ""),
        "project": os.environ.get("MNEMOS_PROJECT", "hermes"),
        "agent": os.environ.get("MNEMOS_AGENT", "hermes-default"),
        "auto_sync": os.environ.get("MNEMOS_AUTO_SYNC", "true").lower()
        in ("true", "1", "yes", "on"),
        "prefetch_limit": int(os.environ.get("MNEMOS_PREFETCH_LIMIT", "5")),
        "sync_interval": int(os.environ.get("MNEMOS_SYNC_INTERVAL", "10")),
    }

    try:
        from hermes_cli.config import cfg_get, load_config

        raw = load_config()

        # Read from both plugins.mnemos (legacy/save_config) and
        # memory.mnemos (where `hermes memory setup` writes). The
        # memory.mnemos section takes precedence because it is what
        # the interactive wizard writes.
        plugins_cfg = cfg_get(raw, "plugins", "mnemos") or {}
        memory_cfg = cfg_get(raw, "memory", "mnemos") or {}
        merged: dict[str, Any] = {}
        for k, v in plugins_cfg.items():
            if v is not None and v != "":
                merged[k] = v
        for k, v in memory_cfg.items():
            if v is not None and v != "":
                merged[k] = v

        for k, v in merged.items():
            if v is not None and v != "":
                # Coerce known-integer keys
                if k in ("prefetch_limit", "sync_interval"):
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        continue
                # Coerce auto_sync string → bool
                if k == "auto_sync" and isinstance(v, str):
                    v = v.lower() in ("true", "1", "yes", "on")
                config[k] = v
    except Exception:
        pass

    return config


# ── HTTP client helpers ───────────────────────────────────────────────────────

def _post_json(url: str, body: dict, api_key: str = "", timeout: float = 10.0) -> Any:
    """POST JSON to the Mnemos API and return the parsed response.

    Raises ``HTTPError``/``URLError`` on network failures so callers can
    record circuit-breaker failures uniformly.
    """
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def _get_json(url: str, api_key: str = "", timeout: float = 10.0) -> Any:
    """GET from the Mnemos API and return the parsed response."""
    req = Request(url, method="GET")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


# ── Tool schemas (OpenAI function-calling format) ─────────────────────────────

MNEMOS_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "mnemos_search",
    "description": (
        "Search Mnemos memory store using hybrid vector + FTS5 full-text "
        "search. Use before architectural decisions, before web searches, "
        "and when resuming a topic — the answer may already be in memory.\n\n"
        "Returns: list of {id, title, content, tags, status, score, "
        "search_type}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query.",
            },
            "project": {
                "type": "string",
                "description": "Project slug to scope the search (optional).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    'Tag filters, e.g. ["gcw:decision", "gcw:learning"].'
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 10, max: 50).",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}

MNEMOS_ADD_SCHEMA: dict[str, Any] = {
    "name": "mnemos_add",
    "description": (
        "Add a memory entry to Mnemos. Tag contract is mandatory: "
        "exactly one project:<slug>, one agent:<slug>, and at least one "
        "gcw:<subtype>. Write what you would want to read back in 30 days. "
        "One idea per entry.\n\n"
        "gcw subtypes: session, checkpoint, bug-pattern, learning, "
        "decision, rule, open-question, legacy."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Markdown body of the memory entry.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    'Required tags: ["project:<slug>", "agent:<slug>", '
                    '"gcw:<subtype>"]. Optional: severity:, stack:, '
                    "source:, applyTo:, domain:."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short title (optional, auto-generated if omitted).",
            },
            "memory_type": {
                "type": "string",
                "description": (
                    "note | fact | snippet | bookmark | conversation | "
                    "session_context"
                ),
                "default": "note",
            },
        },
        "required": ["content", "tags"],
    },
}

MNEMOS_RECALL_CONTEXT_SCHEMA: dict[str, Any] = {
    "name": "mnemos_recall_context",
    "description": (
        "Recall the most recent session checkpoint for a project — the "
        "saved context (goals, progress, decisions) from the last "
        "save_context call. Use at session start before reading project "
        "files, and after context compression to recover state.\n\n"
        "Returns: the latest checkpoint entry or a 'no context found' "
        "message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project slug to recall context for.",
            },
            "query": {
                "type": "string",
                "description": "Optional focus aspect to rank checkpoints by relevance instead of recency.",
            },
            "limit": {
                "type": "integer",
                "description": "Max checkpoints to return (default: 1).",
                "default": 1,
            },
        },
        "required": ["project"],
    },
}

MNEMOS_SAVE_CONTEXT_SCHEMA: dict[str, Any] = {
    "name": "mnemos_save_context",
    "description": (
        "Save a session checkpoint to Mnemos — structured context "
        "capturing current goals, completed work, in-progress items, "
        "decisions, and free-form context. Tagged as a gcw:checkpoint "
        "for later recall via mnemos_recall_context.\n\n"
        "Use at meaningful milestones: end of a work session, before "
        "context compression, or when pivoting topics. Write sparingly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project slug for this checkpoint.",
            },
            "goals": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Current goals/objectives.",
            },
            "completed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Completed items since last checkpoint.",
            },
            "in_progress": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Items currently in progress.",
            },
            "decisions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key decisions made.",
            },
            "context": {
                "type": "string",
                "description": "Free-form context notes.",
            },
        },
        "required": ["project"],
    },
}

MNEMOS_AGENT_RECALL_SCHEMA: dict[str, Any] = {
    "name": "mnemos_agent_recall",
    "description": (
        "Recall agent-scoped context — entries authored by a specific "
        "agent. Use when resuming work as a specific agent to get your "
        "own prior findings and context.\n\n"
        "Returns: list of {id, title, content, tags, created_at}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Agent slug to recall entries for.",
            },
            "project": {
                "type": "string",
                "description": "Optional project scope.",
            },
            "query": {
                "type": "string",
                "description": "Optional focus query for semantic search.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 20).",
                "default": 20,
            },
        },
        "required": ["agent"],
    },
}

MNEMOS_LIST_RECENT_SCHEMA: dict[str, Any] = {
    "name": "mnemos_list_recent",
    "description": (
        "List recent memories from Mnemos. Useful for browsing what has "
        "been stored recently, optionally filtered by status or project.\n\n"
        "Returns: list of {id, title, tags, status, created_at}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project slug to filter by (optional).",
            },
            "status": {
                "type": "string",
                "description": "Filter by status: draft|published|all (default: all).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 20, max: 100).",
                "default": 20,
            },
        },
        "required": [],
    },
}

MNEMOS_LIST_TAGS_SCHEMA: dict[str, Any] = {
    "name": "mnemos_list_tags",
    "description": (
        "List all tags in the Mnemos store with their entry counts. "
        "Use to discover what categories of memory exist and their "
        "relative volume.\n\n"
        "Returns: list of {tag, count}."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

MNEMOS_STATS_SCHEMA: dict[str, Any] = {
    "name": "mnemos_stats",
    "description": (
        "Get Mnemos store statistics — total memories, status "
        "breakdown, tag counts, and storage metrics.\n\n"
        "Returns: JSON object with stats fields."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

MNEMOS_AUTO_COLLECT_STATUS_SCHEMA: dict[str, Any] = {
    "name": "mnemos_auto_collect_status",
    "description": (
        "Get the Mnemos auto-collect compaction signal vector — "
        "tool-call count since last save_context, elapsed seconds, "
        "and a recommendation on whether a checkpoint is warranted.\n\n"
        "Backed by GET /auto-collect on the Mnemos server, which tracks "
        "in-process signals across all HTTP and MCP calls.\n\n"
        "Returns: {auto_collect_enabled, signals, recommendation, "
        "next_reminder_in_calls}."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


# ── New tool schemas (formerly MCP-only, now HTTP-backed) ─────────────────────

MNEMOS_COMPRESS_SCHEMA: dict[str, Any] = {
    "name": "mnemos_compress",
    "description": (
        "Compress large content (tool output, logs, JSON) with zero data "
        "loss via CCR (Compress-Cache-Retrieve). The original is cached "
        "keyed by SHA-256 hash; a short parseable marker is embedded so "
        "the LLM can call mnemos_retrieve to fetch the full original on "
        "demand. Achieves 70–90% token reduction on typical logs.\n\n"
        "Content shorter than ~500 chars is returned as-is.\n\n"
        "Returns: {compressed_text, hash, original_size, compressed_size, "
        "reduction_pct, marker, cached, profile}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Content to compress. ≥500 chars to cache.",
            },
            "profile": {
                "type": "string",
                "description": (
                    "Filter profile: log | terminal | code | docs | web | "
                    "default. Auto-detected if omitted."
                ),
            },
            "project": {
                "type": "string",
                "description": "Project slug to scope the cache entry.",
            },
        },
        "required": ["text"],
    },
}

MNEMOS_RETRIEVE_SCHEMA: dict[str, Any] = {
    "name": "mnemos_retrieve",
    "description": (
        "Retrieve the original uncompressed content for a CCR marker "
        "hash. If query is omitted, returns the full original. If query "
        "is provided, returns FTS5-ranked snippets from within the cached "
        "original — useful when the original is large and only a few "
        "lines are relevant.\n\n"
        "Returns: full retrieval {hash, found, original, size_bytes, "
        "retrieval_count} or snippet retrieval {hash, found, query, "
        "snippets, retrieval_count}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hash": {
                "type": "string",
                "description": "SHA-256 hash from a [compressed: ...] marker.",
            },
            "query": {
                "type": "string",
                "description": "Search query for snippet retrieval (optional).",
            },
            "snippet_count": {
                "type": "integer",
                "description": "Number of snippets when query is provided (default: 5).",
            },
        },
        "required": ["hash"],
    },
}

MNEMOS_INGEST_URL_SCHEMA: dict[str, Any] = {
    "name": "mnemos_ingest_url",
    "description": (
        "Fetch a web page, extract its main content (via trafilatura), "
        "and save it as a memory. Credentials embedded in the URL are "
        "stripped before storage (OWASP A02). Tags follow the same M2 "
        "contract as mnemos_add.\n\n"
        "Returns: {id, title, url}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP/HTTPS URL to fetch.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    'Required tags: ["project:<slug>", "agent:<slug>", '
                    '"gcw:<subtype>"].'
                ),
            },
        },
        "required": ["url", "tags"],
    },
}

MNEMOS_WATCH_START_SCHEMA: dict[str, Any] = {
    "name": "mnemos_watch_start",
    "description": (
        "Start a background file watcher. New and modified files under "
        "the watched paths are auto-indexed into Mnemos. When paths is "
        "empty, the current working directory is watched. When "
        "include_rules is true, *.instructions.md rule files are also "
        "ingested.\n\n"
        "Returns: {status, paths, scan, include_rules}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Directories to watch (default: [cwd]).",
            },
            "scan": {
                "type": "boolean",
                "description": "Run an initial scan to catch up on existing files (default: true).",
            },
            "include_rules": {
                "type": "boolean",
                "description": "Also watch .instructions.md rule files (default: false).",
            },
        },
        "required": [],
    },
}

MNEMOS_WATCH_STOP_SCHEMA: dict[str, Any] = {
    "name": "mnemos_watch_stop",
    "description": (
        "Stop the background file watcher. Idempotent — returns "
        "{\"status\": \"stopped\"} whether or not a watcher was running."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

MNEMOS_WATCH_STATUS_SCHEMA: dict[str, Any] = {
    "name": "mnemos_watch_status",
    "description": (
        "Report the current state of the background file watcher — "
        "running flag, watched paths, queue/index counts, and "
        "include_rules setting.\n\n"
        "Returns: {running, paths, files_queued, files_indexed, include_rules}."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


# ── Provider ──────────────────────────────────────────────────────────────────

class MnemosMemoryProvider(MemoryProvider):
    """Mnemos memory provider — talks to Mnemos HTTP API.

    Architecture::

        Hermes MemoryManager
            ↓ MemoryProvider ABC
            ↓
        MnemosMemoryProvider
            ↓ urllib (no external deps)
            ↓
        Mnemos HTTP API (mnemos serve)
            ↓
        MemoryManager → SQLite + vectors + Obsidian vault

    Key hooks used:

    - ``prefetch()``:           mnemos /search before each turn → context injection
    - ``sync_turn()``:          mnemos /memories after significant turns → auto-save
    - ``on_session_end()``:     extract key facts → single gcw:session entry
    - ``on_memory_write()``:    mirror builtin memory writes → Mnemos
    - ``on_pre_compress()``:    extract facts before context compression
    - ``on_session_switch()``:  reset per-session counters on /reset, /new
    - ``get_tool_schemas()``:   expose mnemos_* tools to the model
    - ``handle_tool_call()``:   proxy tool calls to HTTP API
    """

    def __init__(self, config: dict | None = None):
        self._config = config or _load_config()
        self._base_url = self._config["base_url"].rstrip("/")
        self._api_key = self._config.get("api_key", "")
        self._project = self._config.get("project", "hermes")
        self._agent = self._config.get("agent", "hermes-default")
        self._auto_sync = self._config.get("auto_sync", True)
        self._prefetch_limit = int(self._config.get("prefetch_limit", 5))
        self._sync_interval = int(self._config.get("sync_interval", 10))

        self._session_id = ""
        self._hermes_home = ""
        self._platform = "cli"
        self._agent_identity = ""
        self._agent_workspace = "hermes"
        self._agent_context = "primary"

        # Background threads
        self._prefetch_thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None
        self._session_end_thread: threading.Thread | None = None
        self._prefetch_lock = threading.Lock()
        self._prefetch_result = ""

        # Per-session state
        self._turn_counter = 0
        self._tool_call_counter = 0
        self._last_checkpoint_time = 0.0

        # Circuit breaker
        self._failures = 0
        self._breaker_until = 0.0
        self._breaker_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "mnemos"

    # -- Circuit breaker ───────────────────────────────────────────────────

    def _is_breaker_open(self) -> bool:
        """Return True if the circuit breaker is tripped (too many failures)."""
        with self._breaker_lock:
            if self._failures >= _BREAKER_THRESHOLD:
                if time.monotonic() < self._breaker_until:
                    return True
                # Cooldown expired — reset and allow a retry
                self._failures = 0
                self._breaker_until = 0.0
            return False

    def _record_failure(self) -> None:
        with self._breaker_lock:
            self._failures += 1
            if self._failures >= _BREAKER_THRESHOLD:
                self._breaker_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
                logger.warning(
                    "Mnemos circuit breaker opened after %d failures — "
                    "pausing API calls for %ds",
                    self._failures,
                    _BREAKER_COOLDOWN_SECS,
                )

    def _record_success(self) -> None:
        with self._breaker_lock:
            self._failures = 0

    # -- Core lifecycle ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if Mnemos HTTP API is reachable.

        Makes a lightweight ``GET /health`` call. Should NOT be called on
        every turn — only during agent init to decide whether to activate
        the provider.
        """
        try:
            health_url = f"{self._base_url}/health"
            _get_json(health_url, api_key=self._api_key, timeout=3.0)
            return True
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", "")
        self._platform = kwargs.get("platform", "cli")
        self._agent_identity = kwargs.get("agent_identity", "default")
        self._agent_workspace = kwargs.get("agent_workspace", "hermes")
        self._agent_context = kwargs.get("agent_context", "primary")

        # Use agent_identity for agent tag if available and not default
        if self._agent_identity and self._agent_identity != "default":
            self._agent = self._agent_identity

        # Reset per-session counters
        self._turn_counter = 0
        self._tool_call_counter = 0
        self._last_checkpoint_time = time.time()

        logger.info(
            "Mnemos memory provider initialized: session=%s, platform=%s, "
            "agent=%s, project=%s, base_url=%s, sync_interval=%d",
            self._session_id,
            self._platform,
            self._agent,
            self._project,
            self._base_url,
            self._sync_interval,
        )

    def system_prompt_block(self) -> str:
        """Static text for the system prompt."""
        return (
            "# Mnemos Memory\n"
            "Long-term memory store is active (Mnemos). Use mnemos_search "
            "before architectural decisions and web searches. Use mnemos_add "
            "to persist non-obvious learnings, decisions, and bug-patterns. "
            "Use mnemos_recall_context at session start to recover prior "
            "context. Use mnemos_save_context at meaningful milestones to "
            "checkpoint session state. Use mnemos_agent_recall to recover "
            "your own prior findings. Use mnemos_list_recent, "
            "mnemos_list_tags, and mnemos_stats to inspect the store. "
            "Use mnemos_auto_collect_status to check if a checkpoint is "
            "warranted. Use mnemos_compress to shrink large tool outputs "
            "(logs, JSON) losslessly and mnemos_retrieve to fetch the "
            "original back via its hash marker. Use mnemos_ingest_url to "
            "save a web page as a memory. Use mnemos_watch_start / "
            "mnemos_watch_stop / mnemos_watch_status to manage the "
            "background file watcher.\n"
            "Tag contract: project:<slug> + agent:<slug> + gcw:<subtype> "
            "(session|checkpoint|bug-pattern|learning|decision|rule|"
            "open-question|legacy). Search first, write sparingly, never "
            "block on memory failure."
        )

    # -- Prefetch ──────────────────────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return cached prefetch result (populated by background thread)."""
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
            return result

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue background recall for the next turn."""
        if self._is_breaker_open() or not query:
            return

        def _run() -> None:
            try:
                body = {
                    "query": query,
                    "project": self._project,
                    "limit": self._prefetch_limit,
                }
                results = _post_json(
                    f"{self._base_url}/search",
                    body,
                    api_key=self._api_key,
                    timeout=5.0,
                )
                # /search may return a list or {"results": [...]}
                if isinstance(results, dict):
                    results = results.get("results", [])
                if results:
                    lines = []
                    for r in results:
                        title = r.get("title", "untitled")
                        content = r.get("content", "")[:200]
                        tags = ", ".join(r.get("tags", []))
                        lines.append(f"- {title} [{tags}]: {content}")
                    with self._prefetch_lock:
                        self._prefetch_result = "\n".join(lines)
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mnemos prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="mnemos-prefetch"
        )
        self._prefetch_thread.start()

    # -- Sync turn ─────────────────────────────────────────────────────────

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist a completed turn to Mnemos (non-blocking).

        Honors Mnemos' "write sparingly" philosophy: only sync turns that
        are significant. A turn is significant if either:

        - The user message exceeds ``_SYNC_MIN_USER_CHARS`` (50 chars), OR
        - This is every Nth turn where N = ``sync_interval`` (default 10).

        This avoids flooding memory with trivial exchanges while still
        capturing meaningful interactions at a reasonable cadence. The
        real session-level summary happens in ``on_session_end()``.
        """
        if not self._auto_sync or self._is_breaker_open():
            return

        # Skip non-primary contexts (cron, flush) — their system prompts
        # would corrupt user representations.
        if self._agent_context not in ("primary", ""):
            return

        self._turn_counter += 1
        is_significant = (
            len(user_content) > _SYNC_MIN_USER_CHARS
            or self._turn_counter % self._sync_interval == 0
        )
        if not is_significant:
            return

        sid = session_id or self._session_id

        def _sync() -> None:
            try:
                content = (
                    f"## User\n{user_content[:1000]}\n\n"
                    f"## Assistant\n{assistant_content[:1000]}"
                )
                body = {
                    "content": content,
                    "tags": [
                        f"project:{self._project}",
                        f"agent:{self._agent}",
                        "gcw:session",
                    ],
                    "source": "mcp",
                    "memory_type": "conversation",
                    "metadata": {
                        "session_id": sid,
                        "platform": self._platform,
                        "turn": self._turn_counter,
                    },
                }
                _post_json(
                    f"{self._base_url}/memories",
                    body,
                    api_key=self._api_key,
                    timeout=10.0,
                )
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.debug("Mnemos sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="mnemos-sync"
        )
        self._sync_thread.start()

    # -- Tools ─────────────────────────────────────────────────────────────

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return all HTTP-backed tool schemas (15 tools).

        All tools are now backed by direct Mnemos HTTP endpoints — no
        simulations remain. See the module docstring for the endpoint
        mapping.
        """
        return [
            MNEMOS_SEARCH_SCHEMA,
            MNEMOS_ADD_SCHEMA,
            MNEMOS_RECALL_CONTEXT_SCHEMA,
            MNEMOS_SAVE_CONTEXT_SCHEMA,
            MNEMOS_AGENT_RECALL_SCHEMA,
            MNEMOS_LIST_RECENT_SCHEMA,
            MNEMOS_LIST_TAGS_SCHEMA,
            MNEMOS_STATS_SCHEMA,
            MNEMOS_AUTO_COLLECT_STATUS_SCHEMA,
            MNEMOS_COMPRESS_SCHEMA,
            MNEMOS_RETRIEVE_SCHEMA,
            MNEMOS_INGEST_URL_SCHEMA,
            MNEMOS_WATCH_START_SCHEMA,
            MNEMOS_WATCH_STOP_SCHEMA,
            MNEMOS_WATCH_STATUS_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        """Dispatch a tool call to the appropriate HTTP API handler."""
        # Track tool calls for auto_collect_status
        self._tool_call_counter += 1

        if self._is_breaker_open():
            return json.dumps({
                "error": (
                    "Mnemos API temporarily unavailable (circuit breaker "
                    "open). Will retry automatically."
                )
            })

        try:
            if tool_name == "mnemos_search":
                return self._handle_search(args)
            elif tool_name == "mnemos_add":
                return self._handle_add(args)
            elif tool_name == "mnemos_recall_context":
                return self._handle_recall_context(args)
            elif tool_name == "mnemos_save_context":
                return self._handle_save_context(args)
            elif tool_name == "mnemos_agent_recall":
                return self._handle_agent_recall(args)
            elif tool_name == "mnemos_list_recent":
                return self._handle_list_recent(args)
            elif tool_name == "mnemos_list_tags":
                return self._handle_list_tags(args)
            elif tool_name == "mnemos_stats":
                return self._handle_stats(args)
            elif tool_name == "mnemos_auto_collect_status":
                return self._handle_auto_collect_status(args)
            elif tool_name == "mnemos_compress":
                return self._handle_compress(args)
            elif tool_name == "mnemos_retrieve":
                return self._handle_retrieve(args)
            elif tool_name == "mnemos_ingest_url":
                return self._handle_ingest_url(args)
            elif tool_name == "mnemos_watch_start":
                return self._handle_watch_start(args)
            elif tool_name == "mnemos_watch_stop":
                return self._handle_watch_stop(args)
            elif tool_name == "mnemos_watch_status":
                return self._handle_watch_status(args)
            else:
                return tool_error(f"Unknown tool: {tool_name}")
        except HTTPError as e:
            self._record_failure()
            return tool_error(f"Mnemos API error {e.code}: {e.reason}")
        except URLError as e:
            self._record_failure()
            return tool_error(f"Mnemos unreachable: {e.reason}")
        except Exception as e:
            self._record_failure()
            return tool_error(f"Mnemos tool error: {e}")

    # -- Tool handlers ─────────────────────────────────────────────────────

    def _handle_search(self, args: dict) -> str:
        """POST /search — hybrid vector + FTS5 search."""
        query = args.get("query", "")
        if not query:
            return tool_error("Missing required parameter: query")

        body: dict[str, Any] = {
            "query": query,
            "limit": min(int(args.get("limit", 10)), 50),
        }
        project = args.get("project", self._project)
        if project:
            body["project"] = project
        tags = args.get("tags")
        if tags:
            body["tags"] = tags

        results = _post_json(
            f"{self._base_url}/search",
            body,
            api_key=self._api_key,
        )
        self._record_success()

        # Normalize: API may return a bare list or {"results": [...]}
        if isinstance(results, dict):
            results = results.get("results", [])

        if not results:
            return json.dumps({"result": "No relevant memories found."})

        items = [
            {
                "id": r.get("id"),
                "title": r.get("title", "untitled"),
                "content": r.get("content", "")[:500],
                "tags": r.get("tags", []),
                "score": r.get("score", 0),
                "search_type": r.get("search_type"),
            }
            for r in results
        ]
        return json.dumps({"results": items, "count": len(items)})

    def _handle_add(self, args: dict) -> str:
        """POST /memories — create a new memory entry."""
        content = args.get("content", "")
        tags = args.get("tags", [])
        if not content:
            return tool_error("Missing required parameter: content")
        if not tags:
            return tool_error(
                "Missing required parameter: tags. "
                "Required: project:<slug>, agent:<slug>, gcw:<subtype>"
            )

        body: dict[str, Any] = {
            "content": content,
            "tags": tags,
            "source": "mcp",
        }
        title = args.get("title")
        if title:
            body["title"] = title
        memory_type = args.get("memory_type", "note")
        if memory_type:
            body["memory_type"] = memory_type

        result = _post_json(
            f"{self._base_url}/memories",
            body,
            api_key=self._api_key,
        )
        self._record_success()

        # BUGFIX: the API returns field "title", not "auto_title".
        # Previously this read result.get("auto_title", ...) which always
        # fell through to the default. Now we read the actual title field.
        return json.dumps({
            "result": "Memory stored.",
            "id": result.get("id"),
            "title": result.get("title", title or "auto-generated"),
            "tags": result.get("tags", tags),
            "status": result.get("status", "draft"),
        })

    def _handle_recall_context(self, args: dict) -> str:
        """POST /context/recall — recall the latest session checkpoint(s).

        Calls the real Mnemos ``/context/recall`` endpoint, which returns
        structured checkpoint entries created via ``/context/save``. The
        response contains a ``checkpoints`` array of {id, title, content,
        tags, created_at} objects.
        """
        project = args.get("project", self._project)
        if not project:
            return tool_error("Missing required parameter: project")
        limit = min(int(args.get("limit", 1)), 10)
        query = args.get("query", "")

        body: dict[str, Any] = {
            "project": project,
            "limit": limit,
        }
        if query:
            body["query"] = query

        resp = _post_json(
            f"{self._base_url}/context/recall",
            body,
            api_key=self._api_key,
        )
        self._record_success()

        # Normalize: API returns {checkpoints: [...], message?}
        if isinstance(resp, dict):
            checkpoints = resp.get("checkpoints", [])
            message = resp.get("message")
        else:
            checkpoints = resp
            message = None

        if not checkpoints:
            return json.dumps({
                "result": "No prior checkpoint found for this project.",
                "project": project,
            })

        items = [
            {
                "id": r.get("id"),
                "title": r.get("title", "untitled"),
                "content": r.get("content", ""),
                "tags": r.get("tags", []),
                "created_at": r.get("created_at"),
            }
            for r in checkpoints
        ]
        return json.dumps({
            "results": items,
            "count": len(items),
            "project": project,
            "message": message,
        })

    def _handle_save_context(self, args: dict) -> str:
        """POST /context/save — save a structured session checkpoint.

        Calls the real Mnemos ``/context/save`` endpoint, which stores a
        structured checkpoint (goals, completed, in_progress, decisions,
        free-form context) keyed to the project. Returns {status, id,
        title}.
        """
        project = args.get("project", self._project)
        if not project:
            return tool_error("Missing required parameter: project")

        body: dict[str, Any] = {"project": project}
        # All fields are optional on the API side; only include those
        # that were supplied so the API can apply its own defaults.
        for field in ("goals", "completed", "in_progress", "decisions"):
            if field in args and args[field] is not None:
                body[field] = args[field]
        if args.get("context"):
            body["context"] = args["context"]

        result = _post_json(
            f"{self._base_url}/context/save",
            body,
            api_key=self._api_key,
        )
        self._record_success()

        # Reset the auto-collect counter since we just checkpointed
        self._tool_call_counter = 0
        self._last_checkpoint_time = time.time()

        # API returns {status, id, title}
        return json.dumps({
            "result": "Checkpoint saved.",
            "status": result.get("status", "saved"),
            "id": result.get("id"),
            "title": result.get("title", f"Checkpoint: {project}"),
            "project": project,
        })

    def _handle_agent_recall(self, args: dict) -> str:
        """GET /recall/agent/{name} — agent-scoped recall."""
        agent = args.get("agent", self._agent)
        project = args.get("project")
        query = args.get("query")
        limit = min(int(args.get("limit", 20)), 100)

        url = f"{self._base_url}/recall/agent/{quote_plus(agent)}?limit={limit}"
        if project:
            url += f"&project={quote_plus(project)}"
        if query:
            url += f"&q={quote_plus(query)}"

        results = _get_json(url, api_key=self._api_key)
        self._record_success()

        if isinstance(results, dict):
            results = results.get("results", [])

        if not results:
            return json.dumps({
                "result": "No agent-scoped context found.",
                "agent": agent,
            })

        items = [
            {
                "id": r.get("id"),
                "title": r.get("title", "untitled"),
                "content": r.get("content", "")[:500],
                "tags": r.get("tags", []),
                "created_at": r.get("created_at"),
            }
            for r in results
        ]
        return json.dumps({
            "results": items,
            "count": len(items),
            "agent": agent,
        })

    def _handle_list_recent(self, args: dict) -> str:
        """GET /memories — list recent memories."""
        project = args.get("project")
        status = args.get("status")
        limit = min(int(args.get("limit", 20)), 100)

        url = f"{self._base_url}/memories?limit={limit}"
        if project:
            url += f"&project={quote_plus(project)}"
        if status and status != "all":
            url += f"&status={quote_plus(status)}"

        results = _get_json(url, api_key=self._api_key)
        self._record_success()

        if isinstance(results, dict):
            results = results.get("results", results.get("memories", []))

        if not results:
            return json.dumps({"result": "No recent memories found."})

        items = [
            {
                "id": r.get("id"),
                "title": r.get("title", "untitled"),
                "tags": r.get("tags", []),
                "status": r.get("status", "draft"),
                "created_at": r.get("created_at"),
            }
            for r in results
        ]
        return json.dumps({"results": items, "count": len(items)})

    def _handle_list_tags(self, args: dict) -> str:
        """GET /tags — list all tags with counts."""
        results = _get_json(
            f"{self._base_url}/tags",
            api_key=self._api_key,
        )
        self._record_success()

        if isinstance(results, dict):
            # May be wrapped or be the tag dict itself
            if "tags" in results:
                results = results["tags"]
            elif not results:
                return json.dumps({"result": "No tags found."})
            else:
                # {tag: count} format → normalize to list
                results = [
                    {"tag": k, "count": v} for k, v in results.items()
                ]

        if not results:
            return json.dumps({"result": "No tags found."})

        return json.dumps({"results": results, "count": len(results)})

    def _handle_stats(self, args: dict) -> str:
        """GET /metrics — store statistics."""
        results = _get_json(
            f"{self._base_url}/metrics",
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps({"results": results})

    def _handle_auto_collect_status(self, args: dict) -> str:
        """GET /auto-collect — real auto-collect signal vector.

        Calls the Mnemos ``/auto-collect`` endpoint, which tracks
        in-process signals across all HTTP and MCP calls and returns
        the full signal vector: tool-call count, elapsed time, entropy,
        topic drift, and a recommendation on whether a checkpoint is
        warranted.
        """
        result = _get_json(
            f"{self._base_url}/auto-collect",
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps(result)

    # -- New tool handlers (formerly MCP-only, now HTTP-backed) ─────────────

    def _handle_compress(self, args: dict) -> str:
        """POST /compress — CCR (Compress-Cache-Retrieve) compression."""
        text = args.get("text", "")
        if not text:
            return tool_error("Missing required parameter: text")

        body: dict[str, Any] = {"text": text}
        profile = args.get("profile")
        if profile:
            body["profile"] = profile
        project = args.get("project", self._project)
        if project:
            body["project"] = project

        result = _post_json(
            f"{self._base_url}/compress",
            body,
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps(result)

    def _handle_retrieve(self, args: dict) -> str:
        """POST /retrieve — fetch original content for a CCR hash."""
        hash_val = args.get("hash", "")
        if not hash_val:
            return tool_error("Missing required parameter: hash")

        body: dict[str, Any] = {"hash": hash_val}
        query = args.get("query")
        if query:
            body["query"] = query
        snippet_count = args.get("snippet_count")
        if snippet_count is not None:
            body["snippet_count"] = int(snippet_count)

        result = _post_json(
            f"{self._base_url}/retrieve",
            body,
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps(result)

    def _handle_ingest_url(self, args: dict) -> str:
        """POST /ingest-url — fetch a web page and save it as a memory."""
        url = args.get("url", "")
        if not url:
            return tool_error("Missing required parameter: url")
        tags = args.get("tags", [])
        if not tags:
            return tool_error(
                "Missing required parameter: tags. "
                "Required: project:<slug>, agent:<slug>, gcw:<subtype>"
            )

        body: dict[str, Any] = {"url": url, "tags": tags}

        result = _post_json(
            f"{self._base_url}/ingest-url",
            body,
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps(result)

    def _handle_watch_start(self, args: dict) -> str:
        """POST /watch/start — start the background file watcher."""
        body: dict[str, Any] = {}
        paths = args.get("paths")
        if paths is not None:
            body["paths"] = paths
        scan = args.get("scan")
        if scan is not None:
            body["scan"] = scan
        include_rules = args.get("include_rules")
        if include_rules is not None:
            body["include_rules"] = include_rules

        result = _post_json(
            f"{self._base_url}/watch/start",
            body,
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps(result)

    def _handle_watch_stop(self, args: dict) -> str:
        """POST /watch/stop — stop the background file watcher."""
        result = _post_json(
            f"{self._base_url}/watch/stop",
            {},
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps(result)

    def _handle_watch_status(self, args: dict) -> str:
        """GET /watch/status — report current watcher state."""
        result = _get_json(
            f"{self._base_url}/watch/status",
            api_key=self._api_key,
        )
        self._record_success()
        return json.dumps(result)

    # -- Optional hooks ────────────────────────────────────────────────────

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Mirror built-in memory writes to Mnemos.

        When Hermes writes to MEMORY.md or USER.md via the built-in memory
        tool, this hook fires and duplicates the entry into Mnemos. This
        solves the 2200-char overflow problem: the built-in memory stays
        compact, while Mnemos retains the full history.
        """
        if not self._auto_sync or self._is_breaker_open():
            return
        if action != "add" or not content:
            return

        gcw_subtype = "rule" if target == "user" else "learning"
        agent_tag = "user" if target == "user" else self._agent

        try:
            body = {
                "content": content,
                "tags": [
                    f"project:{self._project}",
                    f"agent:{agent_tag}",
                    f"gcw:{gcw_subtype}",
                ],
                "source": "mcp",
                "memory_type": "fact",
                "metadata": {
                    "mirror_of": "hermes-builtin-memory",
                    "source": "hermes-builtin",
                    "target": target,
                    **(metadata or {}),
                },
            }
            _post_json(
                f"{self._base_url}/memories",
                body,
                api_key=self._api_key,
                timeout=5.0,
            )
            self._record_success()
            logger.debug(
                "Mirrored builtin memory write to Mnemos: %s", action
            )
        except Exception as e:
            self._record_failure()
            logger.debug("Mnemos memory_write mirror failed: %s", e)

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        """Extract key facts before context compression discards old messages.

        Returns text to include in the compression summary so the compressor
        preserves provider-extracted insights.
        """
        if self._is_breaker_open() or not messages:
            return ""

        # Gather recent user messages for a compact summary hint
        user_msgs: list[str] = []
        for msg in messages[-20:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" and content and isinstance(content, str):
                user_msgs.append(content[:200])

        if not user_msgs:
            return ""

        # Hint to the compressor to preserve key facts
        return (
            "[Mnemos] Recent conversation topics that may contain durable "
            "facts:\n"
            + "\n".join(f"- {m}" for m in user_msgs[-5:])
        )

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Extract key facts from the conversation and save as a single
        ``gcw:session`` entry.

        This is the right place for session-level memory — not per-turn.
        We extract user messages and assistant summaries, synthesize a
        compact session record, and write it to Mnemos in a background
        thread. This honors "write sparingly": one entry per session, not
        one per turn.
        """
        if not self._auto_sync or self._is_breaker_open():
            return
        if self._agent_context not in ("primary", ""):
            return
        if not messages or len(messages) < 2:
            return

        def _save_session() -> None:
            try:
                # Extract key user messages (skip trivial ones)
                user_msgs: list[str] = []
                assistant_msgs: list[str] = []
                for msg in messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if not content or not isinstance(content, str):
                        continue
                    if role == "user" and len(content) > _SYNC_MIN_USER_CHARS:
                        user_msgs.append(content[:300])
                    elif role == "assistant" and len(content) > 50:
                        # Take first 300 chars of assistant responses
                        assistant_msgs.append(content[:300])

                if not user_msgs and not assistant_msgs:
                    return

                # Synthesize a compact session summary
                sections: list[str] = []
                if user_msgs:
                    sections.append(
                        "## Key User Messages\n"
                        + "\n".join(f"- {m}" for m in user_msgs[-10:])
                    )
                if assistant_msgs:
                    sections.append(
                        "## Key Assistant Responses\n"
                        + "\n".join(f"- {m}" for m in assistant_msgs[-5:])
                    )
                content = "\n\n".join(sections)
                title = f"Session {self._session_id[:8]} summary"

                body = {
                    "content": content,
                    "title": title,
                    "tags": [
                        f"project:{self._project}",
                        f"agent:{self._agent}",
                        "gcw:session",
                    ],
                    "source": "mcp",
                    "memory_type": "conversation",
                    "metadata": {
                        "session_id": self._session_id,
                        "platform": self._platform,
                        "turn_count": self._turn_counter,
                        "user_msg_count": len(user_msgs),
                        "assistant_msg_count": len(assistant_msgs),
                    },
                }
                _post_json(
                    f"{self._base_url}/memories",
                    body,
                    api_key=self._api_key,
                    timeout=10.0,
                )
                self._record_success()
                logger.info(
                    "Mnemos on_session_end: saved session summary for %s "
                    "(%d turns, %d user msgs)",
                    self._session_id,
                    self._turn_counter,
                    len(user_msgs),
                )
            except Exception as e:
                self._record_failure()
                logger.debug("Mnemos on_session_end failed: %s", e)

        self._session_end_thread = threading.Thread(
            target=_save_session, daemon=True, name="mnemos-session-end"
        )
        self._session_end_thread.start()

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Handle mid-process session_id rotation.

        Resets per-session counters (``_turn_counter``,
        ``_tool_call_counter``) when this is a genuinely new conversation
        (``reset=True``, fired by ``/reset`` / ``/new``). For
        ``/resume`` / ``/branch`` / compression, the logical conversation
        continues so we keep the counters but update the session id.
        """
        old_session_id = self._session_id
        self._session_id = new_session_id

        if reset:
            self._turn_counter = 0
            self._tool_call_counter = 0
            self._last_checkpoint_time = time.time()
            logger.debug(
                "Mnemos on_session_switch (reset): %s → %s, counters reset",
                old_session_id,
                new_session_id,
            )
        else:
            logger.debug(
                "Mnemos on_session_switch (continue): %s → %s, counters "
                "preserved (%d turns)",
                old_session_id,
                new_session_id,
                self._turn_counter,
            )

    # -- Config schema ─────────────────────────────────────────────────────

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "base_url",
                "description": "Mnemos HTTP API base URL",
                "default": "http://127.0.0.1:8000",
                "required": True,
                "env_var": "MNEMOS_BASE_URL",
            },
            {
                "key": "api_key",
                "description": "Bearer token (if Mnemos auth enabled)",
                "secret": True,
                "env_var": "MNEMOS_API_KEY",
            },
            {
                "key": "project",
                "description": "Default project slug for tag contract",
                "default": "hermes",
                "env_var": "MNEMOS_PROJECT",
            },
            {
                "key": "agent",
                "description": "Default agent slug for tag contract",
                "default": "hermes-default",
                "env_var": "MNEMOS_AGENT",
            },
            {
                "key": "auto_sync",
                "description": (
                    "Mirror built-in memory writes and sync significant turns"
                ),
                "default": "true",
                "choices": ["true", "false"],
                "env_var": "MNEMOS_AUTO_SYNC",
            },
            {
                "key": "prefetch_limit",
                "description": "Max results in prefetch (before each turn)",
                "default": "5",
                "env_var": "MNEMOS_PREFETCH_LIMIT",
            },
            {
                "key": "sync_interval",
                "description": (
                    "Sync every Nth turn (in addition to significant turns "
                    "where user message > 50 chars)"
                ),
                "default": "10",
                "env_var": "MNEMOS_SYNC_INTERVAL",
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        """Write config to config.yaml under ``memory.mnemos``.

        This aligns with where ``hermes memory setup`` writes provider
        config (``config["memory"][provider_name]``). The ``_load_config``
        function reads from both ``memory.mnemos`` and ``plugins.mnemos``
        for backward compatibility.
        """
        from pathlib import Path

        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml

            existing: dict[str, Any] = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8-sig") as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("memory", {})
            existing["memory"]["mnemos"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception as e:
            logger.warning("Failed to save Mnemos config: %s", e)

    # -- Shutdown ──────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Clean shutdown — flush background threads."""
        for t in (
            self._prefetch_thread,
            self._sync_thread,
            self._session_end_thread,
        ):
            if t and t.is_alive():
                t.join(timeout=5.0)


# ── Registration ──────────────────────────────────────────────────────────────

def register(ctx) -> None:
    """Register Mnemos as a Hermes memory provider plugin."""
    ctx.register_memory_provider(MnemosMemoryProvider())