# Changelog

All notable changes to Mnemos (forked from ai-brain).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-06-16

The first production hardening release. M15 closes the security and quality
gaps inherited from `ai-brain`; M16 adds the persistent A2A Sessions backend
that GCW agents need for multi-step reasoning; M17 wires the CI gate so future
PRs cannot regress the green state.

### Added

- **A2A Sessions API (M16)** — five HTTP endpoints (`POST /v1/sessions`,
  `GET /v1/sessions/{id}`, `POST /v1/sessions/{id}/turns`,
  `GET /v1/sessions/{id}/turns/{turn_id}`,
  `POST /v1/sessions/{id}/turns/range`) backed by SQLite. GCW agents now have
  a persistent backend for multi-step conversations; on Mnemos unavailability
  the GCW MCP layer falls back to `~/.gcw/a2a-messages.jsonl` (see ADR 0010).
  See [docs/a2a-sessions.md](docs/a2a-sessions.md) and ADR 0007.
- **`docs/security.md`** — 8-section threat model covering SSRF, HF Hub pinning,
  FTS5 injection, dynamic-SQL whitelist, and the IPv6 SSRF gap (ADR 0012).
- **`tests/test_security.py`** — 13 new tests across `TestFts5Escaping`,
  `TestSqlInjectionSafe`, `TestHfHubPinning`, `TestSsrfBlocklist` covering
  every bandit finding class.
- **`EmbeddingConfig.hf_revision: str`** — pinned HF Hub commit SHA for
  `ONNXHubProvider`; override via `MNEMOS_EMBEDDING__HF_REVISION`.
- **`SQLiteStore._build_fts_query(user_query)`** — static FTS5 escape helper
  used by `fts_search`.
- **`SQLiteStore._FIELD_UPDATERS`** — module-level whitelist dict; the single
  source of truth for `update_fields` column names.
- **CI pipeline (M17)** — GitHub Actions workflow that runs `make verify`
  (ruff + mypy --strict + bandit + pip-audit + full test suite) on every
  PR and on `main`. PR badge added to the README.
- **Comprehensive docs set (M20)** — top-level [docs/index.md](docs/index.md)
  landing page plus [docs/getting-started.md](docs/getting-started.md),
  [docs/cli-reference.md](docs/cli-reference.md),
  [docs/mcp-tools.md](docs/mcp-tools.md), [docs/api-reference.md](docs/api-reference.md),
  [docs/architecture.md](docs/architecture.md), [docs/milestones.md](docs/milestones.md).
  README rebuilt to point at the docs rather than duplicate content.

### Changed

- **`pyproject.toml` `[tool.bandit]`** — removed `skips = ["B104", "B608", "B615"]`.
  All three categories now run with no exceptions; the real findings have been
  resolved at the code level (not suppressed). 209 tests passing, `make verify`
  green.
- **Direct dependency pins (M15.5.1)** — `aiohttp>=3.14.1,<4.0` and
  `starlette>=1.3.0,<2.0` are now pinned directly to force the resolver past
  the vulnerable transitive versions still pulled by `chromadb`, `k8s`, and
  `fastapi`. Closes CVE-2026-34993, CVE-2026-47265, CVE-2026-50269,
  CVE-2026-54273 through CVE-2026-54280, CVE-2026-48817, CVE-2026-48818,
  CVE-2026-54282, CVE-2026-54283.
- **Mypy --strict is the production gate (M15.1, ADR 0011)** — the
  `make verify` quality bar now includes `mypy --strict` on `src/`. No
  `# type: ignore` is admitted except with an explicit, one-line
  reason.

### Security

- **B608 (SQL injection)** — `SQLiteStore.update_fields` now uses the static
  `_FIELD_UPDATERS` whitelist dict as the only source of column names for
  the dynamic `UPDATE` setter list. Column names never flow from kwargs into
  the SQL body. See ADR 0008 and `docs/security.md §5`.
- **B608 (FTS5 injection)** — `SQLiteStore.fts_search` escapes user input via
  `_build_fts_query`. FTS5 special chars (`* " ' ( ) :`) are stripped, the
  result is wrapped in double quotes so FTS5 treats it as a literal phrase
  with no operator parsing. See `docs/security.md §4`.
- **B608 (vector store)** — `VectorStore.get_embeddings` now uses
  constant-string placeholders joined with `+` (no f-string) for the
  dynamic `IN (?, ?, …)` clause. No user input is interpolated.
- **B615 (HF Hub download)** — `ONNXHubProvider` now requires an explicit
  `revision=` (commit SHA or tag) on every `hf_hub_download` call. Omitting
  the kwarg raises `ValueError` (fail-closed). Mitigates CWE-494.
  See `docs/security.md §3`.
- **B104 (`0.0.0.0`)** — annotated `# nosec B104` at the SSRF blocklist
  entry; this is the string being REJECTED, not a `bind()`. The HTTP API
  still defaults to `127.0.0.1`. See `docs/security.md §6` and ADR 0012.
- **IPv6 SSRF gap (ADR 0012)** — `_validate_url` now resolves and rejects
  IPv6 loopback (`::1`) and IPv4-mapped (`::ffff:127.0.0.0/104`) literals
  in addition to the previous RFC1918 / link-local blocklist.

### Deprecated

- `ai-brain` project — all new development continues in Mnemos. The
  upstream README carries a DEPRECATED notice (M14).

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
