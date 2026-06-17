# Tag Contract

**🌐 Language / Язык:** English · [Русский](../../ru/user/tag-contract.md)

Mnemos enforces a structured tag schema on every memory entry. This document
describes the contract, the set of valid values, and migration guidance.

---

## Why a tag contract?

Memory entries without consistent structure become unsearchable noise.
The tag contract:

- Pins every entry to exactly **one project** and **one agent**
- Classifies the entry with at least **one GCW subtype** (cognitive category)
- Enables per-agent recall (M3) and project-scoped cleanup
- Prevents ambiguous dual-project entries (a common source of context pollution)

---

## Required tags (must be present on all new entries)

| Tag | Format | Cardinality | Purpose |
|-----|--------|-------------|---------|
| `project:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **exactly 1** | Binds entry to a codebase / initiative |
| `agent:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **exactly 1** | Agent that authored the memory |
| `gcw:<subtype>` | see table below | **at least 1** | Cognitive category |

### GCW subtypes

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
    ["project:myproject", "agent:copilot", "gcw:learning"],
    strict=True,
)

# Use TagContract model directly
tc = TagContract(tags=["project:myproject", "agent:copilot", "gcw:decision"])
print(tc.project)       # "myproject"
print(tc.agent)         # "copilot"
print(tc.gcw_subtypes)  # {"decision"}

# Pass tags when creating a Memory
from mnemos.models import Memory
m = Memory(
    content="Decided to use FTS5 over a dedicated search service.",
    tags=["project:mnemos", "agent:tech-lead", "gcw:decision"],
    project="mnemos",
    agent="tech-lead",
)
```

---

## MCP usage

```
mnemos_add(
    content="Discovered timing issue in FTS5 query planner.",
    tags=["project:mnemos", "agent:copilot", "gcw:bug-pattern"],
    project="mnemos",
    agent="copilot",
)
```

---

## Migration guide (from ai-brain)

ai-brain had no required tag schema. Migrating:

1. Run `mnemos migrate-from-ai-brain` — copies ai-brain SQLite to Mnemos store.
2. Existing entries without `project:` / `agent:` get tag `gcw:legacy` appended
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
| `at least one gcw:` | No `gcw:` tag present |
| `invalid gcw: subtype` | Subtype not in allowed set |
| `invalid slug for project:` | Slug contains uppercase or special chars |
| `invalid slug for agent:` | Slug contains uppercase or special chars |
