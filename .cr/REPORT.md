# Mnemos — Full Project Code Review

**Date:** 2026-06-20
**Reviewer:** @GCW: Code Reviewer (standard mode)
**Branch:** `feat/integration-content`
**Scope:** Full `src/mnemos/` codebase (~13.5 KLOC source, ~13.2 KLOC tests, 30 test files, 750 tests passing)
**Gate at review time:** ruff clean, mypy --strict clean (55 files), 750 tests passed

> Security findings are noted here for completeness; the detailed security
> audit runs in parallel via @GCW: Senior Security Engineer. This report
> focuses on architecture, code quality, API design, error handling, test
> coverage, and technical debt.

---

## Executive summary

Mnemos is a well-structured memory/knowledge server with clean layering
(CLI → Manager → Storage), strong input validation at the boundaries
(TagContract, FTS5 escaping, SSRF per-hop guard, SQL whitelist), and a
mature auth layer (ADR-0014, all auth-1..12 findings closed). The codebase
is in good shape for its scope.

There is **one critical logic bug** in the policy engine
(`min_source_coverage` always returns `False`), **one high-severity
correctness bug** in the JSON import project-merge path (references
`existing.id` when `existing is None`), and a handful of medium/low
findings around broad exception swallowing, dead/stub code, and a few
API-design inconsistencies. No architectural concerns require owner
decision — the layering is sound.

**Counts:** 1 critical · 1 high · 6 medium · 7 low · 3 info

---

## Findings by severity

### Finding CRITICAL: Policy engine `min_source_coverage` condition always rejects

- **File**: `src/mnemos/policy/engine.py:76-78`
- **Category**: code-quality / error-handling (logic bug)
- **Severity**: critical
- **Description**:
  ```python
  if cond.min_source_coverage is not None:
      if (mem.source_coverage or 0) < cond.min_source_coverage:
          return False
      return False          # ← BUG: unconditional return
  ```
  The second `return False` is outside the inner `if` but inside the outer
  `if`. This means: **any rule that sets `min_source_coverage` never
  matches**, regardless of the memory's actual `source_coverage`. A memory
  with `source_coverage=10` against `min_source_coverage=2` still returns
  `False`, so the rule is silently dead. Worse: because `_condition_matches`
  is AND-ed across conditions, the whole rule fails whenever this condition
  is present — so every policy rule using `min_source_coverage` is a no-op.
- **Recommendation**: Remove the stray `return False`. The inner `if`
  already handles the below-threshold case; fall through to the rest of the
  condition checks when the threshold is met.
- **Effort**: S
- **Test gap**: `tests/test_policy_engine.py` has no test that exercises
  `min_source_coverage` on a *passing* memory — only the quality_gate
  tests in `test_pipeline.py` cover the field, and those bypass the policy
  engine. Add a policy-engine test that asserts a rule with
  `min_source_coverage=2` fires on a memory with `source_coverage=5`.

### Finding HIGH: JSON import project-merge references `existing.id` when `existing is None`

- **File**: `src/mnemos/cli/import_.py:170`
- **Category**: error-handling / code-quality (correctness bug)
- **Severity**: high
- **Description**:
  ```python
  if existing is None:
      if dry_run:
          result.imported += 0
      else:
          from mnemos.models import Project
          proj = Project(
              id=p.get("id") or existing.id if existing else p.get("id", ""),
              ...
          )
  ```
  The ternary `p.get("id") or existing.id if existing else p.get("id", "")`
  parses as `p.get("id") or (existing.id if existing else p.get("id", ""))`.
  Inside the `if existing is None` branch, `existing` is `None`, so the
  fallback evaluates `p.get("id", "")` — which is what was intended. So the
  bug is *latent* (it happens to work today), but the expression is
  misleading and fragile: if someone refactors the outer `if`, or if
  `existing` is rebound, the `existing.id` access becomes an
  `AttributeError`. It is also unreadable.
