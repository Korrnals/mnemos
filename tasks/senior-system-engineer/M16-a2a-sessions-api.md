# Task M16 — A2A Sessions API (для GCW v0.6.0)

> **Task ID**: SSE-M16
> **Specialist**: GCW Senior System Engineer
> **Priority**: P0 (блокирует GCW v0.6.0 release)
> **Status**: ⏳ pending assignment
> **Created**: 2026-06-15
> **Source**: `/var/home/abyss/LABs/Projects/Reserching/GithubCopilotWorkflow/docs/a2a/mnemos-requirements.md`

---

## Goal

Реализовать 5 HTTP endpoint'ов для A2A sessions API, которые нужны GCW v0.6.0 для persistent backend вместо file-fallback.

## Background

GCW A2A routing сейчас работает в degraded mode с file-based fallback (`~/.gcw/a2a-messages.jsonl`). Для production-grade нужен persistent backend. Команда GCW сформулировала минимальные требования в `mnemos-requirements.md`.

**Контракт**:
- 5 MUST endpoint'ов + 3 bonus (для v0.7)
- SQLite + FTS5 (как в Mnemos сейчас)
- Атомарная запись (write-then-commit, WAL)
- `mode=summary` дефолт
- Idempotency через `message_id`
- НЕ single point of failure (GCW имеет fallback)

## Acceptance criteria

- [ ] 5 endpoint'ов реализованы и доступны в FastAPI `/docs`
- [ ] `curl` smoke test для каждого endpoint'а проходит
- [ ] Атомарная запись turn работает (crash mid-write не оставляет half-state)
- [ ] `mode=summary` возвращает ≤ 500 байт summary
- [ ] `mode=full` возвращает полный `content`
- [ ] Idempotency: повторный POST с тем же `message_id` → возврат существующего turn, без дубликата
- [ ] OpenAPI schema доступна через `/v1/openapi.json`
- [ ] FTS5 индекс на `content` для будущего `/v1/search`
- [ ] 6+ tests: create/read/write/load/range/idempotency
- [ ] Backward compat: существующие `/memories`, `/recall/*`, `/search` не сломаны

## Спецификация endpoint'ов (из `mnemos-requirements.md`)

### 1. `POST /v1/sessions`
```http
POST /v1/sessions
Content-Type: application/json

{
  "user_id": "abyss",
  "metadata": {
    "started_by": "vscode",
    "workspace": "/var/home/abyss/LABs/Projects/Reserching/GithubCopilotWorkflow"
  }
}
```

Response 201:
```json
{
  "session_id": "conv-2026-06-15-abc",
  "created_at": "2026-06-15T14:23:01Z",
  "metadata": {...}
}
```

**Что делать**:
- Генерировать `session_id` в формате `conv-YYYY-MM-DD-<short-uuid>`
- Сохранить в новую таблицу `sessions`
- Вернуть 201

### 2. `GET /v1/sessions/{id}`
```http
GET /v1/sessions/conv-2026-06-15-abc
```

Response 200:
```json
{
  "session_id": "conv-2026-06-15-abc",
  "user_id": "abyss",
  "created_at": "2026-06-15T14:23:01Z",
  "turns_count": 7,
  "metadata": {...}
}
```

**Что делать**:
- SELECT из `sessions` + COUNT из `turns`
- Вернуть 404 если не найдено

### 3. `POST /v1/sessions/{id}/turns`
```http
POST /v1/sessions/conv-2026-06-15-abc/turns
Content-Type: application/json

{
  "role": "a2a_message",
  "from": "gcw-senior-system-engineer",
  "to": "gcw-senior-dba",
  "message_id": "msg-x7y8z9",
  "content": "<full A2A message JSON, see docs/a2a/protocol-spec.md>",
  "outcome": "delivered",
  "tags": ["migration", "schema", "orders"]
}
```

Response 201:
```json
{
  "turn_id": "turn-42",
  "context_pointer": "memory://conv-2026-06-15-abc#step-2",
  "created_at": "2026-06-15T14:23:05Z"
}
```

**Role enum**: `"user"`, `"agent"`, `"a2a_message"`, `"system"`.
**Outcome enum** (только для `a2a_message`): `"delivered"`, `"rejected"`, `"budget-exhausted"`, `"loop-detected"`.

