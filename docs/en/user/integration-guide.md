<!-- mnemos-integration: v1.2.0 -->
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
- Version stamp `<!-- mnemos-integration: v1.2.0 -->` in every file.
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

## How agents discover the tools

The integration layer assumes the Mnemos MCP server is already connected.
The tools (`mnemos_*`) appear in the agent's tool list once the MCP server is
registered in the client's MCP configuration.

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

## Versioning

Every file in the integration layer carries a version stamp:

```html
<!-- mnemos-integration: v1.2.0 -->
```

This allows `mnemos integration verify` to detect stale files after an update. If
the stamp does not match the installed Mnemos version, the file is flagged
for update.
