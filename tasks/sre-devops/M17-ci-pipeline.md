# Task M17 — CI pipeline (GitHub Actions / GitLab CI)

> **Task ID**: SRE-M17
> **Specialist**: GCW SRE/DevOps
> **Priority**: P1 (после M15)
> **Status**: ⏳ pending assignment
> **Created**: 2026-06-15
> **Source**: [tasks/AUDIT.md §6](../AUDIT.md)

---

## Goal

Поднять CI pipeline, который enforce'ит те же gates, что `make verify` локально, плюс добавляет build matrix, security audit, и coverage check на каждый PR.

## Background

Текущий `make verify`:
- `lint` (ruff)
- `test` (pytest)
- `security` (bandit + pip-audit)
- `security-reminder` (warning для CVE-2026-45829)

Но:
- Не запускается на PR
- Нет coverage enforcement
- Нет build matrix (Python 3.11 vs 3.12 vs 3.13)
- Нет Docker build smoke
- Нет dep update bot

## Acceptance criteria

- [ ] CI запускается на каждый push в `main` и каждый PR
- [ ] Все gates из `make verify` зелёные
- [ ] Coverage check ≥ 80% enforced
- [ ] Build matrix: Python 3.11, 3.12, 3.13
- [ ] Docker build smoke (только `podman build`, не full integration)
- [ ] Dependabot / Renovate config для weekly CVE updates
- [ ] Branch protection: main requires 1 approval + CI green

## Stack decision

**Первый вопрос пользователю**: GitHub или GitLab?

- Если GitHub → `.github/workflows/ci.yml`
- Если GitLab → `.gitlab-ci.yml`
- Если оба → оба файла

**Предположение по умолчанию**: GitHub (т.к. `mcp_github_*` tools присутствуют в окружении). Подтвердить с пользователем.

## Реализация

### `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read
  pull-requests: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  verify:
    name: Lint + Test + Type + Security (Python ${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # full history for blame

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"

      - name: Lint (ruff)
        run: source .venv/bin/activate && ruff check src/ tests/

      - name: Format check
        run: source .venv/bin/activate && ruff format --check src/ tests/

      - name: Type check (mypy --strict)
        run: source .venv/bin/activate && mypy --strict src/mnemos/

      - name: Security (bandit)
        run: source .venv/bin/activate && bandit -r src/ -f json -o bandit-report.json

      - name: Security (pip-audit)
        run: source .venv/bin/activate && pip-audit --ignore-vuln CVE-2026-45829

      - name: Test
        run: source .venv/bin/activate && pytest tests/ -q --tb=short

      - name: Coverage check
        run: source .venv/bin/activate && pytest --cov=src/mnemos --cov-fail-under=80 --cov-report=term-missing --cov-report=xml tests/ -q

      - name: Upload coverage to Codecov
        if: matrix.python-version == '3.12'
        uses: codecov/codecov-action@v4
        with:
          file: coverage.xml
          fail_ci_if_error: true

      - name: Upload bandit report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: bandit-report
          path: bandit-report.json

  build-container:
    name: Container build smoke
    runs-on: ubuntu-latest
    needs: verify
    steps:
      - uses: actions/checkout@v4
      - name: Set up buildah
        run: sudo apt-get install -y buildah
      - name: Build image
        run: buildah bud -t mnemos:test .
      - name: Verify image runs
        run: |
          buildah from --name mnemos-test mnemos:test
          buildah run mnemos-test -- mnemos --help

  release:
    name: Release (only on main, version tags)
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    needs: [verify, build-container]
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Build + push to GHCR
        uses: redhat-actions/buildah-build@v2
        with:
          image: mnemos
          tags: latest ${{ github.ref_name }}
          registries: ghcr.io
          oci: true
      - name: Create GitHub release
        uses: softprops/action-gh-release@v1
        with:
          body: |
            See [CHANGELOG.md](CHANGELOG.md) for details.
          draft: true
```

### `.github/dependabot.yml`

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
    labels: ["dependencies", "security"]
    open-pull-requests-limit: 5
    groups:
      patch:
        patterns: ["*"]
        update-types: ["minor", "patch"]

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    labels: ["ci", "dependencies"]
```

### Branch protection (через `gh api` или Settings UI)

```yaml
# Required status checks
required_status_checks:
  strict: true
  contexts:
    - "Lint + Test + Type + Security (Python 3.11)"
    - "Lint + Test + Type + Security (Python 3.12)"
    - "Lint + Test + Type + Security (Python 3.13)"
    - "Container build smoke"

# Require branches to be up to date before merging
required_linear_history: true

# Require 1 review
required_pull_request_reviews:
  required_approving_review_count: 1
  dismiss_stale_reviews: true
```

## Makefile target additions

```makefile
ci-install:
	uv venv
	.venv/bin/pip install uv
	.venv/bin/uv pip install -e ".[dev]"

ci-verify: lint typecheck security test coverage
	@echo "✅ CI verify passed"
```

## Local validation (перед push)

```bash
# 1. Simulate CI locally
act -j verify  # если act установлен

# 2. Или вручную
source .venv/bin/activate
ruff check src/ tests/
ruff format --check src/ tests/
mypy --strict src/mnemos/
bandit -r src/ -f json
pytest tests/ -q --tb=short
pytest --cov=src/mnemos --cov-fail-under=80 tests/ -q
```

## Files to touch

| Файл | Действие |
|---|---|
| `.github/workflows/ci.yml` | create (~80 строк) |
| `.github/dependabot.yml` | create (~30 строк) |
| `Makefile` | edit — add `ci-install`, `ci-verify` targets |
| `docs/runbooks/ci-cd.md` | create (~100 строк) — операционный runbook |
| Branch protection | manual через gh CLI или UI |

## Verification

```bash
# 1. Локально
source .venv/bin/activate
make verify              # all gates green

# 2. Validate workflow syntax
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
# Должно парситься без ошибок

# 3. (После push) Manual
gh pr create --draft
# Наблюдать за CI runs
```

## Commit strategy

Один commit `chore(m17): add CI pipeline (GitHub Actions) + Dependabot config`.

## Out of scope

- ❌ Deploy to production (отдельная фаза; M15 в PLAN.md говорит "rootless podman container")
- ❌ K8s deployment (вне scope v1)
- ❌ Multi-region (нет v1 требований)
- ❌ E2E в CI (M18 покрывает, но не обязательно в v1 CI)

## Hand-off

Report back to `@GCW: Tech Lead` with:
- Ссылка на первый CI run
- Coverage trend (до/после)
- Branch protection applied (скриншот или JSON state)
- Dependabot PR scheduled
- Любые проблемы с environment provisioning

## Coordination

- Зависит от M15 (mypy/bandit fix; иначе CI красный)
- Параллельно с M18 (E2E tests; можно добавить в CI как separate job)
- Параллельно с M16 (A2A; tests появятся в coverage)
- Перед M19 (final review) — CI должен быть зелёным

## Clarification needed

**Перед стартом уточнить у пользователя**:
1. GitHub или GitLab? (Предположение: GitHub — confirm)
2. Branch protection level — strict (block direct push to main) или relaxed?
3. Codecov integration — да/нет? (бесплатно для open source)
4. Auto-merge dependabot PRs — да/нет? (рекомендую нет, требовать review)
