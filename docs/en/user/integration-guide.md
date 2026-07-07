<!-- mnemos-integration: v2.0.0 -->
# Integration Guide

**🌐 Language / Язык:** English · [Русский](../../ru/user/integration-guide.md)

The Mnemos integration layer is a set of **behavioral triggers** that make
agents actually *use* the memory tools, not just have them available. Without
these triggers, agents forget to recall at session start, skip checkpoints
before compaction, and omit required tags.

---

## What is the integration layer?

Three surfaces, each with a different strength:

| Surface | What it is | How it works | Example |
|---------|------------|--------------|---------|
| **Instructions** | `*.instructions.md` with `applyTo: '**'` | Passive rules — loaded into every agent's context unconditionally. State WHEN and HOW. | "Recall at session start, before reading files" |
| **Skills** | `SKILL.md` files | Workflow guides — step-by-step procedures loaded on-demand. | "How to recall effectively: narrow → broaden" |
| **Prompt mode** | `*.prompt.md` | Active mode — a stronger contract that reshapes the agent's behavior for memory-heavy work. | `mnemos-memory` mode with mandatory recall + checkpoint |

### Instructions vs skills vs prompts

- **Instructions** are always-on rules. They say *when* to act. Every agent
  with `mnemos/*` tools gets them.
- **Skills** are on-demand workflows. They say *how* to act. The agent loads
  them when it needs the procedure.
- **Prompt mode** is an opt-in contract. It says *you are now a memory agent*.
  Use it for sessions where memory continuity is critical.

---

## What's in the package

```text
integrations/
├── instructions/
│   ├── mnemos-session-lifecycle.instructions.md   # recall / checkpoint / save
│   ├── mnemos-memory-ops.instructions.md          # search / add / agent-recall
│   └── mnemos-tag-contract.instructions.md        # required tag composition
├── skills/
│   ├── mnemos-session-init.md                     # recall at session start
│   ├── mnemos-checkpoint.md                       # save mid-session / on compaction
│   ├── mnemos-recall.md                           # effective search (narrow → broaden)
│   ├── mnemos-write.md                            # write good entries
│   └── mnemos-tag-contract.md                     # tag schema reference
└── prompts/
    └── mnemos-memory.prompt.md                    # active memory mode
```

---

## Deploy

### One command (all targets)

```bash
mnemos integration setup
```

Deploys instructions, skills, and prompt mode to the default target
(`~/.copilot/` for VS Code Copilot Chat). Idempotent — safe to re-run.

### Per-target

```bash
mnemos integration setup --target vscode-copilot   # default
mnemos integration setup --target claude-code       # Claude Code
mnemos integration setup --target cursor            # Cursor
```

See `mnemos integration setup --help` for the full target list. Targets are defined
in `integrations/targets.yaml` (managed by Stream A).

### What gets deployed where

| Target | Instructions → | Skills → | Prompts → |
|--------|----------------|----------|-----------|
| `vscode-copilot` | `~/.copilot/instructions/` | `~/.copilot/skills/` | `~/.config/Code/User/prompts/` |
| `claude-code` | `~/.claude/instructions/` | `~/.claude/skills/` | `~/.claude/prompts/` |
| `cursor` | `~/.cursor/instructions/` | `~/.cursor/skills/` | `~/.cursor/prompts/` |

---

## Verify

After deployment, verify that all files landed correctly:

```bash
mnemos integration verify
```

Checks:

- All instruction files present with valid frontmatter (`applyTo: '**'`).
- All skill files present with `name:` and `description:`.
- Prompt mode file present with `mode:` and `tools:`.
- Version stamp `<!-- mnemos-integration: v2.0.0 -->` in every file.
- No `ai-brain` references (except the "adapted from" comment in the prompt).

Exit code `0` = all checks passed. Non-zero = missing or malformed files.

---

## Update

When a new version of Mnemos ships updated integration content:

```bash
mnemos integration update
```

Updates only files that changed. Preserves any local customizations (files
not managed by Mnemos are left alone). After update, run `mnemos integration verify`.

---

## Uninstall

To remove all Mnemos integration files:

```bash
mnemos integration uninstall
```

Removes only files deployed by `mnemos integration setup`. Local customizations are
preserved. **This is a destructive action** — it deletes files. Confirm when
prompted.

---

## Agent MCP wiring