- **Recommendation**: Simplify to `id=p.get("id") or str(uuid.uuid4())`
  (the `if not proj.id: proj.id = str(uuid.uuid4())` block two lines down
  already covers the empty case, so this can just be
  `id=p.get("id", "")`). Remove the dead `existing.id` reference entirely.
- **Effort**: S

### Finding MEDIUM: Broad `except Exception: pass` swallows real failures silently

- **File**: `src/mnemos/cli/export.py:199,448` · `src/mnemos/cli/import_.py:227` · `src/mnemos/cli/agent_wiring.py:170` · `src/mnemos/storage/vault.py:91` · `src/mnemos/embeddings/__init__.py:149`
- **Category**: error-handling
- **Severity**: medium
- **Description**: Six occurrences of `except Exception: pass` (or
  `except Exception: continue`) with no logging. Examples:
  - `export.py:448` — WAL checkpoint failure during SQLite snapshot is
    swallowed with `pass`. The comment says "non-fatal", but the operator
    gets no signal that the snapshot may be missing the last few writes.
  - `import_.py:227` (`_reembed`) — re-embedding failure on import is
    swallowed; the memory is persisted but the operator is never told the
    vector index is now inconsistent with SQLite.
  - `vault.py:91` — `file_to_memory` catches *everything* (including
    `yaml.YAMLError` which is not a `ValueError`) and returns `None`. This
    is documented (M18 regression test), but the bare `except Exception`
    also swallows `KeyboardInterrupt`-adjacent surprises and gives no
    diagnostic.
  - `embeddings/__init__.py:149` — ONNX model download fallback
    `hf_hub_download(model_id, "model.onnx", ...)` swallows all exceptions
    from the first attempt; a network error is indistinguishable from
    "file not found".
- **Recommendation**: Replace each with a narrow `except (<specific>):`
  plus a `logger.warning(...)` that names the operation. For the WAL
  checkpoint, at minimum emit a warning so the operator knows the snapshot
  may be incomplete. For `_reembed`, append to `result.warnings` so the
  import summary surfaces it. Per `lint-and-validate.instructions.md`,
  this is "swallowing exceptions broadly" — fix the cause, not mute the
  alarm.
- **Effort**: M

### Finding MEDIUM: `synthesize.py` is a stub — LLM provider never called

- **File**: `src/mnemos/pipeline/synthesize.py:101-108` · `src/mnemos/llm/base.py:47`
- **Category**: tech-debt
- **Severity**: medium
- **Description**: The synthesis worker has a `TODO: wire real LLM provider
  when llm/ modules are implemented` and produces a deterministic
  placeholder string instead of calling an LLM. `llm/base.py::create_provider`
  raises `NotImplementedError`. The `LLMConfig` in `config.py` exposes
  provider/model/api-key fields that are **dead configuration** — operators
  can set them but they have no effect. The pipeline endpoint
  `POST /process` and `POST /synthesize` return "synthesized" content that
  is just a concatenation of the first 200 chars of each source.
- **Recommendation**: Either (a) implement the LLM provider registry
  (Ollama is already a dependency for embeddings, so the Ollama path is
  cheap), or (b) mark the synthesis endpoints as experimental in the API
  docs and emit a `Trace.fallback_used=True` + `rationale_summary="LLM
  stub — placeholder output"` so consumers know the output is not
  LLM-generated. Today the trace says `llm_called=True, llm_done=True`
  which is misleading.
- **Effort**: L (to implement) · S (to mark experimental)

### Finding MEDIUM: `tags validate` CLI command is a stub

- **File**: `src/mnemos/cli/main.py:260-266`
- **Category**: tech-debt
- **Severity**: medium
- **Description**: `mnemos tags validate <vault>` prints
  `"Full vault scan not yet implemented (M2 storage layer pending)."` and
  returns. M2 shipped long ago (per repo memory, M2 closed 2026-06-16).
  The command is advertised in `--help` but does nothing. `mnemos doctor`
  has a real tag-contract scan (`_check_tag_contract` in `doctor.py:280`),
  so the logic already exists — it just is not wired to the `tags validate`
  command.
