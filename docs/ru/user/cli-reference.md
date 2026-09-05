# Справочник CLI

**🌐 Language / Язык:** [English](../../en/user/cli-reference.md) · Русский

> Полная справка по командной строке `mnemos`.

CLI — тонкая обёртка на Typer вокруг [`MemoryManager`](../architecture/overview.md#memorymanager). Использует Rich для вывода таблиц с цветами и является наиболее удобным способом работы с Mnemos из оболочки.

Полный набор субкоманд определён в `src/mnemos/cli/main.py`. Эта страница отражает то, что реально экспортирует источник — каждый пример здесь можно выполнить на чистой установке.

Пошаговое первое использование — в [getting-started.md](getting-started.md). Для программного доступа — [mcp-tools.md](mcp-tools.md) и [http-api.md](http-api.md).

---

## Синопсис

```text
mnemos [GLOBAL-OPTIONS] SUBCOMMAND [SUBCOMMAND-OPTIONS] [ARGS]
```

| Субкоманда | Назначение |
|------------|------------ |
| [`add`](#add) | Создать новую запись в памяти |
| [`search`](#search) | Гибридный поиск FTS5 + вектор |
| [`recall`](#recall) | Список последних записей, опционально по агенту / проекту |
| [`tags validate`](#tags-validate) | Проверить контракт тегов по всему vault |
| [`workflow`](#workflow) | Жизненный цикл записи: `get` / `set` / `history` |
| [`stats`](#stats) | Показать счётчики состояния |
| [`fts`](#fts) | Управление FTS5-индексом (`rebuild`) |
| [`processor`](#processor) | Управление фоновым конвейером: `status` / `run` / `start` / `stop` |
| [`reindex`](#reindex) | Переиндексация всех published-записей в векторном хранилище |
| [`filter`](#filter) | Запуск контекстного фильтра для записи |
| [`serve`](#serve) | Запустить HTTP API-сервер (FastAPI / Uvicorn) |
| [`mcp-server`](#mcp-server) | Запустить MCP stdio-сервер для VS Code Copilot |
| [`migrate from-ai-brain`](#migrate-from-ai-brain) | Однократный импорт из устаревшей установки `ai-brain` |
| [`auth`](#auth) | Bearer-токены (`auth token`) и TOTP 2FA (`auth totp`) |
| [`integration`](integration-guide.md) | Развёртывание / проверка слоя интеграции (отдельная страница) |
| [`completion`](#completion) | Установка shell-автодополнения (bash / zsh / fish) |
| [`doctor`](#doctor) | Диагностика установки (пути, конфиг, база, vault) |
| [`export`](export-import.md) | Экспорт записей в JSON / SQLite-бэкап (отдельная страница) |
| [`import`](export-import.md) | Импорт записей из файла экспорта (отдельная страница) |
| [`logs`](#logs) | Просмотр трассировок пайплайна |
| [`sync`](sync.md) | Пакетная federation-синхронизация: export / import (отдельная страница) |
| [`scanner`](#scanner) | Фоновый сканер секретов: `run` / `status` |

> Группа `tags` также предоставляет `tags normalize` и `tags rename` (массовое переименование префиксов с dry-run); `migrate tags` — устаревший алиас для `mnemos tags rename --from gcw: --to mnemos: --no-dry-run`.

---

## Глобальные опции

Большинство субкоманд принимают флаг `--config / -c` с путём к YAML-файлу. Порядок поиска:

1. Аргумент `--config` (если указан)
2. Переменная окружения `$MNEMOS_CONFIG`
3. `./config.yaml` в текущей рабочей директории
4. `~/.mnemos/config.yaml`

```bash
mnemos --help
mnemos add --help
```

Остальные глобальные флаги — только `--version / -V` (показать версию) и `--verbose / -v` (DEBUG-логирование для `mnemos serve` и `mnemos mcp-server`). Чтобы изменить уровень логирования на постоянной основе, задайте `logging.level` в конфиге или переменную окружения:

```bash
MNEMOS_LOGGING__LEVEL=DEBUG mnemos serve
```

---

## Переменные окружения

Все настройки переопределяются через переменные окружения с префиксом `MNEMOS_`. Вложенные ключи разделяются `__`.

| Переменная | По умолчанию | Назначение |
|------------|-------------|------------ |
| `MNEMOS_CONFIG` | — | Путь к `config.yaml` |
| `MNEMOS_MNEMOS__DATA_DIR` | `~/.mnemos/data` | БД SQLite + векторный индекс (каноническая форма) |
| `MNEMOS_DATA_DIR` *(устаревший алиас)* | `~/.mnemos/data` | Устаревший алиас `MNEMOS_MNEMOS__DATA_DIR` |
| `MNEMOS_MNEMOS__VAULT_PATH` | `~/.mnemos/vault` | Директория зеркала Obsidian (каноническая форма) |
| `MNEMOS_VAULT__VAULT_PATH` *(устаревший алиас)* | `~/.mnemos/vault` | Устаревший алиас `MNEMOS_MNEMOS__VAULT_PATH` |
| `MNEMOS_MNEMOS__STRICT_TAG_CONTRACT` | `true` | Соблюдение схемы тегов M2 |
| `MNEMOS_API__HOST` | `127.0.0.1` | Адрес по умолчанию для `mnemos serve` |
| `MNEMOS_API__PORT` | `8787` | Порт по умолчанию для `mnemos serve` |
| `MNEMOS_SEARCH__HYBRID_ALPHA` | `0.7` | Вес вектора в RRF-слиянии |
| `MNEMOS_EMBEDDING__PROVIDER` | `nano` | `nano` (mnema-embed-v1, встроенная) / `onnx` / `ollama` / `sentence-transformers` |
| `MNEMOS_LLM__PROVIDER` | `ollama` | LLM для синтеза и контекстного фильтра |
| `MNEMOS_LLM__MODEL` | `qwen2.5:3b` | Имя LLM-модели |
| `MNEMOS_AUTO_COLLECT` | `0` | Установите `1` для включения режима auto-collect MCP |
| `MNEMOS_LOGGING__LEVEL` | `INFO` | Уровень логирования Python |

> **Устаревшие алиасы.** `MNEMOS_DATA_DIR` и `MNEMOS_VAULT__VAULT_PATH` появились до вложенного именования `MNEMOS_MNEMOS__*` и сохранены для совместимости (#139). Работают обе формы. При конфликте каноническое имя переменной — как и явное значение в конфиг-файле — имеет приоритет над алиасом; алиас лишь заполняет пробел, который иначе достался бы значению по умолчанию.

---

## `add`

Создать новую запись в памяти.

```text
mnemos add [CONTENT] [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `CONTENT` (позиционный) | — | Текст для сохранения. Если не указан, читается из stdin. |
| `--title / -t` | авто | Краткий заголовок. Автогенерируется из контента, если не указан. |
| `--tags / -T` | `""` | Теги через запятую (напр. `project:test,agent:me,mnemos:learning`). |
| `--file / -f` | — | Импортировать содержимое файла. Взаимоисключающее с `CONTENT` и `--url`. |
| `--url / -u` | — | Получить и сохранить URL. Требует тегов. |
| `--source / -s` | `cli` | Источник записи: `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `--type` | `note` | Тип записи: `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `--dry-run` | `false` | Проверить теги и показать статистику контекстного фильтра без сохранения. |
| `--config / -c` | — | Путь к `config.yaml`. |

> **Контракт тегов.** Каждая запись должна иметь `project:<slug>`, `agent:<slug>` и хотя бы один `mnemos:<subtype>`. CLI соблюдает это в strict-режиме (по умолчанию). Полная схема — в [tag-contract.md](tag-contract.md).

### Примеры

```bash
# Встроенный контент
mnemos add "Use uv, not pip" --tags project:mnemos agent:tech-writer mnemos:learning

# С заголовком
mnemos add "Always validate SQL with parameterized queries" \
  --title "SQL safety rule" \
  --tags "project:mnemos,agent:security,mnemos:rule,severity:high"

# Из файла
mnemos add --file ~/notes/architecture.md --tags project:mnemos agent:tech-lead mnemos:decision

# Из URL (загружает, извлекает, сохраняет)
mnemos add --url https://example.com/article --tags project:research agent:user mnemos:learning

# Из stdin
echo "Pinned CVE-2026-45829 in chromadb 1.5.9" \
  | mnemos add --tags project:mnemos agent:sre mnemos:bug-pattern,severity:medium
```

---

## `search`

Гибридный поиск: FTS5 + вектор + Reciprocal Rank Fusion.

```text
mnemos search QUERY [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `QUERY` (позиционный) | — | Строка поиска на естественном языке. |
| `--limit / -l` | `10` | Максимум результатов. |
| `--project / -p` | — | Ограничить одним проектом. |
| `--tags / -T` | — | Теги для фильтрации, через запятую. |
| `--include-raw / --published-only` | `--include-raw` | Включать записи `raw`/`processing` (по умолчанию) или ограничиться `published`. |
| `--status` | — | Фильтр по статусу (`raw`/`processing`/`processed`/`published`/`archived`); имеет приоритет над `--include-raw`. |
| `--config / -c` | — | Путь к `config.yaml`. |

Score — это слитый RRF-скор: 0.0 = нет совпадений, 1.0 = первое место. По умолчанию ищутся и сырые записи — только что добавленная запись остаётся `raw`, пока конвейер знаний её не опубликует; используйте `--published-only`, чтобы ограничить выдачу областью векторного индекса.

### Примеры

```bash
# Простой поиск
mnemos search "embedding model"

# С фильтром по проекту
mnemos search "CVE" --project mnemos --limit 20

# Широкий поиск
mnemos search "decision" --limit 50
```

Для более широких возможностей запросов используйте HTTP API `POST /search` (см. [http-api.md#search](http-api.md#post-search--гибридный-поиск)).

---

## `recall`

Список последних записей, опционально ограниченный агентом (M3) и/или проектом.

```text
mnemos recall [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--project / -p` | — | Slug проекта для фильтрации. |
| `--agent / -a` | — | Slug агента для фильтрации. Активирует per-agent recall M3. |
| `--limit / -l` | `10` | Максимум результатов. |
| `--config / -c` | — | Путь к `config.yaml`. |

Когда `--agent` передан **без** запроса, результат — N последних записей этого агента, упорядоченных по `created_at desc`. Это те же данные, которые возвращает MCP-инструмент [`mnemos_agent_recall`](mcp-tools.md#mnemos_agent_recall).

### Примеры

```bash
# 10 последних записей для любого агента
mnemos recall

# Per-agent recall (M3)
mnemos recall --agent tech-writer

# Комбинированный
mnemos recall --agent sre --project mnemos --limit 25
```

---

## `tags validate`

Проверить контракт тегов Mnemos по всей существующей директории Mnemos vault. Сообщает о записях, нарушающих схему M2.

```text
mnemos tags validate VAULT_PATH
```

| Аргумент | Описание |
|----------|---------- |
| `VAULT_PATH` (позиционный) | Путь к директории Mnemos vault (зеркало в markdown). |

> **Статус.** Полная реализация сканирования vault ещё не подключена (`# TODO (M2): scan SQLite + vault markdown files`). Пока команда выводит заглушку. Для проверки тегов через SQLite используйте `mnemos stats` и HTTP API `GET /memories?project=...`.

### Пример

```bash
mnemos tags validate ~/.mnemos/vault
```

---

## `workflow`

Управление жизненным циклом записи через автомат состояний, контролируемый на стороне сервера (`open`, `in-progress`, `blocked`, `resolved`, `done`, `withdrawn`). Автомат и его guardrail живут в `MemoryManager`; CLI лишь превращает нарушения в красную строку ошибки и код выхода 1.

### `workflow get`

Показать текущий статус workflow и владельца блокировки для записи.

```text
mnemos workflow get MEMORY_ID
```

### `workflow set`

Перевести запись в новый статус workflow.

```text
mnemos workflow set MEMORY_ID --to STATUS --actor ACTOR [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `MEMORY_ID` (позиционный) | — | Id целевой записи. |
| `--to` | — (обязательная) | Целевой статус: `open`, `in-progress`, `blocked`, `resolved`, `done`, `withdrawn`. |
| `--actor` | — (обязательная) | Свободный идентификатор актора (Phase 1 weak identity). |
| `--reason` | `""` | Человекочитаемая причина. Обязательна вместе с `--force`. |
| `--force` | `false` | Перекрыть блокировку другого актора (требует `--reason`). |
| `--config / -c` | — | Путь к `config.yaml`. |

### `workflow history`

Показать журнал переходов workflow для записи (новые сверху).

```text
mnemos workflow history MEMORY_ID [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `MEMORY_ID` (позиционный) | — | Id целевой записи. |
| `--limit` | `50` | Максимум строк (новые сверху). |
| `--config / -c` | — | Путь к `config.yaml`. |

### Пример

```bash
ID=550e8400-e29b-41d4-a716-446655440000

mnemos workflow set "$ID" --to in-progress --actor tech-writer
mnemos workflow get "$ID"
mnemos workflow history "$ID" --limit 20
```

### Связанные ресурсы

- MCP-инструмент: [`mnemos_workflow`](mcp-tools.md#mnemos_workflow)

---

## `stats`

Показать счётчики состояния Mnemos и ключевые пути.

```text
mnemos stats [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--config / -c` | — | Путь к `config.yaml`. |

### Ключи вывода

| Ключ | Значение |
|------|--------- |
| `status` | Всегда `ok` (сигнал живости) |
| `version` | Версия Mnemos (сейчас `4.0.0`) |
| `data_dir` | Разрешённая директория данных |
| `vault_path` | Разрешённая директория vault |
| `total` | Общее количество записей (любой статус) |
| `by_status` | Словарь `raw` / `processing` / `processed` / `published` / `archived` |
| `vectors` | Количество векторов в локальном векторном индексе (`vectors.db`) |

### Пример

```bash
mnemos stats
# status: ok
# version: 4.0.0
# data_dir: /home/you/.mnemos/data
# vault_path: /home/you/.mnemos/vault
# total: 142
# by_status: {'raw': 5, 'processing': 0, 'processed': 12, 'published': 120, 'archived': 5}
# vectors: 120
```

---

## `fts`

Управление FTS5-индексом. Сейчас определено одно действие: `rebuild`.

```text
mnemos fts ACTION
```

| Аргумент | Описание |
|----------|---------- |
| `ACTION` (позиционный) | `rebuild` — пересобрать FTS5-индекс и сообщить число проиндексированных строк. Любое другое значение завершается ошибкой. |

### Пример

```bash
mnemos fts rebuild
# ✓ FTS5 index rebuilt: 142 rows indexed
```

---

## `processor`

Управление фоновым процессором (конвейером знаний): просмотр очереди, ручной проход, запуск и остановка фонового цикла.

```text
mnemos processor ACTION
```

| Аргумент | Описание |
|----------|---------- |
| `ACTION` (позиционный) | `status` — глубина очереди, время последней обработки, флаг запуска. `run` — один синхронный проход конвейера (cluster → synthesize → quality gate → publish). `start` — запустить фоновый процессор. `stop` — остановить. |

Сводка `run` сообщает счётчики `clusters`, `synthesized`, `published` и `failed_quality_gate`.

### Пример

```bash
mnemos processor run
#   clusters: 3
#   synthesized: 3
#   published: 2
#   failed_quality_gate: 1
```

### Связанные ресурсы

- HTTP-эквивалент: [`POST /process`](http-api.md#post-process--запустить-end-to-end-пайплайн)

---

## `reindex`

Пересобрать векторный индекс для всех published-записей — каждая запись `published` заново векторизуется и обновляется в `vectors.db`. Используйте после включения эмбеддингов или смены модели.

```text
mnemos reindex [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--batch-size / -b` | `100` | Размер батча эмбеддингов. |
| `--config / -c` | — | Путь к `config.yaml`. |

### Пример

```bash
mnemos reindex --batch-size 50
#   total: 120
#   indexed: 120
#   failed: 0
```

---

## `filter`

Запустить контекстный фильтр (M10) для записи и показать очищенный контент со статистикой сокращения. С `--all` фильтр перезапускается для всех записей с агрегированной сводкой.

```text
mnemos filter [MEMORY_ID] [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `MEMORY_ID` (позиционный) | — | Запись для фильтрации. Опустите при использовании `--all`. |
| `--profile / -p` | автоопределение | `log`, `terminal`, `code`, `docs`, `web` или `default`. |
| `--budget / -b` | — | Токенный бюджет для обрезки. |
| `--all` | `false` | Перезапустить фильтр для ВСЕХ записей; существующий `clean_content` перезаписывается свежим выводом фильтра. |
| `--config / -c` | — | Путь к `config.yaml`. |

> Повторная фильтрация с другим профилем даёт другой `clean_content`. Фильтр идемпотентен только при том же профиле.

### Пример

```bash
mnemos filter 550e8400-e29b-41d4-a716-446655440000 --profile terminal
# ✓ Filtered: 550e8400-e29b-41d4-a716-446655440000
#   profile: terminal
#   clean_content:
#   ...
```

### Связанные ресурсы

- [context-filter.md](context-filter.md) — профили, этапы конвейера, автофильтр
- MCP-инструмент: [`mnemos_filter`](mcp-tools.md#mnemos_filter)

---

## `serve`

Запустить HTTP API-сервер Mnemos (FastAPI / Uvicorn).

```text
mnemos serve [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--host` | `settings.api.host` (127.0.0.1) | Адрес привязки. |
| `--port` | `settings.api.port` (8787) | Порт привязки. |
| `--log-file` | — | Переопределить путь к лог-файлу из конфига; передача флага включает файловое логирование. |
| `--config / -c` | — | Путь к `config.yaml`. |

Сервер использует `uvicorn[standard]` (HTTP/1.1 + WebSockets). Количество воркеров берётся из `settings.runtime.uvicorn_workers`.

> **Безопасность.** Привязка по умолчанию — `127.0.0.1`. Не открывайте этот порт в публичную сеть без обратного прокси с аутентификацией. См. [security.md](../admin/security.md).

### Примеры

```bash
# Привязка по умолчанию
mnemos serve

# Привязка к локальной сети (dev-машина в домашней сети)
mnemos serve --host 0.0.0.0 --port 8000

# С кастомным конфигом
mnemos serve --host 127.0.0.1 --port 9000 --config /etc/mnemos/config.yaml

# Включить файловое логирование без правки конфига
mnemos serve --log-file ~/.mnemos/logs/serve.log
```

Полная поверхность HTTP API документирована в [http-api.md](http-api.md). Swagger UI доступен по адресу `http://HOST:PORT/docs`.

---

## `mcp-server`

Запустить MCP-сервер Mnemos через **stdio** для VS Code Copilot (или любого MCP-совместимого клиента).

```text
mnemos mcp-server [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--config / -c` | — | Путь к `config.yaml`. |

Сервер говорит на JSON-RPC 2.0 через stdin/stdout. TCP-порт отсутствует. Процесс блокируется до EOF или `Ctrl+C`.

### Примеры

```bash
# Прямой вызов (для отладки)
mnemos mcp-server

# С режимом auto-collect
MNEMOS_AUTO_COLLECT=1 mnemos mcp-server

# Из VS Code (сниппет mcp.json)
```

```jsonc
{
  "servers": {
    "mnemos": {
      "type": "stdio",
      "command": "mnemos",
      "args": ["mcp-server"]
    }
  }
}
```

Полный список инструментов — в [mcp-tools.md](mcp-tools.md), подключение к VS Code — в [getting-started.md#run-the-mcp-server](getting-started.md#подключите-ваш-харнес-mcp).

---

## `migrate from-ai-brain`

Однократная миграция с устаревшей установки `ai-brain` (M13).

```text
mnemos migrate from-ai-brain [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--source` | `~/.ai-brain` | Директория данных устаревшего ai-brain (должна содержать `ai_brain.db`). |
| `--vault` | `~/brain-vault` | Vault устаревшего ai-brain (зеркало Obsidian). |
| `--dry-run` | `false` | Показать что будет мигрировано, без записи. |
| `--config / -c` | — | Путь к `config.yaml`. |

Мигратор:

- Преобразует устаревшие значения `source` (напр. `telegram` → `mcp`).
- **Патчит контракт тегов** — каждая устаревшая запись получает `project:legacy`, `agent:unknown`, `mnemos:legacy`, если они отсутствуют.
- Сохраняет исходный `status` (`raw` / `processing` / `processed` / `published` / `archived`).
- Мигрирует столбцы `content_ru` / `content_en` в `metadata` (без потери данных).
- Мигрирует `parent_ids` в `metadata.parent_ids`.

### Примеры

```bash
# Сначала dry run (рекомендуется)
mnemos migrate from-ai-brain --dry-run

# Реальный запуск с путями по умолчанию
mnemos migrate from-ai-brain

# Из восстановления архива
mnemos migrate from-ai-brain --source /tmp/restore/.ai-brain --vault /tmp/restore/brain-vault
```

Вывод — однострочная сводка:

```text
✓ Memories migrated: 1 247
✓ Vault files migrated: 1 247
```

При наличии `Errors: N` список `summary.errors` (выводится в stderr на уровне DEBUG) укажет, какие строки упали. Как правило, это строки с повреждённой схемой — их можно игнорировать или исправить вручную в SQLite.

---

## `auth`

Управление API-токенами и TOTP 2FA (ADR-0014). Две подгруппы: `auth token` (bearer-токены) и `auth totp` (второй фактор). Секреты токенов хранятся хешированными в SQLite рядом с записями памяти.

### `auth token create`

Выпустить новый bearer-токен и показать его **один раз**.

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--name / -n` | — | Человекочитаемая метка. |
| `--expires / -e` | — | Срок в ISO-8601, напр. `2027-01-01`. Даты без часового пояса нормализуются к UTC. |
| `--no-totp` | `false` | Создать токен, пригодный к использованию напрямую как bearer без flow login/verify/session (устанавливает `totp_required=false`). По умолчанию токены требуют TOTP. |
| `--config / -c` | — | Путь к `config.yaml`. |

### `auth token list`

Список всех токенов — только id и метаданные, никогда секреты.

### `auth token revoke TOKEN_ID`

Безвозвратно отозвать токен (позиционный аргумент `TOKEN_ID`).

### `auth totp`

| Субкоманда | Обязательные опции | Назначение |
|------------|--------------------|----------- |
| `enroll` | `--token-id` | Сгенерировать TOTP-секрет и вывести provisioning URI + ASCII QR (если доступен). Требует `MNEMOS_API__TOTP_MASTER_KEY` для шифрования секрета. |
| `disable` | `--token-id` | Удалить TOTP-секрет у токена (отключает 2FA для него). |
| `test` | `--token-id`, `--code` | Проверить 6-значный код против сохранённого секрета (smoke-тест для оператора). |

### Пример

```bash
mnemos auth token create --name "laptop" --expires 2027-01-01
# ✓ Token created:
#   token_id : 7c9e6679-7425-40de-944b-e07fc1f90ae7
#   bearer   : <открытый токен — сохраните сейчас, повторно он не показывается>
```

---

## `completion`

Установить shell-автодополнение для CLI `mnemos`. Без аргументов оболочка определяется автоматически из `$SHELL`, скрипт дополнения записывается в `~/.mnemos/completion/mnemos.<shell>`, а в rc-файл добавляется одна защищённая строка `source` (`~/.bashrc` / `~/.zshrc`; fish автоматически подхватывает свою директорию дополнений). Идемпотентно — повторный запуск не дублирует строку source и мигрирует со старого формата на `eval`.

```text
mnemos completion [SHELL] [OPTIONS]
```

| Аргумент / опция | По умолчанию | Описание |
|------------------|--------------|---------- |
| `SHELL` (позиционный) | авто из `$SHELL` | `bash`, `zsh` или `fish`. |
| `--show-instructions` | `false` | Показать шаги ручной установки для всех поддерживаемых оболочек; файлы не изменяются. |

### Пример

```bash
mnemos completion bash
# ✓ Installed bash completion → /home/you/.mnemos/completion/mnemos.bash
#   Source line added to /home/you/.bashrc
#   Restart your shell or run: source /home/you/.bashrc
```

---

## `doctor`

Проверки состояния Mnemos: конфигурация, директория данных, vault, БД SQLite, векторное хранилище, MCP-сервер, слой интеграции, подключение агентов, контракт тегов.

```text
mnemos doctor [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--json` | `false` | Вывести результаты в JSON (для скриптов / CI) вместо таблицы. |
| `--fix` | `false` | Автоматически исправлять проверки уровня WARN (устаревшая интеграция, неподключённые агенты, отсутствие MCP-регистрации). Проверки уровня FAIL автоматически не исправляются. |
| `--dry-run` | `false` | Вместе с `--fix`: показать, что было бы исправлено, без выполнения. |
| `--paths` | `false` | Вывести все разрешённые пути (data, vault, logs, cache, completion) и выйти. |

Коды выхода: `0` — все проверки пройдены, `1` — одна или несколько провалены, `2` — только предупреждения.

> У `doctor` нет опции `--config`; конфиг читается из `$MNEMOS_CONFIG` или стандартного пути поиска (`./config.yaml`, `~/.mnemos/config.yaml`).

### `doctor --paths`

Показывает все пути, которые использует Mnemos, разрешённые из конфига и окружения:

```bash
mnemos doctor --paths
# data_dir:      /home/you/.mnemos/data
# vault_path:    /home/you/.mnemos/vault
# log_file:      /home/you/.mnemos/logs/mnemos.log
# cache_dir:     /home/you/.mnemos/cache
# completion:    /home/you/.mnemos/completion
# config_file:   /home/you/.mnemos/config.yaml
```

Используйте для проверки консолидированной структуры `~/.mnemos/` после обновления или миграции.

### `doctor --fix` и `--dry-run`

С `--fix` проверки уровня WARN исправляются на месте (устаревшая интеграция → `integration update`, неподключённые агенты → `integration setup --wire-agents --all`, отсутствие MCP-регистрации → MCP setup); затем затронутые проверки запускаются повторно, и сообщается новый статус. Комбинация с `--dry-run` показывает предполагаемые исправления без их выполнения. `--json --fix` добавляет списки `fixed` / `fix_skipped` в JSON-вывод.

```bash
# Только предпросмотр
mnemos doctor --fix --dry-run

# Применить исправления
mnemos doctor --fix

# CI: машиночитаемый вердикт, без исправлений
mnemos doctor --json
```

---

## `logs`

Просмотр трассировок пайплайна (M6, слой объяснимости) — компактная таблица поверх append-only таблицы `traces`.

```text
mnemos logs [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--task / -t` | — | Фильтр по метке задачи (`cluster`, `synthesize`, `publish`, `recall`). |
| `--project / -p` | — | Фильтр по slug проекта. |
| `--limit / -l` | `50` | Максимум показанных трассировок. |
| `--since` | — | Только трассировки после этой ISO-даты (напр. `2026-06-01`). |
| `--follow / -f` | `false` | Опрашивать новые строки раз в 2 с (в стиле `tail -f`). Останов — `Ctrl+C`. |
| `--config / -c` | — | Путь к `config.yaml`. |

### Пример

```bash
mnemos logs --task cluster --project mnemos --limit 20

# Наблюдать конвейер вживую
mnemos logs --follow
```

### Связанные ресурсы

- HTTP-эквивалент: [`GET /traces`](http-api.md#get-traces--список-трассировок-пайплайна)

---

## `scanner`

Фоновый сканер секретов — слой 2 эшелонированной защиты federation. Сканер периодически пересканирует корпус на секреты, пропущенные при записи, и автоматически ставит совпадениям тег `mnemos:no-federate`, исключая их из любого внешнего обмена. Эти субкоманды — ручной запуск и просмотр состояния.

### `scanner run`

Синхронно выполнить один проход сканера и вывести сводку.

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--full` | `false` | Принудительный полный проход по корпусу (игнорировать инкрементальную границу). По умолчанию проход инкрементальный: только записи, изменённые с прошлого успешного прохода. |
| `--config / -c` | — | Путь к `config.yaml`. |

Сводка сообщает `records_scanned`, `records_tagged`, `records_skipped`, `duration_sec`, имена сработавших шаблонов со счётчиками (никогда сами значения) и метку времени.

### `scanner status`

Показать текущее состояние сканера — enabled, running, настроенные интервал и инкрементальный режим, время последнего прохода, суммарное число помеченных записей, следующий плановый запуск.

### Пример

```bash
mnemos scanner run --full
# ✓ Scan complete (full)
#   records_scanned: 142
#   records_tagged:   0
#   records_skipped:  2
#   duration_sec:     1.83
#   patterns_matched: (none)
#   timestamp:        2026-09-05T12:00:00+00:00
```

### Связанные ресурсы

- [sync.md](sync.md#исключение-mnemosno-federate) — что исключает `mnemos:no-federate`

---

## Коды выхода

| Код | Значение |
|-----|--------- |
| 0 | Успех |
| 1 | Ошибка пользователя (отсутствующий аргумент, неверный тег и т.п.) |
| 2 | `mnemos doctor`: предупреждения, ничего не сломано |

CLI не возвращает ненулевой код при «нет результатов» — `mnemos search` завершается с кодом 0 и пустой таблицей.

---

## См. также

- [getting-started.md](getting-started.md) — первое использование
- [mcp-tools.md](mcp-tools.md) — те же возможности через MCP
- [http-api.md](http-api.md) — те же возможности через HTTP
- [context-filter.md](context-filter.md) — профили фильтра, используемые `add --dry-run` и `filter`
- [tag-contract.md](tag-contract.md) — схема тегов, соблюдаемая здесь
- [runbooks/migrate.md](../admin/runbooks/migrate.md) — операционное руководство по миграции
- [обзор архитектуры](../architecture/overview.md) — структура системы

---

_Последнее обновление: 2026-09-05_
