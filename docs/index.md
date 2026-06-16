# Mnemos Documentation

Mnemos is a standalone memory & knowledge server that gives LLM agents (primarily the GCW agent family) real long-term memory. It exposes three equivalent control surfaces — CLI, HTTP, MCP — over a single in-process core, and persists every memory to a local SQLite database, a ChromaDB vector index, and an Obsidian-compatible markdown vault.

---

## Where to start

| If you are… | Read |
|-------------|------|
| Setting Mnemos up for the first time | [getting-started.md](getting-started.md) |
| Wiring Mnemos into VS Code Copilot | [getting-started.md#run-the-mcp-server](getting-started.md#run-the-mcp-server) |
| Looking for a specific command / flag | [cli-reference.md](cli-reference.md) |
| Looking for a specific MCP tool | [mcp-tools.md](mcp-tools.md) |
| Building an HTTP client | [api-reference.md](api-reference.md) |
| Trying to understand the system shape | [architecture.md](architecture.md) |
| Diagnosing a problem | [runbooks/](runbooks/) |

---

## Concepts

- [Architecture](architecture.md) — system overview, layered design, data model, state machines, security boundaries, operational concerns.
- [Tag Contract](tag-contract.md) — the M2 schema enforced on every memory (`project:`, `agent:`, `gcw:`).
- [Knowledge Pipeline](architecture.md#state-machines) — how a memory moves from `raw` → `processing` → `processed` → `published` (M4).
- [A2A Sessions](a2a-sessions.md) — the agent-to-agent conversation contract (M16).
- [Security](security.md) — threat model, SSRF guard, secrets hygiene, request-boundary rules.

---

## API and tools

- [HTTP API Reference](api-reference.md) — every endpoint, request / response shape, error code.
- [MCP Tools Reference](mcp-tools.md) — every `mnemos_*` tool exposed to VS Code Copilot.
- [CLI Reference](cli-reference.md) — every `mnemos` subcommand with flags, defaults, and examples.

---

## Operations

- [Runbooks](runbooks/) — install, migrate, backup / restore, dependency updates.
- [Security Model](security.md) — threat model and defensive design.
- [Architecture Decision Records](adr/) — 13 ADRs covering the M1 → M15 evolution.

---

## Project

- [README](../README.md) — top-level project page, status, milestones.
- [CHANGELOG](../CHANGELOG.md) — release notes.
- [PLAN](../PLAN.md) — phased implementation plan (M1 → M15).
- [ARCHITECTURE](../ARCHITECTURE.md) — high-level architecture (one-page summary; see [docs/architecture.md](architecture.md) for the full version).
- [CONTRIBUTING](../CONTRIBUTING.md) — how to contribute.

---

_Last updated: 2026-06-16_
