# Runbook: Migrate from ai-brain

**🌐 Language / Язык:** English · [Русский](../../../ru/admin/runbooks/migrate.md)

## Overview

Migrate your existing ai-brain data (SQLite DB + vault) into Mnemos format.

## Before you start

1. **Backup your ai-brain data**:
   ```bash
   cp -r ~/.ai-brain ~/.ai-brain.backup-$(date +%Y%m%d)
   cp -r ~/brain-vault ~/brain-vault.backup-$(date +%Y%m%d)
   ```

2. **Install Mnemos** (see `install.md`).

## Dry run

Always run dry-run first to see what will be migrated:

```bash
mnemos migrate-from-ai-brain --dry-run
```

Output shows:
- Number of memories to migrate
- Number of vault files to copy
- Any anticipated errors

## Full migration

```bash
mnemos migrate-from-ai-brain
```

This will:
1. Back up existing Mnemos DB (if any)
2. Migrate all memories with Mnemos tag contract applied in **lax mode**
3. Copy vault files preserving directory structure
4. Map ai-brain sources → Mnemos sources (telegram → mcp)

## Tag contract handling

Legacy ai-brain entries without `project:` / `agent:` / `mnemos:` tags get:
- `project:legacy`
- `agent:unknown`
- `mnemos:legacy`

After migration, review and retag important entries:

```bash
mnemos search "project:legacy" --limit 50
```

## Migrating `gcw:` tags → `mnemos:` tags

If your store contains memories with the legacy `gcw:<subtype>` tag prefix
(from the pre-2.7.8 GCW agent family), rename them in bulk to the canonical
`mnemos:<subtype>` prefix using the safe `tags rename` command:

```bash
# Dry-run first — preview the change, nothing written (default)
mnemos tags rename --from gcw: --to mnemos: --dry-run

# Apply the rename
mnemos tags rename --from gcw: --to mnemos: --no-dry-run
```

Notes:
- `validate_tag_contract()` already auto-migrates valid `gcw:<subtype>` →
  `mnemos:<subtype>` on read, so `gcw:` tags are accepted as an alias. The
  bulk rename is a one-time housekeeping step to canonicalise the stored tags.
- Invalid `gcw:` subtypes (not in the whitelist) are skipped by default and
  counted in `skipped_invalid`. Pass `--invalid-to-legacy` to rename them to
  `mnemos:legacy` instead of skipping.
- The operation is **idempotent** — a second run reports `renamed=0`.
- The deprecated `mnemos migrate tags` command now delegates to this safe path
  and emits a deprecation warning. Prefer `mnemos tags rename` directly.

## Post-migration checklist

- [ ] `mnemos stats` shows expected memory count
- [ ] `mnemos search "hello"` returns results
- [ ] Vault files visible in `~/.mnemos/vault/`
- [ ] MCP server `mnemos_recall_context` works

## Rollback

If something goes wrong:

```bash
# Restore from Mnemos backup
ls ~/.mnemos/*.backup-*
cp ~/.mnemos/mnemos.db.backup-YYYYMMDD-HHMMSS ~/.mnemos/mnemos.db

# Or start fresh
rm -rf ~/.mnemos/data ~/.mnemos/vault
```
