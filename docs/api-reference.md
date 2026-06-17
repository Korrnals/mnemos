# HTTP API Reference

> Complete reference for the Mnemos HTTP API — memory CRUD, search, pipeline, DLQ, context filter, traces, path-scoped rules, and the A2A Sessions API (M16).

The HTTP server is a FastAPI app served by Uvicorn. Start it with:

```bash
mnemos serve --host 127.0.0.1 --port 8000
```

| Resource | URL |
|----------|-----|
| Swagger UI | `http://HOST:PORT/docs` |
| ReDoc | `http://HOST:PORT/redoc` |
| OpenAPI 3.1 schema | `http://HOST:PORT/openapi.json` |

> **Default bind is `127.0.0.1`.** Do not expose this port to a public network without putting a reverse proxy with authentication in front. See [security.md](security.md) for the threat model.

> **Authentication** — when `api.auth_enabled=true`, all routes except `/health`, `/auth/login`, `/auth/verify`, `/docs`, `/redoc`, and `/openapi.json` require a valid session token (either `Authorization: Bearer <session>` header or the `mnemos_session` cookie; the header takes precedence). Use `POST /auth/login` to obtain a session. See the [Authentication](#authentication) section below.

> **CORS** — disabled by default. When `api.cors_enabled=true`, the CORS middleware is registered as the outermost layer so OPTIONS preflight requests are answered before auth. Set `cors_allow_origins` to an explicit list of allowed origins; combining `["*"]` with `cors_allow_credentials=true` is rejected at startup.

For the same capabilities over other transports, see [mcp-tools.md](mcp-tools.md) (MCP) and [cli-reference.md](cli-reference.md) (CLI). For higher-level context, see [architecture.md](architecture.md). The A2A contract is also documented in [a2a-sessions.md](a2a-sessions.md) (link to the design rationale).

---

## Conventions

- All bodies are JSON unless noted.
- All timestamps are ISO 8601 in UTC (`2026-06-15T10:42:00+00:00`).
- IDs are UUIDs unless noted (A2A sessions use `conv-YYYY-MM-DD-<short>`).
- Errors are JSON of shape `{"detail": "..."}`.
- Standard HTTP status codes only — no custom error codes.
- `200 OK` and `201 Created` carry a JSON body. `204 No Content` is used for deletes that return no body.

---

## Status codes

| Code | Meaning in this API |
|------|---------------------|
| `200` | OK (default success) |
| `201` | Created (POST that inserts a row) |
| `400` | Bad request (e.g. trying to publish a non-`processed` memory) |
| `404` | Not found (memory_id, cluster_id, dlq_id, session_id, turn_id) |
| `401` | Unauthorised (missing or invalid session token; only when `api.auth_enabled=true`) |
| `422` | Unprocessable entity (Pydantic validation failure on the request body) |
| `500` | Internal server error (see server logs) |
| `503` | Auth not initialised (fail-closed: AuthMiddleware is active but config is absent) |

---

## Authentication

> **Gated by `api.auth_enabled`.** All four endpoints are mounted at `/auth`. When `api.auth_enabled=false` (default) these routes still exist but the middleware does not enforce credentials on other routes.

The auth model uses **opaque bearer tokens** (prefix `mnk_`) with optional TOTP 2FA. Tokens are stored as PBKDF2-HMAC-SHA256 digests; the plaintext is shown once at `mnemos auth token create` and never again. Sessions are issued after a successful login (+ TOTP verify when `api.totp_enabled=true`) and carry the same `Authorization: Bearer <session>` shape.

### `POST /auth/login` — begin session

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `token` | string | **yes** | Opaque bearer token (`mnk_...`). |

**Response 200 — TOTP disabled**

```json
{
  "session": "mnk_session_...",
  "expires_at": "2026-06-18T02:00:00+00:00"
}
```

**Response 200 — TOTP enabled**

```json
{
  "challenge_id": "chal_a1b2c3d4",
  "ttl_sec": 120
}
```

When TOTP is enabled the session is **not** issued here; call `POST /auth/verify` next with the `challenge_id` and the 6-digit TOTP code.

**Errors**

| Code | Cause |
|------|-------|
| `401` | Unknown token or disabled token. |
| `429` | Rate limit exceeded (5 req / min per IP or per token hash). |

### `POST /auth/verify` — complete TOTP challenge

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `challenge_id` | string | **yes** | From the `POST /auth/login` response. |
| `code` | string | **yes** | 6-digit TOTP code. |

**Response 200**

```json
{
  "session": "mnk_session_...",
  "expires_at": "2026-06-18T02:00:00+00:00"
}
```

Also sets `Set-Cookie: mnemos_session=...; HttpOnly; Secure; SameSite=Strict`.

**Errors**

| Code | Cause |
|------|-------|
| `401` | Invalid or expired challenge, wrong TOTP code. |
| `429` | Rate limit exceeded (5 req / min per challenge). |

### `POST /auth/logout` — invalidate session

Requires a valid session (header or cookie). Deletes the server-side session row and clears the cookie.

**Response 200**

```json
{"ok": true}
```

### `GET /auth/me` — current session info

Requires a valid session.

**Response 200**

```json
{
  "token_id": "tok_...",
  "totp": false,
  "expires_at": "2026-06-18T02:00:00+00:00"
}
```

---

## Health and metrics

### `GET /health`

Liveness probe.

**Response 200**

```json
{"status": "ok"}
```

### `GET /metrics`

Prometheus-style metrics (M5 observability). Currently returns the same shape as `GET /memories` aggregate stats:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "data_dir": "/home/you/.mnemos",
  "vault_path": "/home/you/mnemos-vault",
  "total": 142,
  "by_status": {"raw": 5, "processing": 0, "processed": 12, "published": 120, "archived": 5},
  "vectors": 120
}
```

---

## Tags

### `GET /tags` — list tags with counts

Returns every distinct tag in the memories store with its usage count.

**Response 200** — array of tag objects

| Field | Type | Description |
|-------|------|-------------|
| `tag` | string | Full tag string (e.g. `project:mnemos`). |
| `count` | int | Number of memories carrying this tag. |

Sorted by `count` descending; ties are broken by `tag` ascending (alphabetical).

**Example**

```bash
curl -s http://127.0.0.1:8000/tags
```

```json
[
  {"tag": "project:mnemos", "count": 142},
  {"tag": "agent:tech-writer", "count": 58},
  {"tag": "gcw:learning", "count": 41}
]
```

---

## Memories CRUD

### `POST /memories` — create memory

M2 tag contract is enforced server-side. The endpoint derives `project` and `agent` from your tags and stores them as denormalised columns for fast filtering.

**Request body** — see [MemoryCreate](architecture.md#data-model)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `content` | string | **yes** | — | Primary text. |
| `title` | string | no | auto | Short title. |
| `tags` | string[] | **yes** | — | Must include `project:<slug>`, `agent:<slug>`, and at least one `gcw:<subtype>`. |
| `source` | string | no | `manual` | One of `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `source_url` | string | no | — | Origin URL. |
| `memory_type` | string | no | `note` | One of `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `status` | string | no | `raw` | One of `raw`, `processing`, `processed`, `published`, `archived`. |
| `filter_profile` | string | no | — | One of `log`, `terminal`, `code`, `docs`, `web`, `default`. |
| `metadata` | object | no | `{}` | Free-form key / value store. |
| `category` | string | no | — | Free-form category label. |

**Response 201** — full [`Memory`](#memory-schema) object.

**Example**

```bash
curl -s -X POST http://127.0.0.1:8000/memories \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Use uv, not pip — it resolves transitive CVE closure correctly.",
    "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"]
  }'
