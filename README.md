<!-- markdownlint-disable MD041 -->
# Mnemos

> **A memory & knowledge server for AI agents** — named after the Titaness, built for the GCW agent family.

[![CI](https://github.com/Korrnals/mnemos/actions/workflows/ci.yml/badge.svg)](https://github.com/Korrnals/mnemos/actions/workflows/ci.yml) [![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776ab)](pyproject.toml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](pyproject.toml) [![Version](https://img.shields.io/badge/version-1.1.1-blueviolet)](CHANGELOG.md)

```text
    ╔══════════════════════════════════════════════════════════════╗
    ║   M N E M O S   —   μνημοσύνη  ·  memory for machines         ║
    ║   a Titaness's gift to the agents who would inherit her     ║
    ╚══════════════════════════════════════════════════════════════╝
```

---

## The lore

In Hesiod's *Theogony*, **Mnemosyne** (Μνημοσύνη) is the Titaness of memory — she who, by Zeus, gave birth to the nine Muses and through them made the world's remembering possible. Her name is the root of *mnemonic*, and she is what every singer, poet, and philosopher prays to before they begin.

This software carries her name because it is built for the same task: **to make remembering possible for the things that think**. AI agents, unmoored from any single conversation, lose everything that came before. Mnemos gives them a place to lay it down — structured, searchable, governed by contract — so that what they learn does not vanish with the closing of a session. The Muses, after all, were not for the gods' benefit. They were for the songs.

## What Mnemos is

A single-tenant, local-first memory server. Hybrid search (vector + full-text) over a knowledge pipeline (`raw → processing → processed → published`), a per-agent recall surface, a policy engine for automation, an explainability layer, path-scoped rules ingest, and a five-stage context filter that strips the noise from logs and stdout before anything is sent to a model. Three equivalent control surfaces — CLI, HTTP, MCP — over a single in-process core. SQLite for metadata, a local vector index for recall, an Obsidian-compatible vault for humans.

## How it fits together

```mermaid
flowchart TB
    subgraph CLIENTS["Clients"]
        C1(["VS Code · Copilot\nstdio MCP"])
        C2(["CLI — mnemos …"])
        C3(["HTTP API client"])
    end

    subgraph IFACE["Interface Layer"]
        MCP["mcp_server.py"]
        FAPI["api/main.py · FastAPI"]
        TYPER["cli/main.py · Typer"]
    end

    MGR(["MemoryManager\nmanager.py"])

    subgraph PROC["Processing Subsystems"]
        CF["Context Filter\nfilter/"]
        PP["Knowledge Pipeline\npipeline/"]
        RE["Recall Engine\nrecall/"]
        PE["Policy Engine\npolicy/"]
    end

    subgraph BG["Background Services"]
        WA["Watchers\nwatchers/"]
        AC["Auto-collect\nauto_collect.py"]
    end

    subgraph STORE["Storage Layer"]
        SQ[("SQLite\nFTS5 · traces · projects")]
        VS[("Vector Store\nnumpy + SQLite")]
        VLT[("Obsidian Vault\nmarkdown mirror")]
    end

    C1 -->|"stdio"| MCP
    C2 --> TYPER
    C3 --> FAPI
    MCP --> MGR
    TYPER --> MGR
    FAPI --> MGR
    MGR --> CF
    MGR --> PP
    MGR --> RE
    MGR --> SQ
    MGR --> VS
    MGR --> VLT
    CF -.->|"raw + clean"| SQ
    PP -->|"status transitions"| SQ
    PP -->|"published upsert"| VS
    RE -->|"FTS5 MATCH"| SQ
    RE -->|"cosine search"| VS
    PE -->|"schedule / trigger"| MGR
    WA -->|"file events"| MGR
    AC -.->|"checkpoint reminder"| MCP
```

A more thorough walkthrough — data model, state machines, security boundaries, operational concerns — lives in [docs/architecture.md](docs/architecture.md).

## Quick start

```bash
git clone https://github.com/Korrnals/mnemos.git
cd mnemos
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
mnemos add --content "First memory — use uv, not pip" \
           --tags project:mnemos agent:tech-writer gcw:learning
mnemos search "uv vs pip" --limit 5
```

That's the whole loop: install, write, find. For a step-by-step first run including the MCP and HTTP servers, see [docs/getting-started.md](docs/getting-started.md).

## Three surfaces, one core

The same MemoryManager powers all three interfaces — pick the one that fits the client.

| Surface | Use it when… | Reference |
|---------|--------------|-----------|
| **CLI** — `mnemos …` | You live in a shell, want fast ad-hoc add/search, or are scripting cron jobs | [docs/cli-reference.md](docs/cli-reference.md) |
| **HTTP** — `mnemos serve` | You have a non-MCP client (a web UI, a Telegram bot, a CI runner) | [docs/api-reference.md](docs/api-reference.md) |
| **MCP** — `mnemos mcp-server` | You are VS Code Copilot or any MCP-aware agent; this is the path the GCW family takes | [docs/mcp-tools.md](docs/mcp-tools.md) |

The MCP surface also exposes the **A2A Sessions API** (M16) — a persistent backend for multi-step agent conversations. Five endpoints (`POST /v1/sessions`, append-turn, range-load, …) so GCW can survive restarts without losing context. See [docs/a2a-sessions.md](docs/a2a-sessions.md).

## Documentation

| Page | What it covers |
|------|----------------|
| [docs/index.md](docs/index.md) | Top-level docs landing — where to go next |
| [docs/getting-started.md](docs/getting-started.md) | First run: install → first memory → first search → MCP / HTTP |
| [docs/architecture.md](docs/architecture.md) | System shape, data model, state machines, security boundaries |
| [docs/cli-reference.md](docs/cli-reference.md) | Every `mnemos` subcommand with flags, defaults, examples |
| [docs/mcp-tools.md](docs/mcp-tools.md) | Every `mnemos_*` tool exposed to VS Code Copilot |
| [docs/api-reference.md](docs/api-reference.md) | Every HTTP endpoint (memory CRUD + A2A Sessions, M16) |
| [docs/a2a-sessions.md](docs/a2a-sessions.md) | Agent-to-agent conversation contract (M16) |
| [docs/tag-contract.md](docs/tag-contract.md) | The `project:` / `agent:` / `gcw:` schema enforced on every memory |
| [docs/security.md](docs/security.md) | Threat model, SSRF guard, FTS5 escape, HF Hub pinning |
| [docs/runbooks/](docs/runbooks/) | Install, migrate, backup / restore, dependency updates |
| [docs/adr/](docs/adr/) | Architectural decision records — the *why* behind the design choices |
| [docs/milestones.md](docs/milestones.md) | Milestone ledger with status legend |
| [CHANGELOG.md](CHANGELOG.md) | Release notes — Keep a Changelog format |

## Relationship to the GCW agent family

Mnemos is the standalone backing store for the **GCW (GitHub Copilot Workflow)** senior-agent team. The GCW repo ships a thin stub plugin (`plugins/mnemos-integration`) that runs in a degraded file-mode until Mnemos is reachable; once the MCP server is up, the stub transparently switches to `mnemos_*` tools without code changes. The shared contract is the [tag schema](docs/tag-contract.md) — `project:<slug>`, `agent:<slug>`, and at least one `gcw:<subtype>` — that every memory entry must carry.

## Source, upstream, license

- **Source**: this repository, [github.com/Korrnals/mnemos](https://github.com/Korrnals/mnemos).
- **Upstream**: forked from `ai-brain` on 2026-05-15 with full git history preserved (see [ADR 0001](docs/adr/0001-fork-from-ai-brain.md)).
- **License**: MIT (inherited from ai-brain; see [pyproject.toml](pyproject.toml)).

## Contributing

PRs welcome. Read [PLAN.md](PLAN.md) for the current roadmap, browse the open tasks in [tasks/](tasks/), and follow the conventions in the [docs/](docs/) set. Run `make verify` before opening a PR.

The Git workflow for this repo (branching model, Conventional Commits, PR rules, merge strategy) is documented in [`.github/instructions/git-workflow-mnemos.instructions.md`](.github/instructions/git-workflow-mnemos.instructions.md). The short version: `feat/*` → `dev-<stage>` → `release/X.Y.Z` → `main`; `main` accepts only `release/*` and `hotfix/*` PRs.

---

> **Reproduce the green state**: `make verify` runs the full quality gate (ruff + mypy --strict + bandit + pip-audit + 209 tests). If it is green, the change is good to ship.
