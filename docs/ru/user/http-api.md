# Справочник HTTP API

**🌐 Language / Язык:** [English](../../en/user/http-api.md) · Русский

> Полная справка по HTTP API Mnemos — CRUD записей, поиск, пайплайн, DLQ, контекстный фильтр, трассировки, path-scoped rules и A2A Sessions API (M16).

HTTP-сервер — FastAPI-приложение, обслуживаемое Uvicorn. Запуск:

```bash
mnemos serve --host 127.0.0.1 --port 8000
```

| Ресурс | URL |
|--------|-----|
| Swagger UI | `http://HOST:PORT/docs` |
| ReDoc | `http://HOST:PORT/redoc` |
| Схема OpenAPI 3.1 | `http://HOST:PORT/openapi.json` |

> **Привязка по умолчанию — `127.0.0.1`.** Не открывайте этот порт в публичную сеть без обратного прокси с аутентификацией. Модель угроз — в [security.md](../admin/security.md).

> **Аутентификация** — когда `api.auth_enabled=true`, все маршруты кроме `/health`, `/auth/login`, `/auth/verify`, `/docs`, `/redoc` и `/openapi.json` требуют валидного сессионного токена (заголовок `Authorization: Bearer <session>` или куки `mnemos_session`; заголовок имеет приоритет). Для получения сессии используйте `POST /auth/login`. См. раздел [Аутентификация](#authentication) ниже.

> **CORS** — отключён по умолчанию. При `api.cors_enabled=true` CORS middleware регистрируется крайним слоем, чтобы OPTIONS preflight-запросы отвечались до проверки аутентификации. Укажите явный список разрешённых origins в `cors_allow_origins`; комбинация `["*"]` с `cors_allow_credentials=true` отклоняется при старте.

Те же возможности через другие транспорты — в [mcp-tools.md](mcp-tools.md) (MCP) и [cli-reference.md](cli-reference.md) (CLI). Для общего контекста — [обзор архитектуры](../architecture/overview.md). Контракт A2A также описан в [a2a-sessions.md](../architecture/a2a-sessions.md) (ссылка на обоснование дизайна).

---

## Соглашения

- Все тела запросов — JSON, если не указано иное.
- Все временны́е метки — ISO 8601 в UTC (`2026-06-15T10:42:00+00:00`).
- ID — UUID, если не указано иное (A2A-сессии используют `conv-YYYY-MM-DD-<short>`).
- Ошибки — JSON формата `{"detail": "..."}`.
- Только стандартные HTTP-статус-коды — нет кастомных кодов ошибок.
- `200 OK` и `201 Created` несут JSON-тело. `204 No Content` используется для удалений без тела ответа.

---

## Статус-коды

| Код | Значение в этом API |
|-----|-------------------- |
| `200` | OK (успех по умолчанию) |
| `201` | Created (POST, вставляющий строку) |
| `400` | Bad request (напр. попытка опубликовать не-`processed` запись) |
| `404` | Not found (memory_id, cluster_id, dlq_id, session_id, turn_id) |
| `401` | Unauthorised (отсутствует или недействителен токен; только при `api.auth_enabled=true`) |
| `422` | Unprocessable entity (сбой валидации Pydantic в теле запроса) |
| `500` | Internal server error (см. логи сервера) |
| `503` | Auth not initialised (fail-closed: AuthMiddleware активен, но конфиг отсутствует) |

---

## Аутентификация {#authentication}

> **Управляется `api.auth_enabled`.** Все четыре эндпоинта смонтированы на `/auth`. При `api.auth_enabled=false` (по умолчанию) эти маршруты существуют, но middleware не применяет проверку на других маршрутах.

Модель аутентификации использует **непрозрачные bearer-токены** (префикс `mnk_`) с опциональным TOTP 2FA. Токены хранятся как PBKDF2-HMAC-SHA256 дайджесты; plaintext показывается один раз при `mnemos auth token create` и больше никогда. Сессии выдаются после успешного логина (+ TOTP verify при `api.totp_enabled=true`) и имеют тот же формат `Authorization: Bearer <session>`.

### `POST /auth/login` — начать сессию

**Тело запроса**

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|---------- |
| `token` | string | **да** | Непрозрачный bearer-токен (`mnk_...`). |

**Ответ 200 — TOTP отключён**

```json
{
  "session": "mnk_session_...",
  "expires_at": "2026-06-18T02:00:00+00:00"
}
```

**Ответ 200 — TOTP включён**

```json
{
  "challenge_id": "chal_a1b2c3d4",
  "ttl_sec": 120
}
```

При включённом TOTP сессия **не** выдаётся здесь; вызовите `POST /auth/verify` с `challenge_id` и 6-значным кодом TOTP.

**Ошибки**

| Код | Причина |
|-----|-------- |
| `401` | Неизвестный или отключённый токен. |
| `429` | Превышен лимит запросов (5 req/мин на IP или хэш токена). |

### `POST /auth/verify` — завершить TOTP-challenge

**Тело запроса**

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|---------- |
| `challenge_id` | string | **да** | Из ответа `POST /auth/login`. |
| `code` | string | **да** | 6-значный код TOTP. |

**Ответ 200**

```json
{
  "session": "mnk_session_...",
  "expires_at": "2026-06-18T02:00:00+00:00"
}
```

Также устанавливает `Set-Cookie: mnemos_session=...; HttpOnly; Secure; SameSite=Strict`.

**Ошибки**

| Код | Причина |
|-----|-------- |
| `401` | Недействительный или истёкший challenge, неверный TOTP-код. |
| `429` | Превышен лимит запросов (5 req/мин на challenge). |

### `POST /auth/logout` — инвалидировать сессию

Требует валидной сессии (заголовок или куки). Удаляет строку сессии на сервере и сбрасывает куки.

**Ответ 200**

```json
{"ok": true}
```

### `GET /auth/me` — информация о текущей сессии

Требует валидной сессии.

**Ответ 200**

```json
{
  "token_id": "tok_...",
  "totp": false,
  "expires_at": "2026-06-18T02:00:00+00:00"
}
```

---

## Здоровье и метрики

### `GET /health`

Liveness probe.

**Ответ 200**

```json
{"status": "ok"}
```

### `GET /metrics`

Метрики в стиле Prometheus (наблюдаемость M5). Сейчас возвращает ту же структуру, что и агрегированная статистика `GET /memories`:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "data_dir": "/home/you/.mnemos",
  "vault_path": "/home/you/mnemos-vault",
  "total": 142,
  "by_status": {"raw": 5, "processing": 0, "processed": 12, "published": 120, "archived": 5},
  "vectors": 120
}
```

---

## Теги

### `GET /tags` — список тегов с количеством

Возвращает все уникальные теги в хранилище памяти с количеством использования.

**Ответ 200** — массив объектов тегов

| Поле | Тип | Описание |
|------|-----|---------- |
| `tag` | string | Полная строка тега (напр. `project:mnemos`). |
| `count` | int | Количество записей с этим тегом. |

Отсортировано по `count` убывающе; при равенстве — по `tag` возрастающе (алфавитно).

**Пример**

```bash
curl -s http://127.0.0.1:8000/tags
```

```json
[
  {"tag": "project:mnemos", "count": 142},
  {"tag": "agent:tech-writer", "count": 58},
  {"tag": "gcw:learning", "count": 41}
]
```

---

## CRUD записей

### `POST /memories` — создать запись {#create-memory}

Контракт тегов M2 соблюдается на стороне сервера. Эндпоинт извлекает `project` и `agent` из тегов и сохраняет их как денормализованные столбцы для быстрой фильтрации.

**Тело запроса** — см. [MemoryCreate](../architecture/overview.md#data-model)

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `content` | string | **да** | — | Основной текст. |
| `title` | string | нет | авто | Краткий заголовок. |
| `tags` | string[] | **да** | — | Должны включать `project:<slug>`, `agent:<slug>` и хотя бы один `gcw:<subtype>`. |
| `source` | string | нет | `manual` | Одно из `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `source_url` | string | нет | — | URL происхождения. |
| `memory_type` | string | нет | `note` | Одно из `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `status` | string | нет | `raw` | Одно из `raw`, `processing`, `processed`, `published`, `archived`. |
| `filter_profile` | string | нет | — | Одно из `log`, `terminal`, `code`, `docs`, `web`, `default`. |
| `metadata` | object | нет | `{}` | Произвольное хранилище ключей/значений. |
| `category` | string | нет | — | Произвольная метка категории. |

**Ответ 201** — полный объект [`Memory`](#memory-schema).

**Пример**

```bash
curl -s -X POST http://127.0.0.1:8000/memories \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Use uv, not pip — it resolves transitive CVE closure correctly.",
    "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"]
  }'
