# Milestones

*Historical artifact — English only.*

> **Why this is not in the README.** The M1 → M15 ledger is project history, not a product description — it changes shape every release and belongs in the docs, not at the entry point. The README points here for anyone who wants the implementation timeline.

Mnemos is a fork of [`ai-brain`](../../README.md#source-upstream-license) — see [ADR 0001](adr/0001-fork-from-ai-brain.md) for the fork decision. This page is the running ledger of milestone work since that fork.

## Status legend

| Marker | Meaning |
|--------|---------|
| ✅ | Done — landed on `main`, tests included where applicable |
| 🔄 | In progress — see linked task file |
| ⏳ | Deferred — see the linked ADR for rationale |
| — | Not applicable |

## Milestone ledger

| # | Milestone | Status | Tests | Notes |
|---|-----------|--------|-------|-------|
| M1 | Fork & Rebrand | ✅ | — | Full git history preserved; `ai-brain` → `mnemos` rename; see ADR 0001 |
| M2 | GCW Tag Contract | ✅ | 31 | Strict-mode enforcement at the MCP layer; see ADR 0002 |
| M3 | Per-agent Recall | ✅ | 16 | New `mnemos_agent_recall` MCP tool + `GET /recall/agent/{name}` |
| M4 | Knowledge Pipeline | ✅ | 24 | `raw → processing → processed → published`; vector gated on `published` (ADR 0003) |
| M5 | Policy Engine | ✅ | 24 | Scheduler + event triggers + DLQ + idempotency; deferred cache from M11 |
| M6 | Explainability / Traces | ✅ | included | `traces` table records every pipeline step |
| M7 | Compaction Detection | ✅ | included | Context-size, summary-marker, missing-reference heuristics |
| M8 | Path-scoped Rules Ingest | ✅ | 11 | Watches `.github/instructions/*.instructions.md` |
| M9 | Security Audit | ✅ | 11 | SSRF guard, narrowed exceptions, SQL-injection resistance |
| M10 | Context Filter | ✅ | 32 | Five-stage pipeline (dedup → noise → extract → compress → tokens); see ADR 0004 |
| M11 | Cache Center | ⏳ v2 | — | Deferred — see ADR 0005 (M5 idempotency covers v1 needs) |
| M12 | Docs / Runbooks | ✅ | — | Install, migrate, backup / restore runbooks |
| M13 | Migration CLI | ✅ | 6 | `mnemos migrate-from-ai-brain` with dry-run + backup |
| M14 | ai-brain Archival | ✅ | — | DEPRECATED notice added to upstream README |
| M15 | Production Hardening | ✅ | — | `make verify`: ruff + mypy --strict + bandit + pip-audit + tests; see ADR 0013 |
| M16 | A2A Sessions API | ✅ | — | Five HTTP endpoints; persistent backend for GCW multi-step chains |
| M17 | CI Pipeline | ✅ | — | GitHub Actions workflow gates `make verify` on every PR |
| M20 | Docs Overhaul | 🔄 | — | This slice — `docs/` rebuilt, README curated, link map repaired |

## Evolution

The M-numbering is the implementation contract; the [ADR set](adr/) is the design contract. When in doubt about *why* a milestone made the choices it made, read the matching ADR. The current architectural shape is captured in [architecture overview](../en/architecture/overview.md); the data model is in §2, the state machines in §3.

A new milestone (M18+) opens only when a senior agent raises a task ticket under [tasks/](https://github.com/Korrnals/mnemos/tree/main/tasks) and the planning session locks the scope.