```

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "content": "Use uv, not pip — it resolves transitive CVE closure correctly.",
  "title": "Use uv, not pip",
  "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"],
  "source": "manual",
  "memory_type": "note",
  "status": "raw",
  "project": "mnemos",
  "agent": "tech-writer",
  "created_at": "2026-06-15T10:42:00+00:00",
  "updated_at": "2026-06-15T10:42:00+00:00",
  "raw_content": null,
  "metadata": {}
}
```

**Errors**

| Code | Cause |
|------|-------|
| `422` | Missing required tag (`project:`, `agent:`, or `gcw:`) |
| `500` | SQLite / vault write failure |

### `GET /memories/{memory_id}` — read one

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `memory_id` | UUID | Memory identifier. |

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `include_raw` | bool | `false` | If true, include `raw_content`. |

**Response 200** — full [`Memory`](#memory-schema) object.

**Response 404** — `{"detail": "Memory <id> not found"}`.

**Example**

```bash
curl -s http://127.0.0.1:8000/memories/550e8400-e29b-41d4-a716-446655440000
```

### `GET /memories` — list recent

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `status` | string | — | Filter by `MemoryStatus` enum value. |
| `project` | string | — | Restrict to a project slug. |
| `limit` | int | `20` | Max rows. Hard cap `500`. |

**Response 200** — array of [`Memory`](#memory-schema) (without `raw_content`).

**Example**

```bash
curl -s "http://127.0.0.1:8000/memories?project=mnemos&limit=10"
```

---

## Search

### `POST /search` — hybrid search

RRF fusion of FTS5 and vector legs. Only `published` memories are searched by default.

**Request body** — see `SearchQuery` in `src/mnemos/models.py`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | **yes** | — | Natural-language search string. |
| `tags` | string[] | no | — | Filter: all of these tags must be present. |
| `project` | string | no | — | Restrict to a project slug. |
| `limit` | int | no | `20` | Max results. |
| `include_raw` | bool | no | `false` | If true, returns `raw_content` instead of cleaned content. |

**Response 200** — array of result dicts

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Memory id. |
| `title` | string | Auto / explicit title. |
| `content` | string | Cleaned content, or raw if `include_raw=true`. |
| `tags` | string[] | Tag list. |
| `score` | float | RRF score, higher is better. |
| `search_type` | string | Always `hybrid` here. |

**Example**

```bash
curl -s -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "embedding model", "project": "mnemos", "limit": 5}'
```

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Use uv, not pip",
    "content": "Use uv, not pip — it resolves transitive CVE closure correctly.",
    "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"],
    "score": 0.812,
    "search_type": "hybrid"
  }
]
```

---

## Per-agent recall (M3)

### `GET /recall/agent/{name}` — agent recall

Returns the most recent entries for a single agent, optionally filtered by project and / or a sub-query.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `name` | string | Agent slug. |

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `project` | string | — | Restrict to a project slug. |
| `q` | string | — | Optional FTS / vector sub-query. |
| `limit` | int | `20` | Max rows. Hard cap `100`. |

**Response 200** — array of:

| Field | Type |
|-------|------|
| `id` | UUID |
| `title` | string |
| `content` | string |
| `tags` | string[] |
| `created_at` | ISO 8601 string |

**Example**

```bash
curl -s "http://127.0.0.1:8000/recall/agent/cr-security-reviewer?project=mnemos&limit=5"
```

---

## Knowledge pipeline (M4)

### `POST /process` — run end-to-end pipeline

Cluster → synthesize → quality gate → publish. Heavy operation; can take seconds to minutes for a large backlog.

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `project` | string | — | Scope to one project. |
| `agent` | string | — | Scope to one agent. |
| `limit` | int | `100` | Max raw entries to consider. Hard cap `500`. |

**Response 200**

```json
{
  "status": "ok",
  "clustered": 8,
  "synthesized": 7,
  "published": 6,
  "quality_rejected": 1,
  "errors": 0
}
```

**Example**

```bash
curl -s -X POST "http://127.0.0.1:8000/process?project=mnemos&limit=200"
```

### `POST /synthesize` — synthesize one cluster

**Query parameters**

| Name | Type | Description |
|------|------|-------------|
| `cluster_id` | string | Cluster identifier. |

**Response 200**

```json
{
  "status": "ok",
  "draft_id": "dr-...",
  "cluster_id": "cl-...",
  "source_coverage": 0.92,
  "model_used": "qwen2.5:3b"
}
```

**Response 404** — `{"detail": "Cluster <id> not found or empty"}`.

### `POST /publish/{memory_id}` — publish a processed memory

Moves a single `processed` memory to `published` and indexes it in the vector store.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `memory_id` | UUID | Memory identifier. |

**Response 200**

```json
{
  "status": "published",
  "memory_id": "550e8400-e29b-41d4-a716-446655440000",
  "vector_indexed": true
}
```

**Response 400** — `{"detail": "Publish failed for <id> (status=raw)"}` (only `processed` memories can be published).

---

## Dead-Letter Queue (M5)

The DLQ holds tasks the automation layer could not complete (LLM timeout, transient embedder failure, etc.). Three endpoints manage the queue.

### `GET /dlq` — list DLQ entries

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `task_label` | string | — | Filter by task label. |
| `ready_only` | bool | `false` | If true, only entries whose next-retry time has elapsed. |
| `limit` | int | `50` | Max rows. Hard cap `500`. |

**Response 200** — array of DLQ row dicts (see `SQLiteStore.dlq_list` for the exact shape).

### `POST /dlq/{dlq_id}/retry` — schedule a retry

**Path parameters**

| Name | Type |
|------|------|
| `dlq_id` | string |

**Response 200** — `{"status": "retry_scheduled", "entry": { ... }}`

### `DELETE /dlq/{dlq_id}` — discard a DLQ entry

**Response 200** — `{"status": "discarded", "dlq_id": "..."}`

**Response 404** — `{"detail": "DLQ entry <id> not found"}`.

---

## Context filter (M10)

### `POST /filter/{memory_id}` — apply the 5-stage context filter

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `memory_id` | UUID | Target memory. |

**Request body** — see `FilterRequest` in `src/mnemos/models.py`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `profile` | string | no | auto | One of `log`, `terminal`, `code`, `docs`, `web`, `default`. |
| `budget` | int | no | — | Token / char budget. |

**Response 200** — `FilterResult` (see `manager.apply_context_filter`).

**Response 404** — `{"detail": "..."}`

---

## Traces (M6)

### `GET /traces` — list pipeline traces

The trace layer is the explainability hook. Every pipeline step writes a trace row.

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `task_label` | string | — | Filter by task label (e.g. `pipeline`, `synthesize`, `publish`). |
| `limit` | int | `50` | Max rows. Hard cap `500`. |

**Response 200** — array of trace row dicts.

---

## Path-scoped rules ingest (M8)

### `POST /rules/ingest` — ingest `.instructions.md` files

**Request body** — see `RuleIngestRequest`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `rules_dir` | string | **yes** | Directory to scan recursively. |
| `project` | string | no | Project slug to tag. |
| `agent` | string | no | Agent slug to tag. |
| `pattern` | string | no | Glob (default `*.instructions.md`). |

**Response 200**

```json
{
  "status": "ok",
  "processed": 7,
  "results": [
    {"file_path": ".github/instructions/communication-language.instructions.md", "memory_id": "..."}
  ]
}
```

**Example**

```bash
curl -s -X POST http://127.0.0.1:8000/rules/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "rules_dir": "/home/you/mnemos/.github/instructions",
    "project": "mnemos",
    "agent": "tech-writer"
  }'
