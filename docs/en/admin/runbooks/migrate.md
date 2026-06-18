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
2. Migrate all memories with GCW tag contract applied in **lax mode**
3. Copy vault files preserving directory structure
4. Map ai-brain sources → Mnemos sources (telegram → mcp)

## Tag contract handling

Legacy ai-brain entries without `project:` / `agent:` / `gcw:` tags get:
- `project:legacy`
- `agent:unknown`
- `gcw:legacy`

After migration, review and retag important entries:

```bash
mnemos search "project:legacy" --limit 50
```

## Post-migration checklist

- [ ] `mnemos stats` shows expected memory count
- [ ] `mnemos search "hello"` returns results
- [ ] Vault files visible in `~/mnemos-vault/`
- [ ] MCP server `mnemos_recall_context` works

## Rollback

If something goes wrong:

```bash
# Restore from Mnemos backup
ls ~/.mnemos/*.backup-*
cp ~/.mnemos/mnemos.db.backup-YYYYMMDD-HHMMSS ~/.mnemos/mnemos.db

# Or start fresh
rm -rf ~/.mnemos ~/mnemos-vault
```