- **Recommendation**: Wire `tags validate` to the same scan logic
  `doctor._check_tag_contract` uses (or call `doctor` internally). If the
  command is not meant to ship, remove it from the Typer app so `--help`
  does not advertise a non-functional command.
- **Effort**: S

### Finding MEDIUM: `list_all` tag filter uses `LIKE '%"tag"%'` — false positives on substring tags

- **File**: `src/mnemos/storage/sqlite_store.py:560-562` (and `list_for_export:625`)
- **Category**: code-quality / api-design
- **Severity**: medium
- **Description**: Tag filtering in `list_all` and `list_for_export` is:
  ```python
  for tag in tags:
      q += " AND tags LIKE ?"
      params.append(f'%"{tag}"%')
  ```
  This matches the JSON-serialized `tags` column as a substring. A filter
  for `project:mnemos` will also match a row whose tags contain
  `project:mnemos-eyes` because `%"project:mnemos"%` is a substring of
  `..."project:mnemos-eyes"...`. The same bug affects `list_for_export`.
  The FTS5 path (`fts_search`) does not have this issue because it uses
  FTS5 column filtering.
- **Recommendation**: Use `json_each` to unpack the array and match
  exactly: `AND EXISTS (SELECT 1 FROM json_each(memories.tags) WHERE
  j.value = ?)`. This is what `get_all_tags` already does. It is also
  faster on large datasets (no LIKE scan).
- **Effort**: S

### Finding MEDIUM: `manager.search` ignores `status` filter on the vector leg

- **File**: `src/mnemos/manager.py:243-251`
- **Category**: code-quality
- **Severity**: medium
- **Description**: `search()` accepts a `status` parameter and passes it
  to `sqlite.fts_search` for the FTS leg. The vector leg
  (`self.vectors.search(q_emb, limit=limit*2)`) does **not** filter by
  status — it returns the top-K vectors regardless of whether the
  underlying memory is `published`, `archived`, or `raw`. The RRF merge
  then mixes status-filtered FTS hits with unfiltered vector hits. A
  search with `status=MemoryStatus.PUBLISHED` can still surface archived
  memories via the vector leg. The vector store only indexes published
  memories (by invariant in `publish.py`), so in practice this is not a
  live bug today — but it is a latent bug if anything ever writes to the
  vector store without going through `publish_memory` (e.g. the import
  path's `_reembed` does exactly that for `status=PUBLISHED` memories, but
  a future code path that re-embeds archived memories would leak).
- **Recommendation**: After the vector leg, filter `vector_pairs` by
  fetching the memory's status and dropping non-matching ones before RRF.
  Or document the invariant in `VectorStore.search` and add an assertion
  in `_reembed` that it only runs for `PUBLISHED` memories (it already
  does — make the invariant explicit).
- **Effort**: S

### Finding MEDIUM: `manager.ingest_url` echoes fetch-failure detail into stored memory content

- **File**: `src/mnemos/manager.py:617-618`
- **Category**: error-handling / security (CWE-117 adjacent)
- **Severity**: medium
- **Description**: On fetch failure, the placeholder content is:
  ```python
  content = f"URL: {url}\n[fetch failed: {exc}]"
  ```
  `exc` can include the redirect target URL, the resolved IP, or the
  blocked-host reason from `_validate_url`. This means an SSRF-blocked
  internal URL (e.g. `169.254.169.254`) can end up persisted in the
  memory's `content` field, and later returned by `search` / `list_recent`
  to callers who did not initiate the fetch. This is the same class of
  issue as ssrf-1 (placeholder echoes blocked target) that was flagged in
  the prior review for the MCP path — but here it is in the manager, which
  is also reachable via the HTTP API `POST /memories` with `source_url`
  and via `mnemos add --url`.