```

### `DELETE /rules/ingest` — remove a rule memory

**Request body** — see `RuleRemoveRequest`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file_path` | string | **yes** | Absolute or vault-relative path of the rule. |

**Response 200** — `{"status": "removed", "removed": true, "memory_id": "..."}`

**Response 404** — `{"detail": "Rule for <path> not found"}`.

---

## A2A Sessions API (M16)

> Mounted under `/v1`. The contract is described in detail in [a2a-sessions.md](a2a-sessions.md); this section is the HTTP surface.

All five endpoints share the same error semantics:

- `422` — Pydantic validation failure.
- `404` — `SessionNotFoundError` or `TurnNotFoundError`.
- `500` — any other server-side failure (see logs).

### `POST /v1/sessions` — create a session

**Request body** — `SessionCreate`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `user_id` | string | no | `""` | Up to 256 chars, whitespace stripped. |
| `metadata` | object | no | `{}` | Free-form key / value store. |
| `ttl_expires_at` | string (ISO 8601) | no | — | Optional client hint for TTL. Server may clamp. |

**Response 201** — `SessionRead`

```json
{
  "session_id": "conv-2026-06-15-a1b2c3d4",
  "user_id": "user-42",
  "created_at": "2026-06-15T10:42:00+00:00",
  "updated_at": "2026-06-15T10:42:00+00:00",
  "turns_count": 0,
  "metadata": {},
  "ttl_expires_at": null
}
```

