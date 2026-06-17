# Architecture Decision Records

*Historical artifact — English only.*

This directory contains the architectural decisions for Mnemos, recorded as
ADRs (Architecture Decision Records) following the
[Michael Nygard lightweight template](https://github.com/joelparkerhenderson/architecture_decision_records).

## When to write an ADR

- Choosing among multiple viable options for a structural decision.
- Changing a previously-recorded decision (write a new ADR that **supersedes**
  the old; do not edit history).
- The decision will affect future teammates who didn't see the discussion.

Do **not** write an ADR for trivial choices, project conventions already
documented elsewhere, or anything that fits in a code comment.

## Status legend

- **Proposed**: under discussion, no consensus yet.
- **Accepted**: in force on `main`.
- **Deprecated**: still on disk, but no longer guiding current decisions.
- **Superseded by ADR-NNNN**: replaced by a later decision; kept for context.

## Index

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-fork-from-ai-brain.md) | Fork ai-brain into a standalone Mnemos product | Accepted | 2026-05-15 |
| [0002](0002-gcw-tag-contract-strict-by-default.md) | GCW tag contract is strict by default at the MCP layer | Accepted | 2026-05-15 |
| [0003](0003-knowledge-pipeline-vector-gating.md) | Knowledge Pipeline: gate vector indexing on `status="published"` | Accepted | 2026-05-15 |
| [0004](0004-context-filter-mandatory-v1.md) | Context Filter is a mandatory v1 feature (raw + clean dual storage) | Accepted | 2026-05-15 |
| [0005](0005-cache-center-deferred-to-v2.md) | Cache Center is deferred to v2 (idempotency from M5 covers v1) | Accepted | 2026-05-15 |
| [0006](0006-local-onnx-embeddings.md) | Use local ONNX embeddings (privacy + offline by default) | Accepted | 2026-05-15 |
| [0007](0007-a2a-sessions-api-v1.md) | A2A Sessions API: 5 minimal endpoints with summary-mode default | Accepted | 2026-06-15 |
| [0008](0008-sql-injection-via-fstring.md) | SQL injection via f-string is fixed via whitelisted dispatch (M15) | Accepted | 2026-06-15 |
| [0009](0009-ssrf-guard-in-ingest-url.md) | SSRF guard at the `mnemos_ingest_url` boundary | Accepted | 2026-06-15 |
| [0010](0010-fallback-isolation-from-gcw.md) | GCW A2A is a "best-effort, not a hard dependency" backend | Accepted | 2026-06-15 |
| [0011](0011-mypy-strict-is-real-strict.md) | `mypy --strict` is the production-readiness gate, not a check-the-box lint | Accepted | 2026-06-15 |
| [0012](0012-ipv6-ssrf-gap.md) | Fix IPv6 SSRF gap in `_validate_url` (M15) | Accepted | 2026-06-15 |
| [0013](0013-production-hardening-m15.md) | M15 production hardening is the gate to declaring v1 "done" | Accepted | 2026-06-15 |

## Themes

**Process / fork** (0001): how Mnemos came to be.

**Contract / schema** (0002): how Mnemos and GCW agree on data shape.

**Pipeline / data flow** (0003, 0004, 0005, 0006): how knowledge moves through
Mnemos — vector gating, context filter, cache deferral, embedding choice.

**External surface** (0007, 0009, 0010, 0012): what Mnemos exposes to GCW
(A2A API), to URL ingestion (SSRF guard), and how it tolerates being down
(fallback isolation). 0012 is a tightening of 0009 discovered during M15.

**Quality / hardening** (0008, 0011, 0013): how Mnemos earns "production-ready"
— SQL safety, type strictness, M15 as a gate.

## Conventions

- Filenames: `NNNN-short-kebab-case-title.md`, zero-padded.
- Status changes are appended, not in-place mutations.
- "Accepted" requires the deciders listed; consensus or named owner.
- An ADR is only as good as its **Alternatives considered** section — write it.

## Cross-references

- `tasks/AUDIT.md` — current state of the codebase (input to many ADRs).
- `tasks/tech-lead/TL-001-coordination.md` — coordination across the work that
  produced several of these decisions.
- `docs/en/architecture/overview.md` — system architecture; ADRs explain the *why*, this
  document explains the *what*.
- `docs/en/admin/security.md` — threat model; ADR-0009 and 0012 are the SSRF decisions.
