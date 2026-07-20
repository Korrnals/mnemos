<!-- mnemos-integration: v2.0.0 -->
# Федерация — пакетная синхронизация (Phase 0)

**🌐 Language / Язык:** English · [Русский](./sync.md)

> Кураторская, офлайн, cron-управляемая пакетная синхронизация между
> двумя инстансами mnemos. Сам mnemos не делает сетевых вызовов —
> перенос файла выполняется оператором (rsync / scp / общая директория
> через `scripts/sync-peers.sh`).

---

## Обзор

Пакетная синхронизация позволяет двум инстансам mnemos обмениваться
записями проектов из курируемого списка **shared_projects**. Поток:

1. **Экспорт** — `mnemos sync export` собирает `mnemos.federation.v1`
   compact-payload из записей проектов в `shared_projects`, пропускает
   каждую через moderation-pipeline и записывает результат в файл
   (опционально AES-256-GCM шифрование).
2. **Перенос** — оператор копирует файл на целевой инстанс (rsync / scp
   / cp через общую директорию). `scripts/sync-peers.sh` — cron-ready
   шаблон, объединяющий все три шага.
3. **Импорт** — `mnemos sync import` читает compact-payload
   (расшифровывая при необходимости), валидирует каждую запись и
   мержит идемпотентно по `id` записи.

Это **Phase 0** roadmap'а федерации (ArchCom 2026-07-17 контракт §3.1):
офлайн, оператор-управляемый, без живого сетевого протокола. Phase 2
(mediated pull) строится поверх того же compact-формата и moderation.

---

## Конфигурация

Пакетная синхронизация управляется секцией `federation` в `config.yaml`:

```yaml
federation:
  shared_projects:
    - project-umbra
    - project-mnemos
  moderation_mapping_ttl_hours: 24   # TTL in-memory mapping таблицы
  moderation_refuse_threshold: 0.8   # >80% redacted → refuse
```

| Поле | По умолчанию | Назначение |
|------|--------------|------------|
| `shared_projects` | `[]` (пусто) | Whitelist slug'ов проектов, доступных для синхронизации. Пусто = ничего не синхронизируется. |
| `moderation_mapping_ttl_hours` | `24` | TTL per-run mapping таблицы moderation (только in-memory, не персистится). |
| `moderation_refuse_threshold` | `0.8` | Доля контента, которая должна быть redacted/anonymized для вердикта `refuse`. |

Можно переопределить `shared_projects` на один запуск через
`--shared-projects` (через пробел или запятую) — CLI-значение важнее
конфига.

---

## Экспорт — `mnemos sync export`

```bash
mnemos sync export \
  --output /var/tmp/mnemos-sync.json \
  --shared-projects "project-umbra project-mnemos"
```

Опции:

| Опция | По умолчанию | Назначение |
|-------|--------------|------------|
| `--output` / `-o` | `mnemos-sync.json` | Путь выходного файла (рекомендуется абсолютный). Родительские директории создаются. |
| `--encrypt` | выкл | Шифровать payload через AES-256-GCM. Пароль читается из `MNEMOS_EXPORT_PASSPHRASE`. |
| `--shared-projects` | config `federation.shared_projects` | Список slug'ов через пробел/запятую (переопределяет конфиг). |
| `--dry-run` | выкл | Собрать payload и вывести сводку; файл НЕ записывать. |
| `--config` / `-c` | discovery | Путь к `config.yaml`. |

Что делает экспорт:

1. Разрешает `shared_projects` (CLI > конфиг; оба пусты → ошибка).
2. Запрашивает записи: `project` в `shared_projects`, исключает
   `mnemos:no-federate` и `archived`.
3. Вызывает `build_compact_payload()` — прогоняет moderation-pipeline
   (Layer 3) на каждой записи: `allow` → оригинальный контент,
   `redact` → sanitized-контент, `refuse` → запись исключается и
   учитывается в счётчике.
4. Записывает compact-payload
   (`{"schema": "mnemos.federation.v1", "records": [...], "stats": {...}}`)
   в `--output`, опционально зашифрованным.

Сводка вывода:

```
✓ Exported: 12 records
  refused: 1
  secrets_redacted: 3
  pii_anonymized: 2
  encrypted: false
  shared_projects: project-umbra, project-mnemos
  path: /var/tmp/mnemos-sync.json
```

### Шифрование

`--encrypt` читает пароль из переменной окружения
`MNEMOS_EXPORT_PASSPHRASE` — никогда из CLI-аргумента (аргументы попадают
в список процессов и историю shell). Если переменная не задана, файл не
записывается, команда завершается с ошибкой.

```bash
export MNEMOS_EXPORT_PASSPHRASE="your-passphrase-here"
mnemos sync export --output sync.enc --encrypt
```

Зашифрованный файл несёт magic-заголовок `MNEMOS1`, чтобы сторона
импорта могла его автоматически определить.

---

## Импорт — `mnemos sync import`

```bash
mnemos sync import /var/tmp/mnemos-sync.json
```

Опции:

| Опция | По умолчанию | Назначение |
|-------|--------------|------------|
| `--passphrase-env` | `MNEMOS_EXPORT_PASSPHRASE` | Имя переменной окружения с паролем для расшифровки (**имя**, не значение). |
| `--dry-run` | выкл | Провалидировать payload и вывести отчёт; НЕ записывать. |
| `--config` / `-c` | discovery | Путь к `config.yaml`. |

Что делает импорт:

1. Читает файл. Если зашифрован (magic-заголовок или расширение `.enc`)
   — читает пароль из переменной, названной `--passphrase-env` (fallback
   на `MNEMOS_EXPORT_PASSPHRASE`).
