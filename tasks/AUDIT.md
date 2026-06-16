# Mnemos — Audit & Status (2026-06-15)

> Сводный аудит состояния проекта. Каждое наблюдение ссылается на конкретные файлы/строки.
> На основе этого документа разнесены задачи в `tasks/<specialist>/`.

---

## 0. TL;DR

| Аспект | Реальное состояние | Заявлено в README/CHANGELOG |
|---|---|---|
| Тесты | **209 passed** | 209 passing ✅ |
| Ruff | **0 errors** | clean ✅ |
| Mypy `--strict` | **45 errors** в 9 файлах | "production-ready" ❌ |
| Bandit (skip B104/B608/B615) | 0 findings | "secure" ✅ (но это подтасовка) |
| Bandit (реальный) | **3 HIGH + 7 MEDIUM + 1 LOW** | ❌ скрыто |
| Git working tree | 7 модифицированных + 7 untracked (CHANGELOG, Makefile, runbooks, migrate.py, security tests) | — |
| Реальный статус M-фаз | **M1 done; M2-M14 частично реализованы, но не верифицированы M15** | "M1-M15 complete" ❌ |

**Корневая проблема**: README.md и CHANGELOG.md лгут. Код делает рабочие вещи, но verification gate сломан. M15 (production hardening) **не завершён** — это и есть следующий этап.

---

## 1. Что РЕАЛЬНО сделано (verified)

