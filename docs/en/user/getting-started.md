# Getting Started

**🌐 Language / Язык:** English · [Русский](../../ru/user/getting-started.md)

> Complete first-run guide for Mnemos — from install to your first memory, search, and agent recall.

This page walks you through a working Mnemos install, your first memory, your first search, and your first MCP / HTTP server start. Every command in this document is runnable on a clean Linux / macOS / WSL2 box.

For higher-level context, see [architecture overview](../architecture/overview.md). For every CLI subcommand, see [cli-reference.md](cli-reference.md). For every MCP tool, see [mcp-tools.md](mcp-tools.md). For every HTTP endpoint, see [http-api.md](http-api.md).

---

## Prerequisites

Mnemos needs Python 3.11 or newer and `git`. We recommend `uv` for fast, isolated installs.

| Tool | Version | Why |
|------|---------|-----|
| Python | ≥ 3.11 | Pydantic v2, modern type hints, StrEnum |
| `uv` | latest | Fast, hermetic Python package manager |
| `git` | any | To clone the repo (skip if installing from PyPI) |
| `make` | any | Convenience targets: `make verify`, `make test` |

> **OS notes.** Mnemos is developed on Linux (Arch, Fedora, Ubuntu 22.04+) and is regularly smoke-tested on macOS. Windows works through WSL2. The systemd unit in `contrib/systemd/` is Linux-only.

> **Hardware.** The default ONNX embedding model (`all-MiniLM-L6-v2`) is ~25 MB and runs comfortably on a single CPU core. No GPU is required. A 2 vCPU / 2 GB VM is enough for personal use.

---

## Install

```bash
git clone https://github.com/Korrnals/mnemos.git
cd mnemos
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

If you don't have `uv`:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `[dev]` extra adds `pytest`, `ruff`, `mypy`, `bandit`, and `pip-audit` so you can run the full verification suite.

### Install options

`pip install -e ".[dev]"` — the `.` is the package itself (`mnemos`); `[dev]` and `[mcp]` are **optional extras**. You are not missing a package name.

| Method | Command |
|--------|---------|
| Editable dev (recommended for contributors) | `pip install -e ".[dev,mcp]"` |
| From source, versioned | `pip install ".[mcp]"` |
| Released wheel | `pip install https://github.com/Korrnals/mnemos/releases/download/v1.1.1/mnemos-1.1.1-py3-none-any.whl` |
| Container | `podman run -d -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 --env MNEMOS_API__TOTP_MASTER_KEY=<key> ghcr.io/korrnals/mnemos:1.1.1` — see [container-deployment.md](../admin/runbooks/container-deployment.md) |

### Optional LLM provider extras

Mnemos can call external LLMs for synthesis (M4) and the context filter (M10). Install only what you need:

```bash
uv pip install -e ".[ollama]"     # local Ollama
uv pip install -e ".[openai]"     # OpenAI / Azure OpenAI
uv pip install -e ".[anthropic]"   # Anthropic Claude
uv pip install -e ".[gemini]"     # Google Gemini
```

