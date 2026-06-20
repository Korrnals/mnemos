<!-- mnemos-integration: v1.2.0 -->
---
applyTo: '**'
description: Mnemos session lifecycle — recall at start, checkpoint on compaction, save at end
---

# Mnemos Session Lifecycle

## Applies to / Применяется ко всем агентам

Every agent with access to `mnemos_*` MCP tools must follow this lifecycle.
Memory that is never recalled is wasted; context that is never saved is lost.

---

## WHEN

| Trigger | Action | Tool |
|---------|--------|------|
| **Session start** — before reading any project file | Recall prior context for this project | `mnemos_recall_context` |
| **Before context compaction** — summary marker, long conversation (≳30 turns), harness signals approaching limit | Save a checkpoint | `mnemos_save_context` |
| **Session end / before handoff** — task done, user leaving, switching project | Save final context | `mnemos_save_context` |

### Compaction signals (any one is enough)

- A summary banner or "context compressed" notice from the harness.
- Sudden loss of references to earlier turns (tool outputs, file reads).
- `mnemos_auto_collect_status` returns a composite recommendation of `checkpoint`.
- Conversation exceeds ~30 turns since the last checkpoint.
- Several large tool outputs accumulated in the context window.

---

## HOW

### At session start (MANDATORY, before reading files)

```text
mnemos_recall_context(project=<current-project>)
```

- Call this **before** reading project files or running searches.
- If it returns prior context, surface a short header (≤4 lines) to the user:

  ```text
  Memory: project=<name> | recalled=<N> entries
  Last focus: <one line>
  Open questions: <one line or "none">
  ```

- If it returns nothing, say: `Memory: no prior context for <project>`.
- Never block on recall failure — degrade silently to "no prior context".

### Before context compaction

```text
mnemos_save_context(
  project=<current-project>,
  goals=<one sentence: active goal>,
  completed=<bullets: what is done>,
  in_progress=<one bullet: immediate next action>,
  decisions=<bullets: decisions worth surviving>,
  context=<file paths, architecture notes, gotchas>
)
```

- Keep the body short — this is for waking up after compaction, not for archival.
- Idempotent within a session: re-saving shortly after a previous checkpoint
  should update, not duplicate.

### At session end / before handoff

```text
mnemos_save_context(
  project=<current-project>,
  goals=<final goal state>,
  completed=<all completed work>,
  in_progress=<what the next agent should pick up>,
  decisions=<all key decisions>,
  context=<handoff notes, open questions, blockers>
)
```

- This is the **last** memory operation of the session.
- If the session is ending without meaningful work, skip — do not write empty
  checkpoints.

---

## Discipline

- **Recall is mandatory at start.** An agent that skips recall re-learns what
  was already learned. This wastes tokens and repeats mistakes.
- **Checkpoint on compaction signals, not on a timer.** Writing every N turns
  creates noise; writing only on signals preserves signal.
- **Never block on memory failure.** If `mnemos_*` is unavailable or errors,
  log a one-line notice and continue. Memory is a enhancement, not a
  dependency.
- **Do not dump full recalled content.** Surface a header, then act on it.
  The full content is in the store — recall it again if needed.

---

## See also

- `mnemos-memory-ops.instructions.md` — search before deciding, add when learning
- `mnemos-tag-contract.instructions.md` — required tag composition
- Skill `mnemos-session-init` — step-by-step session start procedure
- Skill `mnemos-checkpoint` — step-by-step checkpoint procedure
