# Changelog

All notable changes to Mnemos.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

### Changed

- **CLI restructure — professional command grouping**:
  - `mnemos util-*` → `mnemos integration *` (detect/setup/update/verify/uninstall)
    — the flat `util-*` namespace is replaced by a real `integration` group.
  - `mnemos tags-validate` → `mnemos tags validate` — nested under a `tags` group.
  - `mnemos migrate-from-ai-brain` → `mnemos migrate from-ai-brain` — nested
    under a `migrate` group.
  - Core commands (`add`, `search`, `recall`, `stats`, `serve`, `mcp-server`)
    stay flat — daily-use commands, like `git add`/`git commit`.
  - `auth token/totp` unchanged.
  - Clean break — no deprecation aliases (owner-confirmed).
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
  - Version stamp `<!-- mnemos-integration: v1.2.0 -->` on every deployed file
  - Idempotent: re-running `integration setup` updates stale files without duplicating
- **`integrations/targets.yaml`** — harness detection rules + deploy maps
  with `~` expansion. A target is detected if ANY of its detect paths exist.
- **`install.sh --instructions` / `--no-instructions`** flag — deploys the
  agent integration pack after MCP setup (interactive prompt over `/dev/tty`,
  same pattern as `--mcp` / `--no-mcp`).

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
