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
| [`mnemos_stats`](#mnemos_stats) | Счётчики состояния и ключевые пути | нет |

---

## `mnemos_add`

Создать новую запись в памяти. MCP-слой применяет контракт тегов GCW ([M2](tag-contract.md)) перед записью.

### Входные параметры

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `content` | string | **да** | — | Текст для запоминания. |
| `title` | string | нет | авто | Краткий заголовок. |
| `tags` | string[] | **да** | — | Должны включать `project:<slug>`, `agent:<slug>` и хотя бы один `gcw:<subtype>`. |
| `memory_type` | string | нет | `note` | Одно из `note`, `fact`, `snippet`, `bookmark`, `conversation`. |
| `filter_profile` | string | нет | авто | Одно из `log`, `terminal`, `code`, `docs`, `web`, `default`. Управляет контекстным фильтром M10. |

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
      "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"]
    }
  }
}
```

### Ошибки

| Ошибка | Причина |
|--------|-------- |
| `❌ Tag contract violation: ...` | Отсутствует тег `project:`, `agent:` или `gcw:`. |
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

### Вывод

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Use uv, not pip",
    "content": "Use uv, not pip — it's faster and resolves transitive CVE closure correctly.",
    "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"],
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
    "tags": ["project:mnemos", "agent:cr-security-reviewer", "gcw:bug-pattern"],
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

Mnemos синтезирует части в единую запись Markdown с тегами `project:<slug>`, `agent:user` и `gcw:checkpoint`.

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
    "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"],
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
  "gcw:learning": 67,
  "gcw:bug-pattern": 12,
  "gcw:decision": 8,
  "gcw:checkpoint": 14
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
      "tags": ["project:research", "agent:user", "gcw:learning"]
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

---

## `mnemos_watch_stop`

Остановить фоновый file watcher.

### Входные параметры

Отсутствуют.

### Вывод

```text
✅ Watcher stopped.
```

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
| `gcw:<subtype>` | `[a-z][a-z0-9\-]*` | не менее 1 | Когнитивная категория |

Допустимые подтипы `gcw:`: `session`, `bug-pattern`, `learning`, `decision`, `rule`, `open-question`, `checkpoint`, `legacy`.

Полная справка: [tag-contract.md](tag-contract.md).

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
