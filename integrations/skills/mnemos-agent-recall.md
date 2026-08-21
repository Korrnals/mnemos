---
name: mnemos-agent-recall
description: Agent-scoped memory recall — what did THIS agent (or a teammate) previously learn, decide, or leave open
---

# Mnemos Agent Recall

Pull the latest memories written by a specific agent slug (yours or a
teammate's). Use it to resume another agent's thread of work or to check
what a specialist agent already established before duplicating it.

## WHEN

- **Resuming work after a context reset** — your own latest entries first.
- **Taking over from another agent** — review their trail before changing
  their decisions.
- **Team coordination** — check what `gcw-tech-lead` / `reviewer` / etc.
  already decided in this project.

## STEPS

1. **Recall your own trail**:

   ```text
   mnemos_agent_recall(agent=<your-slug>, limit=10)
   ```

2. **Scope to the project** to cut noise from other work:

   ```text
   mnemos_agent_recall(agent=<slug>, project=<project-slug>, limit=10)
   ```

3. **Narrow with a query** when the trail is long:

   ```text
   mnemos_agent_recall(agent=<slug>, query="auth refactor", limit=5)
   ```

4. **For open work**, follow up with `mnemos_workflow(action="get", …)` on
   entries tagged `mnemos:open-question`.

## DISCIPLINE

- Agent recall is a **trail, not a search** — for topical questions use
  `mnemos-recall` (hybrid search) instead.
- Entries appear newest-first; if the trail is stale, check
  `mnemos_list_recent` before assuming the agent stopped writing.

## See also

- Skill `mnemos-recall` — topical semantic search
- Skill `mnemos-workflow` — lifecycle of open questions