### 1.1. M1 — Fork & Rebrand ✅
- `git log` подтверждает: `a5887d6 feat(m1): rebrand ai-brain → mnemos` поверх `27f6c74 feat(m1): fork from ai-brain @ 95904e6`
- `upstream-ai-brain` remote сохранён read-only
- Все `brain_*` → `mnemos_*` переименованы (проверено grep'ом ниже)
- Env vars `MNEMOS_*` присутствуют
- Default paths `~/.mnemos/`, `~/mnemos-vault/`

### 1.2. M2 — GCW Tag Contract ✅ (tested)
- `tests/test_tag_contract.py` 31/31 pass
- `TagContract` в `models.py`, `strict_tag_contract` flag в `config.py`
- `mnemos_add` валидирует теги

### 1.3. M3 — Per-agent Recall ✅ (tested)
- `tests/test_agent_recall.py` 16/16 pass
- `mnemos_agent_recall` MCP tool, `list_recent_for_agent` SQLite-метод
- Индексы на `agent`, `project`, `cluster_id`

### 1.4. M4 — Knowledge Pipeline ✅ (tested, частично)
- `tests/test_pipeline.py` 24/24 pass
- `mnemos/pipeline/{cluster,synthesize,quality_gate,publish}.py` присутствуют
- Status enum + vector gating на `status="published"`
- **Но**: mypy жалуется на `attr-defined` — `ClusterResult/PublishResult/QualityResult/SynthesisResult` не экспортируются через `__init__.py`. Падает `manager.py:33-36`

### 1.5. M5 — Policy Engine ✅ (tested)
- `tests/test_policy_engine.py` 24/24 pass
- APScheduler, DLQ, idempotency
- **Но**: mypy находит `Unsupported left operand type for >= ("object")` в `policy/scheduler.py:90` — потенциальный runtime-crash на NoneType

### 1.6. M6 — Traces ✅ (tested)
- `tests/test_traces_compaction.py` (включает traces)
- `traces` table, `task_label`, `rationale_summary` — всё на месте

### 1.7. M7 — Compaction Detection ✅ (tested)
- `auto_collect.py` skeleton, `tests/test_traces_compaction.py` покрывает

### 1.8. M8 — Path-scoped Rules Ingest ✅ (tested)
- `tests/test_path_scoped_rules.py` 11/11 pass
- `watchers/path_scoped.py` работает

### 1.9. M9 — Security Audit ⚠️ ЧАСТИЧНО
- `tests/test_security.py` 11/11 pass
- SSRF guard `_validate_url` в `manager.py` (только что добавлен в uncommitted changes)
- **Но**: 3 bandit-категории **отключены** в `pyproject.toml`:
  ```toml
  [tool.bandit]
  skips = ["B104", "B608", "B615"]
  ```
  Это именно те категории, где находятся реальные проблемы.

### 1.10. M10 — Context Filter ✅ (tested)
- `tests/test_context_filter.py` 32/32 pass
- `mnemos/filter/` — 5-стадийный pipeline (dedup/noise/extract/compress/tokens)
- Профили `log|terminal|code|docs|web|default`

### 1.11. M12 — Docs/Runbooks ✅
- `docs/runbooks/install.md`, `migrate.md`, `backup-restore.md`, `dependency-updates.md` присутствуют

### 1.12. M13 — Migration CLI ✅
- `src/mnemos/cli/migrate.py` + `tests/test_migration.py` 6/6 pass

### 1.13. M14 — ai-brain Archival ✅
- `ai-brain/README.md` помечен DEPRECATED (проверено по тексту в исходном проекте, см. user memory)

### 1.14. M15 — Production Hardening ❌ НЕ ЗАВЕРШЁН
- Makefile существует, но `make verify` **падает** на mypy
- Coverage threshold 80% в Makefile, но не enforced в CI
- bandit `skips` маскирует реальные уязвимости

---

## 2. Что НЕ сделано (gaps)

### 2.1. A2A Sessions API для GCW (из `mnemos-requirements.md`)
Файл: `/var/home/abyss/LABs/Projects/Reserching/GithubCopilotWorkflow/docs/a2a/mnemos-requirements.md`

**Команда GCW v0.6.0 требует 5 endpoint'ов**:
1. `POST /v1/sessions` — create conversation
2. `GET /v1/sessions/{id}` — read metadata
3. `POST /v1/sessions/{id}/turns` — write turn
4. `GET /v1/sessions/{id}/turns/{turn_id}?mode=summary|full` — lazy load
5. `POST /v1/sessions/{id}/turns/range` — bulk load

И bonus для v0.7: `POST /v1/search`, `POST /v1/sessions/{id}/summarize`, WebSocket stream.

**В Mnemos этого нет**. Текущий `api/main.py` — это general CRUD, не session-based. **Это блокер для GCW v0.6.0**.

**Требования к реализации**:
- Атомарная запись turn (write-then-commit, write-ahead log)
- `mode=summary` дефолт
- Idempotency через `message_id`
- TTL на сессии (опционально)
- SQLite + FTS5 backend (для поиска)
- Failure mode: НЕ быть single point of failure (GCW имеет file-based fallback)

### 2.2. M11 — Cache Center (явно deferred в v2)
- Никаких действий не требуется

### 2.3. Web UI
- В исходном `ai-brain` был Web UI. В Mnemos я его не нашёл (grep ничего в `src/mnemos/api/` кроме FastAPI app). Если был — нужно проверить. Если нет — M12.2 говорит "без UI в v1" — приемлемо.

---

## 3. Баги и неточности

### 3.1. Документация оторвана от кода
- `README.md:6` утверждает "M1–M15 complete" — неправда (M15 не complete)
- `CHANGELOG.md` (untracked) хвастается 209 tests — частично верно, но скрывает 45 mypy errors
- `mnemos-progress.md` (repo memory) — старый снэпшот, все M-фазы как `⏳ pending`

### 3.2. Mypy errors (45 штук)
Самые важные:

| Файл | Строка | Категория | Серьёзность |
|---|---|---|---|
| `storage/vault.py` | 103-108 | `meta` имеет тип `object` вместо `dict` | M — нужны type guards |
| `storage/sqlite_store.py` | 544,563,575 | Returning Any from declared dict | L |
| `storage/vector_store.py` | 47,142,150 | Returning Any from Connection/int | M |
| `embeddings/__init__.py` | 92,99,168,176,203,206 | No Any return | L |
| `pipeline/cluster.py` | 110,117,141 | assignment float → floating[Any] | L |
| `manager.py` | 33-36 | `attr-defined` для ClusterResult и т.д. | **H** — это значит, что TYPE_CHECKING импорт не помогает + runtime `from mnemos.pipeline import` падает |
| `manager.py` | 223,230 | Memory \| None → Memory (unbounded) | **H** — потенциальный AttributeError в коде |
| `mcp_server.py` | 120,391 | Untyped decorator | M |
| `policy/triggers.py` | 28,35,70 | Missing type args | L |
| `policy/scheduler.py` | 90,91,92,95,98,99 | `object` вместо dict + operator + | **H** — возможный runtime NoneType crash |

### 3.3. Bandit findings (реальные, со скипом `skips = ["B104", "B608", "B615"]`)

| Файл | Строка | ID | Severity | Проблема |
|---|---|---|---|---|
| `embeddings/__init__.py` | 133 | B615 | MEDIUM | `hf_hub_download` без `revision=` — supply chain risk |
| `embeddings/__init__.py` | 135 | B615 | MEDIUM | то же |
| `embeddings/__init__.py` | 137 | B615 | MEDIUM | то же |
| `manager.py` | 379 | B104 | MEDIUM | `0.0.0.0` binding (для контейнера ОК, но требует comment) |
| `storage/sqlite_store.py` | 419 | B608 | MEDIUM | `f"UPDATE memories SET {setters} WHERE id=?"` — setters фильтруются через allowlist, но bandit не знает. **Нужно рефакторить на whitelisted dispatch** |
| `storage/sqlite_store.py` | 523 | B608 | MEDIUM | `sql = f"""..."""` — большой f-string в FTS search, нужна ревизия |
| `storage/vector_store.py` | 150 | B608 | MEDIUM | то же |

### 3.4. Незакоммиченные правки (working tree)
- `README.md` — изменён (статус "M1–M15 complete")
- `pyproject.toml` — изменён
- `src/mnemos/cli/main.py` — изменён
- `src/mnemos/manager.py` — изменён (добавлен SSRF guard)
- `src/mnemos/storage/sqlite_store.py` — изменён (narrowing exception)
- `src/mnemos/storage/vault.py` — изменён (narrowing exception)
- `tests/test_api.py` — изменён

И untracked: `CHANGELOG.md`, `Makefile`, `bandit-report.json`, `docs/runbooks/`, `src/mnemos/cli/migrate.py`, `tests/test_migration.py`, `tests/test_security.py`.

**Риск**: если прямо сейчас делать `git add .` + commit — уйдёт неконсистентный snapshot. README говорит "production-ready", а код — нет.

### 3.5. pyproject.toml — `warn_return_any = false` при `mypy --strict`
- В `Makefile` стоит `mypy --strict`, но в `pyproject.toml` `warn_return_any = false`. Это рассогласование — strict-режим не настоящий.

### 3.6. `chromadb 1.5.9` + CVE-2026-45829 ignored
- В `Makefile` `pip-audit --ignore-vuln CVE-2026-45829` — намеренно подавлено
- Это ОК, если CVE реально нет фикса. Нужно верифицировать и задокументировать срок ревизии.

### 3.7. Pydantic v2 + pydantic-settings + pydantic
- `pyproject.toml` имеет `pydantic>=2.0, pydantic-settings>=2.0`, `chromadb>=0.5` подтягивает своё
- Risk of duplicate installs / pydantic v3 migration later

---

## 4. Что GCW ждёт от Mnemos (из mnemos-requirements.md)

| Требование | Импакт | Приоритет |
|---|---|---|
| 5 endpoint'ов сессий | Без этого GCW A2A routing работает в file-fallback — persistent backend отсутствует | **MUST** |
| Атомарная запись | Без — race conditions | **MUST** |
| `mode=summary` дефолт | Без — bandwidth waste | **MUST** |
| Idempotency через `message_id` | Retry-safe | Should |
| TTL на сессии | Optional | Could |
| FTS5 для `/v1/search` | Bonus | Could |

**Рекомендация**: реализовать как отдельный подмодуль `mnemos/sessions/` + новые эндпоинты в FastAPI. Не ломать существующий memory API.

---

## 5. Где мы находимся (current state)

| Milestone | Status |
|---|---|
| M1 — Fork & Rebrand | ✅ Done |
| M2 — Tag Contract | ✅ Done + tested |
| M3 — Per-agent Recall | ✅ Done + tested |
| M4 — Knowledge Pipeline | ✅ Done + tested (но mypy attr-defined warning) |
| M5 — Policy Engine | ✅ Done + tested (но mypy NoneType warning) |
| M6 — Traces | ✅ Done + tested |
| M7 — Compaction Detection | ✅ Done + tested |
| M8 — Path-scoped Rules | ✅ Done + tested |
| M9 — Security Audit | ⚠️ Частично — SSRF guard добавлен, но bandit-skips маскируют |
| M10 — Context Filter | ✅ Done + tested |
| M11 — Cache Center | ⏳ Deferred v2 |
| M12 — Docs | ✅ Done |
| M13 — Migration CLI | ✅ Done + tested |
| M14 — ai-brain Archival | ✅ Done |
| M15 — Production Hardening | ❌ **NOT done** — mypy fails, bandit-skips faking, coverage not enforced |
| **M16 — A2A Sessions API** | ⏳ **Новая фаза, нужна для GCW v0.6.0** |

---

## 6. Что дальше (recommended next steps)

**Sequence** (каждое следующее зависит от предыдущего):

1. **M15.1 — Fix mypy strict** (блокирует `make verify`). Без этого ничего не "production-ready".
2. **M15.2 — Remove bandit skips, fix real findings** (B608 SQL — рефакторинг через whitelisted dispatch, B615 — pin revisions, B104 — `# nosec` с обоснованием).
3. **M15.3 — Reconcile docs with reality** (README "M1–M15 complete" → "M1–M14 complete, M15 in progress", CHANGELOG — убрать ложные утверждения, repo memory обновить).
4. **M15.4 — Commit pending changes** через `feat(m15)` PR после прохождения локального verify.
5. **M16 — A2A Sessions API** для GCW v0.6.0 (новые endpoint'ы, сессии, turns, атомарная запись, idempotency).
6. **M17 — E2E smoke test** через MCP-клиент из реальной Copilot-сессии.
7. **M18 — CI** (GitHub Actions / GitLab CI) с теми же gates.

---

## 7. Скоуп-границы (out of scope сейчас)

- ❌ Web UI с нуля (M12 говорит — если нет в ai-brain, не делать)
- ❌ M11 Cache Center
- ❌ Multi-tenant auth (GCW file-fallback покрывает)
- ❌ Streaming WebSocket (Won't per mnemos-requirements.md)
- ❌ Полная замена ai-brain API (M13 — migration, не runtime compat)