`session_id` format is `conv-YYYY-MM-DD-<8 hex>` (UTC date).

### `GET /v1/sessions/{session_id}` — read a session

**Response 200** — `SessionRead`.

**Response 404** — `{"detail": "..."}`

### `POST /v1/sessions/{session_id}/turns` — append a turn

Idempotent on `message_id`: a repeat POST with the same `message_id` returns the existing turn (status code is still `201`) instead of duplicating.

**Request body** — `TurnCreate`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `role` | string | **yes** | — | One of `user`, `agent`, `a2a_message`, `system`. |
| `content` | string | **yes** | — | 1 to 1 000 000 chars. |
| `from` | string | no | — | Sender identifier. Up to 256 chars. |
| `to` | string | no | — | Recipient identifier. |
| `message_id` | string | no | — | Truncated to 256 chars. Idempotency key. |
| `outcome` | string | conditional | auto | One of `delivered`, `rejected`, `budget-exhausted`, `loop-detected`. Required when `role=a2a_message`; rejected otherwise. |
| `tags` | string[] | no | `[]` | Deduplicated, max 32 entries. |

**Response 201** — `TurnRead`

```json
{
  "turn_id": "tr-...",
  "session_id": "conv-2026-06-15-a1b2c3d4",
  "step_number": 1,
  "role": "user",
  "from": "user-42",
  "to": null,
  "summary": null,
  "key_decisions": [],
  "content": "Hello, Mnemos.",
  "outcome": null,
  "tags": [],
  "context_pointer": "ctx-...",
  "message_id": "msg-001",
  "created_at": "2026-06-15T10:42:00+00:00"
}
```

