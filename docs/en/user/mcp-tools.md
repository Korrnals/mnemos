# MCP Tools Reference

**🌐 Language / Язык:** English · [Русский](../../ru/user/mcp-tools.md)

> Complete reference for the `mnemos_*` tools exposed by the Mnemos MCP server (`mnemos mcp-server`).

Mnemos speaks the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) over **stdio JSON-RPC 2.0**. VS Code Copilot and any MCP-aware client can call the tools listed here.

The server is defined in `src/mnemos/mcp_server.py`. Every tool below is registered with the `@server.list_tools()` decorator and dispatched by `call_tool()`.

For a quick start on wiring it into VS Code, see [getting-started.md#run-the-mcp-server](getting-started.md#run-the-mcp-server). For programmatic access, the same capabilities are also available over HTTP — see [http-api.md](http-api.md). For the tag schema enforced by most tools, see [tag-contract.md](tag-contract.md).

---

## Transport

| Property | Value |
|----------|-------|
| Protocol | MCP (JSON-RPC 2.0 over stdio) |
| Server name | `mnemos` |
| Default transport | stdio (no TCP) |
| Tool prefix | `mnemos_` |
| Encoding | UTF-8, JSON |

The server does not bind any port. Stop it with `Ctrl+C` or by sending EOF on stdin.

---

## Tool catalogue (summary)

| Tool | Purpose | Tags required |
|------|---------|---------------|
| [`mnemos_add`](#mnemos_add) | Create a new memory entry | yes |
| [`mnemos_search`](#mnemos_search) | Hybrid FTS + vector search | no |
| [`mnemos_agent_recall`](#mnemos_agent_recall) | Per-agent recall (M3) | no |
| [`mnemos_recall_context`](#mnemos_recall_context) | Restore session context for a project | no |
| [`mnemos_save_context`](#mnemos_save_context) | Persist a session checkpoint | no (auto) |
| [`mnemos_list_recent`](#mnemos_list_recent) | List recent entries | no |
| [`mnemos_list_tags`](#mnemos_list_tags) | List all tags with counts | no |
| [`mnemos_ingest_url`](#mnemos_ingest_url) | Fetch and save a web page | yes |
| [`mnemos_watch_start`](#mnemos_watch_start) | Start a background file watcher | no |
| [`mnemos_watch_stop`](#mnemos_watch_stop) | Stop the file watcher | no |
| [`mnemos_watch_status`](#mnemos_watch_status) | Report watcher status | no |
| [`mnemos_auto_collect_status`](#mnemos_auto_collect_status) | Compaction signal vector (M7) | no |
| [`mnemos_compress`](#mnemos_compress) | Reversible compression (CCR) — cache original, embed marker | no |
| [`mnemos_retrieve`](#mnemos_retrieve) | Retrieve a CCR-cached original or FTS5 snippets | no |
| [`mnemos_align_prefix`](#mnemos_align_prefix) | CacheAligner — relocate dynamic content for prefix cache stability | no |
| [`mnemos_export`](#mnemos_export) | Export memories to a file (JSON or SQLite snapshot) | no |
| [`mnemos_import`](#mnemos_import) | Import memories from an export file (merge or restore) | no |
| [`mnemos_stats`](#mnemos_stats) | Health counters and key paths | no |

---

## `mnemos_add`

Create a new memory entry. The MCP layer enforces the Mnemos tag contract ([M2](tag-contract.md)) before writing.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `content` | string | **yes** | — | Text to remember. |
| `title` | string | no | auto | Short title. |
| `tags` | string[] | **yes** | — | Must include `project:<slug>`, `agent:<slug>`, and at least one `mnemos:<subtype>`. |
| `memory_type` | string | no | `note` | One of `note`, `fact`, `snippet`, `bookmark`, `conversation`. |
| `filter_profile` | string | no | auto | One of `log`, `terminal`, `code`, `docs`, `web`, `default`. Drives M10 context filter. |
| `verbosity` | string | no | config default | One of `default`, `terse`, `minimal`. Injects output-style guidance into the tool result framing. See [Output token reduction](#output-token-reduction-p1-7). |
| `effort` | string | no | config default | One of `low`, `medium`, `high`. Injects reasoning-effort hint into the tool result framing. See [Output token reduction](#output-token-reduction-p1-7). |

### Output

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Use uv, not pip",
  "status": "raw"
}
```

### Example call (JSON-RPC)

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "mnemos_add",
    "arguments": {
      "content": "Use uv, not pip",
      "tags": ["project:mnemos", "agent:tech-writer", "mnemos:learning"]
    }
  }
}
```

### Errors

| Error | Cause |
|-------|-------|
| `❌ Tag contract violation: ...` | Missing `project:`, `agent:`, or `mnemos:` tag. |
| `❌ Error: ...` | SQLite write failure, vault write failure, or embed failure (the latter is non-fatal — see [architecture overview](../architecture/overview.md#vector-store)). |

### Related

- Tag schema: [tag-contract.md](tag-contract.md)
- HTTP equivalent: [`POST /memories`](http-api.md#create-memory)
- CLI equivalent: [`mnemos add`](cli-reference.md#add)

---

## `mnemos_search`

Hybrid search: FTS5 (full-text) + vector + Reciprocal Rank Fusion. Only `published` memories are searched by default.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | **yes** | — | Natural language search string. |
| `tags` | string[] | no | — | Filter: all of these tags must be present. |
| `project` | string | no | — | Restrict to a project slug. |
| `limit` | integer | no | `10` | Max results. |
| `include_raw` | boolean | no | `false` | If true, returns `raw_content` instead of cleaned `content`. |
| `verbosity` | string | no | config default | One of `default`, `terse`, `minimal`. Injects output-style guidance into the tool result framing. See [Output token reduction](#output-token-reduction-p1-7). |
| `effort` | string | no | config default | One of `low`, `medium`, `high`. Injects reasoning-effort hint into the tool result framing. See [Output token reduction](#output-token-reduction-p1-7). |

### Output

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Use uv, not pip",
    "content": "Use uv, not pip — it's faster and resolves transitive CVE closure correctly.",
    "tags": ["project:mnemos", "agent:tech-writer", "mnemos:learning"],
    "score": 0.812,
    "search_type": "hybrid",
    "status": "published"
  }
]
```

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "mnemos_search",
    "arguments": {
      "query": "how to manage Python dependencies",
      "limit": 5,
      "project": "mnemos"
    }
  }
}
```

### Errors

- `❌ Error: ...` — query parsing failure (rare; usually succeeds with an empty result).

### Related

- HTTP equivalent: [`POST /search`](http-api.md#search)
- CLI equivalent: [`mnemos search`](cli-reference.md#search)

---

## `mnemos_agent_recall`

Per-agent recall (M3). Returns the most recent entries for a single agent, optionally filtered by project and / or sub-query.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `agent` | string | **yes** | — | Agent slug, e.g. `cr-security-reviewer`. |
| `project` | string | no | — | Restrict to a project slug. |
| `query` | string | no | — | Optional FTS / vector query within the agent scope. |
| `limit` | integer | no | `20` | Max entries to return. |

When `query` is omitted, the tool returns recent entries (recency-ordered). When `query` is present, it runs a hybrid search scoped to the agent's tags.

### Output

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Bandit B608 hardcoded SQL — flag for triage",
    "content": "Found hardcoded SQL in src/legacy/loader.py:42 ...",
    "tags": ["project:mnemos", "agent:cr-security-reviewer", "mnemos:bug-pattern"],
    "created_at": "2026-06-15T10:42:00+00:00",
    "status": "published"
  }
]
```

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "mnemos_agent_recall",
    "arguments": {
      "agent": "cr-security-reviewer",
      "project": "mnemos",
      "query": "bandit SQL injection",
      "limit": 10
    }
  }
}
```

### Errors

- None typical. Returns an empty array if no matches.

### Related

- HTTP equivalent: [`GET /recall/agent/{name}`](http-api.md#agent-recall)
- CLI equivalent: [`mnemos recall --agent <slug>`](cli-reference.md#recall)

---

## `mnemos_recall_context`

Restore the latest session checkpoint for a project. The **first** thing an agent should call at the start of a session, especially after context compaction.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `project` | string | no | auto (cwd) | Project name. Auto-detected from the current working directory if omitted. |
| `query` | string | no | — | Optional focus aspect. |
| `verbosity` | string | no | config default | One of `default`, `terse`, `minimal`. Injects output-style guidance into the tool result framing. See [Output token reduction](#output-token-reduction-p1-7). |
| `effort` | string | no | config default | One of `low`, `medium`, `high`. Injects reasoning-effort hint into the tool result framing. See [Output token reduction](#output-token-reduction-p1-7). |

### Output

A plain-text block formatted as Markdown:

```text
# Context for project 'mnemos'

---
# Session checkpoint — 2026-06-15T10:42:00+00:00

## Goals
Ship M15 production hardening.
## Completed
bandit clean, mypy --strict green
## In Progress
pip-audit CVE-2026-45829 ignore
## Decisions
Pin chromadb 1.5.9 with audit
## Context
Active files: src/mnemos/manager.py, src/mnemos/api/main.py
```

If no checkpoint is found:

```text
No context found for project 'mnemos'. Start by saving context with mnemos_save_context.
```

In **auto-collect mode** (`MNEMOS_AUTO_COLLECT=1`), a `## 🔄 Auto-Collect Mode Active` block is appended with mandatory session rules.

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "mnemos_recall_context",
    "arguments": { "project": "mnemos" }
  }
}
```

### Related

- `mnemos_save_context` — the matching writer
- [architecture.md#session-context](../architecture/overview.md#session-context)
- HTTP equivalent: [`POST /context/recall`](http-api.md#context-recall)

---

## `mnemos_save_context`

Persist a session checkpoint. Agents should call this **proactively**: after meaningful work, before switching tasks, or when context is large.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `project` | string | no | auto (cwd) | Project name. |
| `goals` | string | no | — | Current session goals. |
| `completed` | string | no | — | What has been completed. |
| `in_progress` | string | no | — | What is in progress. |
| `decisions` | string | no | — | Key technical decisions + rationale. |
| `context` | string | no | — | Other context (file paths, architecture, gotchas). |

Mnemos synthesises the parts into a single Markdown memory tagged with `project:<slug>`, `agent:user`, and `mnemos:checkpoint`.

### Output

```text
✅ Context saved (id=550e8400-...).
```

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "mnemos_save_context",
    "arguments": {
      "project": "mnemos",
      "goals": "Finish M15.1 mypy --strict",
      "completed": "Added None checks in 12 functions",
      "in_progress": "tests/test_api.py:241 type narrowing",
      "decisions": "Use cast() sparingly, prefer TypeGuard"
    }
  }
}
```

### Related

- `mnemos_recall_context` — the matching reader
- Auto-collect mode: [getting-started.md#run-the-mcp-server](getting-started.md#run-the-mcp-server)
- HTTP equivalent: [`POST /context/save`](http-api.md#context-save)

---

## `mnemos_list_recent`

List the most recent memory entries, oldest-last.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `limit` | integer | no | `10` | Max entries. |
| `tags` | string[] | no | — | Filter: any of these tags must be present. |
| `project` | string | no | — | Restrict to a project slug. |

### Output

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Use uv, not pip",
    "tags": ["project:mnemos", "agent:tech-writer", "mnemos:learning"],
    "status": "raw",
    "created_at": "2026-06-15T10:42:00+00:00"
  }
]
```

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "mnemos_list_recent",
    "arguments": { "limit": 20, "project": "mnemos" }
  }
}
```

### Related

- HTTP equivalent: [`GET /memories`](http-api.md#list-recent)
- CLI equivalent: [`mnemos list`](cli-reference.md#list)

---

## `mnemos_list_tags`

List every tag in the memory with its occurrence count.

### Input

None.

### Output

```json
{
  "project:mnemos": 142,
  "agent:tech-writer": 23,
  "agent:sre": 41,
  "mnemos:learning": 67,
  "mnemos:bug-pattern": 12,
  "mnemos:decision": 8,
  "mnemos:checkpoint": 14
}
```

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": { "name": "mnemos_list_tags", "arguments": {} }
}
```

### Related

- HTTP equivalent: [`GET /tags`](http-api.md#tags)
- CLI equivalent: [`mnemos tags`](cli-reference.md#tags)

---

## `mnemos_ingest_url`

Fetch a web page, extract its main content (via `trafilatura`), and save it as a memory.

### Input

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | **yes** | HTTP / HTTPS URL to fetch. |
| `tags` | string[] | **yes** | Same M2 contract as `mnemos_add`. |

> **SSRF guard.** The MCP layer strips `user:password@` from the URL authority before fetching (defence in depth alongside the in-process guard). Do not bypass this by building the URL from a string.

### Output

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "How to manage Python dependencies",
  "url": "https://example.com/article"
}
```

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "mnemos_ingest_url",
    "arguments": {
      "url": "https://example.com/article",
      "tags": ["project:research", "agent:user", "mnemos:learning"]
    }
  }
}
```

### Errors

| Error | Cause |
|-------|-------|
| `❌ Error: ...` | Network failure, blocked URL (SSRF guard), or `trafilatura` extraction failure. |

### Related

- CLI equivalent: [`mnemos add --url <URL>`](cli-reference.md#add)
- HTTP equivalent: [`POST /memories` with manual content](http-api.md#create-memory)
- HTTP equivalent: [`POST /ingest-url`](http-api.md#ingest-url)
- Security: [security.md](../admin/security.md#ssrf-guard)

---

## `mnemos_watch_start`

Start a background file watcher. New and modified files under the watched paths are auto-indexed into Mnemos.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `paths` | string[] | no | `[cwd]` | Directories to watch. |
| `scan` | boolean | no | `true` | Run an initial scan to catch up on existing files. |
| `include_rules` | boolean | no | `false` | Also watch `.github/instructions/*.instructions.md` (M8 path-scoped rules). |

### Output

```text
✅ Watcher started on ['/home/you/project']
# or, with include_rules:
✅ Watcher started on ['/home/you/project'] (including .instructions.md rules)
```

### Example call

```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/call",
  "params": {
    "name": "mnemos_watch_start",
    "arguments": {
      "paths": ["/home/you/mnemos", "/home/you/notes"],
      "include_rules": true
    }
  }
}
```

### Notes

- File size cap is `watcher.max_file_size_kb` (default 512 KB) — files larger than this are skipped.
- Default ignored dirs: `.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`.
- Default watched extensions: `.md`, `.py`, `.js`, `.ts`, `.yaml`, `.yml`, `.toml`, `.json`, `.txt`, `.rst`, `.sh`, `.css`, `.html`, `.sql`.

### Related

- HTTP equivalent: [`POST /watch/start`](http-api.md#watch-start)
- CLI equivalent: [`mnemos watch start`](cli-reference.md#watch-start)

---

## `mnemos_watch_stop`

Stop the background file watcher.

### Input

None.

### Output

```text
✅ Watcher stopped.
```

### Related

- HTTP equivalent: [`POST /watch/stop`](http-api.md#watch-stop)
- CLI equivalent: [`mnemos watch stop`](cli-reference.md#watch-stop)

---

## `mnemos_watch_status`

Report the current state of the background watcher.

### Input

None.

### Output

```json
{
  "running": true,
  "paths": ["/home/you/mnemos"],
  "files_queued": 3,
  "files_indexed": 142,
  "include_rules": false
}
```

### Related

- HTTP equivalent: [`GET /watch/status`](http-api.md#watch-status)
- CLI equivalent: [`mnemos watch status`](cli-reference.md#watch-status)

---

## `mnemos_auto_collect_status`

Return the current compaction-detection signal vector (M7). The agent reads this to decide whether to call `mnemos_save_context` proactively.

### Input

None.

### Output

```json
{
  "auto_collect_enabled": false,
  "signals": {
    "call_counter": {
      "calls_since_save": 7,
      "threshold": 12,
      "triggered": false
    },
    "elapsed_secs": {
      "value": 312,
      "threshold": 900,
      "triggered": false
    },
    "context_size_heuristic": {
      "value": null,
      "note": "populated by client (M7)"
    },
    "summary_marker_detected": {
      "value": null,
      "note": "populated by client (M7)"
    },
    "reference_drop_heuristic": {
      "value": null,
      "note": "populated by client (M7)"
    }
  },
  "recommendation": "ok",
  "next_reminder_in_calls": 5
}
```

The `recommendation` field is one of:

| Value | Meaning |
|-------|---------|
| `ok` | No checkpoint needed yet. |
| `save_checkpoint` | Save now — you are at or past a threshold. |

### Auto-collect mode

Set `MNEMOS_AUTO_COLLECT=1` in the server's environment. The reminder thresholds tighten:

| Setting | Normal | Auto-collect |
|---------|--------|--------------|
| Calls since save | 12 | 6 |
| Elapsed seconds | 900 (15 min) | 480 (8 min) |

Tool descriptions also change (with `🔄 [AUTO-COLLECT] MANDATORY:` prefixes) so agents take the hints more seriously. **Recommended for production agents**, not for one-off scripts.

### Related

- HTTP equivalent: [`GET /auto-collect`](http-api.md#auto-collect)
- CLI equivalent: [`mnemos auto-collect-status`](cli-reference.md#auto-collect-status)

---

## `mnemos_stats`

Return Mnemos health counters.

### Input

None.

### Output

Same shape as the CLI `mnemos stats` command — see [cli-reference.md#stats](cli-reference.md#stats).

```json
{
  "status": "ok",
  "version": "0.1.0",
  "data_dir": "/home/you/.mnemos/data",
  "vault_path": "/home/you/.mnemos/vault",
  "total": 142,
  "by_status": {"raw": 5, "processing": 0, "processed": 12, "published": 120, "archived": 5},
  "vectors": 120
}
```

### Related

- HTTP equivalent: [`GET /metrics`](http-api.md#metrics)
- CLI equivalent: [`mnemos stats`](cli-reference.md#stats)

---

## `mnemos_compress`

Compress large content (tool output, logs, JSON) with **zero data loss**. The original is cached in the `ccr_cache` SQLite table keyed by its SHA-256 hash; the compressed output embeds a short parseable marker so the LLM can call `mnemos_retrieve` to fetch the full original back on demand. Achieves 70–90% token reduction on typical logs and JSON.

Content shorter than `min_size_chars` (default 500) is returned as-is — not cached, not compressed (tiny content has no token savings).

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `text` | string | **yes** | — | Content to compress. ≥500 chars to cache. |
| `profile` | string | no | auto | One of `log`, `terminal`, `code`, `docs`, `web`, `default`. Auto-detected if omitted. |
| `project` | string | no | `""` | Project slug to scope the cache entry. |

### Output

```json
{
  "compressed_text": "[compressed: a1b2... | 30000→900 chars | retrieve via mnemos_retrieve]\n...filtered content...",
  "hash": "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef12345678",
  "original_size": 30000,
  "compressed_size": 900,
  "reduction_pct": 97.0,
  "marker": "[compressed: a1b2... | 30000→900 chars | retrieve via mnemos_retrieve]",
  "cached": true,
  "profile": "log"
}
```

### Marker format

```text
[compressed: <sha-256-hash> | <N>→<M> chars | retrieve via mnemos_retrieve]
```

The marker is the only overhead added on top of the filtered content. It is short, parseable, and LLM-friendly. The hash is content-addressed, so re-compressing the same text is a no-op (the cache entry is reused).

### Example

Compress a 30K-line build log → ~900 chars in the context window. When the LLM needs the full traceback, it calls `mnemos_retrieve` with the hash from the marker.

### Related

- HTTP equivalent: [`POST /compress`](http-api.md#compress)
- CLI equivalent: [`mnemos compress`](cli-reference.md#compress)

---

## `mnemos_retrieve`

Retrieve the original uncompressed content for a CCR marker hash. If `query` is omitted, returns the full original. If `query` is provided, returns FTS5-ranked snippets from within the cached original — useful when the original is large and only a few lines are relevant.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `hash` | string | **yes** | — | SHA-256 hash from a `[compressed: ...]` marker. |
| `query` | string | no | — | Search query for snippet retrieval. |
| `snippet_count` | integer | no | `5` | Number of snippets when `query` is provided. |

### Output (full retrieval)

```json
{
  "hash": "a1b2...",
  "found": true,
  "original": "...full original text...",
  "size_bytes": 30000,
  "retrieval_count": 2
}
```

### Output (snippet retrieval)

```json
{
  "hash": "a1b2...",
  "found": true,
  "query": "Traceback",
  "snippets": [
    {"text": "Traceback (most recent call last):", "rank": 1.0},
    {"text": "  File \"app.py\", line 42, in handler", "rank": 0.8}
  ],
  "retrieval_count": 3
}
```

If the hash is absent from the cache (e.g. evicted by TTL or LRU), `found` is `false` with a `reason` field.

### Related

- HTTP equivalent: [`POST /retrieve`](http-api.md#retrieve)
- CLI equivalent: [`mnemos retrieve`](cli-reference.md#retrieve)

---

## `mnemos_align_prefix`

**CacheAligner (P1-5)** — relocate dynamic content (ISO timestamps, UUIDs, session ids, short-lived tokens, calendar dates) from system-prompt-like text to a `--- Dynamic context ---` block at the end, so the prefix stays byte-identical across requests and provider KV caches (Anthropic `cache_control`, OpenAI prefix caching) hit. Inspired by headroom's CacheAligner (https://github.com/headroomlabs-ai/headroom, Apache 2.0). Original implementation — no headroom code imported.

When CacheAligner is disabled in config, the text is returned unchanged with an empty `extracted` list.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `text` | string | **yes** | — | System-prompt-like text to stabilize. |
| `profile` | string | no | `default` | One of `code`, `docs`, `default`. Toggles which dynamic kinds are extracted. `code` and `docs` skip bare tokens (avoid mangling long identifiers or hyphenated words); `default` extracts all kinds. |

### Output

```json
{
  "aligned_text": "You are a senior engineer.\n\n--- Dynamic context ---\n- timestamp: 2026-07-17T10:30:00Z\n- session_id: sess-abc123def456\n",
  "extracted": [
    {"kind": "timestamp", "value": "2026-07-17T10:30:00Z", "start": 24, "end": 44},
    {"kind": "session_id", "value": "sess-abc123def456", "start": 60, "end": 78}
  ],
  "prefix_stabilized": true,
  "moved_chars": 38
}
```

- `aligned_text` — the input with dynamic spans removed and a `--- Dynamic context ---` block appended at the end, listing each extracted value with its kind.
- `extracted` — the list of extracted spans (`kind`, `value`, `start`, `end` in the *original* text).
- `prefix_stabilized` — `true` when at least one span was extracted from the prefix region (i.e. the aligned prefix is longer than the original prefix up to the first dynamic span).
- `moved_chars` — total characters relocated (sum of span lengths).

### Example

Input:
```text
You are a senior engineer. Today is 2026-07-17T10:30:00Z. Session: sess-abc123def456.
[stable rules follow...]
```

Aligned output (prefix up to the first dynamic span is now byte-stable across requests):
```text
You are a senior engineer. Today is . Session: .
[stable rules follow...]

--- Dynamic context ---
- timestamp: 2026-07-17T10:30:00Z
- session_id: sess-abc123def456
```

### Profile behaviour

| Profile | Skips | Why |
|---------|-------|-----|
| `default` (or omitted) | nothing | extract all kinds |
| `code` | `token` | bare 20+ char tokens would mangle long identifiers / hashes in code |
| `docs` | `token` | prose rarely contains real tokens; avoids mangling long hyphenated words |

The profile's skip set merges (union) with any per-kind toggles from `CacheAlignerConfig` — disabling a kind in config widens what a profile already skips.

### Config

```yaml
cache_aligner:
  enabled: true               # master switch
  extract_timestamps: true   # ISO 8601 timestamps
  extract_uuids: true        # canonical 8-4-4-4-12 UUIDs
  extract_session_ids: true  # sess-*, session:*, sid-*
  extract_dates: true        # calendar dates 2026-07-17 / 2026/07/17
  extract_tokens: true       # bare 20+ char opaque tokens
```

A kind whose toggle is `false` is added to the skip set and stays in-place (not relocated).

### Related

- Architecture: [overview.md#cachealigner-p1-5](../architecture/overview.md#cachealigner-p1-5)
- Config reference: [config.example.yaml](../../../config.example.yaml)

---

## Checkpoint reminder (auto-injected)

Every non-save tool call returns its normal payload **plus** an optional reminder string when one of the auto-collect thresholds is hit:

```text
... normal result ...

⚠️ [mnemos] 12 tool calls since last checkpoint (970s ago). Consider calling mnemos_save_context to preserve your current progress.
```

This is informational; nothing in Mnemos blocks the call. Disable by setting `MNEMOS_AUTO_COLLECT=0` (the default).

---

## Tag contract reminder

The `mnemos_add` and `mnemos_ingest_url` tools reject calls that violate the M2 contract. The three required tag families are:

| Tag | Format | Cardinality | Purpose |
|-----|--------|-------------|---------|
| `project:<slug>` | `[a-z0-9][a-z0-9\-_]{0,63}` | exactly 1 | Binds to a codebase / initiative |
| `agent:<slug>` | `[a-z0-9][a-z0-9\-_]{0,63}` | exactly 1 | Authoring agent |
| `mnemos:<subtype>` | `[a-z][a-z0-9\-]*` | at least 1 | Cognitive category |

Valid `mnemos:` subtypes: `session`, `bug-pattern`, `learning`, `decision`, `rule`, `open-question`, `checkpoint`, `legacy`.

Full reference: [tag-contract.md](tag-contract.md).

---

## Output token reduction (P1-7)

`mnemos_add`, `mnemos_search`, and `mnemos_recall_context` accept two optional parameters that steer the caller's output style without changing what Mnemos stores or returns:

| Parameter | Values | What it does |
|-----------|--------|--------------|
| `verbosity` | `default`, `terse`, `minimal` | Injects an output-style guidance suffix into the tool result framing. `terse` asks for brief, no-preamble output; `minimal` asks for facts only. |
| `effort` | `low`, `medium`, `high` | Injects a reasoning-effort hint. `low` flags a routine step (minimal reasoning); `high` asks for deliberate reasoning and verification. |

These are **hints passed through to the caller**, not model config changes. They are inspired by headroom's output token reduction work. Original implementation.

### Backward compatibility

- Both parameters are optional. Omitting them uses the config defaults (`default_verbosity=default`, `default_effort=medium`).
- The defaults (`default` / `medium`) produce an empty guidance suffix — the tool result is byte-identical to the pre-P1-7 output.
- Invalid values (e.g. `"verbose"`, `"turbo"`) are validated against the allowed frozensets, logged at `WARNING`, and fall back to the config default — graceful degradation, never raises.

### Config

```yaml
output_style:
  enabled: true              # master switch; when false, steering is a no-op
  default_verbosity: default # default when caller omits verbosity
  default_effort: medium     # default when caller omits effort
```

When `output_style.enabled` is `false`, both resolvers return the no-op defaults regardless of caller input.

### Example

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "mnemos_search",
    "arguments": {
      "query": "cache aligner prefix stability",
      "verbosity": "terse",
      "effort": "low"
    }
  }
}
```

The tool result carries the normal payload **plus** a short guidance suffix:

```text
... normal search results ...

---
*Output style: terse. Be brief. No preambles, no restated context, no ceremony. Lead with the result. Omit explanations the caller already has.*
*Effort: low — routine step, minimal reasoning.*
```

---

## `mnemos_export`

Export memories to a file on disk. Thin wrapper over the CLI `mnemos export` logic. Returns metadata only — the export content is **never** returned inline (the stdio transport cannot carry a binary SQLite tarball or a large JSON blob over the JSON-RPC stdout channel).

Federation defence-in-depth (#86) is inherited automatically because the tool wraps the same `run_export` function as the CLI and HTTP surfaces: records tagged `mnemos:no-federate` are excluded from the export, and detected secrets in passing records are replaced with `<REDACTED:<pattern_name>>`.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `output_path` | string | **yes** | — | Absolute path where the export file is written. |
| `format` | enum `json` \| `sqlite` | no | `json` | `json` = metadata-only export (filters apply); `sqlite` = full `tar.gz` snapshot (filters ignored). |
| `compress` | enum `none` \| `gzip` | no | `none` | Compression mode. (`zstd` is CLI-only.) |
| `project` | string | no | — | Filter by project slug (json only). |
| `agent` | string | no | — | Filter by agent slug (json only). |
| `status` | enum `raw` \| `processing` \| `processed` \| `published` \| `archived` | no | — | Filter by memory status (json only). |
| `tags` | array of string | no | — | Filter by tags (json only). |
| `since` | string (ISO-8601) | no | — | Only memories created on or after this date (json only). |
| `until` | string (ISO-8601) | no | — | Only memories created before this date (json only). |
| `encrypt` | boolean | no | `false` | When `true`, encrypt the output. The passphrase is read from the `MNEMOS_EXPORT_PASSPHRASE` environment variable. |

### Returns

```json
{
  "path": "/abs/path/to/backup.json",
  "memory_count": 42,
  "format": "json",
  "compress": "none",
  "encrypted": false,
  "bytes": 18234,
  "warnings": []
}
```

### Security note

- **Passphrase via environment, never in arguments.** When `encrypt=true`, the server reads the passphrase from the `MNEMOS_EXPORT_PASSPHRASE` environment variable. Passing the passphrase value in `output_path` or any other argument would leak it into MCP logs — never do this.
- **No inline content.** The tool writes to `output_path` and returns metadata only. Read the file from disk to inspect the export.
- **`#86` inheritance.** `mnemos:no-federate` records are excluded; secrets in passing records are redacted. No extra configuration needed.

### Example

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "mnemos_export",
    "arguments": {
      "output_path": "/tmp/mnemos-backup.json",
      "format": "json",
      "project": "mnemos",
      "compress": "gzip"
    }
  }
}
```

For an encrypted full snapshot:

```json
{
  "name": "mnemos_export",
  "arguments": {
    "output_path": "/tmp/mnemos-snapshot.tar.gz",
    "format": "sqlite",
    "encrypt": true
  }
}
```

(With `MNEMOS_EXPORT_PASSPHRASE` set in the server's environment.)

---

## `mnemos_import`

Import memories from an export file. Thin wrapper over the CLI `mnemos import` logic. Two modes: **merge** (insert new, skip or overwrite existing) and **restore** (wipe all then import — destructive, requires `confirm=true`).

Import validation (#86) is inherited automatically: schema drift, oversized content, invalid tags, and prompt-injection patterns are handled by the same `run_import` function the CLI and HTTP surfaces use.

### Input

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `source_path` | string | **yes** | — | Absolute path to the export file to import. |
| `mode` | enum `merge` \| `restore` | no | `merge` | `merge` = insert new / skip-or-overwrite existing; `restore` = wipe all then import (requires `confirm=true`). |
| `overwrite` | boolean | no | `false` | Overwrite existing memories (merge mode only). |
| `confirm` | boolean | no | `false` | **Required `true` for `restore` mode** (hard gate — restore wipes all existing data). |
| `dry_run` | boolean | no | `false` | Validate without writing; returns a validation report. |
| `passphrase_env` | string | no | — | Name of the environment variable holding the decryption passphrase (NOT the value). |

### Returns

```json
{
  "mode": "merge",
  "dry_run": false,
  "imported": 12,
  "skipped": 3,
  "updated": 0,
  "errors": [],
  "warnings": [],
  "format_version": "1.0",
  "mnemos_version": "2.10.0"
}
```

### Security note

- **Passphrase via environment variable name, never the value.** `passphrase_env` takes the *name* of the environment variable (e.g. `"MY_IMPORT_PASS"`), and the server reads `os.environ["MY_IMPORT_PASS"]`. Passing the passphrase value as the argument would leak it into MCP logs.
- **Restore requires `confirm=true`.** Without it the tool returns an error and does not touch the live data. Restore wipes all memories, vectors, and projects.
- **`#86` inheritance.** Schema drift is rejected; oversized content (>1 MiB) is rejected; invalid tags raise a tag-contract error; prompt-injection patterns are logged at WARNING (not blocked — content may legitimately discuss injection).

### Example

```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/call",
  "params": {
    "name": "mnemos_import",
    "arguments": {
      "source_path": "/tmp/mnemos-backup.json",
      "mode": "merge",
      "overwrite": false
    }
  }
}
```

Restore (destructive) with confirmation:

```json
{
  "name": "mnemos_import",
  "arguments": {
    "source_path": "/tmp/mnemos-snapshot.tar.gz",
    "mode": "restore",
    "confirm": true
  }
}
```

Encrypted import (with `MNEMOS_IMPORT_PASS` set in the server's environment):

```json
{
  "name": "mnemos_import",
  "arguments": {
    "source_path": "/tmp/encrypted.bin",
    "mode": "merge",
    "passphrase_env": "MNEMOS_IMPORT_PASS"
  }
}
```

---

## See also

- [getting-started.md](getting-started.md) — wiring `mcp.json` and the first call
- [http-api.md](http-api.md) — the same capabilities over HTTP
- [cli-reference.md](cli-reference.md) — the same capabilities over the CLI
- [tag-contract.md](tag-contract.md) — M2 schema enforced by `mnemos_add`
- [security.md](../admin/security.md) — SSRF guard, secrets hygiene
- [architecture overview](../architecture/overview.md#mcp-server) — server lifecycle

---

_Last updated: 2026-06-16_
