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
| [`tags-validate`](#tags-validate) | Проверить контракт тегов по всему vault |
| [`stats`](#stats) | Показать счётчики состояния |
| [`serve`](#serve) | Запустить HTTP API-сервер (FastAPI / Uvicorn) |
| [`mcp-server`](#mcp-server) | Запустить MCP stdio-сервер для VS Code Copilot |
| [`migrate-from-ai-brain`](#migrate-from-ai-brain) | Однократный импорт из устаревшей установки `ai-brain` |

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

Других глобальных флагов нет — у Mnemos нет ключа `verbose`; вместо этого поднимайте уровень логирования Python:

```bash
MNEMOS_LOG_LEVEL=DEBUG mnemos search "test"
```

---

## Переменные окружения

Все настройки переопределяются через переменные окружения с префиксом `MNEMOS_`. Вложенные ключи разделяются `__`.

| Переменная | По умолчанию | Назначение |
|------------|-------------|------------ |
| `MNEMOS_CONFIG` | — | Путь к `config.yaml` |
| `MNEMOS_DATA_DIR` | `~/.mnemos` | БД SQLite + векторный индекс |
| `MNEMOS_VAULT__VAULT_PATH` | `~/.mnemos/vault` | Директория зеркала Obsidian |
| `MNEMOS_STRICT_TAG_CONTRACT` | `true` | Соблюдение схемы тегов M2 |
| `MNEMOS_API__HOST` | `127.0.0.1` | Адрес по умолчанию для `mnemos serve` |
| `MNEMOS_API__PORT` | `8787` | Порт по умолчанию для `mnemos serve` |
| `MNEMOS_SEARCH__HYBRID_ALPHA` | `0.7` | Вес вектора в RRF-слиянии |
| `MNEMOS_EMBEDDING__PROVIDER` | `chromadb` | `chromadb` / `onnx` / `ollama` / `sentence-transformers` |
| `MNEMOS_LLM__PROVIDER` | `ollama` | LLM для синтеза и контекстного фильтра |
| `MNEMOS_LLM__MODEL` | `qwen2.5:3b` | Имя LLM-модели |
| `MNEMOS_AUTO_COLLECT` | `0` | Установите `1` для включения режима auto-collect MCP |
| `MNEMOS_LOG_LEVEL` | `INFO` | Уровень логирования Python |

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
| `--tags / -T` | `""` | Теги через запятую (напр. `project:test,agent:me,gcw:learning`). |
| `--file / -f` | — | Импортировать содержимое файла. Взаимоисключающее с `CONTENT` и `--url`. |
| `--url / -u` | — | Получить и сохранить URL. Требует тегов. |
| `--source / -s` | `cli` | Источник записи: `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `--type` | `note` | Тип записи: `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `--config / -c` | — | Путь к `config.yaml`. |

> **Контракт тегов.** Каждая запись должна иметь `project:<slug>`, `agent:<slug>` и хотя бы один `gcw:<subtype>`. CLI соблюдает это в strict-режиме (по умолчанию). Полная схема — в [tag-contract.md](tag-contract.md).

### Примеры

```bash
# Встроенный контент
mnemos add --content "Use uv, not pip" --tags project:mnemos agent:tech-writer gcw:learning

# С заголовком
mnemos add "Always validate SQL with parameterized queries" \
  --title "SQL safety rule" \
  --tags "project:mnemos,agent:security,gcw:rule,severity:high"

# Из файла
mnemos add --file ~/notes/architecture.md --tags project:mnemos agent:tech-lead gcw:decision

# Из URL (загружает, извлекает, сохраняет)
mnemos add --url https://example.com/article --tags project:research agent:user gcw:learning

# Из stdin
echo "Pinned CVE-2026-45829 in chromadb 1.5.9" \
  | mnemos add --tags project:mnemos agent:sre gcw:bug-pattern,severity:medium
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
| `--config / -c` | — | Путь к `config.yaml`. |

Score — это слитый RRF-скор: 0.0 = нет совпадений, 1.0 = первое место. Поиск учитывает только записи в статусе `published` (область индекса по умолчанию).

### Примеры

```bash
# Простой поиск
mnemos search "embedding model"

# С фильтром по проекту
mnemos search "CVE" --project mnemos --limit 20

# Широкий поиск
mnemos search "decision" --limit 50
```

Для поиска с фильтром по тегам или по сырому контенту используйте HTTP API `POST /search` (см. [http-api.md#search](http-api.md#search)).

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

## `tags-validate`

Проверить контракт тегов GCW по всей существующей директории Mnemos vault. Сообщает о записях, нарушающих схему M2.

```text
mnemos tags-validate VAULT_PATH
```

| Аргумент | Описание |
|----------|---------- |
| `VAULT_PATH` (позиционный) | Путь к директории Mnemos vault (зеркало в markdown). |

> **Статус.** Полная реализация сканирования vault ещё не подключена (`# TODO (M2): scan SQLite + vault markdown files`). Пока команда выводит заглушку. Для проверки тегов через SQLite используйте `mnemos stats` и HTTP API `GET /memories?project=...`.

### Пример

```bash
mnemos tags-validate ~/.mnemos/vault
```

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
| `version` | Версия Mnemos (сейчас `0.1.0`) |
| `data_dir` | Разрешённая директория данных |
| `vault_path` | Разрешённая директория vault |
| `total` | Общее количество записей (любой статус) |
| `by_status` | Словарь `raw` / `processing` / `processed` / `published` / `archived` |
| `vectors` | Количество векторов в индексе ChromaDB |

### Пример

```bash
mnemos stats
# status: ok
# version: 0.1.0
# data_dir: /home/you/.mnemos
# vault_path: /home/you/.mnemos/vault
# total: 142
# by_status: {'raw': 5, 'processing': 0, 'processed': 12, 'published': 120, 'archived': 5}
# vectors: 120
```

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

Полный список инструментов — в [mcp-tools.md](mcp-tools.md), подключение к VS Code — в [getting-started.md#run-the-mcp-server](getting-started.md#run-the-mcp-server).

---

## `migrate-from-ai-brain`

Однократная миграция с устаревшей установки `ai-brain` (M13).

```text
mnemos migrate-from-ai-brain [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--source` | `~/.mnemos` | Директория данных Mnemos (должна содержать `mnemos.db`). |
| `--vault` | `~/.mnemos/vault` | Директория vault Mnemos (зеркало Obsidian). |
| `--dry-run` | `false` | Показать что будет мигрировано, без записи. |
| `--config / -c` | — | Путь к `config.yaml`. |

Мигратор:

- Преобразует устаревшие значения `source` (напр. `telegram` → `mcp`).
- **Патчит контракт тегов** — каждая устаревшая запись получает `project:legacy`, `agent:unknown`, `gcw:legacy`, если они отсутствуют.
- Сохраняет исходный `status` (`raw` / `processing` / `processed` / `published` / `archived`).
- Мигрирует столбцы `content_ru` / `content_en` в `metadata` (без потери данных).
- Мигрирует `parent_ids` в `metadata.parent_ids`.

### Примеры

```bash
# Сначала dry run (рекомендуется)
mnemos migrate-from-ai-brain --dry-run

# Реальный запуск с путями по умолчанию
mnemos migrate-from-ai-brain

# Из восстановления архива
mnemos migrate-from-ai-brain --source /tmp/restore/.ai-brain --vault /tmp/restore/brain-vault
```

Вывод — однострочная сводка:

```text
✓ Memories migrated: 1 247
✓ Vault files migrated: 1 247
```

При наличии `Errors: N` список `summary.errors` (выводится в stderr на уровне DEBUG) укажет, какие строки упали. Как правило, это строки с повреждённой схемой — их можно игнорировать или исправить вручную в SQLite.

---

## `doctor`

Диагностика установки Mnemos — проверяет пути, конфигурацию, базу данных и vault.

```text
mnemos doctor [OPTIONS]
```

| Опция | По умолчанию | Описание |
|-------|-------------|---------- |
| `--paths` | `false` | Вывести все разрешённые пути (data, vault, logs, cache, completion) и выйти. |
| `--config / -c` | — | Путь к `config.yaml`. |

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

---

## Коды выхода

| Код | Значение |
|-----|--------- |
| 0 | Успех |
| 1 | Ошибка пользователя (отсутствующий аргумент, неверный тег и т.п.) |
| 2 | Сбой запуска сервера Uvicorn / stdio |

CLI не возвращает ненулевой код при «нет результатов» — `mnemos search` завершается с кодом 0 и пустой таблицей.

---

## См. также

- [getting-started.md](getting-started.md) — первое использование
- [mcp-tools.md](mcp-tools.md) — те же возможности через MCP
- [http-api.md](http-api.md) — те же возможности через HTTP
- [tag-contract.md](tag-contract.md) — схема тегов, соблюдаемая здесь
- [runbooks/migrate.md](../admin/runbooks/migrate.md) — операционное руководство по миграции
- [обзор архитектуры](../architecture/overview.md) — структура системы

---

_Последнее обновление: 2026-06-16_
