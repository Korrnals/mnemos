# Runbook: Миграция с ai-brain

**🌐 Language / Язык:** [English](../../../en/admin/runbooks/migrate.md) · Русский

## Обзор

Перенос данных из ai-brain (SQLite DB + vault) в формат Mnemos.

## Перед началом

1. **Создайте резервную копию данных ai-brain**:
   ```bash
   cp -r ~/.ai-brain ~/.ai-brain.backup-$(date +%Y%m%d)
   cp -r ~/brain-vault ~/brain-vault.backup-$(date +%Y%m%d)
   ```

2. **Установите Mnemos** (см. `install.md`).

## Тестовый прогон

Всегда запускайте dry-run первым, чтобы увидеть, что будет перенесено:

```bash
mnemos migrate-from-ai-brain --dry-run
```

Вывод показывает:
- Количество записей памяти для миграции
- Количество файлов vault для копирования
- Ожидаемые ошибки

## Полная миграция

```bash
mnemos migrate-from-ai-brain
```

Действия:
1. Резервное копирование существующей БД Mnemos (при наличии)
2. Миграция всех записей с применением контракта тегов GCW в **lax mode**
3. Копирование файлов vault с сохранением структуры каталогов
4. Маппинг источников ai-brain → источники Mnemos (telegram → mcp)

## Обработка контракта тегов

Унаследованные записи ai-brain без тегов `project:` / `agent:` / `mnemos:` получают:
- `project:legacy`
- `agent:unknown`
- `mnemos:legacy`

После миграции просмотрите и перетегируйте важные записи:

```bash
mnemos search "project:legacy" --limit 50
```

## Чеклист после миграции

- [ ] `mnemos stats` показывает ожидаемое количество записей
- [ ] `mnemos search "hello"` возвращает результаты
- [ ] Файлы vault видны в `~/.mnemos/vault/`
- [ ] MCP-команда `mnemos_recall_context` работает

## Откат

Если что-то пошло не так:

```bash
# Восстановление из резервной копии Mnemos
ls ~/.mnemos/data/*.backup-*
cp ~/.mnemos/data/mnemos.db.backup-YYYYMMDD-HHMMSS ~/.mnemos/data/mnemos.db

# Или начать заново
rm -rf ~/.mnemos/data ~/.mnemos/vault
```
