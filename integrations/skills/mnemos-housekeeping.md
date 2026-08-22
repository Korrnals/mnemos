---
name: mnemos-housekeeping
description: Memory store housekeeping — stats, queue depth, tag hygiene, and reprocessing raw entries
---

# Mnemos Housekeeping

Keep the memory store healthy: check stats and queue depth, list recent
entries and tags, reprocess the raw queue when it grows.

## WHEN

- **At session start** — `mnemos_stats()` is a cheap health ping (counts,
  degraded flags, search health).
- **Recall results look stale or thin** — check `embedding_status` and
  `search_health` before blaming the query.
- **After heavy write bursts** — a growing `queue_depth` means the pipeline
  is behind; reprocess to flush.
- **Tag hygiene** — `mnemos_list_tags()` reveals typos and near-duplicates
  (`project:mnemos` vs `project:Project-Mnemos`).

## STEPS

1. **Health ping**:

   ```text
   mnemos_stats()
   ```

   Watch for: `degraded: true`, `fts_available/vector_available: false`,
   `orphaned_vectors: true`.

2. **Flush the pipeline** when `queue_depth > 0` after writes:

   ```text
   mnemos_reprocess()
   ```

3. **Review recent entries and tags**:

   ```text
   mnemos_list_recent(limit=10)
   mnemos_list_tags()
   ```

4. **Fix tag drift** with the bulk rename (dry-run first). Two entry
   points: the grouped pilot tool `mnemos_tags`, or the dedicated
   `mnemos_tags_rename` (same engine, prefix→prefix, idempotent):

   ```text
   mnemos_tags_rename(from_prefix="gcw:", to_prefix="mnemos:", dry_run=true)
   mnemos_tags(action="rename", from_prefix="gcw:", to_prefix="mnemos:",
               dry_run=true)
   ```

## DISCIPLINE

- Reprocess is for **pipeline backlog**, not a fix for bad content — bad
  entries get rewritten, not reprocessed.
- Tag renames are prefix-based and idempotent, but ALWAYS dry-run first.
- Don't poll stats in a loop — it's a check, not a monitor.

## See also

- Skill `mnemos-tag-contract` — what valid tags look like
- Skill `mnemos-checkpoint` — when to save session state
