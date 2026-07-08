# Changelog

All notable changes to Mnemos.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

## [2.7.6] - 2026-07-08

### Fixed
- **README sync workflow** — poll for CI completion (up to 10 min), then admin merge. Handles `CLEAN`, `UNSTABLE`, `BEHIND` states. 15-min job timeout.

## [2.7.5] - 2026-07-08

### Fixed
- **README sync workflow** — retry loop for admin merge (GitHub needs a moment to register PR as mergeable), removed self-approve (GitHub doesn't allow self-approval)

## [2.7.4] - 2026-07-08

### Fixed
- **README sync workflow** — self-approve PR + admin merge instead of auto-merge (branch protection requires 1 approving review)

## [2.7.3] - 2026-07-08

### Fixed
- **README sync workflow** — release workflow now creates a PR + auto-merge instead of direct push to protected `main` branch (was failing on branch protection)
- Added `pull-requests: write` permission to sync-readme job

## [2.7.2] - 2026-07-08

### Added
- **CLI `reindex` command** — `mnemos reindex` rebuilds vector index for all published memories
- **API `POST /reindex`** endpoint — trigger vector rebuild via HTTP (with `batch_size` query param)

### Fixed
- **Embeddings pre-download for PVC mounts** — model now pre-downloaded to `/opt/model-cache` (inside image layer) and copied to `/data/.cache` on first boot via `entrypoint.sh` (fixes: `/data` volume mount hid pre-downloaded model)
- **External `entrypoint.sh`** instead of heredoc in Containerfile (GitHub Actions imagebuilder compatibility)

- **`totp_required` per-token flag**: tokens with `totp_required=False` can use bearer directly (no login/verify/session needed). Enables proper M2M authentication without TOTP code reuse issues.
- **`--no-totp` CLI flag** for `mnemos auth token create`: creates API tokens without TOTP requirement.
- **Direct bearer middleware path**: middleware accepts `mnk_`-prefixed bearer tokens with `totp_required=0` directly, skipping session validation.
- **`skip_quality_check` query param** on `POST /publish/{memory_id}`: allows publishing memories from `raw` status without LLM pipeline.
- **Pre-downloaded embedding model**: `all-MiniLM-L6-v2` ONNX model (~90MB) pre-downloaded in Docker image, enabling vector search out of the box without internet access.
- **`HOME=/data` env in Containerfile**: fixes ChromaDB `/.cache` permission denied error.
- **`include_raw` parameter** in plugin search: defaults to `true` so memories in `raw` status are searchable.
- **Auto-publish on add**: plugin publishes memories after creation so they're immediately searchable.

### Changed

- **Plugin auth refactor**: when `totp_secret` is not configured, plugin uses API token directly instead of TOTP login/verify flow.
- **`/auth/me` response** now includes `totp_required` field for token introspection.
- **Token list** displays `totp_required` column (yes/no).

### Fixed

- **Search returns 0 results**: memories stayed in `raw` status without LLM pipeline; `include_raw=true` default in plugin + auto-publish resolves this.
- **Embeddings `/.cache` permission denied**: `HOME=/data` env var + pre-download in Containerfile.
- **TOTP code reuse for M2M**: API tokens with `totp_required=False` bypass TOTP entirely.
- **`/publish/{id}` only accepted `processed` memories**: now accepts `raw` with `skip_quality_check=true`.

## [2.6.1] — 2026-07-07

### Fixed

- **Critical: `TypeError` when comparing offset-naive and offset-aware datetimes in auth_store**
  (`is_token_active`, `is_challenge_valid`, `is_session_valid`).
  Token `expires_at` from CLI (e.g. `--expires 2027-12-31`) was stored as offset-naive,
  while `datetime.now(UTC)` is offset-aware — Python raises `TypeError` on comparison.
  Added `_parse_datetime_utc()` helper that normalizes any ISO-8601 string to UTC-aware.
- **CLI: `mnemos auth token create --expires` now normalizes to offset-aware ISO-8601**
  before storing. Prevents the naive-datetime bug at the source.

## [2.6.0] - 2026-07-07

### Added

- **Hermes Agent integration** — full `MemoryProvider` plugin for Hermes
  Agent by Nous Research. Connects Mnemos to Hermes' pluggable memory
  system via the HTTP API. Exposes all 15 `mnemos_*` tools as native
  Hermes tools, with automatic prefetch, sync-turn, session-end
  extraction, built-in memory mirroring, and circuit breaker. Config
  via `hermes memory setup` or `memory.mnemos` in config.yaml. Plugin
  at `integrations/hermes/`, target in `targets.yaml`.

- **HTTP API: 9 new endpoints** — all MCP-only tools now have HTTP
  equivalents, enabling the Hermes plugin (and any HTTP client) to
  access the full tool surface:
  - `POST /context/save` — session checkpoint (mirrors `mnemos_save_context`)
  - `POST /context/recall` — session context recall (mirrors `mnemos_recall_context`)
  - `POST /compress` — reversible compression CCR (mirrors `mnemos_compress`)
  - `POST /retrieve` — CCR retrieval (mirrors `mnemos_retrieve`)
  - `GET /auto-collect` — compaction signal vector (mirrors `mnemos_auto_collect_status`)
  - `POST /ingest-url` — URL ingest with credential stripping (mirrors `mnemos_ingest_url`)
  - `POST /watch/start` — file watcher start (mirrors `mnemos_watch_start`)
  - `POST /watch/stop` — file watcher stop (mirrors `mnemos_watch_stop`)
  - `GET /watch/status` — file watcher status (mirrors `mnemos_watch_status`)

- **E2E tests** — 49 new tests covering all new HTTP endpoints and
  the Hermes plugin (circuit breaker, config loading, tool schemas,
  sync-turn significance filter, save_config target).

### Changed

- `SaveContextRequest` fields now accept `str | list[str]` — lists are
  joined with newlines. This matches the Hermes plugin schema which
  declares fields as `type: array`.

- `_auto_collect_state` in HTTP API now reads `MNEMOS_AUTO_COLLECT`
  env var (was hardcoded `False`). Aligns with the MCP server behavior.

- `targets.yaml` — hermes target now includes `plugin` deploy path
  (`~/.hermes/plugins/mnemos/`).

- HTTP API docs (EN/RU) — documented all 9 new endpoints with request
  bodies, responses, examples, and error codes.

### Fixed

- Plugin: default port corrected to `8787` (matching the actual
  `ApiConfig.port` default in `src/mnemos/config.py`). The original
  plugin had `8787` which was correct; this entry documents the
  verification.

- Plugin: `_handle_add` now reads `title` from API response instead
  of non-existent `auto_title` field.

- Plugin: `save_config` now writes to `memory.mnemos` (was
  `plugins.mnemos`), aligning with `hermes memory setup` wizard.

- Plugin: `sync_turn` no longer saves every turn — only significant
  turns (user message > 50 chars) or every Nth turn (default 10).
  Honors Mnemos' "write sparingly" philosophy.

- HTTP API docs: removed duplicate endpoint sections (EN/RU).

### Added

- **CCR reversible compression** — `mnemos_compress` and `mnemos_retrieve`
  MCP tools. Compresses large content (tool output, logs, JSON) via the
  existing 5-stage filter pipeline, caches the original in a new
  `ccr_cache` SQLite table keyed by SHA-256, and embeds a parseable marker
  so the LLM can retrieve the full original back with zero data loss.
  70-90% token reduction. Configurable TTL (default 7 days) + LRU
  eviction (default 10000 entries) + per-project scoping + FTS5 snippet
  search within cached originals. Inspired by headroom's CCR
  (https://github.com/headroomlabs-ai/headroom), Apache 2.0 — original
  implementation integrated into the existing mnemos store.

### Changed

### Fixed

## [2.4.0] - 2026-06-29

### Added

- **Single-memory passthrough** — `run_pipeline()` now promotes raw memories
  that don't form a cluster (min_cluster_size=2) directly to published via a
  lightweight synthesis path. Prevents the queue from growing unbounded when
  most memories are unique (P0-1).
- **Stuck-processing rescue** — memories stuck in `processing` status (from
  prior crashed pipeline runs) are rescued to published on the next pipeline
  cycle (P0-1).
- **`rebuild_vector_index()`** — re-embeds all published memories and upserts
  into the vector store. Used when the embedding pipeline was broken and
  vectors are missing. Idempotent (P0-2).
- **JSON array compression** — filter stage 4 now applies SmartCrusher-inspired
  statistical sampling to JSON arrays with ≥20 items: keeps head (schema),
  tail (recency), and anomaly items (errors), drops the middle with a count
  marker. Target 60%+ reduction on large JSON (P0-3).
- **Code boilerplate stripping** — filter stage 4 for `code` profile collapses
  repeated import blocks and consecutive blank lines (P0-3).
- **Profile-aware extract** — filter stage 3 now drops verbose success lines
  (INFO/DEBUG/started/completed) in `log`/`terminal` profiles, skips JSON
  content (lets compress handle it), and preserves all content for
  `docs`/`web`/`default` profiles (P0-3).

### Fixed

- **Processing queue throughput** — placeholder synthesis now assigns
  `quality_score=0.5` and `confidence=0.5` (was 0.0), and quality gate
  defaults lowered to 0.4/0.4/1 (was 0.6/0.6/2). The previous defaults
  guaranteed every placeholder draft failed the gate, causing the queue to
  grow unbounded (P0-1).
- **Background processor interval** — reduced from 300s to 120s and batch
  size increased from 100 to 200 to keep up with ingest rate (P0-1).
- **Vector indexing on publish** — `publish_memory()` now correctly indexes
  vectors for all published memories. With the queue fix, records now reach
  `published` status and get vector-indexed (P0-2).

## [2.3.0] - 2026-06-25

### Added

- **FTS5 index rebuild** — `SQLiteStore.rebuild_fts_index()` rebuilds the FTS5
  external-content table from the `memories` table. Use when the FTS5 index is
  desynced from `memories` (e.g. after INSERT OR REPLACE corruption). CLI:
  `mnemos fts rebuild`. MCP: `mnemos_reprocess` tool.
- **Background processor** — `MemoryManager.start_background_processor()` runs
  the pipeline (cluster, synthesize, quality_gate, publish) in a daemon thread
  at configurable intervals (default 300s). The MCP server starts it
  automatically on launch; CLI: `mnemos processor start|stop|status|run`.
- **`mnemos_reprocess` MCP tool** — manually trigger the pipeline to drain the
  raw/processing queue without waiting for the background processor interval.
- **FTS5 auto-recovery** — `SQLiteStore.save()` catches `DatabaseError` from
  FTS5 corruption, logs a warning, and calls `rebuild_fts_index()` automatically,
  so search continues to work even if the index was desynced by a prior
  INSERT OR REPLACE.

### Fixed

- **FTS5 corruption on save()** — `SQLiteStore.save()` used INSERT OR REPLACE
  which could desync the FTS5 external-content table, causing
  "fts5: missing row from content table" errors. The save method now detects
  existing rows and uses UPDATE (which fires the correct AFTER UPDATE trigger)
  instead of INSERT OR REPLACE. If corruption is detected, the FTS5 index is
  rebuilt automatically.
- **Background processor not running** — the MCP server had no background
  processor, so raw entries added via `mnemos_add` never progressed through the
  pipeline (raw, processing, processed, published). The MCP server now starts a
  background processor thread on launch, draining the queue at configurable
  intervals.
- **Zero embeddings built** — with no background processor running, the
  embedding pipeline never executed. The background processor now runs the full
  pipeline (cluster, synthesize, quality_gate, publish), which generates
  embeddings for published memories.

## [2.2.0] - 2026-06-24

### Added

- **`mnemos_search` MCP tool gains `status` parameter** — the MCP schema was
  missing `status` even though `manager.search()` accepted it. Callers can now
  filter by `raw`/`processing`/`processed`/`published`/`archived` via MCP.
  `include_raw` description corrected: it controls status filtering, not
  `raw_content` inclusion.
- **`mnemos_stats` health fields** — `stats()` now returns `embedding_status`
  (provider, vectors_indexed, degraded flag), `processor` (queue depth,
  last_processed_at), and `search_health` (fts_available, vector_available,
  mode, orphaned_vectors). Callers can detect a stuck pipeline, degraded
  search, or vector/SQLite drift.
- **`mnemos tags normalize` CLI command** — normalizes existing tags in the
  SQLite store to canonical lowercase + hyphenated form, matching
  `validate_tag_contract` lax-mode normalization. Uses `update_fields()` to
  keep the FTS5 index consistent.
- **`processor.last_processed_at` tracking** — the background processor now
  records the timestamp of its last successful processing cycle, surfaced via
  `stats().processor.last_processed_at` so callers can detect a stuck pipeline.
- **`search_health.orphaned_vectors`** — `search_health` now includes
  `orphaned_vectors` (`True` when vectors exist but `published_count == 0`),
  indicating the vector store drifted out of sync with SQLite (e.g.
  memories were deleted but vectors were not removed).
- **Wheel now includes `scripts/`** — `mcp-setup.sh`, `install.sh`, `deploy.sh`,
  `setup-distrobox.sh` are packaged via hatchling `force-include` so
  `mnemos integration setup` works from a pip-installed wheel, not just a source
  checkout. `register_mcp()` now uses a 3-tier `_find_mcp_setup_script()` helper
  (source-tree → `importlib.resources` → upward search) to locate the script.
  Closes #52.

### Changed

- **`ruff format --check` added to `make verify`** — the `format-check` Make
  target runs `ruff format --check src/ tests/` and is now part of the
  `verify` gate, ensuring formatting violations fail CI before merge.

### Fixed

- **`include_raw` filter implemented** — `manager.search()` was accepting
  `include_raw` as a no-op. Now: `include_raw=False` (default) filters FTS
  results to `published` + `processed` only, preserving the "only searches
  published knowledge by default" contract. `include_raw=True` surfaces
  `raw`/`processing` entries not yet pipeline-processed. Explicit `status`
  parameter always takes precedence. The REST `/search` endpoint and
  `mnemos_agent_recall` query path now pass `include_raw` through correctly.
- **`mnemos_agent_recall` finds raw entries** — the query path now passes
  `include_raw=True` so agent recall surfaces recently-added entries regardless
  of pipeline status. The recency path (no query) already had no status filter.
- **Project/agent tag case normalized in lax mode** — `project:Project-Umbra`
  is now normalized to `project:project-umbra` (canonical lowercase) instead of
  being replaced with `project:unknown`. Prevents duplicate namespaces from
  mixed-case slugs. Strict mode is unchanged (still rejects uppercase).
- **`search_type` indicator reflects actual mode** — when the vector leg is
  empty (embeddings down or no vectors indexed), results now carry
  `search_type="fts_only"` instead of `"hybrid"`, so callers can detect
  degraded search mode.
- **`tags normalize` no longer corrupts the FTS5 index** — the CLI command
  used `sqlite.save()` (INSERT OR REPLACE) to persist normalized tags, which
  could desync the FTS5 external content table (`content=memories`) and cause
  "missing row from content table" errors on subsequent searches. It now uses
  `update_fields()` (plain UPDATE), which fires the `AFTER UPDATE` trigger
  that keeps the FTS5 index consistent. The denormalised `project` and `agent`
  columns are updated in the same statement so per-project / per-agent queries
  stay in sync with the normalized tags.
- **`tags normalize` replaces spaces with hyphens** — the CLI command
  previously only lowercased slugs, diverging from `validate_tag_contract`
  lax-mode normalization. `project:My Project` now becomes `project:my-project`
  (hyphen), matching the contract.
- **CLI `search` gains `--include-raw` and `--status` flags** — the MCP tool
  and Python API already supported `include_raw` and `status` filtering, but
  the CLI `search` command did not expose them. After the `include_raw`
  status-filtering fix, default search no longer surfaces raw entries; CLI
  users can now opt in with `--include-raw` or filter explicitly with
  `--status raw|processing|processed|published|archived`. A `--tags` filter
  flag was also added for parity with the API.
- **`include_raw=True` excludes archived** — `manager.search()` was returning
  `archived` memories when `include_raw=True` and no explicit `status` was
  given. `archived` means "intentionally hidden from normal search"; it is now
  excluded from `include_raw=True` results. An explicit
  `status=MemoryStatus.ARCHIVED` still returns archived entries (explicit
  status always wins). The same status-policy is now applied to the vector
  leg, not just the FTS leg.
- **`search_type` reflects actual vector contribution** — the indicator was
  set to `"hybrid"` whenever the vector leg returned pairs, even if all were
  filtered out by status or already covered by FTS. It now tracks whether any
  vector pair survived filtering AND contributed a new id not already found
  by FTS. A search where the vector leg returned only already-known or
  filtered-out results reports `"fts_only"`.
- **`mnemos_search` MCP tool gives a clear error for invalid `status`** —
  passing `status="invalid"` previously raised a `ValueError` caught by the
  generic handler, producing `❌ Error: 'invalid' is not a valid
  MemoryStatus` without listing valid values. The error now lists all valid
  statuses: `raw, processing, processed, published, archived`.
- **Tag normalization strips leading/trailing spaces** —
  `validate_tag_contract` lax-mode `_normalize_slug` and the CLI
  `tags normalize` command did not strip the slug before lowercasing and
  replacing spaces with hyphens. `project: My Project ` produced
  `project:-my-project-` (leading/trailing hyphens). Both now `.strip()`
  first, yielding `project:my-project`.
- **Dependency bumps** — `pyyaml>=6.0.3`, `httpx>=0.28.1`, `fastapi>=0.138.0`,
  `typer>=0.26.7`, `python-dateutil` updated; GitHub Actions
  `actions/checkout@7`, `actions/upload-artifact@7`,
  `softprops/action-gh-release@3` bumped via dependabot.

## [2.1.0] — 2026-06-23

### Added

- **Consolidated directory layout** — all Mnemos data now lives under a single
  root `~/.mnemos/` with subdirectories: `data/`, `vault/`, `logs/`, `cache/`,
  `completion/`. Old scattered paths (`~/.mnemos-venv`, `~/mnemos-vault`) are
  auto-migrated on first run (idempotent, non-destructive, skips custom paths).
- **Logging configuration** — new `LoggingConfig` section in `config.yaml` with
  `level`, `log_file`, `max_file_size_mb`, `backup_count`, `format`,
  `date_format`. `setup_logging()` configures root logger with console +
  `RotatingFileHandler` + uvicorn integration. CLI `--verbose/-v` flag for
  DEBUG, `--log-file` option on `serve`.
- **`mnemos doctor --paths`** — new flag showing all Mnemos paths in one table
  (root, config, data_dir, db_path, vault, logs, cache, completion, mcp_config).
  JSON output includes `"paths"` key.
- **Shell completion fix** — completion script now stored as a file in
  `~/.mnemos/completion/mnemos.{shell}` instead of inline `eval` in rc files.
  `.bashrc`/`.zshrc` gets a single `source` line with `[ -f ... ] && source ...`
  guard. Old `eval` entries auto-migrated. `_is_installed()` no longer matches
  commented-out lines.

### Changed

- `MnemosConfig` defaults: `vault_path` → `~/.mnemos/vault`, `data_dir` →
  `~/.mnemos/data` (was `~/mnemos-vault`, `~/.mnemos`).
- `scripts/install.sh`: default venv path `~/.mnemos/venv` (was `~/.mnemos-venv`).
- `scripts/mcp-setup.sh`: updated default paths.
- `config.example.yaml`: new paths + `logging:` section.

### Fixed

- **Shell completion not working** — the `eval` line in `.bashrc` was
  commented out (`#eval "$(mnemos --show-completion bash)"`), but
  `_is_installed()` matched the marker inside the comment, reporting "already
  installed" without fixing it. Now checks for active (uncommented) `source`
  lines only.

## [2.0.6] — 2026-06-22

### Fixed

- **MCP server `__main__` block missing** — `python -m mnemos.mcp_server`
  imported the module but never called `main()`, so the server didn't
  start. Added `if __name__ == "__main__"` block with `asyncio.run(main())`.
- **MCP config pointed to source checkout** — `mcp-setup.sh` generated
  config with `PYTHONPATH=src` pointing to the source directory. If the
  source was deleted, MCP broke. Now uses the installed `mnemos mcp-server`
  binary from `~/.mnemos-venv/bin/mnemos` — no source dependency.
- **`mcp-setup.sh` couldn't overwrite stale entries** — added `--force`
  flag to replace an existing `mnemos` entry (e.g. when migrating from
  a source-checkout config to the installed binary).
- **mypy `--strict` failures on numpy-typed code** — `vector_store.py`
  `_pack`/`_unpack` returned `Any` from numpy calls; `embeddings/__init__.py`
  iterated over chromadb's `Embedding?` TypeVar. Both now use explicit
  `cast()` to the declared return types.
- **mypy numpy stub syntax errors (PEP 695)** — added `ignore_errors = true`
  to the `numpy` / `numpy.*` mypy overrides so the PEP 695 `type` statement
  in the stubs doesn't break `--strict` on Python 3.12/3.13.

## [2.0.5] — 2026-06-22

### Fixed

- **`mnemos_filter` MCP tool not registered** — the tool dispatch
  existed but the tool was missing from `list_tools()`, so agents
  couldn't discover or call it. Now properly registered with
  `memory_id`, `profile`, and `budget` parameters.
- **`mnemos_add` missing `filtered` flag** — the return value didn't
  include a `filtered` boolean indicating whether auto-filter ran.
  Now returns `{"filtered": true/false, ...}`.
- **Stale agent wiring tests** — updated assertions to match the
  improved YAML preprocessing + regex fallback behavior.

## [2.0.4] — 2026-06-21

### Fixed

- **`install.sh` UX polish** — when MCP is already configured, the
  installer no longer shows a misleading "Aborting" failure message.
  It now shows a green "already registered" success and continues.
- **Prompts are visually distinct** — interactive prompts in
  `install.sh` are now framed with horizontal rule separators and a
  `[?]` prefix so they stand out from info messages.

## [2.0.3] — 2026-06-21

### Fixed

- **Container build failed** — `pip install .[mcp]` inside the container
  failed with `FileNotFoundError: Forced include not found: /app/integrations`
  because the `integrations/` directory was not copied to the container
  before pip install. The `pyproject.toml` `[tool.hatch.build.targets.wheel.force-include]`
  maps `integrations/` → `mnemos/integrations/` inside the wheel, but hatch
  resolves the source path relative to the build CWD (`/app`), which had no
  `integrations/` directory. Added `COPY integrations/ ./integrations/` to
  `Containerfile` before the `pip install` line. The force-include in
  `pyproject.toml` is correct for wheel builds and was not changed.

## [2.0.2] — 2026-06-21

### Fixed

- **Agent wiring crashes on unquoted `:` in frontmatter** — agent files like
  `mnemos-curator.agent.md` have `description: (GCW) ... STUB mode: operates on...`
  where the unquoted `:` confuses the YAML parser (`mapping values are not
  allowed in this context`). Switched `agent_wiring.py` from `frontmatter.load()`
  to `frontmatter.loads()` and changed the parse-error status from `ERROR` to
  `SKIPPED_NO_FRONTMATTER` so the agent is skipped gracefully and the rest of
  the wiring batch continues. The error is still logged for observability.
- **Noisy "no deploy map" output for partial targets** — `generic-copilot` only
  has a `prompts:` deploy map, and `gcw` has no `prompts:` map. The deploy code
  printed a `SKIPPED` row for every unsupported kind, making the output look
  broken on every run. Unsupported kinds are now skipped silently with a
  `debug`-level log — only kinds the target actually supports appear in the
  result table.
- **`install.sh` printed "Non-interactive terminal — skipping agent wiring"**
  even when the user answered "y" — the `setup_instructions()` function called
  `mnemos integration setup --target all --no-mcp` without `--no-wire-agents`,
  so the default flow ran the interactive prompt in a non-TTY subshell and
  printed the skip message. Added `--no-wire-agents` to the instructions step
  since agent wiring is handled separately by `setup_wire_agents()`.

## [2.0.1] — 2026-06-21

### Fixed

- **Integration files missing from wheel** — `integrations/targets.yaml`,
  `integrations/instructions/*.instructions.md`, `integrations/skills/*.md`,
  and `integrations/prompts/*.prompt.md` were not included in the v2.0.0
  wheel because `pyproject.toml` did not declare them as package data.
  Added `[tool.hatch.build.targets.wheel.force-include]` so the `integrations/`
  directory ships inside the wheel at `mnemos/integrations/`. Also added an
  `importlib.resources` fallback in `load_targets()` and
  `IntegrationManager._default_pack_root()` to find the pack regardless of
  install method (source tree, wheel, or editable).

## [2.0.0] — 2026-06-21

### Added

- **`make lint-shell` target** (`Makefile`) — runs `shellcheck scripts/*.sh`
  and is included in the `verify` gate alongside `lint`, so shell scripts are
  now covered by the same local + CI quality bar as Python code.
- **Git-workflow notes runbook** (`docs/{ru,en}/admin/runbooks/git-workflow-notes.md`)
  — documents the expected `git branch -d` warning after a squash-merge and
  why `-d` is safe despite the warning.
- **Dashboard / metrics API** (`src/mnemos/api/main.py`,
  `src/mnemos/manager.py`, `src/mnemos/storage/sqlite_store.py`) — three
  new endpoints for the `mnemos-eyes` frontend:
  - `GET /api/v1/stats` — structured JSON with volume, filter, pipeline,
    search, vectors, and sessions sections.
  - `GET /api/v1/stats/timeseries` — daily memory counts for configurable
    range (`?range=30d&metric=memories_added`).
  - `GET /api/v1/metrics` — Prometheus text exposition format for
    Grafana/observability.
  - `GET /metrics` kept as backward-compatible alias (returns `stats()`
    JSON).
- **Extended `GET /memories` filters** — `status`, `project`, `agent`,
  `tags` (comma-separated, AND logic), `since`, `until` (ISO datetime),
  `offset` (pagination). Invalid `status` returns 422.
- **Search instrumentation** (`src/mnemos/manager.py`) — in-memory
  counter + latency tracker for `MemoryManager.search()`. Exposed via
  `/api/v1/stats` `search` section and `mnemos_search_requests_total`
  Prometheus metric. Resets on restart (accepted trade-off for
  dashboard).
- **New SQLite aggregate queries** (`src/mnemos/storage/sqlite_store.py`)
  — `count_by_agent()`, `count_by_type()`, `count_by_date()`,
  `count_sessions()`.
- **Agent MCP wiring** (`src/mnemos/cli/agent_wiring.py`,
  `src/mnemos/cli/util.py`) — `mnemos integration setup` now wires
  `mnemos/*` into the `tools:` frontmatter of GCW agent files
  (`~/.copilot/agents/*.agent.md`). Flags: `--wire-agents` (enable),
  `--wire-agents --all` (wire all unwired, no prompt), `--wire-agents
  --select name1,name2` (specific agents), `--no-wire-agents` (skip),
  `--precise` (individual `mnemos/mnemos_*` tokens instead of wildcard),
  `--dry-run` (preview). Only `tools:` is touched; agents with
  `tool_profile:` are skipped (managed by the GCW installer). Idempotent.
- **Agent wiring in `mnemos integration verify`** — the verify report now
  includes an agents section showing wired / unwired / skipped counts.
- **Agent wiring check in `mnemos doctor`** (`src/mnemos/cli/doctor.py`) —
  9th health check reporting agent wiring status; warns if unwired agents
  are detected.
- **Context Filter auto-activation on ingest (M10)**
  (`src/mnemos/filter/pipeline.py`, `src/mnemos/manager.py`,
  `src/mnemos/config.py`) — the five-stage filter (dedup, noise, extract,
  compress, tokens) now auto-runs on every `mnemos_add` when
  `auto_filter: true` (default for new installs). Stores `raw_content` +
  `clean_content` + `filter_stats`; filter failures are non-fatal (memory
  is still saved with raw content). `mnemos_search` /
  `mnemos_recall_context` return `clean_content` when available.
- **`mnemos_filter` MCP tool** (`src/mnemos/mcp_server.py`) — explicit
  re-filter of an existing memory. Parameters: `memory_id` (required),
  `profile` (optional, auto-detected), `budget` (optional token budget).
  Returns `clean_content` + per-stage `stats`.
- **`mnemos filter` CLI command** (`src/mnemos/cli/main.py`) —
  `mnemos filter <id>` re-filters a single memory; `mnemos filter --all`
  re-filters every memory (batch, reports aggregate stats). Flags:
  `--profile`, `--budget`, `--all`.
- **Filter stats in `mnemos stats`** (`src/mnemos/manager.py`) — the stats
  output now includes a filter section: `auto_filter` flag,
  `filtered_count`, `unfiltered_count`, `avg_reduction_pct`, and
  `by_profile` breakdown.
- **Context Filter profiles** — `log | terminal | code | docs | web |
  default`, auto-detected from content heuristics (timestamps, ANSI codes,
  code keywords, HTML tags, markdown structure).
- **`make doctor` target** (`Makefile`) — runs `mnemos doctor --json` as a
  health-check gate, wired into `make verify`. Fails the build on actual
  failures (exit 1); allows warnings (exit 2) since CI environments typically
  lack agent harnesses (integration check warns by design).
- **`install.sh` post-install suggestions** (`scripts/install.sh`) — the
  success message now suggests `mnemos completion` (shell autocompletion),
  `mnemos integration setup` (behavioral instructions), and `mnemos doctor`
  (installation verification). Suggestions only — nothing is auto-run.
- **README Quick Start step 4** (`README.md`, `README.ru.md`) — added
  "Deploy behavioral instructions" / "Установка поведенческих инструкций"
  section covering `mnemos integration setup`. Updated step count from
  "Three" to "Four" in both languages.

### Removed

- **ai-brain provenance comments** (`src/mnemos/`) — removed "Forked from
  ai-brain" / "Key differences from ai-brain" / "Renamed from ai-brain"
  comment blocks from 10 source files (`__init__.py`, `mcp_server.py`,
  `storage/{sqlite_store,vector_store,vault,__init__}.py`,
  `embeddings/__init__.py`, `auto_collect.py`, `models.py`, `cli/main.py`).
  Module docstrings now describe what each module does, not where it came
  from. Provenance lives in ADR 0001 and git history. Functional migration
  code (`cli/migrate.py`, `mnemos migrate from-ai-brain` command) is
  intentionally preserved.
- **`.history/` directory** — deleted the VS Code Local History cache
  (~100+ stale files, gitignored). VS Code recreates files as needed.

### Changed

- **`create_provider()` docstring** (`src/mnemos/llm/base.py`) — updated to
  reference PR 2 (standard providers: Ollama + OpenAI + Anthropic); the
  factory still raises `NotImplementedError` in PR 1.

- **`mnemos completion` command** (`src/mnemos/cli/completion.py`) —
  auto-detects the current shell from `$SHELL`, generates the completion
  script, and auto-installs it into the right rc file (`~/.bashrc`,
  `~/.zshrc`, `~/.config/fish/completions/mnemos.fish`). Idempotent —
  re-running does not duplicate the source line. Supports
  `mnemos completion bash|zsh|fish` for explicit shell selection and
  `mnemos completion --show-instructions` to print manual steps without
  modifying files. No `--install` flag — auto-install is the default.
- **`mnemos doctor` command** (`src/mnemos/cli/doctor.py`) — health check
  that runs 8 checks (config, data dir, vault, SQLite DB, vector store,
  MCP server registration, integration layer, tag contract) and reports
  status with a rich table. Exit codes: 0 = all pass, 1 = one or more
  failed, 2 = warnings only. Supports `--json` for CI/scripting.

### Removed

- **ai-brain provenance comments** (`src/mnemos/`) — removed "Forked from
  ai-brain" / "Key differences from ai-brain" / "Renamed from ai-brain"
  comment blocks from 10 source files (`__init__.py`, `mcp_server.py`,
  `storage/{sqlite_store,vector_store,vault,__init__}.py`,
  `embeddings/__init__.py`, `auto_collect.py`, `models.py`, `cli/main.py`).
  Module docstrings now describe what each module does, not where it came
  from. Provenance lives in ADR 0001 and git history. Functional migration
  code (`cli/migrate.py`, `mnemos migrate from-ai-brain` command) is
  intentionally preserved.
- **`.history/` directory** — deleted the VS Code Local History cache
  (~100+ stale files, gitignored). VS Code recreates files as needed.

### Changed (BREAKING)

- **CLI restructure** — `mnemos util-*` commands renamed to `mnemos integration *`
  (`util-detect` → `integration detect`, `util-setup` → `integration setup`, etc.).
  No deprecation aliases — clean break.
- **`mnemos tags-validate`** → **`mnemos tags validate`** (nested subcommand).
- **`mnemos migrate-from-ai-brain`** → **`mnemos migrate from-ai-brain`** (nested subcommand).
- **`auto_filter: true`** is now the default for new installs. Existing records
  are unaffected (`clean_content` stays `None` until explicitly filtered).
- **`hf_revision` default** changed from a fabricated SHA to `""` — ONNX
  provider now requires explicit pinning. Existing configs with a value are
  unaffected.

### Changed

- **Integration layer** (`integrations/`, `src/mnemos/cli/integration.py`,
  `src/mnemos/cli/util.py`) — versioned pack of instructions + skills +
  prompts that ships inside the package and deploys into detected agent
  harnesses (GCW `~/.copilot/`, generic Copilot `~/.config/Code/User/prompts/`,
  Cursor `~/.cursor/rules/`). New `mnemos integration *` CLI subcommands:
  - `mnemos integration detect` — print detected harnesses + deploy paths
  - `mnemos integration setup` — deploy files + register MCP (unified entry point)
  - `mnemos integration update` — bring stale files to current version
  - `mnemos integration verify` — compare deployed files against shipped pack
  - `mnemos integration uninstall` — remove only stamped files, preserve user files
  - All commands support `--dry-run` and `--target` (default: all detected)
  - Version stamp `<!-- mnemos-integration: v2.0.0 -->` on every deployed file
  - Idempotent: re-running `integration setup` updates stale files without duplicating
- **`integrations/targets.yaml`** — harness detection rules + deploy maps
  with `~` expansion. A target is detected if ANY of its detect paths exist.
- **`install.sh --instructions` / `--no-instructions`** flag — deploys the
  agent integration pack after MCP setup (interactive prompt over `/dev/tty`,
  same pattern as `--mcp` / `--no-mcp`).

### Fixed

- **Shellcheck findings in `scripts/mcp-setup.sh`** — resolved SC2015
  (`A && B || C` replaced with `if/else`) and SC2059 (variables removed from
  `printf` format strings via `%s` args). No suppressions added.

## [1.2.0] — 2026-06-18

### Added

- **CLI `--version` / `-V` flag** (`src/mnemos/cli/main.py`) — eager callback
  prints `mnemos <version>` and exits 0, so `mnemos --version` works on every
  subcommand without interfering with command parsing.
- **Zero-friction installer UX** (`scripts/install.sh`) — drops a `mnemos`
  launcher symlink into `~/.local/bin` (no manual venv activation needed),
  adds an interactive VS Code MCP setup prompt over `/dev/tty` plus
  non-interactive `--mcp` / `--no-mcp` flags for CI, prints the resolved
  version in the success message instead of "unknown", and fixes the
  `mnemos add` example to use positional content + comma-separated tags.

### Changed

- **README rework (EN + RU)** — professional layout with centered banner,
  badges, and navigation; emoji-sectioned thematic blocks (Quick start,
  What it is, Architecture, Surfaces, Lore, Docs, GCW, License, Contributing);
  the 3-step Quick Start now includes the MCP registration step; two
  `<details>` collapsibles cover alternative install methods. EN and RU are
  mirror-synchronized.
- **Version bump 1.1.3 → 1.2.0** — `pyproject.toml`, `src/mnemos/__init__.py`,
  README/README.ru version badges and pinned container tag
  (`ghcr.io/korrnals/mnemos:1.2.0`), `scripts/install.sh` usage example.

## [1.1.3] — 2026-06-18

### Added

- **One-liner install script** (`scripts/install.sh`) — `curl | bash` installer
  that detects Python ≥3.11, creates a venv, installs the latest wheel from
  GitHub Releases, and verifies the CLI. Supports `--container` flag for
  pulling and running the ghcr.io image in one command.
- **One-liner MCP setup** (`scripts/mcp-setup.sh`) — detects the `mnemos`
  executable, finds VS Code `mcp.json` (User or Workspace scope), and
  registers the `mnemos` MCP server entry via safe JSON merge.
- **Russian README** (`README.ru.md`) — full bilingual README with language
  switcher at the top of both `README.md` and `README.ru.md`.
- **Container one-liner** — `--container` flag in `install.sh` pulls
  `ghcr.io/korrnals/mnemos:VERSION`, creates volumes, and starts the container.

### Fixed

- **Banner SVG** — GitHub strips `<style>` tags from inline SVGs, causing
  font-family classes to be lost. All font attributes are now inlined
  directly on each `<text>` element.
- **`config.example.yaml`** — removed stale `telegram:` block (not part of
  the schema) and fixed `brain watch` → `mnemos watch` comment.
- **`config.container.yaml`** — removed `telegram:` block.
- **Broken README links** — removed dead `tasks/` link and
  `.github/instructions/git-workflow-mnemos.instructions.md` link.

### Changed

- **Purged `ai-brain` references** from all user-facing docs (README,
  security, index, getting-started, migrate runbook, milestones, ci-cd
  runbook). Only remaining mention is in ADR-0001 as a brief heritage note.
- **Bilingual README sync** — both READMEs now have identical structure:
  lore, mermaid diagram, quick start, one-liner install, container one-liner,
  three surfaces, documentation table, GCW relationship, contributing.

## [1.1.2] — 2026-06-18

### Documentation

- **Docs reorganized into audience-based tree** — `docs/` split into
  `en/` and `ru/` language axes, each with `user/`, `admin/`, and
  `architecture/` tiers. Added EN hub with MCP guide, fixed cross-links.
- **Russian mirror** — full RU translation of user/admin/architecture
  tiers, parity with EN structure.
- **Lore SVG banner** — `docs/assets/mnemos-banner.svg` added to README
  and docs landing. Classical Greek-key meander, fluted-column hint,
  9-node constellation (Muses + memory graph), gold/marble on midnight.
  Typography refined: centered brand block, gilded Greek source word
  (μνημοσύνη), classical lozenge divider.
- **Container deployment runbook** — new
  `docs/{en,ru}/admin/runbooks/container-deployment.md` covering
  build/push-ghcr/compose/single/kube/quadlet/config/health.
- **Install docs clarified** — added Install options table (editable /
  wheel / container), aligned MCP snippet to VS Code `"servers"` config,
  added Container subsection.

### Added

- **Release CI (`.github/workflows/release.yml`)** — tag-only (`v*.*.*`)
  workflow with two parallel jobs: `build-dist` (sanity-gate tag==pyproject
  version, `python -m build`, attach wheel+sdist to GitHub Release) and
  `build-push-image` (buildah bud → push to `ghcr.io/korrnals/mnemos:VERSION`
  + `:latest`). No external secrets required.
- **Makefile dist/image targets** — `build-dist`, `build-image`,
  `push-image` targets with `VERSION` auto-detection from `pyproject.toml`.

### Changed

- **Version bump 1.1.1 → 1.1.2** — `pyproject.toml`, `src/mnemos/__init__.py`,
  README version badge and wheel/container references updated.

## [1.1.1] — 2026-06-17

### Fixed

- **`mypy --strict` clean on `mcp_server.py`** — the mcp SDK ships its
  `Server.list_tools` / `Server.call_tool` decorators unannotated upstream, which
  tripped `untyped-decorator` / `no-untyped-call` only when the optional `mcp`
  extra is installed. Replaced the environment-fragile inline `type: ignore`
  (which would become "unused" in CI under `warn_unused_ignores`) with a
  module-scoped `[[tool.mypy.overrides]]` so the type-check result is identical
  with or without the `mcp` extra.

### Changed

- **`.gitignore` hardened** — added tool/type/lint caches (`.mypy_cache/`,
  `.pytest_cache/`, `.ruff_cache/`, `.tox/`), coverage artifacts, and the
  generated `bandit-report.json`; de-duplicated the vault entry and renamed it
  `brain-vault/` → `mnemos-vault/` to match the real default `vault_path`.
- **`bandit-report.json` untracked** — it is regenerated by `make security`, so
  it no longer belongs in version control.

### Added

- **`make bootstrap` / `make check-venv`** — bootstrap recreates `.venv` with the
  editable install + dev extras; check-venv fails fast if the editable install
  resolves to a stale path (guards against silent breakage after a project move).

### Documentation

- **Unified Git workflow policy** — added `.github/instructions/git-workflow-mnemos.instructions.md` (shared across `mnemos` and `mnemos-eyes`). Defines the `feat/*` → `dev-<stage>` → `release/X.Y.Z` → `main` branching model, merge strategies, Conventional Commits format, and PR checklist. README Contributing section updated with a pointer.

## [1.1.0] — 2026-06-17

### Added

- **Token auth + TOTP 2FA (ADR-0014)** — opt-in `AuthMiddleware` gated by
  `api.auth_enabled`. Four new endpoints (`POST /auth/login`,
  `POST /auth/verify`, `POST /auth/logout`, `GET /auth/me`) support opaque
  bearer tokens and per-token TOTP (RFC 6238 via `pyotp`). New `ApiConfig`
  keys: `auth_enabled`, `totp_enabled`, `totp_master_key` (env-only via
  `MNEMOS_API__TOTP_MASTER_KEY`), `session_ttl_sec`, `session_pin_ip`,
  `behind_tls_proxy`, `trusted_proxies`. See
  [docs/api-reference.md](docs/api-reference.md#authentication) and ADR-0014.
- **CORS support** — new `ApiConfig` keys: `cors_enabled`,
  `cors_allow_origins`, `cors_allow_credentials`, `cors_allow_methods`,
  `cors_allow_headers`. CORS middleware is the outermost layer so OPTIONS
  preflight is answered before auth. Combining `allow_origins=["*"]` with
  `allow_credentials=True` raises `ValueError` at startup (forbidden by the
  Fetch/CORS spec). See ADR-0014.
- **`GET /tags`** — returns the list of distinct tags with usage counts,
  sorted by count descending then tag ascending as a tie-break.
- **MCP tool dispatch smoke tests** — MCP tool dispatch / routing now has
  smoke-test coverage.

### Security

- **PBKDF2 token hashing** — bearer tokens are stored as PBKDF2-HMAC-SHA256
  digests (600 000 iterations, fixed salt `mnemos.api.auth.fernet.v1`);
  plaintext is shown once at creation and never persisted. (ADR-0014)
- **Fail-closed auth middleware** — `AuthMiddleware` returns HTTP 503
  `{"detail": "Auth not initialised"}` when the API config object is absent,
  rather than silently allowing through. (ADR-0014)
- **Trusted-proxy XFF gating** — `X-Forwarded-For` is honoured for
  rate-limit keying and session-IP pinning only when the direct peer's IP
  falls inside a configured `trusted_proxies` CIDR; XFF headers from
  untrusted peers are ignored entirely. (ADR-0014)
- **TOTP replay prevention** — a per-token `totp_last_step` column records
  the time-step of the last accepted TOTP code; a subsequent code is rejected
  unless its time-step strictly exceeds the recorded value. (ADR-0014)
- **CLI non-loopback bind guard** — `mnemos serve` exports
  `MNEMOS_API__HOST` and `MNEMOS_API__PORT` before launching uvicorn; the
  worker's startup guard refuses a non-loopback bind unless
  `api.auth_enabled=true`. (ADR-0014)
- **Obfuscated-IP / userinfo SSRF regression coverage** — SSRF guard v2
  adds regression tests for decimal, octal, and hex encodings of loopback
  and `169.254.169.254` (AWS / GCP metadata) addresses and for `user@host`
  userinfo masking on redirects; all encodings are blocked via
  `getaddrinfo` resolution before the request is issued. (ADR-0009)

## [0.2.1] — 2026-06-17

### Fixed

- **SSRF via redirects (`MemoryManager.ingest_url`)** — the HTTP client
  followed 30x redirects (`follow_redirects=True`), letting an
  attacker-controlled public host pivot to an internal/loopback/metadata
  endpoint that `_validate_url` never saw. Now `follow_redirects=False`,
  matching the documented v1 posture in `docs/security.md` §2. Regression
  test added (`test_ingest_url_does_not_follow_redirects`). See ADR-0009.
- **SQLite connection leak (`VectorStore`)** — the thread-local connection
  was never closed (no `close()` method), surfacing as
  `ResourceWarning: unclosed database` in tests and leaking file descriptors
  in long-running processes. Added `VectorStore.close()` and wired it into
  `MemoryManager.close()`.
- **Version drift** — `pyproject.toml`, `mnemos.__version__`, and the FastAPI
  app all reported `0.1.0` despite the `v0.2.0` release tag and CHANGELOG.
  Bumped to `0.2.0`; the FastAPI app now derives its version from
  `mnemos.__version__` to prevent future drift.

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
- Default paths: `~/.mnemos/` for data, `~/mnemos-vault/` for Obsidian sync.
- Env vars: `AI_BRAIN_*` → `MNEMOS_*`.

### Deprecated
- ai-brain project — all new development continues in Mnemos.

## ai-brain history (pre-fork)

See `upstream-ai-brain` git remote for full history.
