---
name: mnemos-filter
description: Run or refresh the Context Filter on a stored memory — strip noise, pick profiles, enforce token budgets
---

# Mnemos Filter

The Context Filter is the five-stage noise stripper that runs automatically
on every `mnemos_add`. Use `mnemos_filter` to run it retroactively (when
`auto_filter` was off) or to re-filter an entry with a different profile or
token budget.

## WHEN

- **An entry was written with auto_filter off** — noisy content is bloating
  recall results.
- **The wrong profile was auto-detected** — e.g. a log pasted as docs kept
  its timestamps and ANSI codes.
- **A token budget changed** — re-filter to truncate to the new ceiling.
- **Previewing the cost of keeping content** — the tool reports clean
  content plus reduction stats.

## STEPS

1. **Re-filter with an explicit profile**:

   ```text
   mnemos_filter(memory_id=<id>, profile="terminal")
   ```

   Profiles: `log`, `terminal`, `code`, `docs`, `web`, `default`.
   Omit `profile` to let the filter auto-select.

2. **Enforce a token budget**:

   ```text
   mnemos_filter(memory_id=<id>, profile="log", budget=2000)
   ```

3. **Check the reduction stats** in the result — a tiny reduction means the
   entry was already clean; a huge one means the raw content was mostly
   noise worth compressing instead (see `mnemos-compress`).

## DISCIPLINE

- Filtering rewrites the STORED content — the vault original stays the
  source of truth; don't hand-copy filtered output back into entries.
- Prefer fixing the writer over re-filtering forever: if entries keep
  arriving noisy, adjust how they're added, not the filter.
- `code` profile preserves identifiers; don't use `default` on source code.

## See also

- Skill `mnemos-write` — writing clean entries in the first place
- Skill `mnemos-compress` — zero-loss alternative for big blobs
- [Context filter guide](https://github.com/Korrnals/mnemos/blob/main/docs/en/user/context-filter.md)
