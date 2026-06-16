# Task 0 — Tech Lead Coordination (current session)

> **Task ID**: TL-001
> **Owner**: GCW Tech Lead (current session)
> **Status**: ✅ done (this document is the deliverable)
> **Created**: 2026-06-15

---

## Цель этой сессии

Восстановить контекст проекта Mnemos после паузы, выявить реальное состояние (а не то, что в README), и подготовить директорию `tasks/` с разбивкой работы по senior-агентам для последующего делегирования.

## Что сделано в этой сессии

1. ✅ Прочитан `PLAN.md` (15 фаз M1-M15), `ARCHITECTURE.md` (672 строк), `README.md`, `CHANGELOG.md` (untracked).
2. ✅ Прочитан `mnemos-requirements.md` от GCW (A2A Sessions API, 5 endpoint'ов).
3. ✅ Прочитан repo memory `/memories/repo/mnemos-progress.md` (устаревший снэпшот).
4. ✅ Проверен git status — 7 модифицированных файлов + 7 untracked.
5. ✅ Запущены `pytest` (209/209 ✅), `ruff` (0 errors ✅), `mypy --strict` (**45 errors ❌**), `bandit` (**3 HIGH + 7 MEDIUM + 1 LOW ⚠️**).
6. ✅ Прочитан `bandit-report.json` — реальные findings: B608 (SQL injection), B615 (HF Hub download без pin), B104 (0.0.0.0 binding).
7. ✅ Прочитан `vault.py`, `sqlite_store.py`, `manager.py`, `vector_store.py` — обнаружены 3 места с f-string SQL (реальный B608, не false positive).
8. ✅ Обнаружено: `pyproject.toml` отключает bandit для B104/B608/B615, что маскирует реальные security issues.
9. ✅ Создан `tasks/AUDIT.md` со сводным статусом.
10. ✅ Создана директория `tasks/<senior>/*` с 7 slice-файлами для параллельного исполнения.

## Решения, принятые в этой сессии

| # | Решение | Обоснование |
|---|---|---|
| D1 | README "M1–M15 complete" — **враньё**. Реальный статус: M1–M14 done, M15 in progress. | Mypy fails, bandit-skips фальшивят. |
| D2 | M16 — новая фаза для A2A Sessions API. Без неё GCW v0.6.0 застрянет на file-fallback. | GCW явно указал 5 endpoint'ов в `mnemos-requirements.md` со статусом Must. |
| D3 | Не править код в этой сессии — только спланировать и делегировать. | Tech Lead не пишет production-код (hard rule). |
| D4 | Приоритет 1: M15.1 (mypy) + M15.2 (bandit). Это блокеры для `make verify` green. | Без них нельзя говорить "production-ready". |
| D5 | Bandit B608 не false positive — реальный SQL injection risk в `update_fields` и `fts_search`. | Bandit видит f-string в execute — даже с allowlist это anti-pattern. Нужен рефакторинг. |
| D6 | Bandit B104 (`0.0.0.0` binding в `manager.py:379`) — false positive для контейнера, но требует `# nosec` + обоснование. | Не блокирует, но загрязняет отчёт. |
| D7 | Bandit B615 — реальный supply chain risk. `hf_hub_download` без `revision=` может подтянуть compromised model. | Нужен pin revision. |
| D8 | A2A Sessions API — отдельный подмодуль `mnemos/sessions/`, не править существующий memory API. | Минимизация blast radius. |
| D9 | Не коммитить uncommitted changes до прохождения `make verify`. | git-workflow policy: "lint and validation findings must be FIXED, not suppressed". |

## Hand-off блоки

Созданы 7 slice-файлов для параллельной работы:

| Файл | Senior | Что делает | Зависимости |
|---|---|---|---|
| [tasks/senior-system-engineer/M15.1-mypy-strict.md](senior-system-engineer/M15.1-mypy-strict.md) | SSE | Фиксит 45 mypy errors | — |
| [tasks/senior-security-engineer/M15.2-bandit-cleanup.md](senior-security-engineer/M15.2-bandit-cleanup.md) | SSec | Убирает bandit skips, фиксит B608/B615/B104 | — |
| [tasks/senior-dba/M15.3-sql-injection-refactor.md](senior-dba/M15.3-sql-injection-refactor.md) | SDBA | Рефакторинг f-string SQL в `sqlite_store.py` и `vector_store.py` | зависит от M15.1 review |
| [tasks/tech-writer/M15.4-docs-reconcile.md](tech-writer/M15.4-docs-reconcile.md) | TW | Приводит README/CHANGELOG в соответствие | зависит от M15.1+M15.2+M15.3 |
| [tasks/senior-system-engineer/M15.5-commit-pending.md](senior-system-engineer/M15.5-commit-pending.md) | SSE | Коммитит pending changes через `feat(m15)` PR | зависит от всех M15.* |
| [tasks/senior-system-engineer/M16-a2a-sessions-api.md](senior-system-engineer/M16-a2a-sessions-api.md) | SSE | Реализует 5 endpoint'ов из mnemos-requirements.md | параллельно M15 |
| [tasks/sre-devops/M17-ci-pipeline.md](sre-devops/M17-ci-pipeline.md) | SRE | GitHub Actions / GitLab CI с gates | зависит от M15 |
| [tasks/senior-qa-engineer/M18-e2e-smoke.md](senior-qa-engineer/M18-e2e-smoke.md) | QA | E2E через MCP-клиент, integration tests для A2A | зависит от M15 + M16 |
| [tasks/code-reviewer/M19-final-review.md](code-reviewer/M19-final-review.md) | CR | Финальный review перед merge M15/M16 в main | зависит от всех |

## Sequencing (что параллельно, что последовательно)

```
                ┌─ M15.1 (mypy) ──────────────────┐
                ├─ M15.2 (bandit) ────────────────┤
                │                                │
M1-M14 done ────┤                                ├──> M15.5 commit ──> M19 review ──> merge to main
                │                                │
                ├─ M15.3 (SQL refactor) ─────────┤
                └─ M15.4 (docs reconcile) ───────┘
                
                                         M16 (A2A) ──> M18 (E2E)
                                         M17 (CI) ──> gate enforcement
```

**Параллельно M15** можно запускать M16 (A2A) и M17 (CI) — они трогают разные файлы.
**M19 (final review)** — после всего.

## Ожидаемый порядок запуска (для следующей сессии)

1. **Tech Lead** запускает M15.1 + M15.2 + M15.4 + M16 параллельно (4 подзадачи).
2. После завершения M15.1+M15.2 — Tech Lead запускает M15.3 (SQL refactor) с ссылкой на их review.
3. После завершения всех M15.* + M16 — Tech Lead запускает M15.5 (commit).
4. После commit — Tech Lead запускает M17 (CI) и M18 (E2E) параллельно.
5. После E2E green — Tech Lead запускает M19 (final review).
6. M19 approve → merge to main.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| M15.1 mypy fix ломает runtime behavior | H | Каждое изменение сопровождать pytest run; если 209/209 не green — откат |
| M15.3 SQL refactor ломает update_fields | H | Регрессионные тесты на каждое поле; код-ревью DBA + SSE |
| M16 A2A требует переписать значительную часть API | M | Создать отдельный router + storage; не трогать существующий memory API |
| A2A endpoint'ы конфликтуют с `/v1/` prefix | L | Использовать `/v1/sessions/*` namespace; pre-existing routes оставить |
| pip-audit `--ignore-vuln CVE-2026-45829` отключает проверку | M | Задокументировать обоснование + дедлайн ревизии в `docs/runbooks/dependency-updates.md` |
| chromadb 1.5.9 → 1.5.10 breaking change | L | Запустить M15.1 + M15.2 под chromadb 1.5.10 до commit |

## Definition of Done (для следующей сессии)

- [ ] Все 45 mypy errors исправлены
- [ ] `bandit -r src/` показывает 0 findings (без skips)
- [ ] README.md отражает реальный статус
- [ ] CHANGELOG.md обновлён
- [ ] 209+ tests pass (без regression)
- [ ] Coverage ≥ 80% enforced
- [ ] A2A 5 endpoint'ов работают через curl + FastAPI /docs
- [ ] CI pipeline зелёный на main
- [ ] E2E smoke test green
- [ ] M19 review approve

## Out of scope (явно)

- ❌ Web UI (не в M12)
- ❌ M11 Cache Center (v2)
- ❌ Multi-tenant auth
- ❌ Real-time WebSocket (Won't per GCW)
- ❌ Migration к Postgres (только когда понадобится)