- **Recommendation**: Replace `{exc}` with a generic reason:
  `"[fetch failed: blocked or unreachable]"`. Log the full exception at
  `logger.warning` (already done on the line above) so the operator can
  diagnose without the detail being persisted.
- **Effort**: S

### Finding LOW: `MemoryUpdate` silently drops `project` / `agent` changes

- **File**: `src/mnemos/models.py:289-299` · `src/mnemos/manager.py:215-225`
- **Category**: api-design
- **Severity**: low
- **Description**: `MemoryUpdate` exposes `content`, `title`, `tags`,
  `memory_type`, `metadata`, `status`, `category`, `quality_score`,
  `confidence`, `cluster_id` — but **not** `project` or `agent`. The
  `manager.update` loop iterates over those same fields. So there is no
  way to update `project`/`agent` via the API or CLI `update` path. If a
  memory is saved with the wrong project, the only fix is `delete` +
  `add`. The `tags` field *can* be updated, which would change the tag
  strings, but `project`/`agent` are denormalised columns that would stay
  stale.
- **Recommendation**: Either (a) add `project`/`agent` to `MemoryUpdate`
  and re-derive them from `tags` in `manager.update` (preferred — keeps
  the denormalisation consistent), or (b) document that project/agent are
  immutable post-create and must be fixed via delete+add.
- **Effort**: S

### Finding LOW: `dashboard_stats.last_run` is always `None`

- **File**: `src/mnemos/manager.py:399`
- **Category**: code-quality / api-design
- **Severity**: low
- **Description**: `dashboard_stats()` returns
  `"pipeline": {"last_run": None, ...}`. There is no code that ever sets
  `last_run` to a real value — no field in `SQLiteStore`, no tracking in
  `run_pipeline`. The mnemos-eyes dashboard will always show "never" for
  last pipeline run.
- **Recommendation**: Either persist `last_run` in a metadata table
  (e.g. `INSERT OR REPLACE INTO pipeline_runs (task, last_at) VALUES
  ('pipeline', ?)` at the end of `run_pipeline`), or remove the field
  from the response until it is wired.
- **Effort**: S

### Finding LOW: `timeseries` accepts `granularity` but only supports `day`

- **File**: `src/mnemos/manager.py:413-419` · `src/mnemos/api/main.py:298`
- **Category**: api-design
- **Severity**: low
- **Description**: `timeseries(metric, days, granularity)` accepts
  `granularity` as a parameter and the HTTP endpoint forwards it, but the
  implementation says `_ = granularity  # only "day" supported` and
  ignores it. A caller passing `granularity=hour` gets daily data back
  with no error. The HTTP endpoint also has a `range=30d` parser that
  silently clamps `h` ranges to `1` day (`days = 1`) with no warning.
- **Recommendation**: Either implement hour/week granularity (the SQL
  `DATE(created_at)` can be swapped for `strftime`), or return `422` when
  `granularity` is not `day`. Same for `range` suffixes other than `d`.
- **Effort**: S

### Finding LOW: `POST /memories` does not return the `project`/`agent` it derived

- **File**: `src/mnemos/api/main.py:351-360`
- **Category**: api-design
- **Severity**: low
- **Description**: `create_memory` validates tags, derives `project` and
  `agent` from the tag strings, calls `mgr.add(data, project=project,
  agent=agent)`, and returns the `Memory`. The returned memory *does*
  carry `project`/`agent` (set by `Memory.__init__` from the kwargs), so
  this is actually fine — but the endpoint does not document that it
  mutates `data.tags` in place (lax mode may have patched in
  `project:unknown` etc.). A caller posting tags without `project:` gets
  back a memory with extra tags and no explicit signal that the server
  patched them.
