# Getting Started

**🌐 Language / Язык:** English · [Русский](../../ru/user/getting-started.md)

> Complete first-run guide for Mnemos — from a one-line install to your first memory, first search, and a connected agent harness.

Mnemos is on PyPI — no cloning, no building, no venv knowledge required. This page walks you through the whole first run. Every command is runnable on a clean Linux / macOS / WSL2 box.

For higher-level context, see [architecture overview](../architecture/overview.md). For every CLI subcommand, see [cli-reference.md](cli-reference.md). For every MCP tool, see [mcp-tools.md](mcp-tools.md). For every HTTP endpoint, see [http-api.md](http-api.md).

---

## Install (one command)

```bash
pip install "mnemos-memory-server[mcp]"
```

That is the whole install:

- **`mnemos-memory-server`** is the PyPI package — the memory & knowledge server, the `mnemos` CLI, the MCP server, and the REST API in one wheel.
- **`[mcp]`** adds the MCP SDK — keep it; the MCP server (`mnemos mcp-server`) is the primary integration surface for agent harnesses.
- **Nothing else is downloaded, ever.** The default embedding model (`mnema-embed-v1`, ~30 MB) is bundled inside the wheel — search works fully offline, on CPU, with no API keys.

> ⚠️ **The package name is `mnemos-memory-server`.** `pip install mnemos` installs an *unrelated* project that owns the `mnemos` name on PyPI.

### Isolated variant (recommended for harness wiring)

Harnesses launch the `mnemos` command from `PATH`. A tool install puts it there without touching your project environments:

```bash
uv tool install "mnemos-memory-server[mcp]"
# or
pipx install "mnemos-memory-server[mcp]"
```

### Scripted variant (zero decisions)

The installer creates an isolated venv at `~/.mnemos/venv`, drops a `mnemos` launcher into `~/.local/bin`, and offers to wire VS Code MCP and deploy the integration pack right in the same run:

```bash
curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/install.sh | bash
```

### Other install options

| Method | Command |
|--------|---------|
| Pin a version | `pip install "mnemos-memory-server[mcp]==4.0.0"` |
| Container one-liner | `… install.sh \| bash -s -- --container` — see [container-deployment.md](../admin/runbooks/container-deployment.md) |
| From source (contributors) | `git clone https://github.com/Korrnals/mnemos && cd mnemos && uv venv && source .venv/bin/activate && uv pip install -e ".[dev,mcp]"` |

<details>
<summary><strong>Optional extras</strong> — external LLM providers, only if you need them</summary>

Mnemos calls external LLMs for pipeline synthesis (M4) and enrichment — never for storing or searching. Install only what you need:

```bash
uv pip install "mnemos-memory-server[ollama]"      # local Ollama (default provider)
uv pip install "mnemos-memory-server[openai]"      # OpenAI / Azure OpenAI
uv pip install "mnemos-memory-server[anthropic]"   # Anthropic Claude
uv pip install "mnemos-memory-server[gemini]"      # Google Gemini
```

The default provider is `ollama` pointing at `http://localhost:11434`. See [config.example.yaml](../../../config.example.yaml) for the full provider list.

</details>

### Prerequisites

| Tool | Version | Why |
|------|---------|-----|
| Python | ≥ 3.11 | Runtime floor (`pip` handles it — no manual venv needed) |
| `uv` or `pipx` | latest | Optional, for the isolated tool install |

> **OS notes.** Mnemos is developed on Linux (Arch, Fedora, Ubuntu 22.04+) and is regularly smoke-tested on macOS. Windows works through WSL2. The systemd unit in `contrib/systemd/` is Linux-only.

> **Hardware.** The bundled `mnema-embed-v1` runs comfortably on a single CPU core. No GPU. A 2 vCPU / 2 GB VM is enough for personal use.

---

## First memory (CLI)

```bash
mnemos add "Hello world" --tags project:test agent:getting-started mnemos:learning
```

Expected output:

```text
✓ Saved: Hello world (550e8400-e29b-41d4-a716-446655440000)
```

Mnemos automatically:

1. **Wrote the entry to SQLite** at `~/.mnemos/data/mnemos.db` (created on first run).
2. **Mirrored it to your Obsidian vault** at `~/.mnemos/vault/` as a markdown file with YAML frontmatter.
3. **Validated the tag contract** — `project:test` + `agent:getting-started` + `mnemos:learning` is a valid trio. Skip one and you get `❌ Tag contract violation: ...` instead.