### `GET /v1/sessions/{session_id}/turns/{turn_id}` — read one turn

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `mode` | string | `summary` | `summary` returns the cheap path (omits `content`); `full` returns raw content. |

**Response 200** — `TurnRead`.

### `POST /v1/sessions/{session_id}/turns/range` — bulk range

**Request body** — `TurnRangeRequest`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `from_step` | int | **yes** | — | Inclusive lower bound. |
| `to_step` | int | **yes** | — | Inclusive upper bound. Must be ≥ `from_step`. |
| `mode` | string | no | `summary` | `summary` or `full`. |

**Response 200** — `TurnRangeResponse`

```json
{
  "turns": [ { "...TurnRead..." } ],
  "total": 12,
  "mode": "summary"
}
```

Result is sorted by `step_number` ascending. `total` is the number of turns actually returned (not the whole session).

---

## Memory schema

The `Memory` Pydantic model (defined in `src/mnemos/models.py`) is returned by `POST /memories`, `GET /memories/{id}`, and `GET /memories`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | Server-assigned. |
| `content` | string | Cleaned / effective content. |
| `raw_content` | string \| null | Original pre-filter content. Omitted unless `include_raw=true`. |
| `title` | string \| null | Auto-generated if not provided. |
| `tags` | string[] | Must satisfy the M2 contract. |
| `source` | string | One of `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `source_url` | string \| null | Origin URL. |
| `memory_type` | string | One of `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `status` | string | One of `raw`, `processing`, `processed`, `published`, `archived`. |
| `project` | string | Denormalised from the `project:` tag. |
| `agent` | string | Denormalised from the `agent:` tag. |
| `category` | string \| null | Free-form. |
| `quality_score` | float \| null | Set by the M4 quality gate. |
| `confidence` | float \| null | Set by the M4 quality gate. |
| `cluster_id` | string \| null | M4 cluster pointer. |
| `derived_from` | string[] | Parent memory IDs (synthesis lineage). |
| `file_path` | string \| null | Vault-relative path. |
| `metadata` | object | Free-form. |
| `filter_profile` | string \| null | M10 filter profile applied. |
| `created_at` | ISO 8601 | UTC. |
| `updated_at` | ISO 8601 | UTC. |

---

## OpenAPI / Swagger

The full machine-readable schema is available at `/openapi.json` (3.1.0) and rendered as a UI at `/docs` (Swagger) and `/redoc` (ReDoc). These are generated by FastAPI from the route decorators in `src/mnemos/api/main.py` and `src/mnemos/sessions/api.py`, so the schema never drifts from the running code.

If you need to generate a static client, fetch the schema and run [`openapi-generator`](https://openapi-generator.tech/) against it:

```bash
curl -s http://127.0.0.1:8000/openapi.json -o mnemos-openapi.json
npx @openapitools/openapi-generator-cli generate \
  -i mnemos-openapi.json -g typescript-fetch -o ./mnemos-client
```

---

## See also

- [mcp-tools.md](mcp-tools.md) — same capabilities over MCP
- [cli-reference.md](cli-reference.md) — same capabilities over the CLI
- [architecture.md](architecture.md) — system shape and data model
- [a2a-sessions.md](a2a-sessions.md) — A2A contract and design rationale
- [tag-contract.md](tag-contract.md) — M2 schema enforced by `POST /memories`
- [security.md](security.md) — SSRF guard, secrets hygiene, auth model, request-boundary rules

---

_Last updated: 2026-06-17_
