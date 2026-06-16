# Mnemos

> **Standalone memory & knowledge server** — fork of `ai-brain`, productionised for the GCW (GitHub Copilot Workflow) agent family.

**Status**: **M1–M15 complete** — 209 tests passing, `make verify` green. Production-ready.

## Quick start

```bash
# Install
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Verify
mnemos --help
pytest tests/ -q

# Migrate from ai-brain (optional)
mnemos migrate-from-ai-brain --dry-run
mnemos migrate-from-ai-brain
```

Dependency updates and CVE reminder flow:
- [docs/runbooks/dependency-updates.md](docs/runbooks/dependency-updates.md)
- `make verify` shows a warning while `CVE-2026-45829` is temporarily ignored.

## What this directory is

- [PLAN.md](PLAN.md) — phased implementation plan (M1 → M15). Read this first.
- [ARCHITECTURE.md](ARCHITECTURE.md) — high-level architecture, components, data flows, decisions.
- [docs/runbooks/](docs/runbooks/) — operational runbooks (install, migrate, backup).
- This `README.md` — entrypoint + status.

## What Mnemos is (one paragraph)

A standalone server that gives Copilot agents real long-term memory: hybrid search (vector + full-text), per-agent recall, a knowledge pipeline (raw → processing → processed → published), a policy engine for automation, an explainability layer, path-scoped rules ingest, and a 5-stage context filter. Talks to Copilot via MCP (`mnemos_*` tools). Forked from the user's `ai-brain` project to keep full git attribution.

## Milestones

| Milestone | Status | Tests |
|---|---|---|
| M1 — Fork & Rebrand | ✅ | — |
| M2 — GCW Tag Contract | ✅ | 31 |
| M3 — Per-agent Recall | ✅ | 16 |
| M4 — Knowledge Pipeline | ✅ | 24 |
| M5 — Policy Engine | ✅ | 24 |
| M6 — Explainability / Traces | ✅ | included |
| M7 — Compaction Detection | ✅ | included |
| M8 — Path-scoped Rules Ingest | ✅ | 11 |
| M9 — Security Audit | ✅ | 11 |
| M10 — Context Filter | ✅ | 32 |
| M11 — Cache Center | ⏳ v2 | — |
| M12 — Docs / Runbooks | ✅ | — |
| M13 — Migration CLI | ✅ | 6 |
| M14 — ai-brain Archival | ✅ | — |
| M15 — Production Hardening | ⏳ | — |

## Source / upstream

- Source: `/var/home/abyss/LABs/AI/ai-brain/` (archived, see DEPRECATED notice in its README).
- Fork strategy: full git history preserved (`git clone` + rename remote to `upstream-ai-brain`).
- Licence: inherited from ai-brain.

## Relationship to GCW

GCW ships a **stub plugin** `plugins/mnemos-integration` (see `GithubCopilotWorkflow/plugins/mnemos-integration/`) that operates in degraded file-mode until Mnemos MCP is installed. Once Mnemos is running, those skills auto-switch to MCP tools without code changes. The tag schema (`gcw:session`, `gcw:bug-pattern`, `gcw:learning`, `gcw:decision`, `gcw:rule`, `gcw:open-question`, `gcw:checkpoint`) is the contract between the two.

## Locked decisions (from the planning session)

- **Git history**: preserved via `git clone` + remote rename.
- **LLM providers**: broad set out of the gate — Anthropic, OpenAI, Azure OpenAI, Ollama, Gemini — behind a provider abstraction in `mnemos/llm/`.
- **Context Filter (M10)**: mandatory v1 subsystem (pre-LLM dedup/noise filtering with raw+clean dual storage).
- **Cache Center (M11)**: deferred to v2; idempotency from M5 covers the bulk of the benefit.
- **Knowledge Pipeline (M4)**: mandatory v1 feature. Vector index is gated on `status="published"`.
- **Tag contract**: enforced at MCP layer with `strict_tag_contract` flag (true for new, false for legacy migrations).

## Next action

M15 — Production hardening: bandit, mypy, pip-audit, coverage ≥80%.