```

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "content": "Use uv, not pip — it resolves transitive CVE closure correctly.",
  "title": "Use uv, not pip",
  "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"],
  "source": "manual",
  "memory_type": "note",
  "status": "raw",
  "project": "mnemos",
  "agent": "tech-writer",
  "created_at": "2026-06-15T10:42:00+00:00",
  "updated_at": "2026-06-15T10:42:00+00:00",
  "raw_content": null,
  "metadata": {}
}
```

**Ошибки**

| Код | Причина |
|-----|-------- |
| `422` | Отсутствует обязательный тег (`project:`, `agent:` или `gcw:`) |
| `500` | Сбой записи SQLite / vault |

### `GET /memories/{memory_id}` — получить одну запись

**Параметры пути**

| Имя | Тип | Описание |
|-----|-----|---------- |
| `memory_id` | UUID | Идентификатор записи. |

**Query-параметры**

| Имя | Тип | По умолчанию | Описание |
|-----|-----|-------------|---------- |
| `include_raw` | bool | `false` | При true включает `raw_content`. |

**Ответ 200** — полный объект [`Memory`](#memory-schema).

**Ответ 404** — `{"detail": "Memory <id> not found"}`.

**Пример**

```bash
curl -s http://127.0.0.1:8000/memories/550e8400-e29b-41d4-a716-446655440000
```

### `GET /memories` — список последних

**Query-параметры**

| Имя | Тип | По умолчанию | Описание |
|-----|-----|-------------|---------- |
| `status` | string | — | Фильтр по значению enum `MemoryStatus`. |
| `project` | string | — | Ограничить проектом. |
| `limit` | int | `20` | Максимум строк. Жёсткий cap `500`. |

**Ответ 200** — массив объектов [`Memory`](#memory-schema) (без `raw_content`).

**Пример**

```bash
curl -s "http://127.0.0.1:8000/memories?project=mnemos&limit=10"
```

---

## Поиск {#search}

### `POST /search` — гибридный поиск

RRF-слияние FTS5 и векторной ветки. По умолчанию ищет только среди `published`-записей.

**Тело запроса** — см. `SearchQuery` в `src/mnemos/models.py`

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `query` | string | **да** | — | Строка поиска на естественном языке. |
| `tags` | string[] | нет | — | Фильтр: все эти теги должны присутствовать. |
| `project` | string | нет | — | Ограничить проектом. |
| `limit` | int | нет | `20` | Максимум результатов. |
| `include_raw` | bool | нет | `false` | При true возвращает `raw_content` вместо очищенного контента. |

**Ответ 200** — массив результатов

| Поле | Тип | Описание |
|------|-----|---------- |
| `id` | UUID | ID записи. |
| `title` | string | Авто/явный заголовок. |
| `content` | string | Очищенный контент или raw при `include_raw=true`. |
| `tags` | string[] | Список тегов. |
| `score` | float | RRF-скор, чем выше — тем лучше. |
| `search_type` | string | Всегда `hybrid` здесь. |

**Пример**

```bash
curl -s -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "embedding model", "project": "mnemos", "limit": 5}'
```

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": "Use uv, not pip",
    "content": "Use uv, not pip — it resolves transitive CVE closure correctly.",
    "tags": ["project:mnemos", "agent:tech-writer", "gcw:learning"],
    "score": 0.812,
    "search_type": "hybrid"
  }
]
```

---

## Per-agent recall (M3) {#agent-recall}

### `GET /recall/agent/{name}` — отзыв агента

Возвращает последние записи одного агента, опционально фильтруя по проекту и/или подзапросу.

**Параметры пути**

| Имя | Тип | Описание |
|-----|-----|---------- |
| `name` | string | Slug агента. |

**Query-параметры**

| Имя | Тип | По умолчанию | Описание |
|-----|-----|-------------|---------- |
| `project` | string | — | Ограничить проектом. |
| `q` | string | — | Опциональный FTS/векторный подзапрос. |
| `limit` | int | `20` | Максимум строк. Жёсткий cap `100`. |

**Ответ 200** — массив объектов:

| Поле | Тип |
|------|-----|
| `id` | UUID |
| `title` | string |
| `content` | string |
| `tags` | string[] |
| `created_at` | ISO 8601 string |

**Пример**

```bash
curl -s "http://127.0.0.1:8000/recall/agent/cr-security-reviewer?project=mnemos&limit=5"
```

---

## Пайплайн знаний (M4)

### `POST /process` — запустить end-to-end пайплайн

Кластеризация → синтез → качественный шлюз → публикация. Тяжёлая операция; может занять секунды или минуты при большом бэклоге.

**Query-параметры**

| Имя | Тип | По умолчанию | Описание |
|-----|-----|-------------|---------- |
| `project` | string | — | Ограничить одним проектом. |
| `agent` | string | — | Ограничить одним агентом. |
| `limit` | int | `100` | Максимум raw-записей для обработки. Жёсткий cap `500`. |

**Ответ 200**

```json
{
  "status": "ok",
  "clustered": 8,
  "synthesized": 7,
  "published": 6,
  "quality_rejected": 1,
  "errors": 0
}
```

**Пример**

```bash
curl -s -X POST "http://127.0.0.1:8000/process?project=mnemos&limit=200"
```

### `POST /synthesize` — синтезировать один кластер

**Query-параметры**

| Имя | Тип | Описание |
|-----|-----|---------- |
| `cluster_id` | string | Идентификатор кластера. |

**Ответ 200**

```json
{
  "status": "ok",
  "draft_id": "dr-...",
  "cluster_id": "cl-...",
  "source_coverage": 0.92,
  "model_used": "qwen2.5:3b"
}
```

**Ответ 404** — `{"detail": "Cluster <id> not found or empty"}`.

### `POST /publish/{memory_id}` — опубликовать обработанную запись

Переводит одну `processed`-запись в `published` и индексирует её в векторном хранилище.

**Параметры пути**

| Имя | Тип | Описание |
|-----|-----|---------- |
| `memory_id` | UUID | Идентификатор записи. |

**Ответ 200**

```json
{
  "status": "published",
  "memory_id": "550e8400-e29b-41d4-a716-446655440000",
  "vector_indexed": true
}
```

**Ответ 400** — `{"detail": "Publish failed for <id> (status=raw)"}` (публиковать можно только `processed`-записи).

---

## Dead-Letter Queue (M5)

DLQ хранит задачи, которые автоматизация не смогла завершить (таймаут LLM, временный сбой эмбеддера и т.д.). Три эндпоинта управляют очередью.

### `GET /dlq` — список записей DLQ

**Query-параметры**

| Имя | Тип | По умолчанию | Описание |
|-----|-----|-------------|---------- |
| `task_label` | string | — | Фильтр по метке задачи. |
| `ready_only` | bool | `false` | При true — только записи, у которых наступило время следующего retry. |
| `limit` | int | `50` | Максимум строк. Жёсткий cap `500`. |

**Ответ 200** — массив словарей строк DLQ (точная структура — в `SQLiteStore.dlq_list`).

### `POST /dlq/{dlq_id}/retry` — запланировать повтор

**Параметры пути**

| Имя | Тип |
|-----|-----|
| `dlq_id` | string |

**Ответ 200** — `{"status": "retry_scheduled", "entry": { ... }}`

### `DELETE /dlq/{dlq_id}` — удалить запись DLQ

**Ответ 200** — `{"status": "discarded", "dlq_id": "..."}`

**Ответ 404** — `{"detail": "DLQ entry <id> not found"}`.

---

## Контекстный фильтр (M10)

### `POST /filter/{memory_id}` — применить 5-этапный контекстный фильтр

**Параметры пути**

| Имя | Тип | Описание |
|-----|-----|---------- |
| `memory_id` | UUID | Целевая запись. |

**Тело запроса** — см. `FilterRequest` в `src/mnemos/models.py`

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `profile` | string | нет | авто | Одно из `log`, `terminal`, `code`, `docs`, `web`, `default`. |
| `budget` | int | нет | — | Бюджет токенов / символов. |

**Ответ 200** — `FilterResult` (см. `manager.apply_context_filter`).

**Ответ 404** — `{"detail": "..."}`

---

## Трассировки (M6)

### `GET /traces` — список трассировок пайплайна

Слой трассировок — хук объяснимости. Каждый шаг пайплайна записывает строку трассировки.

**Query-параметры**

| Имя | Тип | По умолчанию | Описание |
|-----|-----|-------------|---------- |
| `task_label` | string | — | Фильтр по метке задачи (напр. `pipeline`, `synthesize`, `publish`). |
| `limit` | int | `50` | Максимум строк. Жёсткий cap `500`. |

**Ответ 200** — массив словарей строк трассировки.

---

## Загрузка path-scoped rules (M8)

### `POST /rules/ingest` — загрузить файлы `.instructions.md`

**Тело запроса** — см. `RuleIngestRequest`

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|---------- |
| `rules_dir` | string | **да** | Директория для рекурсивного сканирования. |
| `project` | string | нет | Slug проекта для тегирования. |
| `agent` | string | нет | Slug агента для тегирования. |
| `pattern` | string | нет | Glob (по умолчанию `*.instructions.md`). |

**Ответ 200**

```json
{
  "status": "ok",
  "processed": 7,
  "results": [
    {"file_path": ".github/instructions/communication-language.instructions.md", "memory_id": "..."}
  ]
}
```

**Пример**

```bash
curl -s -X POST http://127.0.0.1:8000/rules/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "rules_dir": "/home/you/mnemos/.github/instructions",
    "project": "mnemos",
    "agent": "tech-writer"
  }'
