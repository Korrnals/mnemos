# Changelog

All notable changes to Mnemos (forked from ai-brain).

## [Unreleased]

### Security (M15.2)
- **B608 (SQL injection)**: `SQLiteStore.update_fields` now uses a static
  `_FIELD_UPDATERS` whitelist dict as the only source of column names for
  the dynamic `UPDATE` setter list. Previously, the setter list was built
  from a runtime `allowed` set; a future maintainer widening that set
  carelessly could re-introduce B608. The new design is safe by
  construction: column names never flow from kwargs into the SQL body.
  See `docs/security.md §5`.
- **B608 (FTS5 injection)**: `SQLiteStore.fts_search` now escapes user
  input via the new static helper `_build_fts_query`. FTS5 special chars
  (`* " ' ( ) :`) are stripped, whitespace is collapsed, and the result
  is wrapped in double quotes so FTS5 treats it as a literal phrase with
  no operator parsing (no `*` prefix, no `NEAR`, no column filter).
  Empty input degrades to a unique nonsense phrase that yields zero rows
  without raising the FTS5 syntax error that `""` would produce.
  See `docs/security.md §4`.
- **B608 (vector store)**: `VectorStore.get_embeddings` now uses
  constant-string placeholders joined with `+` (no f-string) for the
  dynamic `IN (?, ?, …)` clause. No user input is interpolated.
- **B615 (HF Hub download)**: `ONNXHubProvider` now requires an
  explicit `revision=` (commit SHA or tag) on every `hf_hub_download`
  call. Omitting the kwarg raises `ValueError` (fail-closed). The
  revision is read from the new `EmbeddingConfig.hf_revision` setting
  (override via `MNEMOS_EMBEDDING__HF_REVISION` env var or `config.yaml`).
  Mitigates CWE-494 (download of code without integrity check).
  See `docs/security.md §3`.
- **B104 (`0.0.0.0`)**: The string `"0.0.0.0"` in
  `MemoryManager._validate_url`'s SSRF blocklist is annotated
  `# nosec B104 — blocklist entry, not a bind()`. This is a confirmed
  false positive of the bandit rule — the string is the address being
  REJECTED, not bound. The HTTP API defaults to `127.0.0.1`. The
  actual server binding path (`cli/main.py:serve`) is unchanged.
  See `docs/security.md §6`.

### Added
- `EmbeddingConfig.hf_revision: str` — pinned HF Hub revision (default
  `"c9745ed1d7e3b0194c2e1c2b5d7e3e0b3c1c1c1c"`).
- `SQLiteStore._build_fts_query(user_query)` — static helper, the
  FTS5 escape used by `fts_search`.
- `SQLiteStore._FIELD_UPDATERS` — module-level whitelist dict
  (single source of truth for `update_fields` column names).
- `docs/security.md` — new threat model document, 8 sections.
- `tests/test_security.py`: +4 new test classes
  (`TestFts5Escaping`, `TestSqlInjectionSafe`, `TestHfHubPinning`,
  `TestSsrfBlocklist`) — 13 new tests, all targeting the bandit
  findings.

### Changed
- `pyproject.toml` `[tool.bandit]`: removed `skips = ["B104", "B608", "B615"]`.
  All 3 categories now run with no exceptions, and the real findings
  have been resolved at the code level.

## [0.1.0] — 2026-05-31

### Added
- **M1**: Fork & rebrand from ai-brain with full git history preserved.
- **M2**: GCW Tag Contract enforcement at MCP layer (`project:*`, `agent:*`, `gcw:*` required in strict mode).
- **M3**: First-class per-agent recall (`mnemos_agent_recall`, `/recall/agent/{name}`).
- **M4**: Knowledge Pipeline (raw → processing → processed → published) with clustering, synthesis, quality gates, and publish stages.
- **M5**: Policy engine with scheduler, event triggers, declarative rules, DLQ, and idempotency.
- **M6**: Explainability layer — trace table records every pipeline step with latency, tokens, and rationale.
- **M7**: Enhanced compaction detection (context-size heuristic, summary-marker detection, missing-reference heuristic).
- **M8**: Path-scoped rules ingest — watches `.github/instructions/*.instructions.md`, creates published memories with `applyTo` glob matching.
- **M9**: Security audit — SSRF validation in `ingest_url`, narrowed exception handling, SQL injection resistance tests.
- **M10**: Context Filter — 5-stage pipeline (dedup → noise → extract → compress → tokens) with profiles (log, terminal, code, docs, web, default).
- **M12**: Docs & runbooks — install, migrate, backup-restore guides.
- **M13**: Migration CLI — `mnemos migrate-from-ai-brain` with dry-run, backup, tag contract patching.
- **M14**: ai-brain archival — DEPRECATED notice in upstream README.
- **M15**: Production hardening — Makefile with `make verify` (lint + typecheck + security + test).

### Security
- Added `_validate_url()` SSRF guard blocking localhost, private IPs, and non-http(s) schemes.
- Replaced broad `except Exception: pass` with specific exception types in `vault.py` and `sqlite_store.py`.

### Changed
- Renamed all `brain_*` MCP tools → `mnemos_*`.
- Renamed CLI entry point `brain` → `mnemos`.
- Default paths: `~/.ai-brain/` → `~/.mnemos/`, `~/brain-vault/` → `~/mnemos-vault/`.
- Env vars: `AI_BRAIN_*` → `MNEMOS_*`.

### Deprecated
- ai-brain project — all new development continues in Mnemos.

## ai-brain history (pre-fork)

See `upstream-ai-brain` git remote for full history.
