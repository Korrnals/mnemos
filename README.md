# Mnemos

> **Standalone memory & knowledge server** — fork of `ai-brain`, productionised for the GCW (GitHub Copilot Workflow) agent family.

**Status**: **M1 in progress** — project structure and M2 (TagContract) implemented. M1 git bootstrap requires one-time terminal commands (see ARCHITECTURE.md §11).

## What this directory is

- [PLAN.md](PLAN.md) — phased implementation plan (M1 → M15). Read this first.
- [ARCHITECTURE.md](ARCHITECTURE.md) — high-level architecture, components, data flows, decisions.
- This `README.md` — entrypoint + status.

## What Mnemos is (one paragraph)

A standalone server that gives Copilot agents real long-term memory: hybrid search (vector + full-text), per-agent recall, a knowledge pipeline (raw → processing → processed → published), a policy engine for automation, and an explainability layer. Talks to Copilot via MCP (`mnemos_*` tools). Forked from the user's `ai-brain` project to keep full git attribution.

## Source / upstream

- Source: `/var/home/abyss/LABs/AI/ai-brain/` (will be archived after Mnemos v1).
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

Start a fresh chat session for Mnemos implementation. First step: read [PLAN.md](PLAN.md), then begin **Phase M1** (Fork & Rebrand).
