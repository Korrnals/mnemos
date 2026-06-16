# Mnemos — tasks/ directory

> Распределение работы по senior-агентам GCW. Создано 2026-06-15 на основании [AUDIT.md](AUDIT.md).

---

## Быстрый старт

1. Прочитай [AUDIT.md](AUDIT.md) — там полная картина состояния проекта.
2. Прочитай [tech-lead/TL-001-coordination.md](tech-lead/TL-001-coordination.md) — sequencing и приоритеты.
3. Возьми свой таск (по своему senior-направлению).
4. Report back to `@GCW: Tech Lead` по формату в конце своего таска.

---

## Распределение по специалистам

| Senior | Tasks |
|---|---|
| **GCW: Senior System Engineer** | [M15.1 mypy strict](senior-system-engineer/M15.1-mypy-strict.md) · [M15.5 commit pending](senior-system-engineer/M15.5-commit-pending.md) · [M16 A2A sessions API](senior-system-engineer/M16-a2a-sessions-api.md) |
| **GCW: Senior DBA** | [M15.3 SQL injection refactor](senior-dba/M15.3-sql-injection-refactor.md) |
| **GCW: Senior Security Engineer** | [M15.2 bandit cleanup](senior-security-engineer/M15.2-bandit-cleanup.md) |
| **GCW: Senior QA Engineer** | [M18 E2E + coverage](senior-qa-engineer/M18-e2e-smoke.md) |
| **GCW: SRE/DevOps** | [M17 CI pipeline](sre-devops/M17-ci-pipeline.md) |
| **GCW: Tech Writer** | [M15.4 docs reconcile](tech-writer/M15.4-docs-reconcile.md) |
| **GCW: Code Reviewer** | [M19 final review](code-reviewer/M19-final-review.md) |
| **GCW: Tech Lead** (coordinator) | [TL-001 coordination](tech-lead/TL-001-coordination.md) |

---

## Sequencing (что когда запускать)

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

**Wave 1 (запустить параллельно)**:
- SSE → M15.1 (mypy)
- SSec → M15.2 (bandit)
- TW → M15.4 (docs) — подождёт M15.1+M15.2
- SSE → M16 (A2A) — независимая фаза

**Wave 2 (после M15.1+M15.2)**:
- SDBA → M15.3 (SQL refactor)
- SRE → M17 (CI)
- QA → M18 (E2E, кроме A2A integration)

**Wave 3 (после M15.3 + M16)**:
- TW → M15.4 finalize
- QA → M18 A2A integration tests

**Wave 4 (финализация)**:
- SSE → M15.5 (commit pending changes)
- CR → M19 (final review)
- Tech Lead → merge to main

---

## Конвенции для всех тасков

1. **Conventional commits** (`type(scope): description`).
2. **Branch naming**: `feat/<id>-<slug>`, `fix/<id>-<slug>`, `chore/<id>-<slug>`, `test/<id>-<slug>`.
3. **PR body**: What / Why / How to verify / Risks / Closes.
4. **Sensitive data**: НЕ коммитить credentials, использовать env vars + placeholders.
5. **Lint gates**: `make verify` MUST be green перед коммитом.
6. **Tests**: расширять coverage при добавлении фич.
7. **Markdown lint**: `markdownlint` (если есть) для docs.
8. **No `# noqa` / `# nosec`** без issue-ссылки и обоснования.

---

## Hand-off format (для отчёта senior → Tech Lead)

```markdown
## Hand-off from @GCW: <Senior Name>

### Task
<task ID>

### Status
- [x] <что сделано>
- [ ] <что осталось, если есть>

### Changes
- Files modified: <list>
- New files: <list>
- Tests added: <count>
- Coverage delta: <+/- percent>

### Verification
- `make verify` output: <paste last 10 lines>
- `pytest` output: <paste last 5 lines>
- `bandit` output: <paste last 5 lines>

### Risks / blockers
- <если есть>

### Recommendation
- merge | needs changes | blocked
```

---

## См. также

- [AUDIT.md](AUDIT.md) — сводный статус проекта
- [tech-lead/TL-001-coordination.md](tech-lead/TL-001-coordination.md) — координация
- [/PLAN.md](../PLAN.md) — оригинальный план (M1-M15)
- [/ARCHITECTURE.md](../ARCHITECTURE.md) — архитектура
- [GCW mnemos-requirements.md](../../Projects/Reserching/GithubCopilotWorkflow/docs/a2a/mnemos-requirements.md) — требования от GCW (A2A API)
