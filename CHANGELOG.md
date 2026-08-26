# Changelog

All notable changes to Mnemos.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **ADR-0018 Wave 2 — `on_context_rewrite` lifecycle event (#125)** — the harness can now report a context rewrite so the original of the replaced block lands in LTM losslessly, as a new `src/mnemos/context_rewrite.py` module orchestrated through `MemoryManager.context_rewrite(content, project, agent, session?, supersedes?, diff?, include_marker=False)`. **Idempotent**: the event key is content-addressed (SHA-256 over the length-prefixed canonical tuple `project/agent/session/supersedes/content`, persisted as `metadata["rewrite_event_key"]`, looked up before any write via the new `SQLiteStore.get_memory_id_by_rewrite_event_key` — SQLite `json_extract` over the existing `memories.metadata` column, no schema change); a re-delivery returns a `deduplicated` receipt with the same memory id and performs no duplicate writes. The advisory `diff` is deliberately excluded from the key — not load-bearing, so a re-delivery with a different diff is still the same event. **Version-less**: no ordering promise, no version chains (explicit in the docstring and locked by a receipt-shape test); replacement lineage is a `supersedes` edge through the P1-a store methods (idempotent insert; the FK backstop plus an up-front existence pre-flight convert a missing target into a clean `ValueError` before any write). **Normal pipeline path**: the original enters via `MemoryManager.add` as `raw` (Layer-1 write scan auto-tags `mnemos:no-federate` on a secret hit; zero-loss storage); rehydrate is the EXISTING scanned/gated channels — regression-tested through `assemble_context` (raw invisible until the pipeline advances the entry, then surfaced with provenance and issuance redaction) and `retrieve_content` (the `include_marker=true` CCR marker redeems project-scoped). The advisory diff becomes part of the persisted record, so it gets its own Layer-1 verdict (`rewrite_diff_scan_verdict`: clean/hit/unknown, the P1-a vocabulary) and a hit also tags the record `mnemos:no-federate` — otherwise a secret in the diff would federate unflagged through a channel that only scans `content`. Provenance: `metadata["source"] = "context-rewrite"` + `rewrite_session`; identity via the tag contract (`project:<slug>`/`agent:<slug>` + `mnemos:session`, enforced by `validate_tag_contract` with the caller's strictness knob). Surfaces: MCP tool `mnemos_context_rewrite` (typed args with boundary guards; clean `{"error": …}` shapes) + REST `POST /context/rewrite` (same manager path; **200** for both `stored` and `deduplicated` — the event is idempotent, 201 would lie on re-delivery; `ValueError` → 422); docs `mcp-tools.md` + `http-api.md` (EN+RU). Acceptance: `tests/test_context_rewrite.py` (28 tests: double-delivery dedupe, diff-excluded-from-key, different-session-is-a-new-event, idempotent edge relink, edge created+queryable, unknown-target pre-flight, diff stored-as-metadata/not-echoed, diff-secret no-federate + verdict, tag contract + provenance shape, strict invalid-slug rejection, version-less receipt shape, boundary validation, rehydrate roundtrip incl. the raw→processed status gate, marker redemption, secret-in-original Layer-1 + issuance redaction, MCP/REST surfaces). **W2 security-review round (both findings fixed in the same branch):** F1 — write-surface guardrails: `mnemos.context_rewrite_rate_limit_per_minute` (default 30, 0 disables; counts STORED events per (project, session) in a rolling minute via the #96 guardrail-5 SQL pattern — dedupe re-deliveries consume no quota) raising `ContextRewriteRateLimited` (REST **429**, MCP `{"error": …, "rate_limited": true}`; not a ValueError, so validation stays 422) + boundary size caps `context_rewrite_max_content_chars` (1 MiB) / `context_rewrite_max_diff_chars` (256 KiB); F2 — project-scoped `supersedes`: the target must belong to the caller's project, and the error message is identical for "not found" and "another project's memory" (no global existence oracle, mirroring P1-a `ccr_get`); review test gap — dedupe+include_marker receipt still carries the marker (42 tests total). **Ratification flags:** `mnemos:session` chosen as the subtype (a dedicated `mnemos:context-rewrite` subtype would extend the shared tag-contract vocabulary — committee call); REST 200-not-201 on both receipts; the diff Layer-1 verdict extension.
- **ADR-0017 D1 Wave 1 — `assemble_context` provider contract (#125 contract core)** — one API assembles the model-facing context block for pre-LLM-call injection, as a new `src/mnemos/assemble.py` module orchestrated through `MemoryManager.assemble_context(session, project, file?, budget=2048, mode="sync", expand_ccr=False, async_handle=None)`. Fixed pipeline (recorded verbatim in `stats.stages`): hybrid RRF recall via the standard search path (entry-invariant `CONTEXT_ADMISSIBLE_STATUSES` gate; `file` derives the recall query and pins applyTo-scoped rule memories to the top; project scoping enforced at the channel boundary — see the reported defect below) → **optional CCR stage** (`expand_ccr=true`: inline `[compressed: …]` markers expand via project-scoped `retrieve_content`, budget-aware — an original that would not fit stays compressed with the marker intact) → 5-stage context filter per block → **mandatory secret scan** (`scan_issuance` per block; refuse mode drops the block fail-closed; CCR-channel redactions merged into the per-block count) → CacheAligner per block (before wrapping, so provenance lines stay parseable) → greedy whole-block token budget fill (blocks that do not fit are skipped whole, never truncated). Every injected block carries the provenance prefix `[mnemos:<id> project=<slug> status=<status> retrieved=<iso>]`. `mode` carries both axes (ArchCom addendum 1): delivery `sync` (default) / `async` (result stored in a bounded per-manager registry — cap 32, oldest evicted, entries session-bound per the security review round: only the assembling session may redeem a handle, a mismatch raises without consuming the entry — and returned as a handle envelope, fetched once via `async_handle`) and contentType `code` / `prose` (recall candidates filtered by `metadata["content_type"]`, captured at ingest via `detect_profile` — binary partition, on-the-fly fallback for legacy rows counted in `recall.content_type_fallbacks`). Budget partitioning (addendum 2, MAY) deliberately NOT implemented — monolithic until the D5 baseline corridor. New MCP tool `mnemos_assemble_context` (arg types guarded at the boundary, incl. a non-string `file` → clean error dict) + REST `POST /context/assemble` (same manager path; ValueError → `{"error":…}` / HTTP 422); docs: `mcp-tools.md` + `http-api.md` (EN+RU). `filter/pipeline.estimate_tokens` promoted as the public single source of the token heuristic. Per-block `ccr_hashes` records the content-addressed origins of expanded CCR spans (provenance fidelity — the wrapper names the outer memory only). Acceptance: `tests/test_assemble_context.py` (32 tests: stage order, provenance format, planted-secret redaction + refuse drop, code/prose fixtures, budget, sync/async shapes incl. session-bound handles, CCR on/off/budget-aware, splice-point re-scan of a secret formed by marker expansion, cross-project marker redemption denied, raw-status gate, applyTo pinning, MCP+REST surfaces incl. arg guards). **Reported defect (escalated, not worked around):** `MemoryManager.search` passes `project` to the FTS leg only — the vector leg has no project filter, so a project-scoped search can surface other projects' rows via the vector resolve path (pre-existing; systemic fix changes shared ranking semantics → ArchCom queue as its own ticket; this channel defends at its own boundary and counts `recall.project_scoped_out`).
- **ADR-0018 P1-b — `ccr.require_project_match` knob (Security findings 1+4, CWE-668 ergonomics)** — new `CCRConfig.require_project_match` (default `False`, legacy behavior preserved). Unscoped retrieval of a project-scoped CCR entry (`retrieve_content` with `project=None` on an entry stored under a non-empty project) now always logs a WARNING naming the hash and the entry's project (finding 4 — the ergonomics gap that made unscoped redemption invisible); with the knob enabled the issuance is DENIED instead (`refused=True`, `reason` names the scope requirement, no content — finding 1's deny option). `ccr.retrieve` results now carry the entry's `project` in both `found=True` branches so the manager can audit this without a second `ccr_get` (which would bump the retrieval counter). Acceptance: `TestProjectScopeErgonomics` in `tests/test_p1b_issuance.py`.
- **ADR-0018 P1-a — scan-at-store verdict flag on `ccr_cache`** — `SQLiteStore.ccr_store` now runs `detect_secrets` on the original at store time and persists the verdict in two new columns (`secret_scan_verdict`: `clean` | `hit` | `unknown`, and `secret_scan_at` for freshness auditing; nullable via SQLite migration — legacy rows keep `NULL`, treated as unscanned). The stored original remains verbatim (zero-loss, committee decision); on `hit` a WARNING is logged with the hash and log-safe per-pattern counts only (raw matched values are never logged — hard rule). The verdict is observability only and never fast-paths the P0 issuance scan: patterns evolve between store and retrieve, so a stored `clean` would go stale — `retrieve_content` keeps scanning unconditionally. Re-compressing identical content (same hash) refreshes the verdict, opportunistically upgrading legacy `NULL` rows. `ccr_get` results now carry both fields.
- **ADR-0018 Phase 1 groundwork — minimal `memory_edges` table** — new SQLite table for directed memory edges with exactly one kind (`supersedes`; `CHECK`-constrained), composite PK `(from_memory_id, to_memory_id, kind)` making `add_memory_edge` idempotent (`INSERT OR IGNORE`, returns `True` when inserted), FK to `memories(id)` with `ON DELETE CASCADE`, and a `CHECK (from <> to)` backstop — self-edges are rejected with a friendly `ValueError` in `SQLiteStore.add_memory_edge` (a memory superseding itself is a caller bug). Store methods `add_memory_edge` / `get_direct_edges` (one hop, ordered by creation) + thin `MemoryManager.add_memory_edge` / `get_memory_edges` wrappers. Deliberately NO graph expansion and NO MCP surface — `on_context_rewrite` arrives with mnemos #125 in Phase 2.

- **Pi coding agent integration target (`pi`)** — native mnemos support for [Pi](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) (npm `@earendil-works/pi-coding-agent`). Pi has no built-in MCP client by design — tools arrive via TypeScript extensions — so the target ships a new artefact kind: `integrations/extensions/mnemos-mcp.ts`, a stamped MCP bridge that spawns `mnemos mcp-server` over stdio (the same ADR-0017 D1 wire), performs the JSON-RPC handshake and registers every `mnemos_*` tool as a native Pi tool (`/reload` hot-reloads, `/mnemos` reconnects, `MNEMOS_BIN` overrides the server binary). `mnemos integration setup --target pi` deploys the bridge to `~/.pi/agent/extensions/` plus the skill pack in the nested layout Pi reads natively (`~/.pi/agent/skills/<name>/SKILL.md`); MCP "registration" for Pi is the deployed (stamped) bridge itself — verified, not merged into a config. Engine: new `ArtefactKind.EXTENSION` (`extensions`), `.ts` joins the deployable suffixes, `stamp_content()` gains `line_comment=` so the version stamp stays a valid `// <!-- ... -->` TS comment, and uninstall/verify/update treat the bridge like any stamped artefact (user extensions never touched). Docs: Pi section in `integrations/mcp-presets.md` + integration guides (EN+RU). Acceptance: `TestPiTarget` (11 tests) in `tests/test_integration.py`.

- **ADR-0017 — memory system evolution roadmap** (provider contract `assemble_context`, memory graph + learning loop, phases 0–4).
- **Zero-config loopback profile (#123, ADR-0017 Phase 0 D6)** — `mnemos serve` / `add` / `search` work from a clean install with no config file: built-in defaults are loopback-only bind (127.0.0.1:8787), storage auto-created under `~/.mnemos/`, FTS5 lexical recall active with no embedding provider (the vector leg degrades non-fatally). New `mnemos.config.find_config_file()` distinguishes the zero-config profile; `serve` prints a one-time notice (effective bind + data paths) when no config exists. The interactive `search` CLI now defaults to `include_raw=True` (`--published-only` restores the strict agent-grade contract) so the first add → search roundtrip completes before the knowledge pipeline publishes — the MCP/HTTP surfaces keep the published-only default. Non-loopback binds still refuse to start without auth + TOTP + TLS (regression-tested). Acceptance: `tests/test_zero_config.py`.
- **Universal integration targets: `zcode` + `agents`** — two new `integrations/targets.yaml` targets: `zcode` (native `~/.zcode/skills/<name>/SKILL.md` nested layout, MCP merged additively into `~/.zcode/cli/config.json` → `mcp.servers`) and `agents` (the AGENTS.md standard `~/.agents/`, read natively by ZCode, Claude Code, Codex, Cursor and others; MCP into `~/.agents/mcp.json` → top-level `mcpServers`). `mnemos integration setup --home <dir>` deploys into another environment's home (cross-container installs) with `~` in targets.yaml resolved against it; `--mnemos-bin` covers wrapper-launched setups. `doctor` now detects MCP registration across all known harnesses (VS Code user/workspace, ZCode, `~/.agents`), and the skill pack grew to cover the full 23-tool MCP surface (9 new skills: agent-recall, cache-align, compress, exchange, filter, housekeeping, ingest, watch, workflow).
- **MCP presets + published adapter template (#124, ADR-0017 Phase 0 D1)** — `integrations/mcp-presets.md`: one-line MCP connection configs for Cursor (`~/.cursor/mcp.json`), Claude Code (`claude mcp add --scope user mnemos -- mnemos mcp-server`), Codex (`~/.codex/config.toml` `[mcp_servers.mnemos]`), and Windsurf (`~/.codeium/windsurf/mcp_config.json`) — all the same stdio wire (`command "mnemos", args ["mcp-server"]`; env vars optional, defaults `~/.mnemos/{data,vault}`). `integrations/adapter-template.md`: ~100-line published template (Connect / Expose / Configure + acceptance checklist the template itself passes) for any MCP-capable harness. README (EN+RU) gains the works-with-everything compatibility table (native `integration setup` targets vs presets vs adapter template); integration guides (EN+RU) gain preset/template sections. Drift guard: `tests/test_mcp_presets.py` parses the published artefacts and validates every config fragment against the canonical wire contract (no `mnemos` import — layout-independent). Note: the table's `zcode`/`agents` native-target rows describe the universal-targets work landing in the same Phase 0 merge train.
- **PyPI packaging Phase 0 (#122)** — local publish pipeline `scripts/pypi-publish.sh` (+ `make pypi-publish`): PyPI name gate G0 (free/taken/already-published), version gates G1–G4 (tag ↔ `pyproject.toml` ↔ artifacts ↔ installed package), wheel/sdist build, `twine check`, offline metadata smoke, optional `--full-smoke`. Upload is hard-gated behind `--publish` (+ `--i-own-name` for updates) — first publish and the final package name remain owner decisions (PyPI names/versions are immutable). Name check (2026-08-21): `mnemos` and `mnemos-memory` are taken on PyPI; recommended fallback `mnemos-memory-server` (also free: `mnemos-server`, `mnemos-mcp`). Runbook: `docs/en/admin/runbooks/pypi-publish.md` (+ RU mirror).

### Fixed

- **ADR-0018 P1-b security-review round — title channel, /context/recall symmetry, drop forensics, bump ordering** — four gate findings on the P1-b issuance layer. **F1 (major)**: `auto_title()` derives from the first line of raw content (or echoes an explicitly-set title) and was returned unscanned next to redacted content — a first-line secret leaked verbatim via the `title` field of every scanned channel AND the never-scanned `mnemos_list_recent`. New composite helper `MemoryManager.scan_issuance_item(text, title=...)` scans BOTH strings one result item echoes (merged `redactions`/`redacted_patterns`; refuse mode refuses the item when either trips) and is now used by `mnemos_search` / `mnemos_agent_recall` / `mnemos_list_recent` (title-only), `/search`, `/recall/agent/{name}` and `/context/recall` (F2a — its MCP twin was scanned while the REST route echoed `effective_content()` verbatim); `mnemos_list_recent` items gain per-item `redactions`. **F3**: refuse-mode drop WARNINGs now carry the memory id in the context label (forensic correlation). **F4**: refused/denied CCR issuances no longer bump `retrieval_count`/`last_retrieved_at` — `ccr_get(bump=False)` + `ccr_touch(hash)` move the counter update AFTER the issuance decision, so denial-hammering cannot LRU-pin a scoped entry (a successful issuance still bumps exactly once and the response reflects it). Acceptance: `TestReviewF1TitleScan` / `TestReviewF2aContextRecall` / `TestReviewF3DropForensics` / `TestReviewF4BumpOrdering` in `tests/test_p1b_issuance.py`. Management-plane exclusion (GET /memories{,/{id}} + /filter + /export) re-ratified out of scope (P2 Layer-3).
- **ADR-0018 P1-b / M1 [major] — secret echo on the search/recall channels closed (CWE-532/200, same class as the P0 `mnemos_retrieve` fix)** — `mnemos_search`, `mnemos_agent_recall` and `mnemos_recall_context` (MCP) plus `/search` (REST, including the `include_raw` drill-down swap to `raw_content`) and `/recall/agent/{name}` returned result content verbatim, so a stored secret re-entered working context through every retrieval surface except the already-fixed CCR channel. One shared helper — `MemoryManager.scan_issuance(text, context=...)` — now scans exactly the string each boundary echoes (per item, once; tags/metadata are not content and are not scanned — titles were added by the review round, see above), reuses the P0 semantics (`<REDACTED:<pattern>>` span replacement in the returned copy, stored models never mutated) and the same `ccr.retrieve_refuse_on_secret` flag (refuse mode DROPS the item from list results — fail-closed; the drop is WARNING-logged with pattern counts, never echoed). Every search/recall result item now carries `redactions` (0 when clean) and, when non-zero, log-safe `redacted_patterns` — per item, matching the P0 response convention. A scanner exception on any item refuses that item (list channels) or the whole response (`retrieve_content`) with `reason="scanner error"` instead of a 500/MCP error — fail-closed, observable (m5). Acceptance: `tests/test_p1b_issuance.py` (`TestMcpSearchScan`, `TestMcpAgentRecallScan`, `TestMcpRecallContextScan`, `TestRestSearchScan`, `TestScannerExceptionRefusedShape`).
- **ADR-0018 P1-b / m2 — FTS5 snippet marker-split evading the issuance scan** — `ccr_search` snippets wrap query-matched tokens in `>>>`/`<<<` highlight markers and join non-contiguous fragments with `' ... '`, which splits multi-token secrets (e.g. a JWT whose payload segment matched the query) so `detect_secrets` on the raw marked snippet misses them; the 32-token window can likewise cut a secret at the snippet edge. Snippets are now scanned on a marker-stripped copy (the markers are the store's own `FTS_SNIPPET_*` constants — single source of truth, bound as SQL parameters), and ANY hit withholds the WHOLE snippet (`<REDACTED:snippet>`) because stripped-copy offsets do not map back to the marked text — precise span redaction is unreliable there by construction. Known residual (flagged for the fix-track): a secret fragment truncated below every pattern threshold can still survive in a snippet window; the full-original path remains precisely redacted, and `retrieve_refuse_on_secret` refuses snippet issuance entirely. Acceptance: `TestSnippetMarkerSplit`.
- **ADR-0018 P1-b / m3 follow-up — chained 3-overlap detector resolution** — the P1-a overlap-tail fix is regression-locked against a THREE-finding chain (aws-key and a high-entropy span sharing start 0, plus a JWT starting inside the span and running past it): both partial overlaps chain into a single accepted span covering the whole construct (`max(end)` twice), first-match precedence keeps the `aws-key` label, and the redacted output leaves no fragment. Acceptance: `TestChainedOverlap`.
- **ADR-0018 P1-a — project scoping of CCR retrieval (cross-session marker leakage, CVE-class)** — `ccr_get` / `ccr_search` looked entries up by hash only, so a `[compressed: <hash> | …]` marker redeemed from a different project/session fetched that project's cached original (the marker is the only capability — it was bearer-grade across project boundaries). All lookup layers now accept an optional `project`: `SQLiteStore.ccr_get` (SQL-level `AND project=?`; mismatch returns `None` fail-closed and does NOT bump the retrieval counter), `SQLiteStore.ccr_search` (entry-ownership pre-check before the FTS query — defence in depth for the snippet channel), `ccr.retrieve`, `MemoryManager.retrieve_content` (a mismatch surfaces as `found=False`), and the `mnemos_retrieve` MCP tool + `POST /retrieve` (optional `project` field/arg). Default semantics: the manager holds no ambient project context, so `None` (absent) keeps the legacy unscoped behavior for callers without project context — the explicit parameter is the override and is what integration code should pass. P1-b adds the MCP/REST-level pass-through proof (`TestScopedRetrievePassThrough`).
- **ADR-0018 P1-a / m3 — secrets detector overlap-tail leak** — partial-overlap resolution dropped the later finding entirely, so a discrete pattern matching the prefix of a longer high-entropy run (e.g. an AWS-key-shaped prefix inside a 38-char base64-like span) left the tail of the run unredacted in issued content. The accepted span is now extended to `max(end)` over overlapping findings: the earlier finding keeps its label (first-match precedence for naming is unchanged) but its span grows to cover the tail; fully-contained findings are still dropped (the JWT-wins regression holds), and `matched_value` always equals `content[start:end]`.
- **ADR-0018 P0 — secret scan on `mnemos_retrieve` issuance + context status gate** — the CCR rehydrate channel (`mnemos_retrieve` MCP tool, `POST /retrieve`, `MemoryManager.retrieve_content`) returned cached originals and FTS5 snippets verbatim, so a secret inside a compressed original re-entered working context unchecked (`ccr_store` has no store-time scan by design until P1). Every issuance is now scanned with `detect_secrets` (scanner patterns evolve and stored records age, so the scan runs on retrieval, not once at store time): matched spans are redacted in the returned payload only — `<REDACTED:<pattern>>`, the existing single-source redaction style — and the response reports the count via `redactions` (`0` when clean) plus log-safe `redacted_patterns`; the stored original is never mutated (zero-loss storage, ArchCom decision). New opt-in `ccr.retrieve_refuse_on_secret` (default off) refuses issuance instead of redacting (`refused=True`, no content in the response). Status-gate groundwork: `CONTEXT_ADMISSIBLE_STATUSES` (`published` + `processed`) is now the single constant `MemoryManager.search` gates on — the documented default every content-surfacing path must consult, so future `on_context_rewrite` LTM originals (which enter the pipeline at `raw`) are publish-gated by construction. Honest coverage: `ccr_cache` is pure cache-by-hash with no pipeline status, so the status gate does not apply to it today; the management plane (`GET /memories/{id}`, `mnemos_filter`) and per-agent recall remain deliberately status-agnostic (documented design, unchanged in P0). Acceptance: `tests/test_retrieve_scan.py` (secret echo, redaction note, snippet masking, refuse mode, raw-issuance gate, clean round-trip; fake EXAMPLE-style secrets only).
- **Short env names `MNEMOS_DATA_DIR` / `MNEMOS_VAULT__VAULT_PATH` honoured again (#139)** — since the consolidated `Settings` layout these documented short forms were silently ignored (canonical names are `MNEMOS_MNEMOS__DATA_DIR` / `MNEMOS_MNEMOS__VAULT_PATH`), so the `mcp.json` entries written by `scripts/mcp-setup.sh` and documented short names fell back to the real `~/.mnemos` defaults. Fixed with a fixed-scope compatibility alias source in `Settings` (`mnemos/config.py`) — deliberately just these two aliases, not a general renaming engine. Precedence per field (high → low): config-file value > canonical env name > short alias > `.env` file > defaults — canonical names remain authoritative (they win over the aliases), and an explicit config-file value still beats both, matching pydantic-settings' init-kwargs-over-env semantics. Also fixed the test-isolation leak this uncovered: the `_make_settings` helpers in `tests/test_critical_fixes.py` / `tests/test_ccr_background_cleanup.py` left the alias vars set in `os.environ`; now live, that leak bled into later modules — both files snapshot/restore the two names around each test. `scripts/mcp-setup.sh` and the legacy docs are correct as-is with the shim in place; `integrations/mcp-presets.md` / `integrations/adapter-template.md` prose updated (short names work again from this release; canonical form stays the documented one). Upgrade note: previously-inert `MNEMOS_DATA_DIR` / `MNEMOS_VAULT__VAULT_PATH` set in shell profiles now take effect — unset them or point them at your current layout (`~/.mnemos/data`, `~/.mnemos/vault`).

## [2.14.1] - 2026-07-31

Docs-only patch — records the Architectural Committee decision on transport-level replay protection (ADR-0016, mnemos #88 point #4). No code, no API, no behavior change.

### Docs

- **ADR-0016 — transport-level replay protection committee decision**. Recorded the Architectural Committee decision (2026-07-31) to reject a separate transport-level replay-protection layer (nonce + timestamp + HMAC) on top of mTLS, retaining mTLS-only replay protection (TLS 1.3 AEAD + anti-replay window + pinned SPKI + per-peer ACL + audit log). Decision `82a3608b` (closes #88 point #4); F10 residual risk confirmed accepted, unchanged.

## [2.14.0] - 2026-07-31

New user-visible feature release — the `mnemos_workflow` MCP tool + `workflow_status` entity + state machine (mnemos #96), the workflow lifecycle layer. Separates mutable **workflow state** from the append-only tag classification. New MCP tool + entity + REST + CLI = MINOR bump per SemVer. Branch chain: `feat/96-workflow-status` (10 commits) → `dev-workflow-status` (squash) → `release/2.14.0` → `main` (merge-commit + tag `v2.14.0`).

### Added

- **`workflow_status` entity + state machine (mnemos #96)** — a mutable lifecycle layer for a memory, distinct from the append-only tag classification. Six states (`open`, `in-progress`, `blocked`, `resolved`, `done`, `withdrawn`) with a server-enforced state machine: `blocked → done` is forbidden (a stuck dependency must go through `resolved` first — blocked → resolved → done); `done` / `withdrawn` are terminal (no outgoing edges); `open` has no edge to `blocked` (a memory must enter `in-progress` before it can be blocked). Backed by a SQLite migration (`memory_workflow_status` projection + `memory_workflow_history` audit table). Implemented in `MemoryManager.workflow_set` / `workflow_get` / `workflow_history` with **five guardrails**: G1 audit log (rejected transitions write **no** audit row — the log records state changes, not attempts), G2 stale-lock auto-release (default `24h`), G3 idempotent transitions (same-status is a no-op, no write, no audit row), G4 force-unlock (requires `reason`), G5 per-memory rate limit (default `30`/min — **per-memory, not per-actor**: churn on one memory is throttled regardless of which actor drives it). Phase 1 ships **weak identity** — `actor` is a free-form string with NO authn/authz; the guardrails are the only protection until a future phase binds `actor` to an authenticated principal.
- **`mnemos_workflow` MCP tool (#96)** — grouped lifecycle tool via an `action: enum [set, get, history]` dispatch, reusing the `action: enum` pattern proven by `mnemos_tags` (#97, ArchCom 2026-07-18 session 2). `action="set"` transitions the status (requires `memory_id`, `to`, `actor`); `action="get"` returns the current status + lock owner; `action="history"` returns the audit trail. Thin wrapper over the manager — the state machine and guardrails CANNOT be bypassed from the MCP layer.
- **Nested REST endpoints (#96)** — `GET` / `POST` / `DELETE /api/v1/memories/{memory_id}/workflow` (nested under the memory, not a top-level `/status` — per ArchCom 2026-07-18 session 2). `POST` returns `409` on a guardrail violation; `GET` returns `404` if the memory is missing. `DELETE` is a **cancel / withdraw** (terminal `withdrawn`, irreversible) — it is **not** a lock-release-to-resumable; the state machine has no edge back to `open`.
- **CLI `workflow` subcommand (#96)** — `mnemos workflow get|set|history` thin wrappers over the manager; `ValueError` (guardrail / state-machine violation) surfaces as a red error line + exit 1, mirroring the MCP / REST surfacing.
- **Docs (EN + RU)** — full `mnemos_workflow` reference in `mcp-tools.md` (states diagram, input/output tables, guardrail table, lock semantics, REST equivalent, Phase 1 weak-identity note).

## [2.13.0] - 2026-07-30

New user-visible feature release — the `mnemos_tags` MCP tool (pilot #97), the proof-of-concept for grouped MCP tool consolidation per ArchCom 2026-07-18 session 2. New MCP tool = MINOR bump per SemVer. Branch chain: `feat/97-mnemos-tags-pilot` → `dev-mcp-tags` (squash) → `release/2.13.0` → `main` (merge-commit + tag `v2.13.0`).

### Added

- **`mnemos_tags` MCP tool (pilot #97)** — grouped bulk tag operations via an `action: enum [rename, remove, add]` dispatch, the proof-of-concept for MCP tool consolidation. `action: enum` + flat properties was chosen over `oneOf`/discriminated unions (which MCP clients render unreliably) and over dot-notation, per ArchCom 2026-07-18 session 2 (verified in VS Code / Claude Desktop / Continue). `action="rename"` is identical to `mnemos_tags_rename`; `action="remove"` drops exact tags (or, with `wildcard=true`, prefix-matched tags); `action="add"` appends tags to memories matching a `project`/`agent` filter. All three go through a shared `_commit_tags` path plus an FTS5-safe `UPDATE`, so the external-content index stays consistent. Contract validation is **strict** for `remove`/`add` (a contract-breaking result — removing the last `project:`/`agent:`/`mnemos:` tag, or adding an invalid `mnemos:` subtype / malformed slug — is rejected per memory with an `errors` entry and the write is skipped) and **lax** for `rename` (a prefix swap preserves required tags). `dry_run=true` by default. `mnemos_tags_rename` is kept as a **non-breaking alias** (routes to `action="rename"`); no deprecation notice yet. The `rename` report also exposes a `changed` key (alias of `renamed`) for a uniform report shape across all three actions. Docs: EN + RU `tag-contract.md` (+ catalogue rows in `mcp-tools.md`).

## [2.12.1] - 2026-07-28

Hotfix + hardening release. Closes the federation audit findings (B1–B4, CR#1–#8, QA#1, QA#9, QA#10) and three infrastructure/test improvements (#9, #11, #12) on the unified `fix/federation-auth-bypass` branch (20 commits ahead of `v2.12.0`). No new user-facing API — patch bump per SemVer.

### Added

- **Federation — `access_log_path` in `FederationConfig`** (#11 infra). Operators of containerised deployments can now point the federation access log at a mounted volume instead of the default `~/.mnemos/logs/federation-access.jsonl`. Configurable via `federation.access_log_path` in `config.yaml` (`feat(config): add access_log_path to FederationConfig`, `28caf05`).
- **Federation — cross-host testing guide** (`docs/en/admin/federation-testing.md`, QA#10). Step-by-step guide for running the federation roundtrip across two real hosts over SSH — covers prerequisites, peer-B config, loopback bind + SSH tunnel, and the smoke-test invocation. Companion to the local `scripts/smoke-federation.sh` (QA#9).
- **Federation — `scripts/smoke-federation.sh`** (QA#9). Cron-ready local smoke test: seeds peer B with a clean decision memory, exports a compact `mnemos.federation.v1` payload, imports into peer A, and verifies the memory is searchable on A. One-command verification of the federation roundtrip outside CI (`chore(scripts): add federation smoke test script`, `c16b2a7`; syntax fix `2f036a9`).
- **Compact — public `embed_for` helper** (#11). `MemoryManager.embed_for()` exposed as a public method so `cli/sync.py` no longer reaches into private internals; keeps the sync code path stable across future manager refactors (`refactor(manager): add public embed_for helper`, `2af3fbe`).

### Fixed

#### Security

- **Bearer token timing oracle (B1, CWE-208)**. Federation bearer-token comparison used `==`, leaking token length / prefix via timing differences. Replaced with a constant-time comparison (`hmac.compare_digest`) in the federation server auth path (`fix(federation): use constant-time bearer token comparison`, `9258de5`).
- **`rsync --delete` injection (B2, CWE-78)**. The SSH `rsync-wrapper.sh` accepted arbitrary rsync options from the remote peer, allowing `--delete` (and similar) to wipe files outside the incoming dir. Whitelisted rsync server options and reject `--delete` explicitly. Covered by new rejection tests (`fix(rsync-wrapper): whitelist rsync server options, reject --delete`, `99f7b5d`; `test(wrappers): add rejection tests for dangerous options`, `2389d90`).
- **Import-wrapper flag whitelist (B3)**. The SSH `mnemos-import-wrapper.sh` forwarded unknown `--*` flags to `mnemos sync import`, allowing option injection from a compromised peer A. Whitelisted import flags; unknown `--*` is rejected. Covered by the same wrapper rejection tests (`fix(import-wrapper): whitelist import flags, reject unknown --*`, `ad99e3d`).
- **mTLS pinning warning (B4)**. A peer configured without `mtls_cert_fingerprint` silently opted out of cert pinning. The server now logs a WARNING at startup naming the peer and the mitigation (set the fingerprint or accept the weakened trust boundary) (`fix(config): warn when federation peer mTLS pinning is off`, `f411748`).
- **`AuthMiddleware` bypass on federation pull**. The federation pull endpoint (`/api/v1/federation/pull`) was reachable without auth because `AuthMiddleware` did not exempt it cleanly — pull requests hit the middleware and either failed or bypassed the federation-specific bearer check. The endpoint is now exempted from `AuthMiddleware` and authenticated by the federation bearer path only (`fix(federation): exempt /api/v1/federation/pull from AuthMiddleware`, `a7bf902`).

#### Federation contract

- **CR#1 — `PARTIAL` → `REFUSED` when all candidate records refused**. When every candidate record was refused by moderation, the server returned `PARTIAL` (implying a partial answer was sent). It now returns `REFUSED` so peer A falls back to local `mnemos_search` per КП-2 instead of retrying (`fix(federation): return REFUSED when all candidate records refused`, `9258de5` — same commit as B1).
- **CR#3 — `local_search` exception logging**. Exceptions from `local_search` during federation pull were silently swallowed, hiding failures behind empty results. Now logged at ERROR with the query context (`fix(federation-a2a): log local_search exceptions instead of silent swallow`, `50e2a57`).
- **#2 — `RateLimiter` decoupled from `handle_pull`**. The rate limiter was constructed inside `handle_pull`, coupling it to the request path and making it untestable in isolation. Lifted to a constructor parameter; `handle_pull` now receives it as a dependency (`fix(federation): decouple RateLimiter from handle_pull now parameter`, `dc9ca38`).
- **#5 — rate-limit refusals audit-logged**. Rate-limited pull requests were rejected without an audit-log entry, leaving the access log blind to DDoS attempts. Rate-limit refusals now append a `REFUSED`-coded entry to `federation_access_log` (`fix(federation): audit-log rate-limit refusals`, `1700e5b`).
- **#6 — mTLS cert mismatch audit-logged**. mTLS client-cert fingerprint mismatches were rejected without an audit-log entry. Now append a `REFUSED`-coded entry so the operator can see the failed pin attempt (`fix(federation): audit-log mTLS cert mismatch refusals`, `631377a`; test fix `32b24ab`).
- **#7 — `pydantic.ValidationError` caught explicitly**. Federation-client pull errors surfaced as a generic `ValueError` (the base class), hiding the validation-failure semantics. Now caught as `pydantic.ValidationError` and surfaced with the field path (`fix(federation-client): catch pydantic.ValidationError explicitly`, `5de9e45`).
- **#8 — A2A parameter forwarding**. `timeout_s` and `include_content` were dropped on the A2A federation path, so remote pulls silently used defaults. Now forwarded end-to-end (`fix(federation-a2a): forward timeout_s and include_content via A2A path`, `5278287`).

#### Infrastructure

- **#9 — `scanner` singleton thread safety**. `get_scanner()` returned a shared singleton without synchronisation, racing on first access from concurrent requests. Now guarded by a lock (`fix(scanner): make get_scanner singleton thread-safe`, `e7f42df`).

#### Testing

- **QA#1 — e2e federation roundtrip test**. New `tests/test_federation_e2e.py` covering the full compact → import → search path on a single process, catching the `AuthMiddleware` bypass (B-series) and the `PARTIAL` vs `REFUSED` regression (CR#1) (`test(federation): add e2e roundtrip test`, `8d31033`; ruff format `bc1723e`).
- **CR#4 — malformed JSONL in access log**. Added test coverage for a malformed line in `federation-access.jsonl` so the access-log reader does not crash on corrupted/truncated audit files (`test(federation): cover malformed JSONL line in access log`, `ac37b26`).

#### Documentation

- **#10 — compact `source_agent` non-injectivity**. Documented that `source_agent` in the compact payload is sanitised and non-injective — two different upstream agents may collapse to the same `source_agent` label after moderation; consumers must not use it as a unique key (`docs(compact): document source_agent sanitisation non-injectivity`, `7ee335e`).
- **#12 — wrapper word-splitting limitation**. Documented that the SSH wrappers use shell word-splitting on the rsync/`mnemos` argv and therefore cannot safely handle arguments containing spaces; the policy-gate closure (whitelist + reject) is the mitigation, not a full argv parser (`docs(wrappers): document word-splitting limitation and policy-gate closure`, `dc995bd`).

## [2.12.0] - 2026-07-22

### Added
- **Federation — auto-cron bridge (#104).** Automates the operator step that Phase 0 batch sync (#85 part 2b) left manual: runs `mnemos sync export` on A, pushes the encrypted payload to B over rsync+ssh, then triggers `mnemos sync import` on B over ssh. **mnemos itself stays offline — there is no inbound endpoint on mnemos; all automation is at the host/SSH layer**, per ArchCom 2026-07-20 decision (mnemos memory `4dc7d96e`). Hardened `scripts/sync-peers.sh` — reads `MNEMOS_SYNC_*` env vars, refuses to run without the required set (exit 2), rsync+ssh push + ssh trigger import, `BatchMode=yes` (no password prompt — fail loudly), `MNEMOS_SYNC_DRY_RUN=1` logs commands only and exits before any network/filesystem side effect. Systemd timer templates: `contrib/systemd/mnemos-sync.service` (`Type=oneshot`, runs as `mnemos-sync` user, `EnvironmentFile=/etc/mnemos/sync.env`) + `contrib/systemd/mnemos-sync.timer` (`OnCalendar=*:0/15`). SSH guards on B: `contrib/systemd/rsync-wrapper.sh` restricts rsync to the incoming dir only (rejects out-of-dir destinations, rejects non-rsync commands, refuses interactive shell) + `contrib/systemd/mnemos-import-wrapper.sh` restricts the import trigger to `mnemos sync import` only (pins `--passphrase-env` to the configured name — even a compromised A cannot redirect the passphrase read, rewrites the source path under the incoming dir). Both wrappers append an audit line to `/var/log/mnemos-sync.log` (timestamp + source IP + event + detail). SSH hardening checklist: `docs/en/admin/ssh-sync-hardening.md` (7 points: dedicated `mnemos-sync` user with `nologin`, `authorized_keys` `command=""` + `from=""` + `no-pty`/no-forwarding, two Ed25519 keys for independent revocation, key storage at `/etc/mnemos/` `chmod 600`, quarterly rotation, audit log, firewall + `sshd_config` `Match User mnemos-sync`). Env template: `contrib/systemd/sync.env.example` (RFC-reserved dummies — replace every value before `systemctl enable`). Passphrase flows via env var NAME (`MNEMOS_SYNC_PASSPHRASE_ENV` holds the NAME, value provisioned in the systemd environment — never inline). Tests: `tests/test_sync_peers_script.py` (4 tests — refuses without required env, refuses with partial env, dry-run logs `mnemos sync export` + `rsync` + `ssh`, systemd units valid). Senior Security Engineer assessment (mnemos memory `ed38f162`) — all 7 hardening points implemented.
- **Federation — Phase 1 per-peer ACL** (#105 federation Phase 1, contract §3.2/§6, ADR-0016). New `PeerConfig(BaseModel)` and `FederationConfig.peers: dict[str, PeerConfig]` in `src/mnemos/config.py`. Each peer is keyed by its A2A id (e.g. `mnemos-A`) and carries: `bearer_token_env` (NAME of the env var holding the per-peer bearer token `mnk_fed_<peer_id>_<random>` — never the value, per `sensitive-data.instructions.md`), `allowed_projects` (subset filter on top of the global `shared_projects` whitelist — empty = none/fail-closed, `["*"]` = explicit wildcard), `allowed_types` (record types the peer may pull — empty = none, `["*"]` = all), `rate_limit_per_minute` (per-peer pull rate limit, contract §8 DDoS mitigation, default 30, clamped 1–600), `mtls_cert_fingerprint` (optional SHA-256 of the peer's mTLS client cert for pinning — `None` means operator opts out of pinning). `FederationConfig.peers` defaults to `{}` — empty dict = no peers configured = the federation server refuses all pull requests (fail-closed). Documented in `config.example.yaml` with RFC-reserved dummy values. These are Phase 1 prerequisites — Phase 2 wires the ACL into the server's request path.
- **Federation — trigger codes enum** (#105 federation Phase 1, contract §9). New `src/mnemos/trigger_codes.py`. `TriggerCode(StrEnum)` with exactly five values per contract §9 — `EXHAUSTIVE` (B gave the full sanitized answer, A should not repeat the request), `ALREADY_EXHAUSTED` (B already answered `EXHAUSTIVE` on this topic — checked via `federation_access_log`, A should reuse the prior answer), `PARTIAL` (partial answer — A may refine the query but not repeat it verbatim), `REFUSED` (B refused — content cannot be shared, A falls back to local `mnemos_search` per КП-2), `OFFLINE_LITE` (B online in reduced mode — A gets a partial result and may supplement with local `mnemos_search`). Two helpers: `is_terminal(code)` (`True` for `EXHAUSTIVE`/`ALREADY_EXHAUSTED`/`REFUSED` — A should not re-query the same topic) and `should_fallback_to_local(code)` (`True` for `REFUSED`/`OFFLINE_LITE`). Phase 1 defines the enum + helpers; Phase 2 wires them into the federation server (returned in the `share-finding` A2A payload) and client (dispatched on receive).
- **Federation — `federation_access_log` module** (#105 federation Phase 1, contract §10, КП-5). New `src/mnemos/federation_access_log.py`. B-side append-only JSONL audit log at `~/.mnemos/logs/federation-access.jsonl` recording who queried what, when, with what trigger code, and which records were returned — used for anti-correlation tracking (B sees A already got `EXHAUSTIVE` on topic X → next request returns `ALREADY_EXHAUSTED`). `AccessLogEntry(BaseModel, frozen)`: `peer_id`, `topic_hash` (SHA-256 hex of the query topic — **never plaintext**, per КП-5 §0.п.8), `timestamp` (UTC ISO-8601), `project_scope`, `trigger_code` (`TriggerCode`), `record_ids_accessed`. `FederationAccessLog` class with `append` (atomic line write + `flush` + `os.fsync` for audit integrity, process-local lock for thread safety), `query(peer_id, topic_hash)` (most recent entry for the pair — used by the server to decide `ALREADY_EXHAUSTED`), `query_recent(peer_id, since=...)` (audit reports), `count_by_trigger_code(peer_id, since=...)` (zero-filled aggregate per code). Module helper `hash_topic(topic) -> str` (SHA-256 hex). The log lives **only on B** — never exported, never synced, never included in `mnemos export` (leak surface, like the moderation mapping table). Documented in the module docstring. Phase 1 ships the log + helpers; Phase 2 wires it into the federation server's request path.- **Federation — Phase 0 batch sync CLI + sync-peers.sh + audit log** (#85, part 2b). New `mnemos sync` CLI subcommand (`src/mnemos/cli/sync.py` + `src/mnemos/cli/sync_cmd.py`) for operator-curated, offline, cron-triggered batch sync between two mnemos instances. `mnemos sync export --output <path> [--encrypt] [--shared-projects <list>] [--dry-run]` builds a `mnemos.federation.v1` compact payload from memories in the configured `shared_projects` (excludes `mnemos:no-federate` and non-shared projects), runs moderation via `build_compact_payload` (#85 Part 2a), and writes the result — optionally AES-256-GCM encrypted with a passphrase from `MNEMOS_EXPORT_PASSPHRASE`. `mnemos sync import --source <path> [--passphrase-env <name>] [--dry-run]` reads a compact payload (decrypting if needed), validates each record (reuses #86 `validate_import_record` adapted for the `CompactRecord` shape — content=summary, title, tags), and merges idempotently by record `id` (`fed:<source_agent>:<uuid>` prefix — existing records skipped, never overwritten). Schema drift / oversized / contract violations reject the whole batch (no partial writes). New `scripts/sync-peers.sh` — cron-ready template (rsync/scp/cp transfer, env-var driven, not executable without configuration). New `src/mnemos/audit.py` — append-only JSONL audit log at `~/.mnemos/logs/sync-audit.jsonl` (counters only — records exported/imported/refused, secrets redacted, PII anonymized, errors, warnings; **no raw content, no secrets, no PII values**). Reuse: compact format + moderation (#85 Part 2a/1) and #86 import validation reused verbatim — no duplication; AES-256-GCM encryption reused from `cli/export.py` (#84) — no new crypto. Tests: `tests/test_sync.py` (23 tests — export roundtrip/exclusions/refuse/dry-run/encrypt/missing-passphrase/no-shared-projects/CLI-override, import merge-idempotent/skip-existing/dry-run/encrypted/default-env/validation-rejects-malicious/prompt-injection-warning/missing-file/invalid-JSON, audit log export/import/no-raw-values, audit module unit). Layer 3 (moderation) is now fully wired into the sync export path, completing the federation defence-in-depth.
- **Federation — compact exchange format** (#85, part 2a). New `src/mnemos/compact.py` module — builds `mnemos.federation.v1` compact payload from memories. Runs moderation pipeline (Part 1) first: refuse → record excluded, redact → sanitized content in summary, allow → content as-is. Compact record: `{id (fed: prefix), type (from mnemos: tag), title, summary (≤500 chars), key_points (bullet/number extraction), tags, source_agent, timestamp}`. `build_compact_payload()` aggregate with stats (total/exported/refused/secrets_redacted/pii_anonymized). Idempotent on import via `fed:<source_agent>:<uuid>` id prefix.
- **Federation — moderation pipeline** (#85, part 1). New `src/mnemos/moderation.py` module — shared component for Phase 0 (batch sync) and Phase 2 (pull). Stages: secrets detector (reuses #86 `secrets_detector`), PII scrubber (regex-based, deterministic), neutral-value replacement (RFC 5737 IPs, RFC 5322 emails, RFC 6761 hostnames), verdict (allow/redact/refuse). `moderate(content, tags) -> ModerationResult` with in-memory mapping table (TTL 24h, never persisted). `FederationConfig` in `config.py` (shared_projects whitelist, mapping TTL, refuse threshold). Layer 3 of defence-in-depth (ArchCom 2026-07-17 §2.2.1).
- **MCP — `mnemos_export` and `mnemos_import` MCP tools** (#84). Federation Part 1 — the MCP surface for export/import that #85 (batch sync Phase 0) builds on (ArchCom 2026-07-17 federation contract §3.1). Two new tools registered in `src/mnemos/mcp_server.py` (17 → 19 tools). `mnemos_export` writes a JSON or SQLite-tar.gz export to an absolute `output_path` and returns metadata only (`{path, memory_count, format, compress, encrypted, bytes, warnings}`) — content is never returned inline (stdio transport cannot carry binary or large JSON). Supports all filters (`project` / `agent` / `status` / `tags` / `since` / `until`) and `compress=gzip`. When `encrypt=true` the passphrase is read from the `MNEMOS_EXPORT_PASSPHRASE` environment variable — never from tool arguments (per `sensitive-data.instructions.md` args appear in MCP logs). `mnemos_import` reads an export file at an absolute `source_path`, supports `merge` / `restore` modes (`restore` requires `confirm=true` as a hard gate), `overwrite`, and `dry_run`. For encrypted inputs the passphrase is read from the environment variable **named** by `passphrase_env` (the name, not the value). Returns `{mode, dry_run, imported, skipped, updated, errors, warnings, format_version, mnemos_version}`. Both tools are thin wrappers over the existing clean `run_export` / `run_import` functions (`cli/export.py`, `cli/import_.py`) — no new export/import logic, no refactoring required (those functions already take plain kwargs, no Typer `ctx`). Inherits #86 federation defence-in-depth: export excludes `mnemos:no-federate` records and redacts detected secrets in passing records; import validates content/tags/title/schema/prompt-injection. Verified by `tests/test_mcp_export.py` and `tests/test_mcp_import.py` (happy path, restore-confirm gate, passphrase-via-env, dry-run, #86 redaction/exclusion, import validation, argument validation).

### Fixed
- **Integration — `update()` now removes orphaned stamped files** (Troubleshooter RCA). `IntegrationManager.update()` previously only iterated pack files (via `deploy()`), so stamped files removed from the pack in a later release (orphans) were never cleaned up. `verify()` flagged them as STALE, but `update` could not clear them — the doctor's remediation hint ("run `mnemos integration update`") was wrong for orphans. `update()` now scans each target's deploy directories after deploying pack files and removes any stamped file not in the current pack (reuses the orphan-detection logic from `verify()` and the safe-removal logic from `uninstall()`). User files (no mnemos stamp) are never touched. This makes `update` symmetric with `verify`: whatever `verify` flags as stale, `update` clears. New tests: `test_update_removes_orphan_stamped_file`, `test_update_keeps_pack_files`, `test_doctor_no_stale_after_update`, `test_update_orphan_dry_run_does_not_delete`, `test_update_preserves_user_files`.
- **CI — `local-ci.sh` doctor step now FAILs on warnings instead of SKIPping** (Troubleshooter RCA). The doctor step ran `mnemos doctor` with `set +e` and, on non-zero exit, recorded `SKIP` with the message "non-fatal consistency check". This hid real warnings (stale integration, missing files, unwired agents) — the same anti-pattern as `# noqa` (silencing the alarm instead of fixing the cause). The step now records `FAIL` on non-zero exit with an actionable message pointing to `mnemos integration update` / `mnemos integration setup --wire-agents --all`. The `command -v mnemos` guard is preserved — a legitimate SKIP when the CLI is not installed in the venv.

_No further released changes yet. See `## [2.10.0]` below for the most recent cut._

## [2.11.0] - 2026-07-20

### Added
- **Federation — Phase 0 batch sync CLI + sync-peers.sh + audit log** (#85, part 2b). New `mnemos sync` CLI subcommand (`src/mnemos/cli/sync.py` + `src/mnemos/cli/sync_cmd.py`) for operator-curated, offline, cron-triggered batch sync between two mnemos instances. `mnemos sync export --output <path> [--encrypt] [--shared-projects <list>] [--dry-run]` builds a `mnemos.federation.v1` compact payload from memories in the configured `shared_projects` (excludes `mnemos:no-federate` and non-shared projects), runs moderation via `build_compact_payload` (#85 Part 2a), and writes the result — optionally AES-256-GCM encrypted with a passphrase from `MNEMOS_EXPORT_PASSPHRASE`. `mnemos sync import --source <path> [--passphrase-env <name>] [--dry-run]` reads a compact payload (decrypting if needed), validates each record (reuses #86 `validate_import_record` adapted for the `CompactRecord` shape — content=summary, title, tags), and merges idempotently by record `id` (`fed:<source_agent>:<uuid>` prefix — existing records skipped, never overwritten). Schema drift / oversized / contract violations reject the whole batch (no partial writes). New `scripts/sync-peers.sh` — cron-ready template (rsync/scp/cp transfer, env-var driven, not executable without configuration). New `src/mnemos/audit.py` — append-only JSONL audit log at `~/.mnemos/logs/sync-audit.jsonl` (counters only — records exported/imported/refused, secrets redacted, PII anonymized, errors, warnings; **no raw content, no secrets, no PII values**). Reuse: compact format + moderation (#85 Part 2a/1) and #86 import validation reused verbatim — no duplication; AES-256-GCM encryption reused from `cli/export.py` (#84) — no new crypto. Tests: `tests/test_sync.py` (23 tests — export roundtrip/exclusions/refuse/dry-run/encrypt/missing-passphrase/no-shared-projects/CLI-override, import merge-idempotent/skip-existing/dry-run/encrypted/default-env/validation-rejects-malicious/prompt-injection-warning/missing-file/invalid-JSON, audit log export/import/no-raw-values, audit module unit). Layer 3 (moderation) is now fully wired into the sync export path, completing the federation defence-in-depth.
- **Federation — compact exchange format** (#85, part 2a). New `src/mnemos/compact.py` module — builds `mnemos.federation.v1` compact payload from memories. Runs moderation pipeline (Part 1) first: refuse → record excluded, redact → sanitized content in summary, allow → content as-is. Compact record: `{id (fed: prefix), type (from mnemos: tag), title, summary (≤500 chars), key_points (bullet/number extraction), tags, source_agent, timestamp}`. `build_compact_payload()` aggregate with stats (total/exported/refused/secrets_redacted/pii_anonymized). Idempotent on import via `fed:<source_agent>:<uuid>` id prefix.
- **Federation — moderation pipeline** (#85, part 1). New `src/mnemos/moderation.py` module — shared component for Phase 0 (batch sync) and Phase 2 (pull). Stages: secrets detector (reuses #86 `secrets_detector`), PII scrubber (regex-based, deterministic), neutral-value replacement (RFC 5737 IPs, RFC 5322 emails, RFC 6761 hostnames), verdict (allow/redact/refuse). `moderate(content, tags) -> ModerationResult` with in-memory mapping table (TTL 24h, never persisted). `FederationConfig` in `config.py` (shared_projects whitelist, mapping TTL, refuse threshold). Layer 3 of defence-in-depth (ArchCom 2026-07-17 §2.2.1).
- **MCP — `mnemos_export` and `mnemos_import` MCP tools** (#84). Federation Part 1 — the MCP surface for export/import that #85 (batch sync Phase 0) builds on (ArchCom 2026-07-17 federation contract §3.1). Two new tools registered in `src/mnemos/mcp_server.py` (17 → 19 tools). `mnemos_export` writes a JSON or SQLite-tar.gz export to an absolute `output_path` and returns metadata only (`{path, memory_count, format, compress, encrypted, bytes, warnings}`) — content is never returned inline (stdio transport cannot carry binary or large JSON). Supports all filters (`project` / `agent` / `status` / `tags` / `since` / `until`) and `compress=gzip`. When `encrypt=true` the passphrase is read from the `MNEMOS_EXPORT_PASSPHRASE` environment variable — never from tool arguments (per `sensitive-data.instructions.md` args appear in MCP logs). `mnemos_import` reads an export file at an absolute `source_path`, supports `merge` / `restore` modes (`restore` requires `confirm=true` as a hard gate), `overwrite`, and `dry_run`. For encrypted inputs the passphrase is read from the environment variable **named** by `passphrase_env` (the name, not the value). Returns `{mode, dry_run, imported, skipped, updated, errors, warnings, format_version, mnemos_version}`. Both tools are thin wrappers over the existing clean `run_export` / `run_import` functions (`cli/export.py`, `cli/import_.py`) — no new export/import logic, no refactoring required (those functions already take plain kwargs, no Typer `ctx`). Inherits #86 federation defence-in-depth: export excludes `mnemos:no-federate` records and redacts detected secrets in passing records; import validates content/tags/title/schema/prompt-injection. Verified by `tests/test_mcp_export.py` and `tests/test_mcp_import.py` (happy path, restore-confirm gate, passphrase-via-env, dry-run, #86 redaction/exclusion, import validation, argument validation).
- **Security — background scanner for `mnemos:no-federate` auto-tagging** (#89). Layer 2 of the federation defence-in-depth (ArchCom 2026-07-17 §2.2.1). New `src/mnemos/scanner.py` module — periodic background scanner that re-scans stored memories for secrets missed by the write-path scanner (#86, Layer 1) and auto-tags them `mnemos:no-federate`. Catches false negatives from evasive phrasing, schema gaps, or pre-#86 content. New `src/mnemos/scanner_runtime.py` — singleton runtime with thread-safe start/stop, configurable interval, graceful shutdown. Wired into the API server lifecycle (`src/mnemos/api/main.py`), exposed via `mnemos scanner` CLI subcommand (`src/mnemos/cli/scanner_cmd.py`) and `mnemos_scanner_status` surface. `FederationConfig` gains scanner settings (interval, enabled, batch size). Audit log extended (`src/mnemos/audit.py`) with scanner counters (scanned, tagged, refused). Tests: `tests/test_scanner.py` (618 lines — scanner logic, runtime singleton, lifecycle, integration with #86 detector, idempotent tagging, graceful shutdown). Completes defence-in-depth: Layer 1 (write-path, #86) + Layer 2 (background, this) + Layer 3 (moderation at export, #85).
- **CI — `scripts/local-ci.sh` for local verification while GitHub Actions is billing-locked** (#92). New `scripts/local-ci.sh` — runs the 8-step local CI pipeline (lint, type-check, format, tests, coverage, security scans, doctor, integration verify) outside GitHub Actions. Operator workaround for the GitHub Actions billing lock: CI runs locally with the same gate semantics. Doctor step FAILs on warnings (not SKIPs) per the RCA fix below. `make verify` delegates to `local-ci.sh` when `GITHUB_ACTIONS` is unset.

### Fixed
- **Integration — `update()` now removes orphaned stamped files** (Troubleshooter RCA). `IntegrationManager.update()` previously only iterated pack files (via `deploy()`), so stamped files removed from the pack in a later release (orphans) were never cleaned up. `verify()` flagged them as STALE, but `update` could not clear them — the doctor's remediation hint ("run `mnemos integration update`") was wrong for orphans. `update()` now scans each target's deploy directories after deploying pack files and removes any stamped file not in the current pack (reuses the orphan-detection logic from `verify()` and the safe-removal logic from `uninstall()`). User files (no mnemos stamp) are never touched. This makes `update` symmetric with `verify`: whatever `verify` flags as stale, `update` clears. New tests: `test_update_removes_orphan_stamped_file`, `test_update_keeps_pack_files`, `test_doctor_no_stale_after_update`, `test_update_orphan_dry_run_does_not_delete`, `test_update_preserves_user_files`.
- **CI — `local-ci.sh` doctor step now FAILs on warnings instead of SKIPping** (Troubleshooter RCA). The doctor step ran `mnemos doctor` with `set +e` and, on non-zero exit, recorded `SKIP` with the message "non-fatal consistency check". This hid real warnings (stale integration, missing files, unwired agents) — the same anti-pattern as `# noqa` (silencing the alarm instead of fixing the cause). The step now records `FAIL` on non-zero exit with an actionable message pointing to `mnemos integration update` / `mnemos integration setup --wire-agents --all`. The `command -v mnemos` guard is preserved — a legitimate SKIP when the CLI is not installed in the venv.
- **Tests — scanner singleton thread leak** (#101). Flaky tests in `tests/test_scanner.py`, `tests/test_a2a_sessions.py`, `tests/test_api.py`, `tests/test_api_export_import.py`, `tests/test_auth.py`, `tests/test_auth_security.py`, `tests/test_cors.py`, `tests/test_dashboard_metrics.py`, `tests/test_hermes_integration.py` were intermittently failing because the `ScannerRuntime` singleton left a background thread alive between test runs. Tests now explicitly stop the runtime in teardown so the singleton does not leak across test modules.

## [2.10.0] - 2026-07-18

### Added
- **Security — `mnemos:no-federate` auto-tagging + export redaction + import validation** (#86). First layer of the federation defence-in-depth (ArchCom 2026-07-17 federation contract §2.2.1). New `src/mnemos/secrets_detector.py` module — reusable secret-pattern scanner (AWS keys, GitHub tokens, Slack tokens, OpenAI/Anthropic keys, JWTs, PEM private keys, connection strings, high-entropy base64 spans). Stable public API (`detect_secrets`, `redact_content`, `findings_by_pattern`) consumed by Layer 1 (this issue), Layer 2 background scanner (#89, future), and Layer 3 moderation pipeline (Phase 0 #85, future). Write-path scanner auto-adds `mnemos:no-federate` tag on `mnemos_add` / `POST /memories` / `ingest_url` / `ingest_path_scoped_rules` when a secret is detected (idempotent, logs pattern counts only — never raw values). `MemoryManager.remove_no_federate()` removes the tag with explicit confirmation and re-detects if the secret is still present. Export (`mnemos export` JSON) excludes `mnemos:no-federate` records entirely and redacts secrets in passing records (`<REDACTED:<pattern_name>>`); payload gains `redaction_summary` with counts. Import validates content (max 1 MiB, no control chars except `\n`/`\t`, UTF-8), tags (reuses `validate_tag_contract`, max 32, max 128 chars), title (max 256 chars), schema drift (rejects unknown `Memory` fields), and logs prompt-injection patterns at WARNING without blocking. `--dry-run` returns a validation report without writing. `no-federate` added to `MNEMOS_TAG_SUBTYPES` whitelist as an exclusion marker (decision: option (a) — add to whitelist with a comment, not a special-case bypass).

## [2.9.0] - 2026-07-17

### Added
- **P1-5 CacheAligner — prefix stabilization for KV cache hits** (`src/mnemos/cache_aligner.py`). Extracts dynamic content (ISO timestamps, UUIDs, session ids, short-lived tokens, calendar dates) from system-prompt-like text and relocates it to a `--- Dynamic context ---` block at the end, so the prefix stays byte-identical across requests and provider KV caches (Anthropic `cache_control`, OpenAI prefix caching) hit. Inspired by headroom's CacheAligner (https://github.com/headroomlabs-ai/headroom, Apache 2.0). Original implementation — no headroom code imported. New `CacheAlignerConfig` in `config.py` (per-kind toggles), `MemoryManager.align_prefix()` method, and `mnemos_align_prefix` MCP tool.
- **P1-7 Output token reduction — verbosity steering + effort routing**. New optional `verbosity` (`default`/`terse`/`minimal`) and `effort` (`low`/`medium`/`high`) parameters on `mnemos_add`, `mnemos_search`, `mnemos_recall_context`. When set to `terse`/`minimal` or `low`/`high`, a short guidance suffix is injected into the tool result framing. Defaults preserve the exact pre-P1-7 behaviour (backward compatible). Inspired by headroom's output token reduction work. Original implementation. New `OutputStyleConfig` in `config.py`.
- **T3 — CCR cleanup wired to background processor**. `ccr_cleanup()` (TTL expiry + LRU eviction) now runs automatically from `_processor_loop` on its own interval (`ccr_cleanup_interval_sec`, default 1200s = 20 min), not every processor cycle. Guarded by `ccr.enabled`; exceptions are caught and logged so the processor loop never crashes. New `ccr_cleanup_interval_sec` field on `CCRConfig` (60–86400s).

## [2.8.0] - 2026-07-16

### Added
- **`mnemos_tags_rename` MCP tool** — bulk rename tags matching `from_prefix:<subtype>` → `to_prefix:<subtype>` across existing memories. Safe: uses `update_fields` (plain UPDATE) so the FTS5 external-content index stays consistent. `dry_run=true` by default, idempotent. Use to migrate `gcw:` → `mnemos:` tags (#79).
- **`POST /tags/rename` HTTP endpoint** — mirrors the MCP tool with Pydantic request model (`TagsRenameRequest`), `dry_run=true` default.
- **`mnemos tags rename` CLI subcommand** — `mnemos tags rename --from gcw: --to mnemos: --no-dry-run`. Parity with `mnemos tags normalize` / `mnemos tags validate`.
- **`mnemos:synthesized` whitelist subtype** — pipeline-synthesised entries now carry a valid `mnemos:` category instead of falling back to `mnemos:legacy`. `gcw:synthesized` auto-migrates to `mnemos:synthesized` via `validate_tag_contract`.

### Fixed
- **CLI/MCP startup crash (typer Option double-name)** — `mnemos tags rename` declared `typer.Option("--from", "from_prefix", ...)` where the second positional string was parsed as an additional option name, clashing with the auto-registered parameter name → `TypeError: Name 'from_prefix' defined twice`. This broke **every** CLI invocation and MCP server startup. Removed the redundant second positional string from `from_prefix` and `to_prefix` options (#80).

### Deprecated
- **`mnemos migrate tags`** — deprecated; emits a warning and delegates to the safe `tags_rename` path. Use `mnemos tags rename --from gcw: --to mnemos: --no-dry-run` instead. The old raw-`sqlite3` implementation is no longer called.

## [2.7.8] - 2026-07-09

### Fixed
- **Background processor not running in HTTP API**: `mgr.start_background_processor()` and `mgr.stop_background_processor()` added to the FastAPI lifespan. Without this, memories added via `POST /memories` stayed in `raw` status forever — the pipeline (cluster → synthesize → quality-gate → publish) never ran. Same bug class as the MCP server fix in [2.3.0].

### Changed
- **Tag contract rename `gcw:` → `mnemos:`**: all tag subtypes renamed from `gcw:<subtype>` to `mnemos:<subtype>` (e.g. `gcw:learning` → `mnemos:learning`). Mnemos is an independent project — the `gcw` prefix was a leftover from the GCW agent family. 76 files, 456 lines updated across src, tests, docs, integrations.
- **Integration target `gcw` → `copilot`**: the harness target detecting `~/.copilot/` is now named `copilot` instead of `gcw`. CLI help updated.

### Backward Compatibility
- **`gcw:` tags accepted as alias for `mnemos:`**: `validate_tag_contract()` auto-migrates `gcw:<subtype>` to `mnemos:<subtype>` for valid subtypes. Old memories with `gcw:` tags continue to work without manual migration. Invalid `gcw:` subtypes (not in whitelist) are preserved as-is for error reporting.

## [2.7.6] - 2026-07-09

### Added
- **CLI `reindex` command** — `mnemos reindex` rebuilds vector index for all published memories
- **API `POST /reindex`** endpoint — trigger vector rebuild via HTTP (with `batch_size` query param)
- **`scripts/release.sh`** — one-command release: bumps version in pyproject.toml, plugin.yaml, README files, commits, tags, pushes. Replaces the broken CI-based README sync approach.
- **`totp_required` per-token flag**: tokens with `totp_required=False` can use bearer directly (no login/verify/session needed). Enables proper M2M authentication without TOTP code reuse issues.
- **`--no-totp` CLI flag** for `mnemos auth token create`: creates API tokens without TOTP requirement.
- **Direct bearer middleware path**: middleware accepts `mnk_`-prefixed bearer tokens with `totp_required=0` directly, skipping session validation.
- **`skip_quality_check` query param** on `POST /publish/{memory_id}`: allows publishing memories from `raw` status without LLM pipeline.
- **Pre-downloaded embedding model**: `all-MiniLM-L6-v2` ONNX model (~90MB) pre-downloaded in Docker image (to `/opt/model-cache`), copied to PVC on first boot via `entrypoint.sh`. Enables vector search out of the box without internet access.
- **`HOME=/data` env in Containerfile**: fixes ChromaDB `/.cache` permission denied error.
- **`include_raw` parameter** in plugin search: defaults to `true` so memories in `raw` status are searchable.
- **Auto-publish on add**: plugin publishes memories after creation so they're immediately searchable.

### Changed

- **Plugin auth refactor**: when `totp_secret` is not configured, plugin uses API token directly instead of TOTP login/verify flow.
- **`/auth/me` response** now includes `totp_required` field for token introspection.
- **Token list** displays `totp_required` column (yes/no).
- **Removed `sync-readme` CI job** from release workflow. README version sync is now done locally via `scripts/release.sh` before tagging. CI only builds artifacts — it does not commit to `main`.

### Fixed

- **Search returns 0 results**: memories stayed in `raw` status without LLM pipeline; `include_raw=true` default in plugin + auto-publish resolves this.
- **Embeddings pre-download for PVC mounts**: model pre-downloaded to `/opt/model-cache` (inside image layer) and copied to `/data/.cache` on first boot via `entrypoint.sh` (fixes: `/data` volume mount hid pre-downloaded model).
- **External `entrypoint.sh`** instead of heredoc in Containerfile (GitHub Actions imagebuilder compatibility).
- **TOTP code reuse for M2M**: API tokens with `totp_required=False` bypass TOTP entirely.
- **`/publish/{id}` only accepted `processed` memories**: now accepts `raw` with `skip_quality_check=true`.
- **Embeddings `/.cache` permission denied**: `HOME=/data` env var + pre-download in Containerfile.
- **All 36 failing tests**: pytest-asyncio missing from dev deps + `load_config()` mock needed for `test_default_base_url`. 971/971 green.

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
- **M2**: Mnemos Tag Contract enforcement at MCP layer (`project:*`, `agent:*`, `mnemos:*` required in strict mode).
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