```

### `DELETE /rules/ingest` — удалить запись rule

**Тело запроса** — см. `RuleRemoveRequest`

| Поле | Тип | Обязательное | Описание |
|------|-----|--------------|---------- |
| `file_path` | string | **да** | Абсолютный или vault-относительный путь к rule. |

**Ответ 200** — `{"status": "removed", "removed": true, "memory_id": "..."}`

**Ответ 404** — `{"detail": "Rule for <path> not found"}`.

---

## A2A Sessions API (M16)

> Смонтировано под `/v1`. Контракт подробно описан в [a2a-sessions.md](../architecture/a2a-sessions.md); этот раздел — HTTP-поверхность.

Все пять эндпоинтов используют одну семантику ошибок:

- `422` — сбой валидации Pydantic.
- `404` — `SessionNotFoundError` или `TurnNotFoundError`.
- `500` — любой другой сбой на стороне сервера (см. логи).

### `POST /v1/sessions` — создать сессию

**Тело запроса** — `SessionCreate`

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `user_id` | string | нет | `""` | До 256 символов, пробельные символы обрезаются. |
| `metadata` | object | нет | `{}` | Произвольное хранилище ключей/значений. |
| `ttl_expires_at` | string (ISO 8601) | нет | — | Опциональная клиентская подсказка для TTL. Сервер может скорректировать. |

**Ответ 201** — `SessionRead`

```json
{
  "session_id": "conv-2026-06-15-a1b2c3d4",
  "user_id": "user-42",
  "created_at": "2026-06-15T10:42:00+00:00",
  "updated_at": "2026-06-15T10:42:00+00:00",
  "turns_count": 0,
  "metadata": {},
  "ttl_expires_at": null
}
```

Формат `session_id`: `conv-YYYY-MM-DD-<8 hex>` (дата UTC).

### `GET /v1/sessions/{session_id}` — получить сессию

**Ответ 200** — `SessionRead`.

**Ответ 404** — `{"detail": "..."}`

### `POST /v1/sessions/{session_id}/turns` — добавить ход

Идемпотентен по `message_id`: повторный POST с тем же `message_id` возвращает существующий ход (статус-код по-прежнему `201`) вместо дублирования.

**Тело запроса** — `TurnCreate`

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `role` | string | **да** | — | Одно из `user`, `agent`, `a2a_message`, `system`. |
| `content` | string | **да** | — | От 1 до 1 000 000 символов. |
| `from` | string | нет | — | Идентификатор отправителя. До 256 символов. |
| `to` | string | нет | — | Идентификатор получателя. |
| `message_id` | string | нет | — | Усечено до 256 символов. Ключ идемпотентности. |
| `outcome` | string | условное | авто | Одно из `delivered`, `rejected`, `budget-exhausted`, `loop-detected`. Обязательно при `role=a2a_message`; иначе отклоняется. |
| `tags` | string[] | нет | `[]` | Дедуплицируется, максимум 32 записи. |

**Ответ 201** — `TurnRead`

```json
{
  "turn_id": "tr-...",
  "session_id": "conv-2026-06-15-a1b2c3d4",
  "step_number": 1,
  "role": "user",
  "from": "user-42",
  "to": null,
  "summary": null,
  "key_decisions": [],
  "content": "Hello, Mnemos.",
  "outcome": null,
  "tags": [],
  "context_pointer": "ctx-...",
  "message_id": "msg-001",
  "created_at": "2026-06-15T10:42:00+00:00"
}
```

### `GET /v1/sessions/{session_id}/turns/{turn_id}` — получить один ход

**Query-параметры**

| Имя | Тип | По умолчанию | Описание |
|-----|-----|-------------|---------- |
| `mode` | string | `summary` | `summary` — дешёвый путь (без `content`); `full` — полный raw-контент. |

**Ответ 200** — `TurnRead`.

### `POST /v1/sessions/{session_id}/turns/range` — диапазон ходов

**Тело запроса** — `TurnRangeRequest`

| Поле | Тип | Обязательное | По умолчанию | Описание |
|------|-----|--------------|-------------|---------- |
| `from_step` | int | **да** | — | Включительно нижняя граница. |
| `to_step` | int | **да** | — | Включительно верхняя граница. Должна быть ≥ `from_step`. |
| `mode` | string | нет | `summary` | `summary` или `full`. |

**Ответ 200** — `TurnRangeResponse`

```json
{
  "turns": [ { "...TurnRead..." } ],
  "total": 12,
  "mode": "summary"
}
```

Результат отсортирован по `step_number` возрастающе. `total` — количество фактически возвращённых ходов (не всей сессии).

---

## Схема Memory {#memory-schema}

Pydantic-модель `Memory` (определена в `src/mnemos/models.py`) возвращается из `POST /memories`, `GET /memories/{id}` и `GET /memories`.

| Поле | Тип | Примечания |
|------|-----|----------- |
| `id` | UUID | Назначается сервером. |
| `content` | string | Очищенный / эффективный контент. |
| `raw_content` | string \| null | Исходный контент до фильтрации. Не включается без `include_raw=true`. |
| `title` | string \| null | Автогенерируется, если не указан. |
| `tags` | string[] | Должны удовлетворять контракту M2. |
| `source` | string | Одно из `manual`, `web`, `file`, `mcp`, `obsidian`, `cli`, `rule`, `synthesized`. |
| `source_url` | string \| null | URL происхождения. |
| `memory_type` | string | Одно из `note`, `fact`, `snippet`, `bookmark`, `conversation`, `session_context`. |
| `status` | string | Одно из `raw`, `processing`, `processed`, `published`, `archived`. |
| `project` | string | Денормализовано из тега `project:`. |
| `agent` | string | Денормализовано из тега `agent:`. |
| `category` | string \| null | Произвольное поле. |
| `quality_score` | float \| null | Устанавливается качественным шлюзом M4. |
| `confidence` | float \| null | Устанавливается качественным шлюзом M4. |
| `cluster_id` | string \| null | Указатель кластера M4. |
| `derived_from` | string[] | ID родительских записей (линия синтеза). |
| `file_path` | string \| null | Vault-относительный путь. |
| `metadata` | object | Произвольное поле. |
| `filter_profile` | string \| null | Применённый профиль фильтра M10. |
| `created_at` | ISO 8601 | UTC. |
| `updated_at` | ISO 8601 | UTC. |

---

## OpenAPI / Swagger

Полная машиночитаемая схема доступна на `/openapi.json` (3.1.0) и рендерится как UI на `/docs` (Swagger) и `/redoc` (ReDoc). Они генерируются FastAPI из декораторов маршрутов в `src/mnemos/api/main.py` и `src/mnemos/sessions/api.py`, поэтому схема никогда не расходится с работающим кодом.

Для генерации статического клиента — скачайте схему и выполните [`openapi-generator`](https://openapi-generator.tech/):

```bash
curl -s http://127.0.0.1:8000/openapi.json -o mnemos-openapi.json
npx @openapitools/openapi-generator-cli generate \
  -i mnemos-openapi.json -g typescript-fetch -o ./mnemos-client
```

---

## См. также

- [mcp-tools.md](mcp-tools.md) — те же возможности через MCP
- [cli-reference.md](cli-reference.md) — те же возможности через CLI
- [обзор архитектуры](../architecture/overview.md) — структура системы и модель данных
- [a2a-sessions.md](../architecture/a2a-sessions.md) — контракт A2A и обоснование дизайна
- [tag-contract.md](tag-contract.md) — схема M2, соблюдаемая `POST /memories`
- [security.md](../admin/security.md) — защита от SSRF, безопасность секретов, модель аутентификации, правила на границе запросов

---

_Последнее обновление: 2026-06-17_