The tag contract is documented in [tag-contract.md](tag-contract.md). The short version: every memory needs **exactly one** `project:<slug>`, **exactly one** `agent:<slug>`, and **at least one** `mnemos:<subtype>` (e.g. `mnemos:learning`, `mnemos:bug-pattern`, `mnemos:decision`).

> **Note.** Newly added memories start in the `raw` state. The background processor (running in both MCP and HTTP API modes) automatically clusters, synthesises, quality-gates, and publishes them. The vector search index only includes `published` memories. To rebuild it manually: `mnemos reindex` (CLI) or `POST /reindex` (HTTP API).

---

## First search

Hybrid search combines SQLite FTS5 full-text with vector similarity and merges the rankings using Reciprocal Rank Fusion (RRF):

```bash
mnemos search "hello"
```

Useful flags:

| Flag | Effect |
|------|--------|
| `--limit N` / `-l N` | Max results (default 10) |
| `--project P` / `-p P` | Restrict to a project slug |

For programmatic access with more options (vector weight, raw content, tag filter), use the HTTP API — see [http-api.md#search](http-api.md#search).

---

## Connect your harness (MCP)

The MCP server is the primary integration surface: your agent harness spawns `mnemos mcp-server` over stdio and gets the full `mnemos_*` tool set. Pick your harness:

| Harness | Fastest path |
|---------|--------------|
| VS Code Copilot | `curl -fsSL …/scripts/mcp-setup.sh \| bash`, then reload the window |
| Claude Code | `claude mcp add --scope user mnemos -- mnemos mcp-server` |
| Cursor | paste one line into `~/.cursor/mcp.json` |
| OpenCode | paste one block into `~/.config/opencode/opencode.json` |
| Codex / Windsurf | one TOML / JSON block each |
| ZCode, pi, Hermes Agent | `mnemos integration setup --target zcode` / `--target pi` / `--target hermes` |
| Anything else | [adapter-template.md](../../../integrations/adapter-template.md) |

**The full copy-paste instructions for every harness live on one page: [Connect Mnemos to any harness](../../../integrations/mcp-presets.md).** The behavioral layer — instructions, skills, and prompt mode that make agents actually *use* the memory — is a separate one-pass step:

```bash
mnemos integration setup
```

See the [integration guide](integration-guide.md) for targets and flags.

Manual VS Code reference — user- or workspace-scope `mcp.json`:

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

> **Tip — auto-collect mode.** Set `MNEMOS_AUTO_COLLECT=1` in the server's `env` block to make Mnemos nudge your agent to call `mnemos_save_context` every ~6 tool calls. See [mcp-tools.md#auto-collect-mode](mcp-tools.md#auto-collect-mode) for the trade-offs.

---

## Run the HTTP API (optional)

For non-MCP clients, dashboards, and A2A traffic:

```bash
mnemos serve --host 127.0.0.1 --port 8787
```

| Endpoint | Purpose |
|----------|---------|
| `http://127.0.0.1:8787/health` | Liveness check |
| `http://127.0.0.1:8787/metrics` | Stats (Prometheus-style) |
| `http://127.0.0.1:8787/docs` | Swagger UI |
| `http://127.0.0.1:8787/v1/sessions` | A2A sessions API (M16) |

> **Security.** The default bind is `127.0.0.1`. Do not expose this port without a reverse proxy with authentication in front — see [security.md](../admin/security.md).

Smoke-test it:

```bash
curl -s http://127.0.0.1:8787/health | jq
# {"status":"ok"}
```

---

## Verify your installation

```bash
mnemos doctor
```

runs health checks over the store, config, MCP transport, and known harness registrations — and prints one PASS/WARN/FAIL line per check. `mnemos doctor --fix` auto-resolves the common warnings (stale integration files, unwired agents, missing MCP registration).

To run the full development gate (contributors only): clone the repo, `uv pip install -e ".[dev,mcp]"`, then `make verify` — ruff + mypy `--strict` + bandit + pip-audit + the test suite. If `pip-audit` complains about a pinned CVE, see the [dependency-updates runbook](../admin/runbooks/dependency-updates.md).

---

## Migrate from legacy ai-brain

If you have an existing legacy `ai-brain` install (`~/.ai-brain/ai_brain.db` + `~/brain-vault/`), Mnemos imports it in one command. Dry-run first:

```bash
mnemos migrate from-ai-brain --dry-run
```

Read the summary, then run for real:

```bash
mnemos migrate from-ai-brain
```

The migrator translates legacy source types, patches the tag contract (`project:legacy`, `agent:unknown`, `mnemos:legacy`), preserves entry statuses, and migrates the `content_ru` / `content_en` columns into `metadata` (no data loss). Use `--source PATH` and `--vault PATH` for non-default locations.

---

## Configuration

Mnemos reads `config.yaml` from the current directory or `~/.mnemos/config.yaml`. See [config.example.yaml](../../../config.example.yaml) for the full schema. The most useful knobs:

| Setting | Default | Purpose |
|---------|---------|---------|
| `mnemos.data_dir` | `~/.mnemos/data` | SQLite store + vector index |
| `mnemos.vault_path` | `~/.mnemos/vault` | Obsidian mirror |
| `mnemos.strict_tag_contract` | `true` | Enforce the tag contract (set `false` only for legacy imports) |
| `embedding.provider` | `nano` | `nano` (mnema-embed-v1, bundled) / `onnx` / `ollama` / `sentence-transformers` |
| `search.hybrid_alpha` | `0.7` | Weight of the vector leg in RRF (0.0 = pure FTS, 1.0 = pure vector) |
| `api.host` / `api.port` | `127.0.0.1` / `8787` | `mnemos serve` defaults |
| `llm.provider` / `llm.model` | `ollama` / `qwen2.5:3b` | Pipeline synthesis & context filter |

Any of these can be overridden by env vars (`MNEMOS_*`, with `__` for nesting):

```bash
MNEMOS_SEARCH__HYBRID_ALPHA=0.5 mnemos search "deployment"
```

### Logging

Mnemos logs to `~/.mnemos/logs/mnemos.log` by default (rotating, 10 MB × 3 files):

```yaml
logging:
  level: INFO                    # DEBUG | INFO | WARNING | ERROR
  log_file: ~/.mnemos/logs/mnemos.log
  max_file_size_mb: 10
  backup_count: 3
```

CLI: `mnemos --verbose serve` for DEBUG level, `mnemos serve --log-file /path/to/log` to override.

---

## Troubleshooting

### `mnemos` command not found

If you installed with plain `pip` into a venv, the venv must be active. Prefer the isolated install (`uv tool` / `pipx` / `install.sh`) — it puts `mnemos` on `PATH` in every shell (`~/.local/bin`; add it to `PATH` if your distro does not).

### `mnemos mcp-server` fails with an import error about `mcp`

The `[mcp]` extra is missing: `pip install "mnemos-memory-server[mcp]"`.

### Search returns only "raw" entries

The vector index only includes `published` memories; new entries start `raw` and are published by the background processor. To publish immediately, set `status: "published"` on creation via the HTTP API, or let the pipeline run.

### `sqlite3.OperationalError: database is locked`

Another `mnemos` process (CLI, MCP, or HTTP) holds the write lock. SQLite uses WAL mode but only one writer is allowed at a time. Close the other process, or wait for its transaction to commit (default busy-timeout is 5 s). For multi-harness setups, give each harness its own data dir — see the one-owner-per-store note in the [integration guide](integration-guide.md).

### MCP server runs but no tools appear in the harness

1. Check the harness config parses (valid JSONC / TOML, no trailing commas).
2. Restart the harness after editing its config.
3. Probe the wire directly: `printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.0.0"}}}\n' | mnemos mcp-server` — a JSON-RPC reply with `"serverInfo":{"name":"mnemos"...}` means the server side is fine.
4. Run `mnemos doctor` — the MCP transport and registration checks point at the broken link.

---

## Where to go next

| If you want to… | Read |
|-----------------|------|
| Connect a specific harness (VS Code, Claude Code, Cursor, OpenCode, Codex, Windsurf, pi, Hermes…) | [Connect Mnemos to any harness](../../../integrations/mcp-presets.md) |
| Deploy the behavioral pack (instructions / skills / prompts / agent wiring) | [integration-guide.md](integration-guide.md) |
| See every CLI subcommand | [cli-reference.md](cli-reference.md) |
| See every MCP tool | [mcp-tools.md](mcp-tools.md) |
| See every HTTP endpoint | [http-api.md](http-api.md) |
| Understand the system shape | [architecture overview](../architecture/overview.md) |
| Read the tag schema | [tag-contract.md](tag-contract.md) |
| Run an operational task | [admin/runbooks/install.md](../admin/runbooks/install.md) |
| Review security boundaries | [security.md](../admin/security.md) |
| See why a decision was made | [project/adr/](../../project/adr/) |

---

_Last updated: 2026-09-05_
