# Mnemos memory — always-on rules

You have persistent shared memory through the `mnemos_*` MCP tools. Follow
these rules in every session, unprompted.

## Session lifecycle

| Trigger | Action |
|---------|--------|
| **Session start** — BEFORE reading any project file | `mnemos_recall_context(project=<current-project>)`, then surface a ≤4-line header: `Memory: project=<name> \| recalled=<N> entries` plus last focus and open questions. If empty, say so in one line. Never block on recall failure. |
| **Before context compaction** — a summary/compression banner appears, earlier turns become unreachable, or the conversation passes ~30 turns since the last checkpoint | `mnemos_save_context(project=…, goals=…, completed=…, next_steps=…)` |
| **Session end / project handoff** | `mnemos_save_context(...)` — context that is never saved is lost. |

## Priority operations

- `mnemos_search` BEFORE an architectural decision (pattern, library,
  approach) and BEFORE a web search — the answer may already be in memory.
  Recall beats re-deriving.
- `mnemos_add` whenever you learn something non-obvious, make a decision
  with a tradeoff, or hit a surprising gotcha — future agents will search
  for exactly this.
- `mnemos_agent_recall` when resuming work as a named agent role.

## Tag contract (every `mnemos_add` / `mnemos_ingest_url` call)

Tags are the searchability backbone of the store. Every write MUST carry:

- exactly one `project:<slug>` — the codebase or initiative;
- exactly one `agent:<slug>` — the authoring agent (`agent:user` for
  user-authored);
- at least one `mnemos:<subtype>` — e.g. `mnemos:decision`,
  `mnemos:learning`, `mnemos:bug-pattern`, `mnemos:context`.

Without the contract, memory degrades into unstructured noise: project and
agent-scoped recall stop working.

These are PRIORITY tools: prefer recall over re-deriving, and always save
before the session ends.
