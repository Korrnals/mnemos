# CLI Reference

**🌐 Language / Язык:** English · [Русский](../../ru/user/cli-reference.md)

> Complete reference for the `mnemos` command-line tool.

The CLI is a thin Typer-based wrapper around [`MemoryManager`](../architecture/overview.md#memorymanager). It uses Rich for table / colour output and is the most convenient way to interact with Mnemos from a shell.

The full set of subcommands is defined in `src/mnemos/cli/main.py`. This page mirrors what the source actually exposes — every example here is runnable on a clean install.

For a step-by-step first run, see [getting-started.md](getting-started.md). For programmatic access, see [mcp-tools.md](mcp-tools.md) and [http-api.md](http-api.md).

---

## Synopsis

```text
mnemos [GLOBAL-OPTIONS] SUBCOMMAND [SUBCOMMAND-OPTIONS] [ARGS]
```

| Subcommand | Purpose |
|------------|---------|
| [`add`](#add) | Create a new memory entry |
| [`search`](#search) | Hybrid FTS5 + vector search |
| [`recall`](#recall) | List recent memories, optionally per agent / per project |
| [`tags-validate`](#tags-validate) | Validate the tag contract across a vault |
| [`stats`](#stats) | Show health counters |
| [`serve`](#serve) | Start the HTTP API server (FastAPI / Uvicorn) |
| [`mcp-server`](#mcp-server) | Start the MCP stdio server for VS Code Copilot |
| [`migrate-from-ai-brain`](#migrate-from-ai-brain) | One-shot import from a legacy `ai-brain` install |

---

## Global options

Most subcommands accept a `--config / -c` flag pointing at a YAML file. Search order is:

1. `--config` argument (if present)
2. `$MNEMOS_CONFIG` env var
3. `./config.yaml` in the current working directory
4. `~/.mnemos/config.yaml`

```bash
mnemos --help
mnemos add --help
```

There are no other global flags — Mnemos does not have a `verbose` switch; bump Python's logging instead:

```bash
MNEMOS_LOG_LEVEL=DEBUG mnemos search "test"
```

---

## Environment variables

All settings are env-overridable via the `MNEMOS_` prefix. Nested keys use `__` as the delimiter.

| Variable | Default | Purpose |
|----------|---------|---------|
| `MNEMOS_CONFIG` | — | Path to `config.yaml` |
| `MNEMOS_DATA_DIR` | `~/.mnemos` | SQLite DB + vector index |
| `MNEMOS_VAULT__VAULT_PATH` | `~/mnemos-vault` | Obsidian mirror directory |
| `MNEMOS_STRICT_TAG_CONTRACT` | `true` | Enforce M2 tag schema |
| `MNEMOS_API__HOST` | `127.0.0.1` | Default for `mnemos serve` |
| `MNEMOS_API__PORT` | `8787` | Default for `mnemos serve` |
| `MNEMOS_SEARCH__HYBRID_ALPHA` | `0.7` | Vector weight in RRF fusion |
| `MNEMOS_EMBEDDING__PROVIDER` | `chromadb` | `chromadb` / `onnx` / `ollama` / `sentence-transformers` |
| `MNEMOS_LLM__PROVIDER` | `ollama` | LLM for synthesis + context filter |
| `MNEMOS_LLM__MODEL` | `qwen2.5:3b` | LLM model name |
| `MNEMOS_AUTO_COLLECT` | `0` | Set `1` to enable MCP auto-collect mode |
| `MNEMOS_LOG_LEVEL` | `INFO` | Python logging level |

---

## `add`

Create a new memory entry.

```text
mnemos add [CONTENT] [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `CONTENT` (positional) | — | Text to remember. If omitted, reads from stdin. |
| `--title / -t` | auto | Short title. Auto-generated from content if omitted. |
| `--tags / -T` | `""` | Comma-separated tags (e.g. `project:test,agent:me,gcw:learning`). |
| `--file / -f` | — | Import the contents of a file. Mutually exclusive with `CONTENT` and `--url`. |
| `--url / -u` | — | Fetch and ingest a URL. Requires tags. |
| `--source / -s` | `cli` | Memory source enum: `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `--type` | `note` | Memory type: `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `--config / -c` | — | Path to `config.yaml`. |

> **Tag contract.** Every entry must have `project:<slug>`, `agent:<slug>`, and at least one `gcw:<subtype>`. The CLI enforces this in strict mode (the default). See [tag-contract.md](tag-contract.md) for the full schema.

### Examples

```bash
# Inline content
mnemos add --content "Use uv, not pip" --tags project:mnemos agent:tech-writer gcw:learning

# With a title
mnemos add "Always validate SQL with parameterized queries" \
  --title "SQL safety rule" \
  --tags "project:mnemos,agent:security,gcw:rule,severity:high"

# From a file
mnemos add --file ~/notes/architecture.md --tags project:mnemos agent:tech-lead gcw:decision

# From a URL (fetches, extracts, saves)
mnemos add --url https://example.com/article --tags project:research agent:user gcw:learning

# From stdin
echo "Pinned CVE-2026-45829 in chromadb 1.5.9" \
  | mnemos add --tags project:mnemos agent:sre gcw:bug-pattern,severity:medium
```

---

## `search`

Hybrid search: FTS5 + vector + Reciprocal Rank Fusion.

```text
mnemos search QUERY [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `QUERY` (positional) | — | Natural-language search string. |
| `--limit / -l` | `10` | Maximum results. |
| `--project / -p` | — | Restrict to a single project slug. |
| `--config / -c` | — | Path to `config.yaml`. |

The score is the fused RRF score, with 0.0 = no match and 1.0 = top hit. Searches only consider `published` memories (the default vector index scope).

### Examples

```bash
# Plain search
mnemos search "embedding model"

# With project filter
mnemos search "CVE" --project mnemos --limit 20

# Wide-net recall
mnemos search "decision" --limit 50
```

For tag-filtered or raw-content search, use the HTTP API `POST /search` (see [http-api.md#search](http-api.md#search)).

---

## `recall`

List recent memories, optionally scoped to an agent (M3) and / or a project.

```text
mnemos recall [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--project / -p` | — | Project slug to filter on. |
| `--agent / -a` | — | Agent slug to filter on. Enables M3 per-agent recall. |
| `--limit / -l` | `10` | Maximum results. |
| `--config / -c` | — | Path to `config.yaml`. |

When `--agent` is passed **without** a query, the result is the N most recent entries for that agent, ordered by `created_at desc`. This is the same data the MCP tool [`mnemos_agent_recall`](mcp-tools.md#mnemos_agent_recall) returns.

### Examples

```bash
# Most recent 10 entries for any agent
mnemos recall

# Per-agent recall (M3)
mnemos recall --agent tech-writer

# Combined
mnemos recall --agent sre --project mnemos --limit 25
```

---

## `tags-validate`

Validate the GCW tag contract across an existing Mnemos vault directory. Reports entries that violate the M2 schema.

```text
mnemos tags-validate VAULT_PATH
```

| Argument | Description |
|----------|-------------|
| `VAULT_PATH` (positional) | Path to a Mnemos vault directory (markdown mirror). |

> **Status.** The full vault-scan implementation is not yet wired in (`# TODO (M2): scan SQLite + vault markdown files`). For now the command prints a placeholder. Use `mnemos stats` and the HTTP API `GET /memories?project=...` to inspect tags via SQLite instead.

### Example

```bash
mnemos tags-validate ~/mnemos-vault
```

---

## `stats`

Show Mnemos health counters and key paths.

```text
mnemos stats [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--config / -c` | — | Path to `config.yaml`. |

### Output keys

| Key | Meaning |
|-----|---------|
| `status` | Always `ok` (liveness signal) |
| `version` | Mnemos version (currently `0.1.0`) |
| `data_dir` | Resolved data directory |
| `vault_path` | Resolved vault directory |
| `total` | Total memory count (any status) |
| `by_status` | Dict of `raw` / `processing` / `processed` / `published` / `archived` |
| `vectors` | Number of vectors in the ChromaDB index |

### Example

```bash
mnemos stats
# status: ok
# version: 0.1.0
# data_dir: /home/you/.mnemos
# vault_path: /home/you/mnemos-vault
# total: 142
# by_status: {'raw': 5, 'processing': 0, 'processed': 12, 'published': 120, 'archived': 5}
# vectors: 120
```

---

## `serve`

Start the Mnemos HTTP API server (FastAPI / Uvicorn).

```text
mnemos serve [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `settings.api.host` (127.0.0.1) | Bind address. |
| `--port` | `settings.api.port` (8787) | Bind port. |
| `--config / -c` | — | Path to `config.yaml`. |

The server uses `uvicorn[standard]` (HTTP/1.1 + WebSockets). The number of workers comes from `settings.runtime.uvicorn_workers`.

> **Security.** The default bind is `127.0.0.1`. Do not expose this port to a public network without putting a reverse proxy with authentication in front. See [security.md](../admin/security.md).

### Examples

```bash
# Default bind
mnemos serve

# LAN bind (dev box on your home network)
mnemos serve --host 0.0.0.0 --port 8000

# Custom config
mnemos serve --host 127.0.0.1 --port 9000 --config /etc/mnemos/config.yaml
```

The full HTTP API surface is documented in [http-api.md](http-api.md). The Swagger UI is served at `http://HOST:PORT/docs`.

---

## `mcp-server`

Start the Mnemos MCP server over **stdio** for VS Code Copilot (or any MCP-aware client).

```text
mnemos mcp-server [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--config / -c` | — | Path to `config.yaml`. |

The server speaks JSON-RPC 2.0 over stdin/stdout. There is no TCP port. The process blocks until EOF or `Ctrl+C`.

### Examples

```bash
# Direct invocation (for debugging)
mnemos mcp-server

# With auto-collect mode
MNEMOS_AUTO_COLLECT=1 mnemos mcp-server

# From VS Code (mcp.json snippet)
```

```jsonc
{
  "servers": {
    "mnemos": {
      "type": "stdio",
      "command": "mnemos",
      "args": ["mcp-server"]
    }
  }
}
```

See [mcp-tools.md](mcp-tools.md) for the full tool list and [getting-started.md#run-the-mcp-server](getting-started.md#run-the-mcp-server) for the VS Code wiring.

---

## `migrate-from-ai-brain`

One-shot migration from a legacy `ai-brain` install (M13).

```text
mnemos migrate-from-ai-brain [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--source` | `~/.mnemos` | Mnemos data directory (must contain `mnemos.db`). |
| `--vault` | `~/mnemos-vault` | Mnemos vault directory (Obsidian mirror). |
| `--dry-run` | `false` | Show what would be migrated, write nothing. |
| `--config / -c` | — | Path to `config.yaml`. |

The migrator:

- Translates legacy `source` values (e.g. `telegram` → `mcp`).
- **Patches the tag contract** — every legacy entry gets `project:legacy`, `agent:unknown`, `gcw:legacy` added if missing.
- Preserves the original `status` (`raw` / `processing` / `processed` / `published` / `archived`).
- Migrates `content_ru` / `content_en` columns into `metadata` (no data loss).
- Migrates `parent_ids` into `metadata.parent_ids`.

### Examples

```bash
# Dry run first (recommended)
mnemos migrate-from-ai-brain --dry-run

# Real run with default paths
mnemos migrate-from-ai-brain

# From a tarball restore
mnemos migrate-from-ai-brain --source /tmp/restore/.ai-brain --vault /tmp/restore/brain-vault
```

Output is a one-line summary:

```text
✓ Memories migrated: 1 247
✓ Vault files migrated: 1 247
```

If you see `Errors: N`, the `summary.errors` list (printed to stderr at DEBUG level) tells you which rows failed. They are typically schema-corrupt rows that you can ignore or fix by hand in SQLite.

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error (missing argument, invalid tag, etc.) |
| 2 | Uvicorn / stdio server bootstrap failure |

The CLI does not return non-zero for "no results" — `mnemos search` exits 0 with an empty table.

---

## See also

- [getting-started.md](getting-started.md) — first-run walkthrough
- [mcp-tools.md](mcp-tools.md) — the same capabilities exposed over MCP
- [http-api.md](http-api.md) — the same capabilities exposed over HTTP
- [tag-contract.md](tag-contract.md) — the tag schema enforced here
- [runbooks/migrate.md](../admin/runbooks/migrate.md) — operational migration guide
- [architecture overview](../architecture/overview.md) — system shape

---

_Last updated: 2026-06-16_