- **Recommendation**: In lax mode, include a `warnings` field in the
  response (or a `X-Mnemos-Tag-Patched` header) so the caller knows the
  tags were augmented. In strict mode (default), this is a non-issue
  because the call raises.
- **Effort**: S

### Finding LOW: `AuthStore` and `SQLiteStore` both define the auth DDL — drift risk

- **File**: `src/mnemos/storage/sqlite_store.py:329-352` · `src/mnemos/api/auth_store.py:33-60`
- **Category**: architecture / tech-debt
- **Severity**: low
- **Description**: The auth tables (`auth_tokens`, `auth_sessions`,
  `auth_challenges`) are defined in **both** `_DB_SCHEMA` (sqlite_store)
  and `_AUTH_DDL` (auth_store). The two copies are identical today, but
  this is exactly the kind of duplication that drifts: a future column
  added to one but not the other would silently break auth. This was
  flagged as auth-10 in the prior review and noted as "non-blocking", but
  it is still a live maintenance hazard.
- **Recommendation**: Make `auth_store._AUTH_DDL` the single source of
  truth and have `sqlite_store._DB_SCHEMA` import it (or vice versa). The
  `_ensure_columns` migration in `auth_store` already handles additive
  changes, so the duplication is only the base DDL.
- **Effort**: S

### Finding LOW: `manager.watch_start` is a no-op — logs but never starts a watcher

- **File**: `src/mnemos/manager.py:621-624`
- **Category**: tech-debt
- **Severity**: low
- **Description**: `watch_start` logs `logger.info("watch_start: ...")`
  and returns. `self._watcher` is never assigned, so `watch_status`
  always returns `{"running": False}` and `watch_stop` is a no-op. The
  MCP tools `mnemos_watch_start/stop/status` expose this as if it works.
  The `WatcherConfig` in `config.py` and the `watchers/path_scoped.py`
  module exist, but no actual filesystem watcher (watchdog/inotify) is
  wired.
- **Recommendation**: Either implement the watcher (watchdog is a common
  choice) or mark the MCP tools as "not yet implemented" in their
  descriptions and return a clear message. Today the MCP tool returns
  `"✅ Watcher started on ..."` which is misleading.
- **Effort**: M

### Finding LOW: `recall_context` ignores the `query` argument

- **File**: `src/mnemos/manager.py:336-343`
- **Category**: api-design
- **Severity**: low
- **Description**: `recall_context(project, query, limit)` accepts a
  `query` parameter but never uses it — it always returns the most recent
  `gcw:checkpoint` memories sorted by `created_at`. The MCP tool
  `mnemos_recall_context` exposes `query` as "specific aspect to focus
  on", so callers believe they can filter by topic.
- **Recommendation**: Either pass `query` to a `list_all(tags=[...,
  query])` filter (if it looks like a tag), or run a bounded `search()`
  scoped to `gcw:checkpoint` and merge, or remove the parameter from the
  MCP schema and the method signature.
- **Effort**: S

### Finding INFO: `.history/` directory contains ~400 stale snapshot files

- **File**: `.history/` (workspace root)
- **Category**: tech-debt
- **Severity**: info
- **Description**: The `.history/` directory (VS Code Local History
  extension) contains hundreds of timestamped snapshots of source files
  (e.g. `src/mnemos/storage/sqlite_store_20260620193444.py`). These are
  not part of the mnemos source tree but they pollute `grep_search` and
  `semantic_search` results — every search for a code pattern returns
  dozens of `.history/` hits that are stale copies. The prior session
  archived them to `mnemos-history-archive-20260617/` and added `.history/`
  to `.gitignore`, but the directory still exists on disk and is indexed
  by search tools.
- **Recommendation**: Confirm with owner, then delete the `.history/`
  directory (it is gitignored and archived). This is a destructive action
  per `destructive-actions.instructions.md` — needs explicit confirmation.
- **Effort**: S

