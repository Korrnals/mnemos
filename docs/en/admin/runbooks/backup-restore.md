# Runbook: Backup & Restore

**🌐 Language / Язык:** English · [Русский](../../../ru/admin/runbooks/backup-restore.md)

## Backup

### Full backup

```bash
# Mnemos data + vault
tar czf mnemos-backup-$(date +%Y%m%d).tar.gz \
  ~/.mnemos \
  ~/mnemos-vault
```

### Automated (cron)

```bash
# Daily backup at 02:00
0 2 * * * tar czf ~/backups/mnemos-$(date +\%Y\%m\%d).tar.gz ~/.mnemos ~/mnemos-vault
```

## Restore

```bash
# Stop MCP server / API if running

# Extract backup
tar xzf mnemos-backup-20260115.tar.gz -C /

# Or selective restore
cp mnemos-backup-20260115/.mnemos/mnemos.db ~/.mnemos/
rsync -a mnemos-backup-20260115/mnemos-vault/ ~/mnemos-vault/
```

## Point-in-time recovery

Mnemos creates automatic DB backups before migrations:

```bash
ls ~/.mnemos/*.backup-*
# ~/.mnemos/mnemos.db.backup-20260115-143022

cp ~/.mnemos/mnemos.db.backup-20260115-143022 ~/.mnemos/mnemos.db
```

## Export / Import

### Export to JSON

```bash
python -c "
import json, sqlite3
conn = sqlite3.connect('~/.mnemos/mnemos.db')
rows = conn.execute('SELECT * FROM memories').fetchall()
print(json.dumps([dict(r) for r in rows], indent=2, default=str))
" > mnemos-export.json
```

### Import from JSON

Use `mnemos add --file` or API `POST /memories` for bulk import.