2. Парсит JSON, проверяет `schema == "mnemos.federation.v1"`, парсит
   каждую запись в `CompactRecord`.
3. Валидирует каждую запись (переиспользует #86 import validation —
   длина контента, tag contract, длина title, schema drift,
   prompt-injection warnings). При любой ошибке **весь батч
   отвергается** (без частичных записей).
4. Мержит идемпотентно по `id` записи
   (`fed:<source_agent>:<uuid>`): существующие записи **пропускаются**
   (не перезаписываются); новые создаются с `MemorySource.MCP`.

Сводка вывода:

```
✓ Imported: 11 records
  skipped: 1
  format_version: mnemos.federation.v1
```

### Идемпотентность

Повторный импорт того же файла безопасен: каждая запись несёт
`fed:<source_agent>:<uuid>` id. Второй импорт находит каждую запись уже
существующей и пропускает — без дубликатов, без перезаписей. Это делает
cron-синхронизацию безопасной для повторных запусков.

---

## `scripts/sync-peers.sh` — cron-шаблон

Cron-ready shell-шаблон, объединяющий экспорт → перенос → импорт. Задай
переменные окружения и запусти. Без конфигурации не исполняется.

Обязательные переменные:

| Переменная | Назначение |
|------------|------------|
| `SOURCE_MNEMOS_DIR` | Путь к исходному репо mnemos (с `.venv`). |
| `TARGET_MNEMOS_DIR` | Путь к целевому репо mnemos (с `.venv`). |
| `SHARED_PROJECTS` | Список slug'ов проектов через пробел. |

Опциональные переменные:

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `ENCRYPT` | `0` | `1` для шифрования экспорта. |
| `MNEMOS_EXPORT_PASSPHRASE` | — | Требуется при `ENCRYPT=1`. |
| `TRANSFER_METHOD` | `cp` | `rsync` / `scp` / `cp`. |
| `TRANSFER_DEST_HOST` | — | Целевой хост для `rsync`/`scp`. |
| `SYNC_FILE` | `/tmp/mnemos-sync-<ts>.json` | Путь файла экспорта. |
| `DRY_RUN` | `0` | `1` для end-to-end dry-run. |
| `SOURCE_CONFIG` / `TARGET_CONFIG` | discovery | Путь `config.yaml` для каждой стороны. |

Пример crontab (почасовая зашифрованная синхронизация на peer через scp):

```cron
0 * * * * SOURCE_MNEMOS_DIR=/opt/mnemos-a TARGET_MNEMOS_DIR=/opt/mnemos-b \
          SHARED_PROJECTS="project-umbra project-mnemos" \
          MNEMOS_EXPORT_PASSPHRASE="$PASS" ENCRYPT=1 \
          TRANSFER_METHOD=scp TRANSFER_DEST_HOST=peer.example.com \
          /opt/mnemos-a/scripts/sync-peers.sh >> /var/log/mnemos-sync.log 2>&1
```

Для `rsync`/`scp` на удалённый peer скрипт выводит точную команду
`mnemos sync import`, которую нужно запустить на peer (он не может
ssh-ить и запустить целевой venv сам). Для `cp` (локальная синхронизация
на том же хосте) скрипт выполняет шаг импорта напрямую.

---

## Audit-лог

Каждый `mnemos sync export` и `mnemos sync import` дописывает одну
JSONL-запись в `~/.mnemos/logs/sync-audit.jsonl`. Лог append-only —
`tail -f` для мониторинга, `jq` для агрегатов, или отправка в SIEM.

Формат записей (только **счётчики** — без сырого контента, секретов, PII):

```json
{"timestamp": "2026-07-19T10:00:00Z", "action": "sync-export", "output": "/var/tmp/mnemos-sync.json", "records_exported": 12, "records_refused": 1, "secrets_redacted": 3, "pii_anonymized": 2, "encrypted": false, "shared_projects": ["project-umbra", "project-mnemos"]}
{"timestamp": "2026-07-19T10:05:00Z", "action": "sync-import", "source": "/var/tmp/mnemos-sync.json", "records_imported": 11, "records_skipped": 1, "errors": [], "warnings": [], "encrypted": false, "format_version": "mnemos.federation.v1"}
```

Audit-лог — операционный след: какие проекты синхронизировались, сколько
записей экспортировано / refused / redacted, какие импорты упали. **Сырой
контент и значения секретов никогда не попадают в audit-лог** — только
счётчики, пути и статус-флаги.

---

## Исключение `mnemos:no-federate`

Записи с тегом `mnemos:no-federate` целиком исключаются из экспорта
синхронизации. Тег автоматически добавляется при записи сканером Layer 1
(#86), когда детектируется секретный паттерн; владелец может снять его
с явным подтверждением через `MemoryManager.remove_no_federate()`. См.
[Tag Contract — `mnemos:no-federate`](./tag-contract.md#mnemosno-federate--federation-exclusion-marker)
для полного lifecycle.

Даже без тега moderation-pipeline (Layer 3) прогоняет каждую запись при
экспорте и отказывает записям, чей контент почти полностью
secrets/PII — defence-in-depth, чтобы один пропущенный слой не утёк
секрет. См. [Security — Federation defence-in-depth](../admin/security.md#11-federation-defence-in-depth).

---

## См. также

- [Export & Import](./export-import.md) — полные бэкапы (JSON / SQLite).
- [Security — Federation defence-in-depth](../admin/security.md#11-federation-defence-in-depth) — трёхслойная модель.
- [Tag Contract — `mnemos:no-federate`](./tag-contract.md#mnemosno-federate--federation-exclusion-marker) — маркер исключения.
- [MCP Tools](./mcp-tools.md) — `mnemos_export` / `mnemos_import` MCP-инструменты (MCP-поверхность для полного export/import).