The default provider is `ollama` pointing at `http://localhost:11434`. See [architecture overview](../architecture/overview.md#llm-providers) for the full provider matrix.

---

## Verify

Run the full verification gate. All five steps must be green before you start.

```bash
make verify
```

The `verify` target runs, in order:

| Step | Tool | What it checks |
|------|------|----------------|
| 1 | `ruff` | Lint (PEP-8, import order, common bugs) |
| 2 | `pytest` | Test suite (unit + integration) |
| 3 | `bandit` | Security lint (M9) |
| 4 | `pip-audit` | Dependency CVE scan (M15) |
| 5 | reminder | Prints the pinned CVE reminder |

A clean run ends with `✅ All verification checks passed`.

If `pip-audit` complains about a pinned CVE, see [dependency-updates runbook](../admin/runbooks/dependency-updates.md) for the weekly-review workflow.

---

## First memory (CLI)

The CLI uses Typer and prints a Rich-formatted table. Add your first entry:

```bash
mnemos add --content "Hello world" --tags project:test agent:getting-started gcw:learning
```

Expected output:

```text
✓ Saved: Hello world (550e8400-e29b-41d4-a716-446655440000)
```

Mnemos automatically:

1. **Wrote the entry to SQLite** at `~/.mnemos/mnemos.db`.
2. **Mirrored it to your Obsidian vault** at `~/mnemos-vault/` as a markdown file with YAML frontmatter.
3. **Validated the tag contract** — `project:test` + `agent:getting-started` + `gcw:learning` is a valid M2 trio. If you skip one, you get `❌ Tag contract violation: ...` instead.

The tag contract is documented in [tag-contract.md](tag-contract.md). The short version: every memory needs **exactly one** `project:<slug>`, **exactly one** `agent:<slug>`, and **at least one** `gcw:<subtype>` (e.g. `gcw:learning`, `gcw:bug-pattern`, `gcw:decision`).

> **Note.** Newly added memories start in the `raw` state. The vector search index only includes `published` memories. To move a memory to `published`, run the pipeline (see [install runbook](../admin/runbooks/install.md)) or use the HTTP API `POST /process` (see [http-api.md](http-api.md#knowledge-pipeline-m4)).

---

## First search

Hybrid search combines SQLite FTS5 with vector similarity and merges the rankings using Reciprocal Rank Fusion (RRF).

```bash
mnemos search "hello"
```

Expected output (Rich table):

```text
┏━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Score ┃ Title      ┃ Tags                                  ┃ Status   ┃
┡━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ 1.000 │ Hello world│ project:test, agent:getting-started, │ raw      │
│       │            │ gcw:learning                          │          │
└───────┴────────────┴──────────────────────────────────────┴──────────┘
```

Useful flags:

| Flag | Effect |
|------|--------|
| `--limit N` / `-l N` | Max results (default 10) |
| `--project P` / `-p P` | Restrict to a project slug |

For programmatic access with more options (vector weight, raw content, tag filter), use the HTTP API — see [http-api.md#search](http-api.md#search).

---

## First agent recall

`recall` returns recent memories. Filter by agent to get only what one specific Copilot agent saved (M3):

```bash
mnemos recall --agent getting-started
```

Expected output (Rich list):

```text
Hello world  (550e8400…)
  tags: project:test, agent:getting-started, gcw:learning
```

Combine with `--project` to scope further:

```bash
mnemos recall --agent getting-started --project test --limit 5
```

This is the same data the MCP tool [`mnemos_agent_recall`](mcp-tools.md#mnemos_agent_recall) exposes to Copilot agents.

---

## Run the MCP server

The MCP server speaks stdio JSON-RPC — VS Code Copilot talks to it directly. **It is the primary integration surface for GCW agents.**

> The `mcp` package is an optional extra — install it first with `pip install -e ".[mcp]"` (or `uv pip install -e ".[mcp]"`).

```bash
mnemos mcp-server
```

The process blocks on stdin/stdout; it does not bind any TCP port. Stop it with `Ctrl+C`.

### VS Code `mcp.json` snippet

Add this to your VS Code `mcp.json` (User or Workspace):

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

After saving, VS Code lists the `mnemos_*` tools in the Copilot Chat "tools" picker. See [mcp-tools.md](mcp-tools.md) for the full tool catalogue.

> **Tip — auto-collect mode.** Set `MNEMOS_AUTO_COLLECT=1` in the env above to make Mnemos nag your agent to call `mnemos_save_context` every ~6 tool calls. See [mcp-tools.md#auto-collect](mcp-tools.md#auto-collect-mode) for the trade-offs.

---

## Run the HTTP API

For agent-to-agent (A2A) traffic and custom clients, start the HTTP server:

```bash
mnemos serve --host 127.0.0.1 --port 8000
```

| Endpoint | Purpose |
|----------|---------|
| `http://127.0.0.1:8000/health` | Liveness check |
| `http://127.0.0.1:8000/metrics` | Stats (Prometheus-style) |
| `http://127.0.0.1:8000/docs` | Swagger UI |
| `http://127.0.0.1:8000/redoc` | ReDoc |
| `http://127.0.0.1:8000/openapi.json` | OpenAPI 3.1 schema |
| `http://127.0.0.1:8000/v1/sessions` | A2A sessions API (M16) |

> **Security.** The default bind is `127.0.0.1`. Do not expose this port to a network without putting a reverse proxy with authentication in front. Mnemos is not designed as a public service — see [security.md](../admin/security.md) for the threat model.

Smoke-test it:

```bash
curl -s http://127.0.0.1:8000/health | jq
# {"status":"ok"}
```

See [http-api.md](http-api.md) for every endpoint, request body, and response shape.

---

## Migrate from legacy ai-brain

If you have an existing legacy `ai-brain` install (`~/.ai-brain/ai_brain.db` + `~/brain-vault/`), Mnemos can import it in one command.

### Dry-run first

```bash
mnemos migrate-from-ai-brain --dry-run
```

This prints a summary (no writes). Read the output, confirm the counts match your expectation, then run for real:

```bash
mnemos migrate-from-ai-brain
```

The migrator:

- Translates legacy `source: telegram` → `source: mcp`, and similar for every source type.
- **Patches the tag contract** — every legacy entry gets `project:legacy`, `agent:unknown`, `gcw:legacy` added.
- Preserves `status: raw / processing / processed / published / archived`.
- Migrates the `content_ru` / `content_en` columns into `metadata` (no data loss).

> **Path overrides.** Use `--source PATH` and `--vault PATH` to point at a non-default location, e.g. an exported ai-brain tarball.

---

## Configuration

Mnemos reads `config.yaml` from the current directory or `~/.mnemos/config.yaml`. See [config.example.yaml](../../../config.example.yaml) for the full schema. The most useful knobs:

| Setting | Default | Purpose |
|---------|---------|---------|
| `mnemos.vault_path` | `~/mnemos-vault` | Obsidian mirror |
| `mnemos.data_dir` | `~/.mnemos` | SQLite + vector index |
| `mnemos.strict_tag_contract` | `true` | Enforce M2 contract (set `false` only for legacy imports) |
| `embedding.provider` | `chromadb` | `chromadb` / `onnx` / `ollama` / `sentence-transformers` |
| `search.hybrid_alpha` | `0.7` | Weight of vector leg in RRF (0.0 = pure FTS, 1.0 = pure vector) |
| `api.host` / `api.port` | `127.0.0.1` / `8787` | `mnemos serve` defaults |
| `llm.provider` / `llm.model` | `ollama` / `qwen2.5:3b` | M4 synthesis & M10 context filter |

Any of these can be overridden by env vars (`MNEMOS_*`, with `__` for nesting). Example:

```bash
MNEMOS_SEARCH__HYBRID_ALPHA=0.5 mnemos search "deployment"
```

---

## Troubleshooting

### `make verify` fails on `pip-audit`

You probably hit a pinned CVE. The most common is `CVE-2026-45829` in `chromadb 1.5.9`. The current policy is to **ignore it with audit** and re-check weekly. See [dependency-updates runbook](../admin/runbooks/dependency-updates.md) for the workflow.

### First model download is slow

The first time you start Mnemos, the default ONNX embedding model (`all-MiniLM-L6-v2`, ~25 MB) is downloaded from Hugging Face. Subsequent starts are instant. To pre-warm:

```bash
python -c "from mnemos.embeddings import create_embedding_provider; from mnemos.config import load_settings; create_embedding_provider(load_settings().embedding).embed('warm up')"
```

### Search returns only "raw" entries

The vector search index only includes `published` memories. Newly added memories default to `raw`. Either run the pipeline (`POST /process`) or set `status: "published"` on creation. The CLI does not expose a `--status` flag yet — use the HTTP API for now.

### `sqlite3.OperationalError: database is locked`

Another `mnemos` process (CLI, MCP, or HTTP) holds the write lock. SQLite uses WAL mode but only one writer is allowed at a time. Close the other process, or wait for its transaction to commit (default busy-timeout is 5 s).

### MCP server starts but no tools appear in Copilot

1. Check `mcp.json` parses (no trailing commas, valid JSONC).
2. Restart VS Code after editing `mcp.json`.
3. Check the **Output → Model Context Protocol** channel for stderr from `mnemos mcp-server`.
4. Run `mnemos mcp-server` standalone to see Python tracebacks directly.

### `mnemos` command not found

Your virtualenv is not active. Run `source .venv/bin/activate` (or the equivalent on your shell) before invoking `mnemos`. If you installed system-wide with `pipx`, the binary is in `~/.local/bin/mnemos`.

---

## Where to go next

| If you want to… | Read |
|-----------------|------|
| See every CLI subcommand | [cli-reference.md](cli-reference.md) |
| See every MCP tool Copilot can call | [mcp-tools.md](mcp-tools.md) |
| See every HTTP endpoint | [http-api.md](http-api.md) |
| Understand the system shape | [architecture overview](../architecture/overview.md) |
| Read the tag schema | [tag-contract.md](tag-contract.md) |
| Read the A2A sessions contract | [a2a-sessions.md](../architecture/a2a-sessions.md) |
| Run an operational task | [admin/runbooks/install.md](../admin/runbooks/install.md) |
| Review security boundaries | [security.md](../admin/security.md) |
| See why a decision was made | [project/adr/](../../project/adr/) |

---

_Last updated: 2026-06-16_
