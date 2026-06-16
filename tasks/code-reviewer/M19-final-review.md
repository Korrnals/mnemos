# Task M19 — Final code review перед merge to main

> **Task ID**: CR-M19
> **Specialist**: GCW Code Reviewer
> **Priority**: P0 (блокирует merge)
> **Status**: ⏳ pending assignment
> **Created**: 2026-06-15
> **Source**: [tasks/AUDIT.md](../AUDIT.md)

---

## Goal

Финальный code review всех изменений M15 (production hardening) + M16 (A2A sessions API) + M17 (CI) + M18 (E2E) перед merge в `main`.

## Background

После завершения M15-M18 у нас будет несколько PR'ов (или один большой merge):
- `feat/m15-production-hardening` — mypy fix + bandit fix + SQL refactor + docs + M13 migrate CLI
- `feat/m16-a2a-sessions-api` — 5 endpoint'ов + tests
- `chore/m17-ci-pipeline` — GitHub Actions + Dependabot
- `test/m18-e2e-smoke` — E2E + concurrency + regression tests

Нужно убедиться, что:
- Качество кода соответствует GCW standards
- Нет regression (209+ → больше, ни одного сломанного)
- Security posture не ухудшена
- Architecture не нарушена
- Performance не деградировала
- Tests покрывают критичные paths

## Acceptance criteria

- [ ] Все 4 PR'а reviewed с approve
- [ ] Все findings (если есть) исправлены до merge
- [ ] `make verify` зелёный на main
- [ ] Coverage ≥ 80% (замерить до и после)
- [ ] Architecture review подтвердил clean-architecture-rules
- [ ] Security review подтвердил OWASP ASVS basics

## Review checklist

### 1. Security review (по `iam-review` skill)

