# Справочник MCP-инструментов

**🌐 Language / Язык:** [English](../../en/user/mcp-tools.md) · Русский

> Полная справка по инструментам `mnemos_*`, экспортируемым MCP-сервером Mnemos (`mnemos mcp-server`).

Mnemos говорит на [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) поверх **stdio JSON-RPC 2.0**. VS Code Copilot и любой MCP-совместимый клиент могут вызывать инструменты, перечисленные здесь.

Сервер определён в `src/mnemos/mcp_server.py`. Каждый инструмент регистрируется с помощью декоратора `@server.list_tools()` и диспетчеризируется функцией `call_tool()`.

Быстрое подключение к VS Code — в [getting-started.md#run-the-mcp-server](getting-started.md#run-the-mcp-server). Те же возможности доступны через HTTP — см. [http-api.md](http-api.md). Схема тегов, соблюдаемая большинством инструментов, — в [tag-contract.md](tag-contract.md).

---

## Транспорт

| Свойство | Значение |
|----------|--------- |
| Протокол | MCP (JSON-RPC 2.0 поверх stdio) |
| Имя сервера | `mnemos` |
| Транспорт по умолчанию | stdio (без TCP) |
| Префикс инструментов | `mnemos_` |
| Кодировка | UTF-8, JSON |

Сервер не занимает никакой порт. Остановить через `Ctrl+C` или отправкой EOF на stdin.

---

## Каталог инструментов (сводка)

| Инструмент | Назначение | Требует теги |
|------------|------------ |--------------|
| [`mnemos_add`](#mnemos_add) | Создать новую запись | да |
| [`mnemos_search`](#mnemos_search) | Гибридный поиск FTS + вектор | нет |
| [`mnemos_agent_recall`](#mnemos_agent_recall) | Per-agent recall (M3) | нет |
| [`mnemos_recall_context`](#mnemos_recall_context) | Восстановить контекст сессии для проекта | нет |
| [`mnemos_save_context`](#mnemos_save_context) | Сохранить контрольную точку сессии | нет (авто) |
| [`mnemos_list_recent`](#mnemos_list_recent) | Список последних записей | нет |
| [`mnemos_list_tags`](#mnemos_list_tags) | Список всех тегов с количеством | нет |
| [`mnemos_ingest_url`](#mnemos_ingest_url) | Загрузить и сохранить веб-страницу | да |
| [`mnemos_watch_start`](#mnemos_watch_start) | Запустить фоновый file watcher | нет |
| [`mnemos_watch_stop`](#mnemos_watch_stop) | Остановить file watcher | нет |
| [`mnemos_watch_status`](#mnemos_watch_status) | Статус watcher'а | нет |
| [`mnemos_auto_collect_status`](#mnemos_auto_collect_status) | Вектор сигналов сжатия контекста (M7) | нет |
| [`mnemos_compress`](#mnemos_compress) | Обратимое сжатие (CCR) — кэш оригинала, маркер в вывод | нет |
| [`mnemos_retrieve`](#mnemos_retrieve) | Извлечение оригинала из кэша CCR или FTS5-сниппеты | нет |
| [`mnemos_align_prefix`](#mnemos_align_prefix) | CacheAligner — перенос динамического контента для стабильности prefix cache | нет |
| [`mnemos_export`](#mnemos_export) | Экспорт записей в файл (JSON или SQLite-снимок) | нет |
| [`mnemos_import`](#mnemos_import) | Импорт записей из файла экспорта (merge или restore) | нет |
| [`mnemos_stats`](#mnemos_stats) | Счётчики состояния и ключевые пути | нет |

---

## `mnemos_add`

Создать новую запись в памяти. MCP-слой применяет контракт тегов Mnemos ([M2](tag-contract.md)) перед записью.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `content` | string | **да** | — | Текст для запоминания. |
| `title` | string | нет | авто | Краткий заголовок. |
| `tags` | string[] | **да** | — | Должны включать `project:<slug>`, `agent:<slug>` и хотя бы один `mnemos:<subtype>`. |
| `memory_type` | string | нет | `note` | Одно из `note`, `fact`, `snippet`, `bookmark`, `conversation`. |
| `filter_profile` | string | нет | авто | Одно из `log`, `terminal`, `code`, `docs`, `web`, `default`. Управляет контекстным фильтром M10. |
| `verbosity` | string | нет | из конфига | Одно из `default`, `terse`, `minimal`. Вставляет подсказку по стилю вывода во framing результата. См. [Сокращение токенов вывода (P1-7)](#сокращение-токенов-вывода-p1-7). |
| `effort` | string | нет | из конфига | Одно из `low`, `medium`, `high`. Вставляет подсказку по уровню размышлений во framing результата. См. [Сокращение токенов вывода (P1-7)](#сокращение-токенов-вывода-p1-7). |

### Вывод

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Use uv, not pip",
  "status": "raw"
}
```

### Пример вызова (JSON-RPC)

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "mnemos_add",
    "arguments": {
      "content": "Use uv, not pip",
      "tags": ["project:mnemos", "agent:tech-writer", "mnemos:learning"]
    }
  }
}
```

### Ошибки

| Ошибка | Причина |
|--------|-------- |
| `❌ Tag contract violation: ...` | Отсутствует тег `project:`, `agent:` или `mnemos:`. |
| `❌ Error: ...` | Сбой записи SQLite, vault или ошибка эмбеддинга (последняя не критична — см. [обзор архитектуры](../architecture/overview.md#vector-store)). |

### Связанные ресурсы

- Схема тегов: [tag-contract.md](tag-contract.md)
- HTTP-эквивалент: [`POST /memories`](http-api.md#create-memory)
- CLI-эквивалент: [`mnemos add`](cli-reference.md#add)

---

## `mnemos_search`

Гибридный поиск: FTS5 (полнотекстовый) + вектор + Reciprocal Rank Fusion. По умолчанию ищет только среди `published`-записей.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `query` | string | **да** | — | Строка поиска на естественном языке. |
| `tags` | string[] | нет | — | Фильтр: все эти теги должны присутствовать. |
| `project` | string | нет | — | Ограничить проектом. |
| `limit` | integer | нет | `10` | Максимум результатов. |
| `include_raw` | boolean | нет | `false` | Если true, возвращает `raw_content` вместо очищенного `content`. |
| `verbosity` | string | нет | из конфига | Одно из `default`, `terse`, `minimal`. Вставляет подсказку по стилю вывода во framing результата. См. [Сокращение токенов вывода (P1-7)](#сокращение-токенов-вывода-p1-7). |
| `effort` | string | нет | из конфига | Одно из `low`, `medium`, `high`. Вставляет подсказку по уровню размышлений во framing результата. См. [Сокращение токенов вывода (P1-7)](#сокращение-токенов-вывода-p1-7). |

### Вывод

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Use uv, not pip",
    "content": "Use uv, not pip — it's faster and resolves transitive CVE closure correctly.",
    "tags": ["project:mnemos", "agent:tech-writer", "mnemos:learning"],
    "score": 0.812,
    "search_type": "hybrid",
    "status": "published"
  }
]
```

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "mnemos_search",
    "arguments": {
      "query": "how to manage Python dependencies",
      "limit": 5,
      "project": "mnemos"
    }
  }
}
```

### Ошибки

- `❌ Error: ...` — сбой парсинга запроса (редко; обычно завершается с пустым результатом).

### Связанные ресурсы

- HTTP-эквивалент: [`POST /search`](http-api.md#search)
- CLI-эквивалент: [`mnemos search`](cli-reference.md#search)

---

## `mnemos_agent_recall`

Per-agent recall (M3). Возвращает последние записи одного агента, опционально фильтруя по проекту и/или подзапросу.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `agent` | string | **да** | — | Slug агента, напр. `cr-security-reviewer`. |
| `project` | string | нет | — | Ограничить проектом. |
| `query` | string | нет | — | Опциональный FTS/векторный запрос в рамках агента. |
| `limit` | integer | нет | `20` | Максимум записей. |

При отсутствии `query` инструмент возвращает последние записи (по убыванию времени). При наличии `query` выполняет гибридный поиск в рамках тегов агента.

### Вывод

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Bandit B608 hardcoded SQL — flag for triage",
    "content": "Found hardcoded SQL in src/legacy/loader.py:42 ...",
    "tags": ["project:mnemos", "agent:cr-security-reviewer", "mnemos:bug-pattern"],
    "created_at": "2026-06-15T10:42:00+00:00",
    "status": "published"
  }
]
```

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "mnemos_agent_recall",
    "arguments": {
      "agent": "cr-security-reviewer",
      "project": "mnemos",
      "query": "bandit SQL injection",
      "limit": 10
    }
  }
}
```

### Ошибки

- Нет типичных. Возвращает пустой массив при отсутствии совпадений.

### Связанные ресурсы

- HTTP-эквивалент: [`GET /recall/agent/{name}`](http-api.md#agent-recall)
- CLI-эквивалент: [`mnemos recall --agent <slug>`](cli-reference.md#recall)

---

## `mnemos_recall_context`

Восстановить последнюю контрольную точку сессии для проекта. **Первое**, что агент должен вызвать при старте сессии, особенно после сжатия контекста.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `project` | string | нет | авто (cwd) | Имя проекта. Автоопределяется из текущей директории, если не указано. |
| `query` | string | нет | — | Опциональный фокусный аспект. |
| `verbosity` | string | нет | из конфига | Одно из `default`, `terse`, `minimal`. Вставляет подсказку по стилю вывода во framing результата. См. [Сокращение токенов вывода (P1-7)](#сокращение-токенов-вывода-p1-7). |
| `effort` | string | нет | из конфига | Одно из `low`, `medium`, `high`. Вставляет подсказку по уровню размышлений во framing результата. См. [Сокращение токенов вывода (P1-7)](#сокращение-токенов-вывода-p1-7). |

### Вывод

Блок простого текста в формате Markdown:

```text
# Context for project 'mnemos'

---
# Session checkpoint — 2026-06-15T10:42:00+00:00

## Goals
Ship M15 production hardening.
## Completed
bandit clean, mypy --strict green
## In Progress
pip-audit CVE-2026-45829 ignore
## Decisions
Pin chromadb 1.5.9 with audit
## Context
Active files: src/mnemos/manager.py, src/mnemos/api/main.py
```

Если контрольная точка не найдена:

```text
No context found for project 'mnemos'. Start by saving context with mnemos_save_context.
```

В **режиме auto-collect** (`MNEMOS_AUTO_COLLECT=1`) к выводу добавляется блок `## 🔄 Auto-Collect Mode Active` с обязательными правилами сессии.

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "mnemos_recall_context",
    "arguments": { "project": "mnemos" }
  }
}
```

### Связанные ресурсы

- `mnemos_save_context` — парный инструмент записи
- [architecture.md#session-context](../architecture/overview.md#session-context)
- HTTP-эквивалент: [`POST /context/recall`](http-api.md#context-recall)

---

## `mnemos_save_context`

Сохранить контрольную точку сессии. Агенты должны вызывать это **превентивно**: после значимой работы, перед переключением задач или при большом размере контекста.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `project` | string | нет | авто (cwd) | Имя проекта. |
| `goals` | string | нет | — | Текущие цели сессии. |
| `completed` | string | нет | — | Что завершено. |
| `in_progress` | string | нет | — | Что в процессе. |
| `decisions` | string | нет | — | Ключевые технические решения + обоснование. |
| `context` | string | нет | — | Прочий контекст (пути к файлам, архитектура, особенности). |

Mnemos синтезирует части в единую запись Markdown с тегами `project:<slug>`, `agent:user` и `mnemos:checkpoint`.

### Вывод

```text
✅ Context saved (id=550e8400-...).
```

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "mnemos_save_context",
    "arguments": {
      "project": "mnemos",
      "goals": "Finish M15.1 mypy --strict",
      "completed": "Added None checks in 12 functions",
      "in_progress": "tests/test_api.py:241 type narrowing",
      "decisions": "Use cast() sparingly, prefer TypeGuard"
    }
  }
}
```

### Связанные ресурсы

- `mnemos_recall_context` — парный инструмент чтения
- Режим auto-collect: [getting-started.md#run-the-mcp-server](getting-started.md#run-the-mcp-server)
- HTTP-эквивалент: [`POST /context/save`](http-api.md#context-save)

---

## `mnemos_list_recent`

Список последних записей в памяти, старые — последними.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `limit` | integer | нет | `10` | Максимум записей. |
| `tags` | string[] | нет | — | Фильтр: хотя бы один из этих тегов должен присутствовать. |
| `project` | string | нет | — | Ограничить проектом. |

### Вывод

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Use uv, not pip",
    "tags": ["project:mnemos", "agent:tech-writer", "mnemos:learning"],
    "status": "raw",
    "created_at": "2026-06-15T10:42:00+00:00"
  }
]
```

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "mnemos_list_recent",
    "arguments": { "limit": 20, "project": "mnemos" }
  }
}
```

### Связанные ресурсы

- HTTP-эквивалент: [`GET /memories`](http-api.md#list-recent)
- CLI-эквивалент: [`mnemos list`](cli-reference.md#list)

---

## `mnemos_list_tags`

Список всех тегов в памяти с количеством вхождений.

### Входные параметры

Отсутствуют.

### Вывод

```json
{
  "project:mnemos": 142,
  "agent:tech-writer": 23,
  "agent:sre": 41,
  "mnemos:learning": 67,
  "mnemos:bug-pattern": 12,
  "mnemos:decision": 8,
  "mnemos:checkpoint": 14
}
```

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": { "name": "mnemos_list_tags", "arguments": {} }
}
```

### Связанные ресурсы

- HTTP-эквивалент: [`GET /tags`](http-api.md#tags)
- CLI-эквивалент: [`mnemos tags`](cli-reference.md#tags)

---

## `mnemos_ingest_url`

Загрузить веб-страницу, извлечь основной контент (через `trafilatura`) и сохранить как запись.

### Входные параметры

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|---------- |
| `url` | string | **да** | HTTP / HTTPS URL для загрузки. |
| `tags` | string[] | **да** | Тот же контракт M2, что и в `mnemos_add`. |

> **Защита от SSRF.** MCP-слой удаляет `user:password@` из authority URL перед загрузкой (глубокая защита совместно с внутрипроцессной защитой). Не обходите это, собирая URL из строки.

### Вывод

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "How to manage Python dependencies",
  "url": "https://example.com/article"
}
```

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "mnemos_ingest_url",
    "arguments": {
      "url": "https://example.com/article",
      "tags": ["project:research", "agent:user", "mnemos:learning"]
    }
  }
}
```

### Ошибки

| Ошибка | Причина |
|--------|-------- |
| `❌ Error: ...` | Сетевой сбой, заблокированный URL (защита от SSRF) или ошибка извлечения `trafilatura`. |

### Связанные ресурсы

- CLI-эквивалент: [`mnemos add --url <URL>`](cli-reference.md#add)
- HTTP-эквивалент: [`POST /memories` с ручным контентом](http-api.md#create-memory)
- HTTP-эквивалент: [`POST /ingest-url`](http-api.md#ingest-url)
- Безопасность: [security.md](../admin/security.md#ssrf-guard)

---

## `mnemos_watch_start`

Запустить фоновый file watcher. Новые и изменённые файлы по отслеживаемым путям автоматически индексируются в Mnemos.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `paths` | string[] | нет | `[cwd]` | Директории для наблюдения. |
| `scan` | boolean | нет | `true` | Выполнить начальное сканирование для обработки существующих файлов. |
| `include_rules` | boolean | нет | `false` | Также отслеживать `.github/instructions/*.instructions.md` (правила M8). |

### Вывод

```text
✅ Watcher started on ['/home/you/project']
# или, с include_rules:
✅ Watcher started on ['/home/you/project'] (including .instructions.md rules)
```

### Пример вызова

```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/call",
  "params": {
    "name": "mnemos_watch_start",
    "arguments": {
      "paths": ["/home/you/mnemos", "/home/you/notes"],
      "include_rules": true
    }
  }
}
```

### Примечания

- Лимит размера файла — `watcher.max_file_size_kb` (по умолчанию 512 КБ); файлы больше пропускаются.
- Игнорируемые директории по умолчанию: `.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`.
- Отслеживаемые расширения по умолчанию: `.md`, `.py`, `.js`, `.ts`, `.yaml`, `.yml`, `.toml`, `.json`, `.txt`, `.rst`, `.sh`, `.css`, `.html`, `.sql`.

### Связанные ресурсы

- HTTP-эквивалент: [`POST /watch/start`](http-api.md#watch-start)
- CLI-эквивалент: [`mnemos watch start`](cli-reference.md#watch-start)

---

## `mnemos_watch_stop`

Остановить фоновый file watcher.

### Входные параметры

Отсутствуют.

### Вывод

```text
✅ Watcher stopped.
```

### Связанные ресурсы

- HTTP-эквивалент: [`POST /watch/stop`](http-api.md#watch-stop)
- CLI-эквивалент: [`mnemos watch stop`](cli-reference.md#watch-stop)

---

## `mnemos_watch_status`

Сообщить текущее состояние фонового watcher'а.

### Входные параметры

Отсутствуют.

### Вывод

```json
{
  "running": true,
  "paths": ["/home/you/mnemos"],
  "files_queued": 3,
  "files_indexed": 142,
  "include_rules": false
}
```

### Связанные ресурсы

- HTTP-эквивалент: [`GET /watch/status`](http-api.md#watch-status)
- CLI-эквивалент: [`mnemos watch status`](cli-reference.md#watch-status)

---

## `mnemos_auto_collect_status`

Вернуть текущий вектор сигналов обнаружения сжатия контекста (M7). Агент читает это для принятия превентивного решения о вызове `mnemos_save_context`.

### Входные параметры

Отсутствуют.

### Вывод

```json
{
  "auto_collect_enabled": false,
  "signals": {
    "call_counter": {
      "calls_since_save": 7,
      "threshold": 12,
      "triggered": false
    },
    "elapsed_secs": {
      "value": 312,
      "threshold": 900,
      "triggered": false
    },
    "context_size_heuristic": {
      "value": null,
      "note": "populated by client (M7)"
    },
    "summary_marker_detected": {
      "value": null,
      "note": "populated by client (M7)"
    },
    "reference_drop_heuristic": {
      "value": null,
      "note": "populated by client (M7)"
    }
  },
  "recommendation": "ok",
  "next_reminder_in_calls": 5
}
```

Поле `recommendation` принимает одно из значений:

| Значение | Значение |
|----------|--------- |
| `ok` | Контрольная точка пока не нужна. |
| `save_checkpoint` | Сохранить сейчас — достигнут или превышен порог. |

### Режим auto-collect

Установите `MNEMOS_AUTO_COLLECT=1` в окружении сервера. Пороги напоминаний ужесточаются:

| Настройка | Обычный | Auto-collect |
|-----------|---------|--------------|
| Вызовов с момента сохранения | 12 | 6 |
| Прошедших секунд | 900 (15 мин) | 480 (8 мин) |

Описания инструментов также меняются (с префиксами `🔄 [AUTO-COLLECT] MANDATORY:`), чтобы агенты серьёзнее воспринимали подсказки. **Рекомендуется для продакшн-агентов**, не для одноразовых скриптов.

### Связанные ресурсы

- HTTP-эквивалент: [`GET /auto-collect`](http-api.md#auto-collect)
- CLI-эквивалент: [`mnemos auto-collect-status`](cli-reference.md#auto-collect-status)

---

## `mnemos_stats`

Вернуть счётчики состояния Mnemos.

### Входные параметры

Отсутствуют.

### Вывод

Та же структура, что и у команды CLI `mnemos stats` — см. [cli-reference.md#stats](cli-reference.md#stats).

```json
{
  "status": "ok",
  "version": "0.1.0",
  "data_dir": "/home/you/.mnemos",
  "vault_path": "/home/you/.mnemos/vault",
  "total": 142,
  "by_status": {"raw": 5, "processing": 0, "processed": 12, "published": 120, "archived": 5},
  "vectors": 120
}
```

### Связанные ресурсы

- HTTP-эквивалент: [`GET /metrics`](http-api.md#metrics)
- CLI-эквивалент: [`mnemos stats`](cli-reference.md#stats)

---

## `mnemos_compress`

Сжатие большого контента (вывод инструментов, логи, JSON) **без потери данных**. Оригинал кэшируется в таблице `ccr_cache` SQLite по SHA-256 хешу; сжатый вывод содержит короткий парсимый маркер, по которому LLM может вызвать `mnemos_retrieve` и получить полный оригинал по требованию. Даёт 70–90% сокращения токенов на типичных логах и JSON.

Контент короче `min_size_chars` (по умолчанию 500) возвращается как есть — не кэшируется и не сжимается (мелкий контент не даёт экономии токенов).

### Входные параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `text` | string | **да** | — | Контент для сжатия. ≥500 символов для кэширования. |
| `profile` | string | нет | auto | Один из `log`, `terminal`, `code`, `docs`, `web`, `default`. Автоопределение, если опущен. |
| `project` | string | нет | `""` | Slug проекта для привязки записи кэша. |

### Вывод

```json
{
  "compressed_text": "[compressed: a1b2... | 30000→900 chars | retrieve via mnemos_retrieve]\n...отфильтрованный контент...",
  "hash": "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef12345678",
  "original_size": 30000,
  "compressed_size": 900,
  "reduction_pct": 97.0,
  "marker": "[compressed: a1b2... | 30000→900 chars | retrieve via mnemos_retrieve]",
  "cached": true,
  "profile": "log"
}
```

### Формат маркера

```text
[compressed: <sha-256-хеш> | <N>→<M> символов | retrieve via mnemos_retrieve]
```

Маркер — единственный оверхед поверх отфильтрованного контента. Короткий, парсимый, удобный для LLM. Хеш адресован по содержимому, поэтому повторное сжатие того же текста — no-op (запись кэша переиспользуется).

### Пример

Сжать лог сборки на 30K строк → ~900 символов в контекстном окне. Когда LLM нужен полный traceback, он вызывает `mnemos_retrieve` с хешем из маркера.

### Связанные ресурсы

- HTTP-эквивалент: [`POST /compress`](http-api.md#compress)
- CLI-эквивалент: [`mnemos compress`](cli-reference.md#compress)

---

## `mnemos_retrieve`

Извлечение оригинального несжатого контента по хешу маркера CCR. Если `query` опущен — возвращается полный оригинал. Если `query` задан — возвращаются FTS5-ранжированные сниппеты из кэшированного оригинала (полезно, когда оригинал большой, а релевантны несколько строк).

### Входные параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `hash` | string | **да** | — | SHA-256 хеш из маркера `[compressed: ...]`. |
| `query` | string | нет | — | Поисковый запрос для извлечения сниппетов. |
| `snippet_count` | integer | нет | `5` | Количество сниппетов при заданном `query`. |

### Вывод (полное извлечение)

```json
{
  "hash": "a1b2...",
  "found": true,
  "original": "...полный оригинальный текст...",
  "size_bytes": 30000,
  "retrieval_count": 2
}
```

### Вывод (извлечение сниппетов)

```json
{
  "hash": "a1b2...",
  "found": true,
  "query": "Traceback",
  "snippets": [
    {"text": "Traceback (most recent call last):", "rank": 1.0},
    {"text": "  File \"app.py\", line 42, in handler", "rank": 0.8}
  ],
  "retrieval_count": 3
}
```

Если хеша нет в кэше (например, вытеснен по TTL или LRU), `found` равно `false` с полем `reason`.

### Связанные ресурсы

- HTTP-эквивалент: [`POST /retrieve`](http-api.md#retrieve)
- CLI-эквивалент: [`mnemos retrieve`](cli-reference.md#retrieve)

---

## `mnemos_align_prefix`

**CacheAligner (P1-5)** — переносит динамический контент (ISO-таймстампы, UUID, session id, короткоживущие токены, календарные даты) из system-prompt-подобного текста в блок `--- Dynamic context ---` в конце, чтобы prefix оставался побайтово идентичным между запросами и KV-кэши провайдеров (Anthropic `cache_control`, OpenAI prefix caching) попадали. Инспирировано headroom CacheAligner (https://github.com/headroomlabs-ai/headroom, Apache 2.0). Оригинальная реализация — код headroom не импортируется.

Когда CacheAligner отключён в конфиге, текст возвращается без изменений с пустым списком `extracted`.

### Входные параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `text` | string | **да** | — | System-prompt-подобный текст для стабилизации. |
| `profile` | string | нет | `default` | Одно из `code`, `docs`, `default`. Переключает, какие виды динамического контента извлекаются. `code` и `docs` пропускают «голые» токены (избегают искажения длинных идентификаторов или дефисных слов); `default` извлекает все виды. |

### Вывод

```json
{
  "aligned_text": "You are a senior engineer.\n\n--- Dynamic context ---\n- timestamp: 2026-07-17T10:30:00Z\n- session_id: sess-abc123def456\n",
  "extracted": [
    {"kind": "timestamp", "value": "2026-07-17T10:30:00Z", "start": 24, "end": 44},
    {"kind": "session_id", "value": "sess-abc123def456", "start": 60, "end": 78}
  ],
  "prefix_stabilized": true,
  "moved_chars": 38
}
```

- `aligned_text` — входной текст с удалёнными динамическими спанами и добавленным в конец блоком `--- Dynamic context ---`, где каждое извлечённое значение указано со своим `kind`.
- `extracted` — список извлечённых спанов (`kind`, `value`, `start`, `end` в *оригинальном* тексте).
- `prefix_stabilized` — `true`, если хотя бы один спан извлечён из prefix-области (т.е. выровненный prefix длиннее оригинального prefix вплоть до первого динамического спана).
- `moved_chars` — суммарно перенесено символов (сумма длин спанов).

### Пример

Вход:
```text
You are a senior engineer. Today is 2026-07-17T10:30:00Z. Session: sess-abc123def456.
[стабильные правила далее...]
```

Выровненный вывод (prefix вплоть до первого динамического спана теперь побайтово стабилен между запросами):
```text
You are a senior engineer. Today is . Session: .
[стабильные правила далее...]

--- Dynamic context ---
- timestamp: 2026-07-17T10:30:00Z
- session_id: sess-abc123def456
```

### Поведение профиля

| Профиль | Пропускает | Почему |
|---------|------------|--------|
| `default` (или опущен) | ничего | извлекает все виды |
| `code` | `token` | «голые» 20+ символьные токены исказили бы длинные идентификаторы / хеши в коде |
| `docs` | `token` | в прозе редко бывают реальные токены; избегаем искажения длинных дефисных слов |

Skip-множество профиля объединяется (union) с поключевыми тогглами из `CacheAlignerConfig` — отключение вида в конфиге расширяет то, что профиль уже пропускает.

### Конфигурация

```yaml
cache_aligner:
  enabled: true               # главный переключатель
  extract_timestamps: true   # ISO 8601 таймстампы
  extract_uuids: true        # канонические 8-4-4-4-12 UUID
  extract_session_ids: true  # sess-*, session:*, sid-*
  extract_dates: true        # календарные даты 2026-07-17 / 2026/07/17
  extract_tokens: true       # «голые» 20+ символьные непрозрачные токены
```

Вид с тогглом `false` добавляется в skip-множество и остаётся на месте (не переносится).

### Связанные ресурсы

- Архитектура: [overview.md#cachealigner-p1-5](../architecture/overview.md#cachealigner-p1-5)
- Референс конфига: [config.example.yaml](../../../config.example.yaml)

---

## Напоминание о контрольной точке (автовставка)

Каждый вызов не-save инструмента возвращает нормальный результат **плюс** опциональную строку-напоминание при достижении одного из порогов auto-collect:

```text
... normal result ...

⚠️ [mnemos] 12 tool calls since last checkpoint (970s ago). Consider calling mnemos_save_context to preserve your current progress.
```

Это информационное сообщение; ничто в Mnemos не блокирует вызов. Отключить, установив `MNEMOS_AUTO_COLLECT=0` (по умолчанию).

---

## Напоминание о контракте тегов

Инструменты `mnemos_add` и `mnemos_ingest_url` отклоняют вызовы, нарушающие контракт M2. Три обязательных семейства тегов:

| Тег | Формат | Кардинальность | Назначение |
|-----|--------|----------------|------------ |
| `project:<slug>` | `[a-z0-9][a-z0-9\-_]{0,63}` | ровно 1 | Привязывает к кодовой базе / инициативе |
| `agent:<slug>` | `[a-z0-9][a-z0-9\-_]{0,63}` | ровно 1 | Агент-автор |
| `mnemos:<subtype>` | `[a-z][a-z0-9\-]*` | не менее 1 | Когнитивная категория |

Допустимые подтипы `mnemos:`: `session`, `bug-pattern`, `learning`, `decision`, `rule`, `open-question`, `checkpoint`, `legacy`.

Полная справка: [tag-contract.md](tag-contract.md).

---

## Сокращение токенов вывода (P1-7)

`mnemos_add`, `mnemos_search` и `mnemos_recall_context` принимают два опциональных параметра, которые управляют стилем вывода вызывающей стороны, не меняя того, что Mnemos хранит или возвращает:

| Параметр | Значения | Что делает |
|----------|----------|------------|
| `verbosity` | `default`, `terse`, `minimal` | Вставляет во framing результата подсказку по стилю вывода. `terse` просит краткий вывод без преамбул; `minimal` просит только факты. |
| `effort` | `low`, `medium`, `high` | Вставляет подсказку по уровню размышлений. `low` помечает рутинный шаг (минимум размышлений); `high` просит вдумчивых размышлений и проверки. |

Это **подсказки, передаваемые вызывающей стороне**, а не изменения конфигурации модели. Инспирировано работой headroom по сокращению токенов вывода. Оригинальная реализация.

### Обратная совместимость

- Оба параметра опциональны. Пропуск использует значения из конфига по умолчанию (`default_verbosity=default`, `default_effort=medium`).
- Значения по умолчанию (`default` / `medium`) дают пустую подсказку — результат инструмента побайтово идентичен выводу до P1-7.
- Невалидные значения (например, `"verbose"`, `"turbo"`) валидируются по разрешённым frozenset'ам, логируются на уровне `WARNING` и откатываются к значению из конфига — мягкая деградация, никогда не выбрасывает исключение.

### Конфигурация

```yaml
output_style:
  enabled: true              # главный переключатель; при false steering — no-op
  default_verbosity: default # значение по умолчанию, если вызывающая сторона опустила verbosity
  default_effort: medium     # значение по умолчанию, если вызывающая сторона опустила effort
```

Когда `output_style.enabled` равно `false`, оба resolver'а возвращают no-op-значения по умолчанию независимо от ввода вызывающей стороны.

### Пример

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "mnemos_search",
    "arguments": {
      "query": "cache aligner prefix stability",
      "verbosity": "terse",
      "effort": "low"
    }
  }
}
```

Результат инструмента содержит обычный payload **плюс** короткую подсказку:

```text
... обычные результаты поиска ...

---
*Output style: terse. Be brief. No preambles, no restated context, no ceremony. Lead with the result. Omit explanations the caller already has.*
*Effort: low — routine step, minimal reasoning.*
```

---

## `mnemos_export`

Экспорт записей в файл на диске. Тонкая обёртка над логикой CLI `mnemos export`. Возвращает только метаданные — содержимое экспорта **никогда** не возвращается в теле ответа (stdio-транспорт не может передать бинарный SQLite-tarball или большой JSON-блок через канал JSON-RPC поверх stdout).

Защита federation defense-in-depth (#86) наследуется автоматически, так как инструмент вызывает ту же функцию `run_export`, что и CLI/HTTP: записи с тегом `mnemos:no-federate` исключаются из экспорта, а обнаруженные секреты в проходящих записях заменяются на `<REDACTED:<pattern_name>>`.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|----------|
| `output_path` | string | **да** | — | Абсолютный путь, куда записывается файл экспорта. |
| `format` | enum `json` \| `sqlite` | нет | `json` | `json` = экспорт только метаданных (фильтры применяются); `sqlite` = полный `tar.gz`-снимок (фильтры игнорируются). |
| `compress` | enum `none` \| `gzip` | нет | `none` | Режим сжатия. (`zstd` — только для CLI.) |
| `project` | string | нет | — | Фильтр по slug проекта (только json). |
| `agent` | string | нет | — | Фильтр по slug агента (только json). |
| `status` | enum `raw` \| `processing` \| `processed` \| `published` \| `archived` | нет | — | Фильтр по статусу записи (только json). |
| `tags` | array of string | нет | — | Фильтр по тегам (только json). |
| `since` | string (ISO-8601) | нет | — | Только записи, созданные не ранее этой даты (только json). |
| `until` | string (ISO-8601) | нет | — | Только записи, созданные до этой даты (только json). |
| `encrypt` | boolean | нет | `false` | Если `true`, шифрует результат. Парольная фраза читается из переменной окружения `MNEMOS_EXPORT_PASSPHRASE`. |

### Возвращаемое значение

```json
{
  "path": "/abs/path/to/backup.json",
  "memory_count": 42,
  "format": "json",
  "compress": "none",
  "encrypted": false,
  "bytes": 18234,
  "warnings": []
}
```

### Замечание по безопасности

- **Парольная фраза через окружение, никогда в аргументах.** При `encrypt=true` сервер читает парольную фразу из переменной окружения `MNEMOS_EXPORT_PASSPHRASE`. Передача значения в `output_path` или любой другой аргумент приведёт к утечке в логи MCP — никогда так не делайте.
- **Без встроенного контента.** Инструмент пишет в `output_path` и возвращает только метаданные. Прочитайте файл с диска, чтобы осмотреть экспорт.
- **Наследование #86.** Записи `mnemos:no-federate` исключаются; секреты в проходящих записях редактируются. Дополнительная настройка не нужна.

### Пример

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "mnemos_export",
    "arguments": {
      "output_path": "/tmp/mnemos-backup.json",
      "format": "json",
      "project": "mnemos",
      "compress": "gzip"
    }
  }
}
```

Зашифрованный полный снимок:

```json
{
  "name": "mnemos_export",
  "arguments": {
    "output_path": "/tmp/mnemos-snapshot.tar.gz",
    "format": "sqlite",
    "encrypt": true
  }
}
```

(При установленной в окружении сервера `MNEMOS_EXPORT_PASSPHRASE`.)

---

## `mnemos_import`

Импорт записей из файла экспорта. Тонкая обёртка над логикой CLI `mnemos import`. Два режима: **merge** (вставка новых, пропуск или перезапись существующих) и **restore** (полная очистка и импорт — деструктивный, требует `confirm=true`).

Валидация импорта (#86) наследуется автоматически: дрейф схемы, слишком большой контент, невалидные теги и prompt-injection-паттерны обрабатываются той же функцией `run_import`, что и в CLI/HTTP.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|----------|
| `source_path` | string | **да** | — | Абсолютный путь к файлу экспорта для импорта. |
| `mode` | enum `merge` \| `restore` | нет | `merge` | `merge` = вставка новых / пропуск-или-перезапись существующих; `restore` = полная очистка и импорт (требует `confirm=true`). |
| `overwrite` | boolean | нет | `false` | Перезапись существующих записей (только режим merge). |
| `confirm` | boolean | нет | `false` | **Обязательно `true` для режима `restore`** (жёсткий гейт — restore стирает все данные). |
| `dry_run` | boolean | нет | `false` | Валидация без записи; возвращает отчёт валидации. |
| `passphrase_env` | string | нет | — | Имя переменной окружения с парольной фразой для расшифровки (НЕ само значение). |

### Возвращаемое значение

```json
{
  "mode": "merge",
  "dry_run": false,
  "imported": 12,
  "skipped": 3,
  "updated": 0,
  "errors": [],
  "warnings": [],
  "format_version": "1.0",
  "mnemos_version": "2.11.0"
}
```

### Замечание по безопасности

- **Парольная фраза через имя переменной окружения, не значение.** `passphrase_env` принимает *имя* переменной окружения (например, `"MY_IMPORT_PASS"`), и сервер читает `os.environ["MY_IMPORT_PASS"]`. Передача значения в аргументе приведёт к утечке в логи MCP.
- **Restore требует `confirm=true`.** Без него инструмент возвращает ошибку и не трогает живые данные. Restore стирает все записи, векторы и проекты.
- **Наследование #86.** Дрейф схемы отклоняется; слишком большой контент (>1 МиБ) отклоняется; невалидные теги вызывают ошибку контракта тегов; prompt-injection-паттерны логируются на WARNING (не блокируются — контент может правомерно обсуждать инъекцию).

### Пример

```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/call",
  "params": {
    "name": "mnemos_import",
    "arguments": {
      "source_path": "/tmp/mnemos-backup.json",
      "mode": "merge",
      "overwrite": false
    }
  }
}
```

Restore (деструктивный) с подтверждением:

```json
{
  "name": "mnemos_import",
  "arguments": {
    "source_path": "/tmp/mnemos-snapshot.tar.gz",
    "mode": "restore",
    "confirm": true
  }
}
```

Зашифрованный импорт (при установленной в окружении сервера `MNEMOS_IMPORT_PASS`):

```json
{
  "name": "mnemos_import",
  "arguments": {
    "source_path": "/tmp/encrypted.bin",
    "mode": "merge",
    "passphrase_env": "MNEMOS_IMPORT_PASS"
  }
}
```

---

## См. также

- [getting-started.md](getting-started.md) — подключение `mcp.json` и первый вызов
- [http-api.md](http-api.md) — те же возможности через HTTP
- [cli-reference.md](cli-reference.md) — те же возможности через CLI
- [tag-contract.md](tag-contract.md) — схема M2, соблюдаемая `mnemos_add`
- [security.md](../admin/security.md) — защита от SSRF, безопасность секретов
- [обзор архитектуры](../architecture/overview.md#mcp-server) — жизненный цикл сервера

---

_Последнее обновление: 2026-06-16_
