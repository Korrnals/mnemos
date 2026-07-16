# Tag Contract

**🌐 Language / Язык:** English · [Русский](../../ru/user/tag-contract.md)

Mnemos enforces a structured tag schema on every memory entry. This document
describes the contract, the set of valid values, and migration guidance.

---

## Why a tag contract?

Memory entries without consistent structure become unsearchable noise.
The tag contract:

- Pins every entry to exactly **one project** and **one agent**
- Classifies the entry with at least **one Mnemos subtype** (cognitive category)
- Enables per-agent recall (M3) and project-scoped cleanup
- Prevents ambiguous dual-project entries (a common source of context pollution)

---

## Required tags (must be present on all new entries)

| Tag | Format | Cardinality | Purpose |
|-----|--------|-------------|---------|
| `project:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **exactly 1** | Binds entry to a codebase / initiative |
| `agent:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **exactly 1** | Agent that authored the memory |
| `mnemos:<subtype>` | see table below | **at least 1** | Cognitive category |

### Mnemos subtypes

| Subtype | When to use |
|---------|-------------|
| `session` | Session continuity checkpoints |
| `bug-pattern` | Recurring failure modes, root-cause patterns |
| `learning` | New facts acquired during a task |
| `decision` | Explicit architectural / product decisions |
| `rule` | Hard constraints and invariants (from instructions files, etc.) |
| `open-question` | Unresolved questions requiring future investigation |
| `checkpoint` | Mid-session snapshots for compaction survival |
| `legacy` | Migrated entries from ai-brain or pre-contract stores |

---

## Optional tags

These are accepted but not required. Unknown prefixes not listed here are
rejected in strict mode.

| Tag | Format | Purpose |
|-----|--------|---------|
| `source:<slug>` | any string | Origin of the entry (chat, file, url, …) |
| `applyTo:<glob>` | file glob | Scope a `rule` to specific file paths |
| `milestone:<id>` | any string | Links entry to a project milestone |
| `domain:<slug>` | any string | Domain sub-classifier within a project |

---

## Validation modes

### Strict mode (`strict_tag_contract=True`, default for new installs)

- All three required tag families must be present.
- `TagContractError` is raised if any required tag is missing, malformed,
  or duplicated.
- Used by `mnemos_add` (MCP tool) and `Memory(strict_tags=True)`.

### Lax mode (`strict_tag_contract=False`, for migrations)

- Missing required tags emit a warning but do **not** raise.
- Multiple `project:` / `agent:` tags still raise (always ambiguous).
- Used by `mnemos migrate-from-ai-brain` CLI command.

---

## Python API

```python
from mnemos.models import validate_tag_contract, TagContract, TagContractError

# Validate a list of tags (strict, raises on violations)
clean_tags = validate_tag_contract(
    ["project:myproject", "agent:copilot", "mnemos:learning"],
    strict=True,
)

# Use TagContract model directly
tc = TagContract(tags=["project:myproject", "agent:copilot", "mnemos:decision"])
print(tc.project)       # "myproject"
print(tc.agent)         # "copilot"
print(tc.mnemos_subtypes)  # {"decision"}

# Pass tags when creating a Memory
from mnemos.models import Memory
m = Memory(
    content="Decided to use FTS5 over a dedicated search service.",
    tags=["project:mnemos", "agent:tech-lead", "mnemos:decision"],
    project="mnemos",
    agent="tech-lead",
)
```

---

## MCP usage

```
mnemos_add(
    content="Discovered timing issue in FTS5 query planner.",
    tags=["project:mnemos", "agent:copilot", "mnemos:bug-pattern"],
    project="mnemos",
    agent="copilot",
)
```

---

## Bulk tag rename (`gcw:` → `mnemos:` and other prefix changes)

The `mnemos tags rename` command (and the equivalent `mnemos_tags_rename`
MCP tool / `POST /tags/rename` HTTP endpoint) bulk-renames tags matching a
source prefix to a target prefix across existing memories. It is the safe
replacement for the deprecated `mnemos migrate tags` subcommand.

```bash
# Dry-run first — preview only, nothing written (default)
mnemos tags rename --from gcw: --to mnemos: --dry-run

# Apply the rename
mnemos tags rename --from gcw: --to mnemos: --no-dry-run

# Restrict to specific subtypes
mnemos tags rename --from gcw: --to mnemos: --subtypes decision --subtypes learning --no-dry-run

# Scope to a single project / agent
mnemos tags rename --from gcw: --to mnemos: --project mnemos --no-dry-run

# Send invalid subtypes to <to_prefix>legacy instead of skipping them
mnemos tags rename --from gcw: --to mnemos: --invalid-to-legacy --no-dry-run
```

**Why this is safe:** the rename goes through `SQLiteStore.update_fields`
(a plain `UPDATE`), so the FTS5 `AFTER UPDATE` trigger fires and the
external-content index stays consistent — unlike the old `migrate tags`
path which used raw `sqlite3` writes and bypassed the trigger. The
operation is **idempotent**: a second run with the same arguments reports
`renamed=0` because the `from_prefix:` tags no longer exist.

**Re-embedding:** vectors are keyed by `memory_id` and the embedded text
is derived from `title + content + tags`. Tags are part of the embedded
text, so a rename *technically* changes the embedding input, but the
contribution is small relative to content. The rename deliberately does
**not** re-embed — semantic search continues to work because the stored
vectors still point to the same memory ids and the FTS5 leg (which reflects
the new tags via the trigger) carries tag-filtered queries. If exact
tag-vector alignment is required, run `mnemos reindex` afterwards.

**Audit trail:** each call writes a single row to the trace table with
`step="tags_rename"` recording the prefixes, dry-run flag, and counts.

The report returned (and printed by the CLI) has the shape:

```json
{
  "scanned": 42,
  "renamed": 18,
  "skipped_invalid": 0,
  "errors": [],
  "dry_run": false,
  "from_prefix": "gcw:",
  "to_prefix": "mnemos:"
}
```

---

## Migration guide (from ai-brain)

ai-brain had no required tag schema. Migrating:

1. Run `mnemos migrate-from-ai-brain` — copies ai-brain SQLite to Mnemos store.
2. Existing entries without `project:` / `agent:` get tag `mnemos:legacy` appended
   and are stored with `strict_tags=False`.
3. Run `mnemos tags-validate` to list all entries with incomplete contract.
4. Edit entries manually or run `mnemos tags-validate --auto-patch` to apply
   best-effort defaults (`project:unknown`, `agent:unknown`).
5. Flip `strict_tag_contract=True` in `~/.mnemos/config.yaml` once clean.

---

## TagContractError reference

```
mnemos.models.TagContractError
```

Raised by `validate_tag_contract(..., strict=True)` and `TagContract(strict=True)`.

Common messages:

| Message fragment | Cause |
|-----------------|-------|
| `exactly one project:` | 0 or ≥2 `project:` tags |
| `exactly one agent:` | 0 or ≥2 `agent:` tags |
| `at least one mnemos:` | No `mnemos:` tag present |
| `invalid mnemos: subtype` | Subtype not in allowed set |
| `invalid slug for project:` | Slug contains uppercase or special chars |
| `invalid slug for agent:` | Slug contains uppercase or special chars |
