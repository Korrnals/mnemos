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
| [`tags validate`](#tags-validate) | Validate the tag contract across a vault |
| [`workflow`](#workflow) | Memory workflow lifecycle: `get` / `set` / `history` |
| [`stats`](#stats) | Show health counters |
| [`fts`](#fts) | FTS5 index management (`rebuild`) |
| [`processor`](#processor) | Background pipeline control: `status` / `run` / `start` / `stop` |
| [`reindex`](#reindex) | Re-embed all published memories into the vector index |
| [`filter`](#filter) | Run the Context Filter on a memory |
| [`serve`](#serve) | Start the HTTP API server (FastAPI / Uvicorn) |
| [`mcp-server`](#mcp-server) | Start the MCP stdio server for VS Code Copilot |
| [`migrate from-ai-brain`](#migrate-from-ai-brain) | One-shot import from a legacy `ai-brain` install |
| [`auth`](#auth) | API bearer tokens (`auth token`) and TOTP 2FA (`auth totp`) |
| [`integration`](integration-guide.md) | Deploy / verify the integration layer (dedicated page) |
| [`completion`](#completion) | Install shell completion (bash / zsh / fish) |
| [`doctor`](#doctor) | Diagnose the installation (paths, config, database, vault) |
| [`export`](export-import.md) | Export memories to a JSON / SQLite backup (dedicated page) |
| [`import`](export-import.md) | Import memories from an export file (dedicated page) |
| [`logs`](#logs) | View pipeline traces |
| [`sync`](sync.md) | Federation batch sync export / import (dedicated page) |
| [`scanner`](#scanner) | Background secrets scanner: `run` / `status` |

> The `tags` group also provides `tags normalize` and `tags rename` (bulk prefix rename with dry-run); `migrate tags` is a deprecated alias for `mnemos tags rename --from gcw: --to mnemos: --no-dry-run`.

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

The only other global flags are `--version / -V` (print the version) and `--verbose / -v` (DEBUG logging for `mnemos serve` and `mnemos mcp-server`). To change the log level permanently, set `logging.level` in the config file or the corresponding env var:

```bash
MNEMOS_LOGGING__LEVEL=DEBUG mnemos serve
```

---

## Environment variables

All settings are env-overridable via the `MNEMOS_` prefix. Nested keys use `__` as the delimiter.

| Variable | Default | Purpose |
|----------|---------|---------|
| `MNEMOS_CONFIG` | — | Path to `config.yaml` |
| `MNEMOS_MNEMOS__DATA_DIR` | `~/.mnemos/data` | SQLite DB + vector index (canonical form) |
| `MNEMOS_DATA_DIR` *(legacy alias)* | `~/.mnemos/data` | Legacy alias for `MNEMOS_MNEMOS__DATA_DIR` |
| `MNEMOS_MNEMOS__VAULT_PATH` | `~/.mnemos/vault` | Obsidian mirror directory (canonical form) |
| `MNEMOS_VAULT__VAULT_PATH` *(legacy alias)* | `~/.mnemos/vault` | Legacy alias for `MNEMOS_MNEMOS__VAULT_PATH` |
| `MNEMOS_MNEMOS__STRICT_TAG_CONTRACT` | `true` | Enforce M2 tag schema |
| `MNEMOS_API__HOST` | `127.0.0.1` | Default for `mnemos serve` |
| `MNEMOS_API__PORT` | `8787` | Default for `mnemos serve` |
| `MNEMOS_SEARCH__HYBRID_ALPHA` | `0.7` | Vector weight in RRF fusion |
| `MNEMOS_EMBEDDING__PROVIDER` | `nano` | `nano` (mnema-embed-v1, bundled) / `onnx` / `ollama` / `sentence-transformers` |
| `MNEMOS_LLM__PROVIDER` | `ollama` | LLM for synthesis + context filter |
| `MNEMOS_LLM__MODEL` | `qwen2.5:3b` | LLM model name |
| `MNEMOS_AUTO_COLLECT` | `0` | Set `1` to enable MCP auto-collect mode |
| `MNEMOS_LOGGING__LEVEL` | `INFO` | Python logging level |

> **Legacy aliases.** `MNEMOS_DATA_DIR` and `MNEMOS_VAULT__VAULT_PATH` predate the nested `MNEMOS_MNEMOS__*` naming and are kept for compatibility (#139). Both forms work. On conflict the canonical env name — and an explicit value in the config file — wins over the legacy alias; the alias only fills the gap that would otherwise fall through to the default.

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
| `--tags / -T` | `""` | Comma-separated tags (e.g. `project:test,agent:me,mnemos:learning`). |
| `--file / -f` | — | Import the contents of a file. Mutually exclusive with `CONTENT` and `--url`. |
| `--url / -u` | — | Fetch and ingest a URL. Requires tags. |
| `--source / -s` | `cli` | Memory source enum: `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `--type` | `note` | Memory type: `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `--dry-run` | `false` | Validate tags and preview context-filter stats without saving. |
| `--config / -c` | — | Path to `config.yaml`. |

> **Tag contract.** Every entry must have `project:<slug>`, `agent:<slug>`, and at least one `mnemos:<subtype>`. The CLI enforces this in strict mode (the default). See [tag-contract.md](tag-contract.md) for the full schema.

### Examples

```bash
# Inline content
mnemos add "Use uv, not pip" --tags project:mnemos agent:tech-writer mnemos:learning

# With a title
mnemos add "Always validate SQL with parameterized queries" \
  --title "SQL safety rule" \
  --tags "project:mnemos,agent:security,mnemos:rule,severity:high"

# From a file
mnemos add --file ~/notes/architecture.md --tags project:mnemos agent:tech-lead mnemos:decision

# From a URL (fetches, extracts, saves)
mnemos add --url https://example.com/article --tags project:research agent:user mnemos:learning

# From stdin
echo "Pinned CVE-2026-45829 in chromadb 1.5.9" \
  | mnemos add --tags project:mnemos agent:sre mnemos:bug-pattern,severity:medium
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
| `--tags / -T` | — | Comma-separated tags to filter by. |
| `--include-raw / --published-only` | `--include-raw` | Include `raw`/`processing` entries (default), or restrict to `published` knowledge. |
| `--status` | — | Filter by status (`raw`/`processing`/`processed`/`published`/`archived`); takes precedence over `--include-raw`. |
| `--config / -c` | — | Path to `config.yaml`. |

The score is the fused RRF score, with 0.0 = no match and 1.0 = top hit. By default raw entries are searched too — a just-added memory stays `raw` until the knowledge pipeline publishes it; use `--published-only` to restrict results to the vector-index scope.

### Examples

```bash
# Plain search
mnemos search "embedding model"

# With project filter
mnemos search "CVE" --project mnemos --limit 20

# Wide-net recall
mnemos search "decision" --limit 50
```

For richer query power over HTTP, use the API `POST /search` (see [http-api.md#search](http-api.md#search)).

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

## `tags validate`

Validate the Mnemos tag contract across an existing Mnemos vault directory. Reports entries that violate the M2 schema.

```text
mnemos tags validate VAULT_PATH
```

| Argument | Description |
|----------|-------------|
| `VAULT_PATH` (positional) | Path to a Mnemos vault directory (markdown mirror). |

> **Status.** The full vault-scan implementation is not yet wired in (`# TODO (M2): scan SQLite + vault markdown files`). For now the command prints a placeholder. Use `mnemos stats` and the HTTP API `GET /memories?project=...` to inspect tags via SQLite instead.

### Example

```bash
mnemos tags validate ~/.mnemos/vault
```

---

## `workflow`

Manage the workflow lifecycle of a memory through the server-enforced state machine (`open`, `in-progress`, `blocked`, `resolved`, `done`, `withdrawn`). The state machine and its guardrails live in `MemoryManager`; the CLI only translates violations into a red error line and exit code 1.

### `workflow get`

Show the current workflow status and lock owner for a memory.

```text
mnemos workflow get MEMORY_ID
```

### `workflow set`

Transition a memory's workflow status.

```text
mnemos workflow set MEMORY_ID --to STATUS --actor ACTOR [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `MEMORY_ID` (positional) | — | Target memory id. |
| `--to` | — (required) | Target status: `open`, `in-progress`, `blocked`, `resolved`, `done`, `withdrawn`. |
| `--actor` | — (required) | Free-form actor id (Phase 1 weak identity). |
| `--reason` | `""` | Human-readable reason. Required with `--force`. |
| `--force` | `false` | Override a lock held by another actor (requires `--reason`). |
| `--config / -c` | — | Path to `config.yaml`. |

### `workflow history`

Show the workflow transition audit log for a memory (newest first).

```text
mnemos workflow history MEMORY_ID [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `MEMORY_ID` (positional) | — | Target memory id. |
| `--limit` | `50` | Max rows to show (newest first). |
| `--config / -c` | — | Path to `config.yaml`. |

### Example

```bash
ID=550e8400-e29b-41d4-a716-446655440000

mnemos workflow set "$ID" --to in-progress --actor tech-writer
mnemos workflow get "$ID"
mnemos workflow history "$ID" --limit 20
```

### Related

- MCP tool: [`mnemos_workflow`](mcp-tools.md#mnemos_workflow)

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
| `version` | Mnemos version (currently `4.0.0`) |
| `data_dir` | Resolved data directory |
| `vault_path` | Resolved vault directory |
| `total` | Total memory count (any status) |
| `by_status` | Dict of `raw` / `processing` / `processed` / `published` / `archived` |
| `vectors` | Number of vectors in the local vector index (`vectors.db`) |

### Example

```bash
mnemos stats
# status: ok
# version: 4.0.0
# data_dir: /home/you/.mnemos/data
# vault_path: /home/you/.mnemos/vault
# total: 142
# by_status: {'raw': 5, 'processing': 0, 'processed': 12, 'published': 120, 'archived': 5}
# vectors: 120
```

---

## `fts`

FTS5 index management. One action is currently defined: `rebuild`.

```text
mnemos fts ACTION
```

| Argument | Description |
|----------|-------------|
| `ACTION` (positional) | `rebuild` — rebuild the FTS5 index and report the number of rows indexed. Any other value exits with an error. |

### Example

```bash
mnemos fts rebuild
# ✓ FTS5 index rebuilt: 142 rows indexed
```

---

## `processor`

Background processor (knowledge pipeline) management: inspect the queue, run a manual pass, or start / stop the background loop.

```text
mnemos processor ACTION
```

| Argument | Description |
|----------|-------------|
| `ACTION` (positional) | `status` — queue depth, last processed timestamp, running flag. `run` — one synchronous pipeline pass (cluster → synthesize → quality gate → publish). `start` — start the background processor. `stop` — stop it. |

The `run` summary reports `clusters`, `synthesized`, `published`, and `failed_quality_gate` counts.

### Example

```bash
mnemos processor run
#   clusters: 3
#   synthesized: 3
#   published: 2
#   failed_quality_gate: 1
```

### Related

- HTTP equivalent: [`POST /process`](http-api.md#post-process--run-end-to-end-pipeline)

---

## `reindex`

Rebuild the vector index for all published memories — re-embeds every `published` entry and upserts it into `vectors.db`. Use after enabling embeddings or switching embedding models.

```text
mnemos reindex [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--batch-size / -b` | `100` | Batch size for embedding. |
| `--config / -c` | — | Path to `config.yaml`. |

### Example

```bash
mnemos reindex --batch-size 50
#   total: 120
#   indexed: 120
#   failed: 0
```

---

## `filter`

Run the Context Filter (M10) on a memory and print the clean content plus reduction stats. With `--all`, re-runs the filter over every memory and reports aggregate counts.

```text
mnemos filter [MEMORY_ID] [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `MEMORY_ID` (positional) | — | Memory to filter. Omit when using `--all`. |
| `--profile / -p` | auto-detected | `log`, `terminal`, `code`, `docs`, `web`, or `default`. |
| `--budget / -b` | — | Token budget for truncation. |
| `--all` | `false` | Re-run the filter on ALL memories; existing `clean_content` is overwritten with fresh filter output. |
| `--config / -c` | — | Path to `config.yaml`. |

> Re-filtering with a different profile produces different `clean_content`. The filter is idempotent only when the same profile is used.

### Example

```bash
mnemos filter 550e8400-e29b-41d4-a716-446655440000 --profile terminal
# ✓ Filtered: 550e8400-e29b-41d4-a716-446655440000
#   profile: terminal
#   clean_content:
#   ...
```

### Related

- [context-filter.md](context-filter.md) — profiles, pipeline stages, auto-filter behaviour
- MCP tool: [`mnemos_filter`](mcp-tools.md#mnemos_filter)

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
| `--log-file` | — | Override the config log-file path; passing it enables file logging. |
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

# Enable file logging without touching the config file
mnemos serve --log-file ~/.mnemos/logs/serve.log
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

See [mcp-tools.md](mcp-tools.md) for the full tool list and [getting-started.md#run-the-mcp-server](getting-started.md#connect-your-harness-mcp) for the VS Code wiring.

---

## `migrate from-ai-brain`

One-shot migration from a legacy `ai-brain` install (M13).

```text
mnemos migrate from-ai-brain [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--source` | `~/.ai-brain` | Legacy ai-brain data directory (must contain `ai_brain.db`). |
| `--vault` | `~/brain-vault` | Legacy ai-brain vault directory (Obsidian mirror). |
| `--dry-run` | `false` | Show what would be migrated, write nothing. |
| `--config / -c` | — | Path to `config.yaml`. |

The migrator:

- Translates legacy `source` values (e.g. `telegram` → `mcp`).
- **Patches the tag contract** — every legacy entry gets `project:legacy`, `agent:unknown`, `mnemos:legacy` added if missing.
- Preserves the original `status` (`raw` / `processing` / `processed` / `published` / `archived`).
- Migrates `content_ru` / `content_en` columns into `metadata` (no data loss).
- Migrates `parent_ids` into `metadata.parent_ids`.

### Examples

```bash
# Dry run first (recommended)
mnemos migrate from-ai-brain --dry-run

# Real run with default paths
mnemos migrate from-ai-brain

# From a tarball restore
mnemos migrate from-ai-brain --source /tmp/restore/.ai-brain --vault /tmp/restore/brain-vault
```

Output is a one-line summary:

```text
✓ Memories migrated: 1 247
✓ Vault files migrated: 1 247
```

If you see `Errors: N`, the `summary.errors` list (printed to stderr at DEBUG level) tells you which rows failed. They are typically schema-corrupt rows that you can ignore or fix by hand in SQLite.

---

## `auth`

Manage API auth tokens and TOTP 2FA (ADR-0014). Two sub-groups: `auth token` (bearer tokens) and `auth totp` (second factor). Token secrets are stored hashed in the SQLite DB next to your memories.

### `auth token create`

Mint a new bearer token and print it **once**.

| Option | Default | Description |
|--------|---------|-------------|
| `--name / -n` | — | Human-readable label. |
| `--expires / -e` | — | ISO-8601 expiry, e.g. `2027-01-01`. Naive dates are normalised to UTC. |
| `--no-totp` | `false` | Create a token usable directly as a bearer without the login/verify/session flow (sets `totp_required=false`). By default tokens require TOTP. |
| `--config / -c` | — | Path to `config.yaml`. |

### `auth token list`

List all tokens — IDs and metadata only, never secrets.

### `auth token revoke TOKEN_ID`

Permanently revoke a token (positional `TOKEN_ID` argument).

### `auth totp`

| Subcommand | Required options | Purpose |
|------------|------------------|---------|
| `enroll` | `--token-id` | Generate a TOTP secret and print the provisioning URI + optional ASCII QR. Requires `MNEMOS_API__TOTP_MASTER_KEY` to encrypt the secret. |
| `disable` | `--token-id` | Remove the TOTP secret from a token (disables 2FA for it). |
| `test` | `--token-id`, `--code` | Verify a 6-digit code against the enrolled secret (operator smoke-test). |

### Example

```bash
mnemos auth token create --name "laptop" --expires 2027-01-01
# ✓ Token created:
#   token_id : 7c9e6679-7425-40de-944b-e07fc1f90ae7
#   bearer   : <plaintext token — store it now, it will not be shown again>
```

---

## `completion`

Install shell completion for the `mnemos` CLI. With no arguments it auto-detects the current shell from `$SHELL`, writes the completion script to `~/.mnemos/completion/mnemos.<shell>`, and adds a single guarded `source` line to your rc file (`~/.bashrc` / `~/.zshrc`; fish auto-sources its completions directory). Idempotent — re-running does not duplicate the source line and migrates away the old `eval`-based format.

```text
mnemos completion [SHELL] [OPTIONS]
```

| Argument / Option | Default | Description |
|-------------------|---------|-------------|
| `SHELL` (positional) | auto from `$SHELL` | `bash`, `zsh`, or `fish`. |
| `--show-instructions` | `false` | Print manual install steps for all supported shells; no files modified. |

### Example

```bash
mnemos completion bash
# ✓ Installed bash completion → /home/you/.mnemos/completion/mnemos.bash
#   Source line added to /home/you/.bashrc
#   Restart your shell or run: source /home/you/.bashrc
```

---

## `doctor`

Run Mnemos health checks: config, data dir, vault, SQLite DB, vector store, MCP server registration, integration layer, agent wiring, tag contract.

```text
mnemos doctor [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--json` | `false` | Emit results as JSON (for scripting / CI) instead of a table. |
| `--fix` | `false` | Auto-fix WARN-level checks (stale integration, unwired agents, missing MCP registration). FAIL-level checks are not auto-fixable. |
| `--dry-run` | `false` | With `--fix`: preview what would be fixed without executing. |
| `--paths` | `false` | Print all resolved paths (data dir, vault, logs, cache, completion) and exit. |

Exit codes: `0` = all checks pass, `1` = one or more checks failed, `2` = warnings only.

> `doctor` does not take `--config`; it reads the config from `$MNEMOS_CONFIG` or the default search path (`./config.yaml`, `~/.mnemos/config.yaml`).

### `doctor --paths`

Shows every path Mnemos uses, resolved from config and environment:

```bash
mnemos doctor --paths
# data_dir:      /home/you/.mnemos/data
# vault_path:    /home/you/.mnemos/vault
# log_file:      /home/you/.mnemos/logs/mnemos.log
# cache_dir:     /home/you/.mnemos/cache
# completion:    /home/you/.mnemos/completion
# config_file:   /home/you/.mnemos/config.yaml
```

Use this to verify the consolidated `~/.mnemos/` layout after upgrade or migration.

### `doctor --fix` and `--dry-run`

With `--fix`, WARN-level checks are repaired in place (stale integration → `integration update`, unwired agents → `integration setup --wire-agents --all`, missing MCP registration → MCP setup); the affected checks are then re-run and the new status reported. Combine with `--dry-run` to preview the fixes without executing them. `--json --fix` reports the `fixed` / `fix_skipped` lists in the JSON payload.

```bash
# Preview only
mnemos doctor --fix --dry-run

# Apply fixes
mnemos doctor --fix

# CI: machine-readable verdict, no fixes
mnemos doctor --json
```

---

## `logs`

View pipeline traces (M6 explainability layer) — a compact table over the append-only `traces` table.

```text
mnemos logs [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--task / -t` | — | Filter by task label (`cluster`, `synthesize`, `publish`, `recall`). |
| `--project / -p` | — | Filter by project slug. |
| `--limit / -l` | `50` | Maximum number of traces to show. |
| `--since` | — | Only traces after this ISO date (e.g. `2026-06-01`). |
| `--follow / -f` | `false` | Poll every 2 s and print new rows (`tail -f` style). Stop with `Ctrl+C`. |
| `--config / -c` | — | Path to `config.yaml`. |

### Example

```bash
mnemos logs --task cluster --project mnemos --limit 20

# Watch the pipeline live
mnemos logs --follow
```

### Related

- HTTP equivalent: [`GET /traces`](http-api.md#get-traces--list-pipeline-traces)

---

## `scanner`

Background secrets scanner — Layer 2 of the federation defence-in-depth. The scanner periodically re-scans the corpus for secrets missed at write time and auto-tags hits `mnemos:no-federate` so they are excluded from all external exchange. These subcommands are the manual trigger and status view.

### `scanner run`

Run one scanner pass synchronously and print the summary.

| Option | Default | Description |
|--------|---------|-------------|
| `--full` | `false` | Force a full corpus scan (ignore the incremental boundary). Default is incremental: only records modified since the last successful scan. |
| `--config / -c` | — | Path to `config.yaml`. |

The summary reports `records_scanned`, `records_tagged`, `records_skipped`, `duration_sec`, matched pattern names with counts (never raw values), and the timestamp.

### `scanner status`

Print the scanner's current state — enabled, running, configured interval and incremental mode, last scan timestamp, cumulative records tagged, next scheduled run.

### Example

```bash
mnemos scanner run --full
# ✓ Scan complete (full)
#   records_scanned: 142
#   records_tagged:   0
#   records_skipped:  2
#   duration_sec:     1.83
#   patterns_matched: (none)
#   timestamp:        2026-09-05T12:00:00+00:00
```

### Related

- [sync.md](sync.md#mnemosno-federate-exclusion) — what `mnemos:no-federate` excludes

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error (missing argument, invalid tag, etc.) |
| 2 | `mnemos doctor`: one or more checks warn, nothing is broken |

The CLI does not return non-zero for "no results" — `mnemos search` exits 0 with an empty table.

---

## See also

- [getting-started.md](getting-started.md) — first-run walkthrough
- [mcp-tools.md](mcp-tools.md) — the same capabilities exposed over MCP
- [http-api.md](http-api.md) — the same capabilities exposed over HTTP
- [context-filter.md](context-filter.md) — filter profiles used by `add --dry-run` and `filter`
- [tag-contract.md](tag-contract.md) — the tag schema enforced here
- [runbooks/migrate.md](../admin/runbooks/migrate.md) — operational migration guide
- [architecture overview](../architecture/overview.md) — system shape

---

_Last updated: 2026-09-05_
