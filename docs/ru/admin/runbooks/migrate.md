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
2. Миграция всех записей с применением контракта тегов Mnemos в **lax mode**
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

## Миграция тегов `gcw:` → `mnemos:`

Если в хранилище есть записи с устаревшим префиксом тегов `gcw:<subtype>`
(от семейства агентов GCW до версии 2.7.8), переименуйте их массово в
канонический префикс `mnemos:<subtype>` безопасной командой `tags rename`:

```bash
# Сначала dry-run — предпросмотр, ничего не записывается (по умолчанию)
mnemos tags rename --from gcw: --to mnemos: --dry-run

# Применить переименование
mnemos tags rename --from gcw: --to mnemos: --no-dry-run
```

Замечания:
- `validate_tag_contract()` уже автоматически мигрирует валидные
  `gcw:<subtype>` → `mnemos:<subtype>` при чтении, поэтому теги `gcw:`
  принимаются как alias. Массовое переименование — разовая операция для
  канонизации хранящихся тегов.
- Неверные подтипы `gcw:` (не из whitelist) по умолчанию пропускаются и
  учитываются в `skipped_invalid`. Передайте `--invalid-to-legacy`, чтобы
  переименовать их в `mnemos:legacy` вместо пропуска.
- Операция **идемпотентна** — повторный запуск вернёт `renamed=0`.
- Устаревшая команда `mnemos migrate tags` теперь делегирует на этот
  безопасный путь и выдаёт предупреждение. Предпочитайте `mnemos tags rename`.

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
