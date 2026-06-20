<!-- mnemos-integration: v1.2.0 -->
---
applyTo: '**'
description: Mnemos memory operations — search before deciding, add when learning, agent-scoped recall
---

# Mnemos Memory Operations

## Applies to / Применяется ко всем агентам

This instruction governs the day-to-day memory operations: searching before
acting, writing when learning, and recalling agent-scoped context. It
complements `mnemos-session-lifecycle.instructions.md` (which governs
start/checkpoint/end).

---

## WHEN

| Trigger | Action | Tool |
|---------|--------|------|
| **Before an architectural decision** — choosing a pattern, library, approach | Search memory for prior decisions on this topic | `mnemos_search` |
| **When learning something non-obvious** — a gotcha, a hidden constraint, a surprising behaviour | Capture it as a learning or bug-pattern | `mnemos_add` |
| **Before a web search** — querying the internet for a solution | Search memory first; the answer may already be stored | `mnemos_search` |
| **When resuming work as a specific agent** — e.g. `cr-security-reviewer` re-entering | Recall this agent's recent entries | `mnemos_agent_recall` |
| **When a decision is made** — design, process, or tradeoff chosen | Capture the decision and rationale | `mnemos_add` |

---

## HOW

### Search before deciding

```text
mnemos_search(
  query=<natural language query>,
  project=<current-project>,   # optional, scope to project
  tags=["gcw:decision"],        # optional, narrow by tag
  limit=10
)
```

- Start narrow (tag-filtered, project-scoped), then broaden if no hits.
- If 0 results, say so explicitly — do not fabricate prior context.
- Default to recency when ranking ties.

### Agent-scoped recall

```text
mnemos_agent_recall(
  agent=<your-agent-slug>,
  project=<current-project>,    # optional
  query=<optional focus>,
  limit=20
)
```

- Use when you need **your own** prior context, not the project's.
- Example: `cr-security-reviewer` resuming a review should recall its own
  past findings before re-reviewing.

### Capture when learning

```text
mnemos_add(
  content=<markdown: what you learned, with context>,
  tags=[
    "project:<slug>",
    "agent:<slug>",
    "gcw:learning"              # or gcw:bug-pattern, gcw:decision, gcw:rule
  ],
  title=<short title>           # optional, auto-generated if omitted
)
```

- **Tag contract is mandatory.** See `mnemos-tag-contract.instructions.md`.
- Write what you would want to read back in 30 days. Trivia is noise.
- One idea per entry. Split complex learnings into multiple entries.

### Capture a decision

```text
mnemos_add(
  content=<markdown: decision + rationale + alternatives considered>,
  tags=[
    "project:<slug>",
    "agent:<slug>",
    "gcw:decision"
  ]
)
```

- Include the **why**, not just the **what**. A decision without rationale
  is a rule without justification — future agents cannot tell if it still
  applies.

---

## Discipline

- **Search first, always.** Before a web search, before an architectural
  decision — check memory. Re-learning what was already learned is waste.
- **Write sparingly.** Memory is not a log of every action. Write when you
  learned something **non-obvious** that a future agent would benefit from.
- **Update by key, don't duplicate.** If you are re-capturing something,
  search for it first and update rather than appending a duplicate.
- **Never block on memory failure.** If `mnemos_*` errors, log a notice
  and continue with the task. Memory is an enhancement, not a dependency.

---

## See also

- `mnemos-session-lifecycle.instructions.md` — recall at start, checkpoint on compaction
- `mnemos-tag-contract.instructions.md` — required tag composition
- Skill `mnemos-recall` — effective search (narrow → broaden)
- Skill `mnemos-write` — writing good entries