Deploying instructions and skills tells agents *when* to call memory tools.
**Agent MCP wiring** goes one step further: it adds `mnemos/*` to the
`tools:` frontmatter of GCW agent files (`~/.copilot/agents/*.agent.md`) so
the tools are actually granted to the agent at request time.

Without wiring, an agent may have the behavioural instructions but no
`mnemos_*` tools in its frontmatter — the harness won't pass them to the
model. Wiring closes that gap.

### What it does

- Scans `~/.copilot/agents/` for `*.agent.md` files.
- Parses YAML frontmatter and adds `mnemos/*` (wildcard) or individual
  `mnemos/mnemos_*` tool references to the `tools:` array.
- **Only `tools:` is touched** — `model:`, `model_tier:`, `agents:`, and
  other keys are never modified.
- Idempotent — re-running does not duplicate `mnemos/*` entries.

### What gets skipped

| Condition | Why |
|-----------|-----|
| Agent already has `mnemos/*` or `mnemos/mnemos_*` in `tools:` | Already wired — no change needed. |
| Agent uses `tool_profile:` instead of `tools:` | Resolved by the GCW installer (`make install-all`); mutating it would be overwritten on the next install. |
| Agent has no parseable frontmatter | Cannot safely edit — reported as skipped. |

### Usage

`mnemos integration setup` wires agents in the same pass as file deployment
and MCP registration. The wiring flags control the behaviour:

```bash
# Wire all unwired agents (no prompt)
mnemos integration setup --wire-agents --all

# Wire specific agents by name or filename stem
mnemos integration setup --wire-agents --select tech-lead,code-reviewer

# Skip agent wiring entirely (no prompt)
mnemos integration setup --no-wire-agents

# Preview what would change without modifying files
mnemos integration setup --wire-agents --dry-run
```

If neither `--wire-agents` nor `--no-wire-agents` is passed, the command
prompts interactively (same pattern as the MCP registration prompt). In a
non-interactive terminal (CI / pipe), it defaults to wiring all unwired
agents.

| Flag | Description |
|------|-------------|
| `--wire-agents` | Enable agent wiring (interactive prompt by default) |
| `--wire-agents --all` | Wire all unwired agents without prompting |
| `--wire-agents --select name1,name2` | Wire only the named agents (matches `name`, filename stem, or filename) |
| `--no-wire-agents` | Skip agent wiring entirely (explicit opt-out) |
| `--precise` | Use individual `mnemos/mnemos_*` tool names instead of the `mnemos/*` wildcard |
| `--dry-run` | Show what would change without modifying files |

### Wildcard vs precise mode

- **Wildcard** (default): adds a single `mnemos/*` entry granting all
  mnemos tools. Compact frontmatter, grants everything.
- **Precise** (`--precise`): adds individual `mnemos/mnemos_*` entries
  (add, search, recall_context, agent_recall, save_context, list_recent,
  list_tags, ingest_url, stats, auto_collect_status). Explicit grant list —
  `watch_*` admin tools are intentionally excluded.

Use precise mode when you want fine-grained control over which tools each
agent gets. Use wildcard mode for convenience when all agents should have
the full mnemos toolset.

### Verifying wiring

After wiring, verify the state:

```bash
mnemos integration verify
```

The agents section of the verify report shows:

- **Wired** — agents with `mnemos/*` or `mnemos/mnemos_*` in `tools:`.
- **Unwired** — agents without mnemos tools (candidates for wiring).
- **Skipped** — agents with `tool_profile:` (managed by the GCW installer).

`mnemos doctor` also includes an agent wiring check (9th check) that
reports the same summary and warns if unwired agents are detected.

---

## Context Filter

The Context Filter is a five-stage pipeline (dedup, noise, extract,
compress, tokens) that strips noise from raw content before it reaches a
model. It runs automatically on every `mnemos_add` when `auto_filter: true`
(the default for new installs).

Key surfaces:

- **Auto-filter on ingest** — `mnemos_add` stores `raw_content` +
  `clean_content` + `filter_stats`. Search and recall return
  `clean_content` when available.
- **`mnemos_filter` MCP tool** — explicit re-filter of an existing memory
  (override profile, set token budget).
- **`mnemos filter` CLI** — `mnemos filter <id>` for a single memory,
  `mnemos filter --all` to backfill unfiltered records.
- **Filter stats in `mnemos stats`** — filtered/unfiltered counts, average
  reduction, breakdown by profile.
