---
name: mnemos-exchange
description: Export and import the memory store — backups, migration between instances, federation payloads
---

# Mnemos Exchange

Move memories between mnemos instances (or into a backup file) via
`mnemos_export` / `mnemos_import`. JSON for selective payloads, SQLite for
full snapshots.

## WHEN

- **Before risky operations** — a dated export is a cheap safety net.
- **Migrating instances** — new machine, new container, split → merge.
- **Federation Phase 0 sync** — compact payloads between peers.

## STEPS

1. **Export** (JSON by default; filters optional):

   ```text
   mnemos_export(output_path="/backup/mnemos-2026-08-21.json",
                 format="json", project=<slug>)
   ```

   Full snapshot for restore purposes:

   ```text
   mnemos_export(output_path="...", format="sqlite")
   ```

2. **Import on the target** — merge is the safe default:

   ```text
   mnemos_import(source_path="...", mode="merge", dry_run=true)  # preview!
   mnemos_import(source_path="...", mode="merge")
   ```

3. **Restore mode is destructive** — wipes the target store first; requires
   `confirm=true`. Use only for full disaster recovery.

## RULES

- **Always `dry_run=true` first** — the validation report shows what would
  happen with zero risk.
- Encrypted exports: the passphrase comes from the environment variable
  named by `passphrase_env` — never inline.
- Import rejects schema drift and oversized content; fix the source rather
  than forcing.

## See also

- Skill `mnemos-write` — what belongs in the store in the first place
- `mnemos sync` CLI — federation batch sync between instances