**Что делать**:
- Pydantic model `TurnCreate` с валидацией role + outcome
- **Idempotency**: проверить `message_id` в `turns` — если есть, вернуть существующий turn (не дублировать)
- Сгенерировать `turn_id = "turn-N"` где N = MAX(turn_id) + 1 для session
- `context_pointer` = `f"memory://{session_id}#step-{step_number}"`
- `step_number` = `turns_count` до этой вставки + 1
- INSERT в `turns` (single transaction, commit, THEN return)
- Вернуть 201

### 4. `GET /v1/sessions/{id}/turns/{turn_id}`
Query: `mode=summary` (default) или `mode=full`

Response 200 (mode=summary):
```json
{
  "turn_id": "turn-42",
  "role": "a2a_message",
  "from": "gcw-senior-system-engineer",
  "to": "gcw-senior-dba",
  "summary": "Need migration for orders.archived_at column.",
  "key_decisions": ["add column, not table"],
  "context_pointer": "memory://conv-2026-06-15-abc#step-2",
  "tags": ["migration", "orders"]
}
```

Response 200 (mode=full): то же + полный `content`.

**Что делать**:
- Для `summary` mode — генерировать summary из content (первые 200 символов + extract key_decisions)
- Для `full` — отдавать `content` целиком
- `key_decisions` — извлекать из content простым regex (например строки с "DECISION:")

### 5. `POST /v1/sessions/{id}/turns/range`
```http
POST /v1/sessions/conv-2026-06-15-abc/turns/range
Content-Type: application/json

{
  "from_step": 1,
  "to_step": 5,
  "mode": "summary"
}
```

Response 200:
```json
{
  "turns": [
    {"turn_id": "turn-40", "summary": "..."},
    {"turn_id": "turn-41", "summary": "..."}
  ],
  "total": 5
}
```

**Что делать**:
- SELECT из `turns` WHERE session_id=? AND step BETWEEN from AND to
- Опциональный `mode` параметр (default summary)

## Архитектура

### Новые файлы
- `src/mnemos/sessions/__init__.py`
- `src/mnemos/sessions/models.py` — Pydantic models `SessionCreate`, `SessionRead`, `TurnCreate`, `TurnRead`, `TurnRangeRequest`
- `src/mnemos/sessions/store.py` — `SessionStore` (SQLite + WAL)
- `src/mnemos/sessions/summary.py` — генерация summary (extractive, без LLM в v1)
- `src/mnemos/sessions/api.py` — FastAPI router с 5 endpoint'ами
- `tests/test_a2a_sessions.py` — 6+ tests

### Изменения в существующих файлах
- `src/mnemos/api/main.py` — подключить `sessions.api.router` с prefix `/v1`
- `pyproject.toml` — добавить `pydantic[email]` если нужно (нет, не нужно)

### Database schema (миграция v15)
```sql
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}',
    ttl_expires_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);

CREATE TABLE IF NOT EXISTS turns (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    turn_id         TEXT NOT NULL,  -- e.g. "turn-42"
    step_number     INTEGER NOT NULL,
    role            TEXT NOT NULL,
    from_agent      TEXT,
    to_agent        TEXT,
    message_id      TEXT,           -- for idempotency
    content         TEXT NOT NULL,
    summary         TEXT,
    key_decisions   TEXT NOT NULL DEFAULT '[]',
    outcome         TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    context_pointer TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(session_id, turn_id),
    UNIQUE(message_id)  -- NULL message_id allowed
);

CREATE INDEX IF NOT EXISTS idx_turns_session_step ON turns(session_id, step_number);
CREATE INDEX IF NOT EXISTS idx_turns_message_id ON turns(message_id);
CREATE INDEX IF NOT EXISTS idx_turns_created ON turns(created_at);

-- FTS5 для будущего /v1/search bonus endpoint
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    id UNINDEXED,
    session_id UNINDEXED,
    content,
    summary,
    tags,
    from_agent UNINDEXED,
    to_agent UNINDEXED,
    content=turns,
    content_rowid=rowid,
    tokenize='unicode61'
);
```

## Implementation notes