- **Profiles** — `log | terminal | code | docs | web | default`,
  auto-detected from content heuristics.

For the full guide with stage details, profile table, examples, and
configuration, see [context-filter.md](context-filter.md).

---

## `mnemos integration setup` — default flow

By default, `mnemos integration setup` now **prompts for agent wiring**
in the same pass as file deployment and MCP registration. This closes the
gap where instructions were deployed but agents lacked `mnemos/*` in
their `tools:` frontmatter.

```bash
mnemos integration setup
# → Deploys instructions + skills + prompts
# → Registers the MCP server
# → Prompts: "Wire mnemos/* into GCW agents? [Y/n]"
```

| Flag | Behaviour |
|------|-----------|
| (none, interactive) | Prompts for agent wiring (default) |
| `--wire-agents --all` | Wire all unwired agents without prompting |
| `--wire-agents --select name1,name2` | Wire only the named agents |
| `--no-wire-agents` | Skip agent wiring entirely |
| `--precise` | Use individual `mnemos/mnemos_*` tool names instead of the wildcard |
| `--dry-run` | Preview what would change without modifying files |

In a non-interactive terminal (CI / pipe), the command defaults to
wiring all unwired agents. See the [Agent MCP wiring](#agent-mcp-wiring)
section above for the full flag reference.

---

## `mnemos add --dry-run` — filter preview

Preview how the Context Filter will transform content **before saving**.
Validates the tag contract, runs the five-stage filter pipeline, and
prints stats — without writing anything to the store.

```bash
mnemos add "long log output..." --tags "project:mnemos,agent:tech-lead,gcw:trace" --dry-run
```

Output:

```text
[dry-run] Filter preview (no memory saved):
  Input:     320 tokens
  Output:    180 tokens (43.8% reduction)
  Profile:   log (auto-detected)
  Dedup:     2 exact, 0 near-duplicates removed
  Noise:     14 lines cleaned
  Budget:    not set (no truncation)
[dry-run] Memory would be saved with these filter stats.
```

| Field | Meaning |
|-------|---------|
| Input / Output | Estimated token count before and after filtering |
| Profile | Auto-detected content profile (`log`, `terminal`, `code`, `docs`, `web`, `default`) |
| Dedup | Exact and near-duplicate lines removed |
| Noise | ANSI codes, progress bars, timestamps, separators stripped |
| Budget | Token budget if set (truncation); `not set` means no truncation |

> `--dry-run` is not supported with `--url` (content is fetched at ingest
> time). Use it with positional content or `--file`.

---

## `mnemos doctor --fix` — auto-fix warnings

`mnemos doctor` runs health checks and reports status. With `--fix`, it
**auto-fixes WARN-level checks** — no manual intervention needed for the
common cases.

```bash
mnemos doctor          # report only
mnemos doctor --fix    # fix warnings, then re-check
mnemos doctor --fix --dry-run   # preview what would be fixed
```

| Warning | Auto-fix action |
|---------|-----------------|
| Integration stale | `mnemos integration update` — redeploy stale files to current version |
| Agent wiring — unwired agents | `mnemos integration setup --wire-agents --all` |
| MCP server not registered | MCP registration via `mcp-setup.sh` |

**FAIL-level checks are not auto-fixable** — they require manual
diagnosis (missing config, broken SQLite DB, missing vault). After
fixes, `doctor` re-runs the affected checks and reports the new status.

Exit codes: `0` = all pass, `1` = one or more failed, `2` = warnings
only.

---

## `mnemos logs` — pipeline traces

View the pipeline trace log (the `traces` table) directly from the CLI.
Shows cluster, synthesize, publish, and recall steps with latency, LLM,
cache, and fallback flags.

```bash
mnemos logs                       # last 50 traces
mnemos logs --task cluster        # only cluster traces
mnemos logs --project mnemos      # filter by project
mnemos logs --limit 100           # more rows
mnemos logs --since 2026-06-01    # only traces after this date
mnemos logs --follow              # poll for new traces (tail -f)
```

| Flag | Description |
|------|-------------|
| `--task`, `-t` | Filter by task label (`cluster`, `synthesize`, `publish`, `recall`) |
| `--project`, `-p` | Filter by project slug |
| `--limit`, `-l` | Maximum number of traces (default 50) |
| `--since` | Only traces after this ISO date |
| `--follow`, `-f` | Poll for new traces (tail -f style) |
| `--config`, `-c` | Path to config.yaml |

The table columns: Timestamp, Task, Project, Step, Item, Latency, LLM
(called?), Cache (hit?), Fallback (used?). Traces are the audit log for
the knowledge pipeline — see the [Context Filter](context-filter.md) and
[architecture overview](../architecture/overview.md) for the pipeline
stages.

---

## How agents discover the tools

The integration layer assumes the Mnemos MCP server is already connected.
The tools (`mnemos_*`) appear in the agent's tool list once the MCP server is
registered in the client's MCP configuration. Agent MCP wiring (above)
ensures the `tools:` frontmatter actually grants those tools to each agent.

For VS Code Copilot Chat, see [getting-started.md](getting-started.md#run-the-mcp-server)
for MCP server setup. Once connected, the instructions and skills in this
package tell the agent *when* and *how* to call those tools.

---

## Tag contract

Every `mnemos_add` and `mnemos_ingest_url` call must carry:

- **exactly one** `project:<slug>`
- **exactly one** `agent:<slug>` (or `agent:user`)
- **at least one** `gcw:<subtype>`

See [tag-contract.md](tag-contract.md) for the full schema. The integration
layer reinforces this in three places: the `mnemos-tag-contract` instruction,
the `mnemos-tag-contract` skill, and the `mnemos-memory` prompt mode.

---

## Hermes Agent

Mnemos provides a native `MemoryProvider` plugin for [Hermes Agent](https://hermes-agent.nousresearch.com/) by Nous Research. The plugin connects Mnemos to Hermes' pluggable memory system via the HTTP API.

### Installation

1. Start Mnemos server:
   ```bash
   mnemos serve --host 127.0.0.1 --port 8787 &
   ```

2. Deploy the integration:
   ```bash
   mnemos integration setup --target hermes
   ```
   This copies the plugin to `~/.hermes/plugins/mnemos/` and deploys skills/instructions to `~/.hermes/skills/`.

3. Activate via the wizard:
   ```bash
   hermes memory setup
   ```
   Select "mnemos" from the provider list and configure the base URL.

4. Restart your Hermes session (`/restart` in gateway, or relaunch CLI).

### Tools

The plugin exposes all 15 `mnemos_*` tools as native Hermes tools:

| Tool | HTTP endpoint |
|------|--------------|
| `mnemos_search` | POST /search |
| `mnemos_add` | POST /memories |
| `mnemos_recall_context` | POST /context/recall |
| `mnemos_save_context` | POST /context/save |
| `mnemos_agent_recall` | GET /recall/agent/{name} |
| `mnemos_list_recent` | GET /memories |
| `mnemos_list_tags` | GET /tags |
| `mnemos_stats` | GET /metrics |
| `mnemos_auto_collect_status` | GET /auto-collect |
| `mnemos_ingest_url` | POST /ingest-url |
| `mnemos_compress` | POST /compress |
| `mnemos_retrieve` | POST /retrieve |
| `mnemos_watch_start` | POST /watch/start |
| `mnemos_watch_stop` | POST /watch/stop |
| `mnemos_watch_status` | GET /watch/status |

### Configuration

Config is stored in `~/.hermes/config.yaml` under `memory.mnemos`:

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `http://127.0.0.1:8787` | Mnemos HTTP API base URL |
| `api_key` | (empty) | Bearer token if auth enabled |
| `project` | `hermes` | Default project slug for tag contract |
| `agent` | `hermes-default` | Default agent slug for tag contract |
| `auto_sync` | `true` | Mirror built-in memory writes and sync significant turns |
| `prefetch_limit` | `5` | Max results in prefetch (before each turn) |
| `sync_interval` | `10` | Sync every Nth turn |

### Architecture

The plugin implements the Hermes `MemoryProvider` ABC:

- **prefetch()** — hybrid search before each turn → context injection
- **sync_turn()** — saves significant turns (user > 50 chars or every Nth)
- **on_memory_write()** — mirrors built-in MEMORY.md/USER.md writes to Mnemos
- **on_session_end()** — extracts key facts from the conversation
- **on_pre_compress()** — extracts facts before context compression
- **Circuit breaker** — 5 failures → 120s cooldown

---

## Versioning

Every file in the integration layer carries a version stamp:

```html
<!-- mnemos-integration: v2.0.0 -->
```

This allows `mnemos integration verify` to detect stale files after an update. If
the stamp does not match the installed Mnemos version, the file is flagged
for update.