- [ ] Нет hardcoded secrets / API keys
- [ ] URL ingestion валидируется через `_validate_url()` (SSRF)
- [ ] SQL injection невозможен (whitelisted dispatch)
- [ ] HF Hub скачивания с pinned revisions
- [ ] No `0.0.0.0` binding без обоснования
- [ ] Network binding documented в threat model
- [ ] Error messages не утекают sensitive data
- [ ] Logs sanitised (нет токенов, паролей в traceback'ах)
- [ ] FTS5 special chars escaping работает
- [ ] Idempotency проверена тестом

### 2. Architecture review (по `clean-architecture-rules`)

- [ ] `mnemos/sessions/*` — отдельный модуль, не зависит от `mnemos/manager.py` через cross-imports
- [ ] FastAPI router подключён через dependency injection, не глобальный state
- [ ] SQLiteStore connection pooling thread-safe
- [ ] VectorStore и SQLiteStore не share mutable state
- [ ] Manager не God object — делегирует к подсистемам
- [ ] Pipeline stages (cluster→synthesize→publish) идемпотентны
- [ ] Policy engine stateless per run
- [ ] MCP server thin wrapper, бизнес-логика в manager

### 3. Quality review (по `findings-schema`)

- [ ] Type hints везде (после M15.1)
- [ ] Нет `Any` без обоснования
- [ ] Pydantic models для всех external inputs
- [ ] No broad `except: pass` (только narrow exceptions)
- [ ] No `# noqa` без обоснования
- [ ] Docstrings на public API
- [ ] Naming consistent (snake_case, PascalCase, etc.)
- [ ] Magic numbers extracted в константы
- [ ] DRY — нет copy-paste блоков > 10 строк

### 4. Performance review

- [ ] FTS5 использует индекс (EXPLAIN QUERY PLAN)
- [ ] Connection pooling эффективен (no per-request open/close)
- [ ] Cache hit rate > 50% на representative workload
- [ ] Memory footprint < 200 MB на 10k memories
- [ ] p95 latency < 200ms на search (замерить)
- [ ] A2A endpoints: write < 50ms, read summary < 10ms

### 5. Test review

- [ ] Coverage ≥ 80% enforced
- [ ] Все MCP tools покрыты happy-path + error-case
- [ ] Concurrency tests (10 concurrent writes) pass
- [ ] E2E test (subprocess + stdio) pass
- [ ] Flaky test strategy (reruns + tracking issue)
- [ ] Test fixtures не зависят от внешних ресурсов

### 6. Documentation review

- [ ] README status отражает реальность
- [ ] CHANGELOG обновлён под [Unreleased]
- [ ] ARCHITECTURE.md описывает новые компоненты
- [ ] OpenAPI schema доступна через /docs
- [ ] Runbooks актуальны
- [ ] Threat model обновлён (если был)
- [ ] Repo memory обновлена

### 7. Git workflow review

- [ ] Conventional commits format
- [ ] `feat(m15):` / `feat(m16):` / `chore(m17):` / `test(m18):`
- [ ] PR bodies заполнены (What/Why/How to verify/Risks)
- [ ] Sensitive data scan: `gitleaks` или аналог
- [ ] No force-push в shared branches
- [ ] Feature branches удалены после merge
- [ ] CHANGELOG bump версии

## Findings schema (по `findings-schema` skill)

Каждый finding в формате:

```yaml
- id: "CR-M15-001"
  severity: "blocker|major|minor|suggestion"
  category: "security|architecture|quality|performance|test|docs"
  file: "src/mnemos/storage/sqlite_store.py"
  line: 419
  rule: "B608|SQL injection|..."
  description: "..."
  recommendation: "..."
  status: "open|resolved|wontfix"
```

Блокирующие findings → не merge'ить до исправления.

## Процедура review

1. **Подготовить review environment**:
   ```bash
   git fetch origin
   git checkout main
   git pull
   git checkout feat/m15-production-hardening  # или какой нужен
   make verify
   ```

2. **Запустить cr-* workers параллельно** (если доступны):
   - `cr-security-reviewer` для security
   - `cr-architecture-reviewer` для architecture
   - `cr-quality-reviewer` для quality
   - `cr-performance-reviewer` для performance
   - `cr-test-reviewer` для test coverage

   Если cr-* агенты недоступны — ручной review по checklist.

3. **Агрегировать findings**, создать summary file.

4. **Отправить автору** (SSE) список findings с приоритетами.

5. **После исправления** — re-review только изменённых мест.

6. **Approve** → merge to main.

## Merge strategy

По `git-workflow.instructions.md`:
- **Squash-merge** для feat/fix/refactor/chore — keeps main linear
- **Merge commit** для release/* (если есть)
- **Fast-forward** для hotfix/*
- **Delete feature branch** after merge

```bash
# После approve
gh pr merge feat/m15-production-hardening --squash --delete-branch
gh pr merge feat/m16-a2a-sessions-api --squash --delete-branch
gh pr merge chore/m17-ci-pipeline --squash --delete-branch
gh pr merge test/m18-e2e-smoke --squash --delete-branch
```

## Post-merge verification

```bash
git checkout main
git pull
source .venv/bin/activate
make verify
pytest --cov=src/mnemos --cov-fail-under=80 tests/ -q
# Всё должно быть зелёным
```

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| M15 + M16 + M17 + M18 merged одновременно → конфликты | H | Squash merge каждого отдельно; после каждого — main зелёный |
| Coverage падает ниже 80% | M | CI enforcement; PR блокируется |
| Security regression | H | cr-security-reviewer обязателен |
| Performance regression | M | p95 benchmark в CI (отдельная задача) |

## Out of scope

- ❌ Re-design архитектуры (M20+)
- ❌ Postgres migration (нет v1 требований)
- ❌ Web UI (M12 говорит — не делать)
- ❌ Multi-tenant auth (не в v1)

## Hand-off

Report back to `@GCW: Tech Lead` with:
- Список findings по severity
- Approve/reject per PR
- Coverage delta (before/after)
- Security posture assessment
- Final recommendation: merge or block

## Coordination

- После M15.5 (commit M15) + M16 + M17 + M18 завершены
- Перед merge в main
- Tech Lead собирает все 4 PR'а и решает merge order