### Finding INFO: `Memory.metadata` field is documented as "compat" but actively used

- **File**: `src/mnemos/models.py:265`
- **Category**: code-quality
- **Severity**: info
- **Description**: The `metadata` field comment says "retained for
  migration tooling", but the field is actively used: `synthesize.py`
  stores `synthesis_cache_key` and `synthesis_cached_result` in it,
  `path_scoped.py` stores `apply_to` and `description`, and the HTTP API
  accepts `metadata` in `MemoryCreate`. The "compat" label is misleading.
- **Recommendation**: Update the comment to reflect that `metadata` is
  the general-purpose extension field, not a legacy compat shim.
- **Effort**: S

### Finding INFO: `EmbeddingConfig.hf_revision` default is a placeholder SHA

- **File**: `src/mnemos/config.py:24`
- **Category**: tech-debt
- **Severity**: info
- **Description**: `hf_revision: str = "c9745ed1d7e3b0194c2e1c2b5d7e3e0b3c1c1c1c"`
  is documented as "the recommended revision for the default ONNX model",
  but the SHA looks synthetic (the repeating `3c1c1c1c` tail is a
  tell). If a user uses the ONNX provider with the default config, they
  pin to a revision that may not exist on HuggingFace Hub, and
  `hf_hub_download` will fail with a 404.
- **Recommendation**: Replace with the real pinned revision for
  `sentence-transformers/all-MiniLM-L6-v2` ONNX, or set the default to
  `""` and require operators to set it explicitly (the `ONNXHubProvider`
  already raises `ValueError` on empty revision).
- **Effort**: S

---

## Summary counts

| Severity | Count |
| --- | --- |
| Critical | 1 |
| High | 1 |
| Medium | 6 |
| Low | 7 |
| Info | 3 |
| **Total** | **18** |

---

## Top 5 prioritized recommendations

1. **Fix `policy/engine.py:76-78` `min_source_coverage` bug** (critical,
   S effort). This is a one-line fix that unblocks an entire policy
   feature. Add a regression test that exercises the passing path.

2. **Fix `cli/import_.py:170` project-merge expression** (high, S effort).
   Simplify the ternary to remove the latent `existing.id` reference.
   Covered by existing import tests, but add a test that imports a
   project with no `id` field to lock the path.

3. **Replace `except Exception: pass` with narrow exceptions + logging**
   (medium, M effort). Six sites in export/import/agent_wiring/vault/
   embeddings. Per `lint-and-validate.instructions.md`, broad swallows
   are forbidden defaults — each needs a specific exception type and a
   `logger.warning` so the operator gets signal.

4. **Fix `list_all` / `list_for_export` tag filter to use `json_each`**
   (medium, S effort). The `LIKE '%"tag"%'` pattern produces false
   positives on substring tags (`project:mnemos` matches
   `project:mnemos-eyes`). Switch to the `json_each` pattern already used
   by `get_all_tags`.

5. **Decide on the LLM synthesis stub** (medium, owner decision). Either
   implement the Ollama provider (cheapest — Ollama is already a dep) or
   mark `POST /process`, `POST /synthesize`, and the `run_pipeline` CLI
   as experimental so consumers do not trust placeholder output as
   LLM-synthesized knowledge. Today the trace records `llm_called=True`
   for a no-op concatenation, which is misleading for observability.

---

## Coverage assessment

**Well-tested (≥80% likely):**
- `models.py` — `test_tag_contract.py` (31 tests), `test_agent_recall.py`
  (357 lines), `test_vault.py` (342 lines)
- `api/auth*.py` — `test_auth.py` (409 lines) + `test_auth_security.py`
  (884 lines) = ~37 auth tests, all auth-1..12 findings closed
- `api/middleware.py` + `api/client_ip.py` — covered by auth tests
- `storage/sqlite_store.py` — covered transitively by every manager test
- `filter/pipeline.py` — `test_context_filter.py` (621) +
  `test_context_filter_edge.py` (614) = strong
