# Mnemos Documentation (English)

**🌐 Language / Язык:** English · [Русский](../ru/index.md)

> Mnemos is a standalone memory & knowledge MCP server for LLM/GCW agents. It gives every agent real long-term memory — structured, searchable, governed by a strict tag contract — that persists across sessions, restarts, and context compression.

---

## Connect mnemos as an MCP server

**MCP is the primary integration surface.** This is how VS Code Copilot and GCW agents talk to Mnemos.

### What is implemented

| Property | Value |
|----------|-------|
| Protocol | MCP over **stdio JSON-RPC 2.0** |
| Server name | `mnemos` |
| Transport | stdio — no TCP port |
| Tool prefix | `mnemos_` |
| Source | `src/mnemos/mcp_server.py` |

### Install the MCP extra

The `mcp` package is an **optional extra** — it is not in the base or `[dev]` install:

```bash
pip install -e ".[mcp]"
# or
uv pip install -e ".[mcp]"
```

### Start the server

```bash
mnemos mcp-server
```

The process blocks on stdin/stdout. Stop with `Ctrl+C` or EOF on stdin.

### VS Code `mcp.json` snippet

Add to your VS Code **User** or **Workspace** `mcp.json`:

```jsonc
{
  "servers": {
    "mnemos": {
      "type": "stdio",
      "command": "mnemos",
      "args": ["mcp-server"],
      "env": {
        "MNEMOS_DATA_DIR": "/home/youruser/.mnemos",
        "MNEMOS_VAULT__VAULT_PATH": "/home/youruser/mnemos-vault"
      }
    }
  }
}
```

After saving, the `mnemos_*` tools appear in the Copilot Chat "tools" picker.

### Auto-collect mode

Set `MNEMOS_AUTO_COLLECT=1` in the env block above to make Mnemos prompt your agent to call `mnemos_save_context` after every ~6 tool calls (proactive checkpoint nagging). See [mcp-tools.md#auto-collect-mode](user/mcp-tools.md#auto-collect-mode) for trade-offs.

### 13 MCP tools (`mnemos_` prefix)

| Tool | Purpose |
|------|---------|
| `mnemos_search` | Hybrid FTS5 + vector search (published memories) |
| `mnemos_add` | Create a memory — **enforces the GCW tag contract** |
| `mnemos_agent_recall` | Per-agent recall (M3) — filter by agent slug |
| `mnemos_save_context` | Persist a session checkpoint |
| `mnemos_recall_context` | Restore the latest checkpoint for a project |
| `mnemos_list_recent` | List recent entries |
| `mnemos_list_tags` | List all tags with counts |
| `mnemos_ingest_url` | Fetch a web page and save to memory |
| `mnemos_watch_start` | Start background file watcher |
| `mnemos_watch_stop` | Stop the watcher |
| `mnemos_watch_status` | Report watcher status |
| `mnemos_auto_collect_status` | Compaction-detection signal vector (M7) |
| `mnemos_stats` | Health counters and key paths |

Full catalogue with input schemas, examples, and HTTP equivalents: **[user/mcp-tools.md](user/mcp-tools.md)**

VS Code wiring walkthrough: **[user/getting-started.md#run-the-mcp-server](user/getting-started.md#run-the-mcp-server)**

---

## Where to start

| If you are… | Read |
|-------------|------|
| Setting Mnemos up for the first time | [user/getting-started.md](user/getting-started.md) |
| Wiring Mnemos into VS Code Copilot | [user/getting-started.md#run-the-mcp-server](user/getting-started.md#run-the-mcp-server) |
| Looking for a specific command / flag | [user/cli-reference.md](user/cli-reference.md) |
| Looking for a specific MCP tool | [user/mcp-tools.md](user/mcp-tools.md) |
| Building an HTTP client | [user/http-api.md](user/http-api.md) |
| Trying to understand the system shape | [architecture/overview.md](architecture/overview.md) |
| Diagnosing a problem | [admin/runbooks/install.md](admin/runbooks/install.md) |

---

## User docs

- [Getting Started](user/getting-started.md) — install → first memory → first search → MCP / HTTP.
- [MCP Tools Reference](user/mcp-tools.md) — every `mnemos_*` tool exposed to VS Code Copilot.
- [HTTP API Reference](user/http-api.md) — every endpoint, request / response shape, error code.
- [CLI Reference](user/cli-reference.md) — every `mnemos` subcommand with flags, defaults, and examples.
- [Tag Contract](user/tag-contract.md) — the M2 schema enforced on every memory (`project:`, `agent:`, `gcw:`).

---

## Admin / Ops

- [Runbooks — Install](admin/runbooks/install.md) — first-run operational checklist.
- [Runbooks — Container Deployment](admin/runbooks/container-deployment.md) — build, push, compose, podman, Kubernetes, quadlet.
- [Runbooks — Migrate](admin/runbooks/migrate.md) — import from legacy `ai-brain`.
- [Runbooks — Backup & Restore](admin/runbooks/backup-restore.md) — backup, point-in-time recovery.
- [Runbooks — Dependency Updates](admin/runbooks/dependency-updates.md) — CVE triage + weekly review.
- [Runbooks — CI/CD](admin/runbooks/ci-cd.md) — GitHub Actions pipeline operation.
- [Security Model](admin/security.md) — threat model, SSRF guard, secrets hygiene, auth model.

---

## Architecture

- [System Overview](architecture/overview.md) — layered design, data model, state machines, security boundaries, operational concerns.
- [Knowledge Pipeline](architecture/overview.md#state-machines) — how a memory moves from `raw` → `processing` → `processed` → `published` (M4).
- [A2A Sessions](architecture/a2a-sessions.md) — the agent-to-agent conversation contract (M16).

---

## Project (historical, English only)

- [Architecture Decision Records](../project/adr/README.md) — 14 ADRs covering the M1 → M16 evolution.
- [Milestones](../project/milestones.md) — milestone ledger with status legend.
- [Code Review 2026-06](../project/code-review-2026-06.md) — final code review findings and fixes.
- [Sessions](../project/sessions/) — orchestration session documents.

---

## Repo root

- [README](../../README.md) — top-level project page, status, milestones.
- [CHANGELOG](../../CHANGELOG.md) — release notes.
- [PLAN](../../PLAN.md) — phased implementation plan (M1 → M15).
- [ARCHITECTURE](../../ARCHITECTURE.md) — high-level architecture summary (one-pager; see [architecture/overview.md](architecture/overview.md) for the full version).

---

_Last updated: 2026-06-17_