### Атомарность записи
```python
async def create_turn(...):
    conn = self._get_conn()
    try:
        # Idempotency check first
        if message_id:
            existing = conn.execute(
                "SELECT * FROM turns WHERE message_id = ?", (message_id,)
            ).fetchone()
            if existing:
                return self._row_to_turn(existing)  # return existing, no insert

        # Compute step_number
        step = conn.execute(
            "SELECT COALESCE(MAX(step_number), 0) + 1 FROM turns WHERE session_id = ?",
            (session_id,)
        ).fetchone()[0]

        # Generate turn_id
        turn_id = f"turn-{step}"
        context_pointer = f"memory://{session_id}#step-{step}"

        # Generate summary
        summary = extract_summary(content)  # 200 chars + key decisions
        key_decisions = extract_key_decisions(content)  # regex

        # Insert
        conn.execute("""
            INSERT INTO turns (id, session_id, turn_id, step_number, role,
                from_agent, to_agent, message_id, content, summary,
                key_decisions, outcome, tags, context_pointer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (...))
        conn.commit()
        # Update session updated_at
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()
        return TurnRead(...)
    except Exception:
        conn.rollback()
        raise
```

WAL mode + explicit commit = атомарная запись.

### Summary generation (extractive, без LLM v1)
```python
def extract_summary(content: str, max_chars: int = 200) -> str:
    """Take first paragraph or first N chars."""
    # Strip leading whitespace
    text = content.strip()
    # Get first paragraph
    first_para = text.split("\n\n")[0]
    if len(first_para) <= max_chars:
        return first_para
    # Truncate at word boundary
    truncated = first_para[:max_chars].rsplit(" ", 1)[0]
    return truncated + "..."

def extract_key_decisions(content: str) -> list[str]:
    """Find lines starting with DECISION: or - [x] (markdown task done)."""
    decisions = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("DECISION:") or line.startswith("- [x]"):
            decisions.append(line)
    return decisions[:5]  # max 5
```

## Testing strategy

В `tests/test_a2a_sessions.py`:
1. `test_create_session` — POST /v1/sessions, проверка 201 + session_id format
2. `test_get_session` — GET /v1/sessions/{id}, проверка counts
3. `test_write_turn` — POST /v1/sessions/{id}/turns, проверка атомарности
4. `test_idempotency` — повторный POST с тем же message_id → тот же turn_id
5. `test_load_turn_summary` — mode=summary, без content
6. `test_load_turn_full` — mode=full, с content
7. `test_range_summary` — POST /v1/.../turns/range
8. `test_404_unknown_session` — error handling
9. `test_validation_error` — invalid role/outcome → 422

## Files to touch

| Файл | Действие |
|---|---|
| `src/mnemos/sessions/__init__.py` | create |
| `src/mnemos/sessions/models.py` | create (~80 строк) |
| `src/mnemos/sessions/store.py` | create (~200 строк) |
| `src/mnemos/sessions/summary.py` | create (~30 строк) |
| `src/mnemos/sessions/api.py` | create (~150 строк) |
| `src/mnemos/api/main.py` | edit — add router include |
| `src/mnemos/storage/sqlite_store.py` | edit — add new tables to _DB_SCHEMA + migrations |
| `tests/test_a2a_sessions.py` | create (~250 строк, 6+ tests) |
| `docs/a2a-sessions.md` | create — user-facing API docs |

## Verification

```bash
cd /var/home/abyss/LABs/AI/mnemos
source .venv/bin/activate
pytest tests/test_a2a_sessions.py -v
pytest tests/ -q                          # all 215+ should pass
ruff check src/ tests/
mypy --strict src/mnemos/

# Manual smoke test
python -m mnemos.cli.main serve &
sleep 3
curl -X POST http://localhost:8000/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"user_id": "abyss", "metadata": {"test": true}}'
# Should return 201 with session_id

curl http://localhost:8000/v1/openapi.json | jq '.paths | keys'
# Should include /v1/sessions, /v1/sessions/{session_id}, etc.
```

## Commit strategy

Один commit `feat(m16): implement A2A sessions API for GCW v0.6.0`.

## Out of scope (для v0.7+)

- Bonus endpoint'ы: `POST /v1/search`, `POST /v1/sessions/{id}/summarize`, `WebSocket /v1/sessions/{id}/stream`
- LLM-based summary (сейчас extractive)
- TTL cleanup job (опционально)
- Multi-tenant auth (опционально)
- Postgres migration (только когда понадобится)

## Hand-off

Report back to `@GCW: Tech Lead` with:
- 5 endpoint'ов доступны через `/docs`
- Smoke test результаты (curl outputs)
- Количество новых тестов + общий счёт (должно быть 215+)
- Любые блокеры / новые findings
- Ссылка на OpenAPI schema

## Coordination

- Параллельно с M15.1 (mypy) — разные файлы
- После M15.1 — запустить `mypy --strict` на новом коде
- После M19 (final review) — merge to main