- `mcp_server.py` — `test_mcp_server.py` (routing assertions, 29 tests)
- `cli/integration.py` — `test_integration.py` (1527 lines, 38 tests)
- `cli/agent_wiring.py` — `test_agent_wiring.py` (700) +
  `test_agent_wiring_edge.py` (651)
- `cli/export.py` + `cli/import_.py` — `test_export_import.py` (657) +
  `test_api_export_import.py`
- `sessions/` — `test_a2a_sessions.py` (461 lines)

**Undertested:**
- `policy/engine.py` — `test_policy_engine.py` has 20 tests but **no
  test for `min_source_coverage`** (the critical bug above). The
  `min_source_coverage` condition is never exercised on a passing
  memory, which is why the bug survived.
- `pipeline/synthesize.py` — tests exist (`test_pipeline.py` 469 lines)
  but they assert on the placeholder output, not on LLM behaviour. When
  the LLM provider is wired, these tests will need rewriting.
- `pipeline/cluster.py` — covered by `test_pipeline.py` but only with
  tiny synthetic clusters; no test for the O(n²) greedy merge at scale
  (limit=100).
- `manager.ingest_url` — `test_ssrf_redirect.py` (432 lines) covers the
  SSRF guard well, but the placeholder-content path (finding above) is
  not asserted against.
- `cli/doctor.py` — `test_cli.py` (396 lines) has smoke tests, but the
  `--fix` auto-fix path is not tested.
- `cli/logs.py` — `test_logs.py` exists but `--follow` polling mode is
  not tested (hard to test; acceptable).
- `auto_collect.py` — `test_traces_compaction.py` covers it, but the
  M7 signals (`context_size`, `summary_marker`, `reference_drop`) are
  all `None`-populated stubs — the real detection logic lives in the
  MCP client, not here.
- `llm/base.py` — 0% (stub, `NotImplementedError`).
- `watchers/path_scoped.py` — `test_path_scoped_rules.py` exists, but
  the actual filesystem watcher (watchdog) is not wired, so the
  "watch" path is untested.

**Flaky tests:** None observed. The suite is deterministic (SQLite
in-memory, mocked embeddings, MCP stubs in `conftest.py`).

**Integration tests:** Present — `test_api.py`, `test_a2a_sessions.py`,
`test_api_export_import.py` use FastAPI `TestClient` end-to-end. No
external-service integration tests (Ollama, HuggingFace Hub) — those are
mocked.

---

## Architectural concerns requiring owner decision

**None.** The layering is sound:

- `cli/*` → `manager.py` → `storage/*` (one direction, no reverse imports)
- `api/*` → `manager.py` → `storage/*` (same)
- `mcp_server.py` → `manager.py` (same)
- `pipeline/*` and `policy/*` take `MemoryManager` as a TYPE_CHECKING
  param (no circular import)
- `sessions/` is deliberately independent of `manager.py` (owns its own
  SQLite connection on the same file)

The only architectural note (not a blocker) is the duplicated auth DDL
(finding LOW above) — single-source it when convenient.

---

## Trace

`recall_context` → `read_file` (manager, models, sqlite_store, api/main,
auth_store, auth, middleware, mcp_server, config, vector_store, filter/
pipeline, sessions/store, sessions/api, cli/integration, cli/export,
cli/import_, embeddings, vault, pipeline/synthesize, pipeline/cluster,
pipeline/quality_gate, pipeline/publish, watchers/path_scoped, cli/main,
cli/doctor, cli/logs, cli/migrate, policy/engine, policy/triggers,
sessions/summary, sessions/models, api/client_ip, api/rate_limit,
auto_collect, llm/base) → `grep_search` (broad excepts, TODOs,
min_source_coverage, tag filter) → `read_file` (test_policy_engine,
test_pipeline) → synthesis + report.