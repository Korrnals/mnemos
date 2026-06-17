# Runbook: Резервное копирование и восстановление

**🌐 Language / Язык:** [English](../../../en/admin/runbooks/backup-restore.md) · Русский

## Резервное копирование

### Полная резервная копия

```bash
# Данные Mnemos + vault
tar czf mnemos-backup-$(date +%Y%m%d).tar.gz \
  ~/.mnemos \
  ~/mnemos-vault
```

### Автоматизация (cron)

```bash
# Ежедневное резервное копирование в 02:00
0 2 * * * tar czf ~/backups/mnemos-$(date +\%Y\%m\%d).tar.gz ~/.mnemos ~/mnemos-vault
```

## Восстановление

```bash
# Остановить MCP-сервер / API, если запущены

# Распаковать резервную копию
tar xzf mnemos-backup-20260115.tar.gz -C /

# Или выборочное восстановление
cp mnemos-backup-20260115/.mnemos/mnemos.db ~/.mnemos/
rsync -a mnemos-backup-20260115/mnemos-vault/ ~/mnemos-vault/
```

## Восстановление на момент времени

Mnemos автоматически создаёт резервные копии БД перед миграциями:

```bash
ls ~/.mnemos/*.backup-*
# ~/.mnemos/mnemos.db.backup-20260115-143022

cp ~/.mnemos/mnemos.db.backup-20260115-143022 ~/.mnemos/mnemos.db
```

## Экспорт / Импорт

### Экспорт в JSON

```bash
python -c "
import json, sqlite3
conn = sqlite3.connect('~/.mnemos/mnemos.db')
rows = conn.execute('SELECT * FROM memories').fetchall()
print(json.dumps([dict(r) for r in rows], indent=2, default=str))
" > mnemos-export.json
```

### Импорт из JSON

Используйте `mnemos add --file` или API `POST /memories` для массового импорта.
