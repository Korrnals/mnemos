<!-- markdownlint-disable MD041 -->
<p align="center">
  <img src="docs/assets/mnemos-banner.svg" alt="Mnemos — сервер памяти и знаний для AI-агентов" width="100%">
</p>

<p align="center">
  <a href="README.md">🇬🇧 English</a> · <strong>🇷🇺 Русский</strong>
</p>

# Mnemos

> **Сервер памяти и знаний для AI-агентов** — назван в честь титаниды, создан для семейства агентов GCW.

[![CI](https://github.com/Korrnals/mnemos/actions/workflows/ci.yml/badge.svg)](https://github.com/Korrnals/mnemos/actions/workflows/ci.yml) [![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776ab)](pyproject.toml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](pyproject.toml) [![Version](https://img.shields.io/badge/version-1.1.3-blueviolet)](CHANGELOG.md)

---

## Лор

В «Теогонии» Гесиода **Мнемосина** (Μνημοσύνη) — титанида памяти. Она, от Зевса, родила девять муз и через них сделала возможным воспоминание мира. Её имя — корень слова *мнемонический*.

Это программное обеспечение носит её имя, потому что создано для той же задачи: **сделать воспоминание возможным для тех, кто мыслит**. AI-агенты, оторванные от единственного разговора, теряют всё, что было до. Mnemos даёт им место, где можно это сохранить — структурированно, с поиском, по контракту — чтобы то, что они узнали, не исчезало с закрытием сессии.

## Что такое Mnemos

Однотенантный, локально-ориентированный сервер памяти. Гибридный поиск (вектор + полнотекстовый) по конвейеру знаний (`raw → processing → processed → published`), поверхность recall для каждого агента, движок политик для автоматизации, слой объяснимости, ингест path-scoped rules и пятиступенчатый контекстный фильтр, который очищает логи и stdout от шума перед отправкой модели. Три эквивалентных поверхности управления — CLI, HTTP, MCP — над единым ядром in-process. SQLite для метаданных, локальный векторный индекс для recall, Obsidian-совместимый vault для людей.

## Как это устроено

```mermaid
flowchart TB
    subgraph CLIENTS["Клиенты"]
        C1(["VS Code · Copilot\nstdio MCP"])
        C2(["CLI — mnemos …"])
        C3(["HTTP API клиент"])
    end

    subgraph IFACE["Слой интерфейсов"]
        MCP["mcp_server.py"]
        FAPI["api/main.py · FastAPI"]
        TYPER["cli/main.py · Typer"]
    end

    MGR(["MemoryManager\nmanager.py"])

    subgraph PROC["Подсистемы обработки"]
        CF["Context Filter\nfilter/"]
        PP["Knowledge Pipeline\npipeline/"]
        RE["Recall Engine\nrecall/"]
        PE["Policy Engine\npolicy/"]
    end

    subgraph BG["Фоновые сервисы"]
        WA["Watchers\nwatchers/"]
        AC["Auto-collect\nauto_collect.py"]
    end

    subgraph STORE["Слой хранения"]
        SQ[("SQLite\nFTS5 · traces · projects")]
        VS[("Vector Store\nnumpy + SQLite")]
        VLT[("Obsidian Vault\nmarkdown mirror")]
    end

    C1 -->|"stdio"| MCP
    C2 --> TYPER
    C3 --> FAPI
    MCP --> MGR
    TYPER --> MGR
    FAPI --> MGR
    MGR --> CF
    MGR --> PP
    MGR --> RE
    MGR --> SQ
    MGR --> VS
    MGR --> VLT
    CF -.->|"raw + clean"| SQ
    PP -->|"status transitions"| SQ
    PP -->|"published upsert"| VS
    RE -->|"FTS5 MATCH"| SQ
    RE -->|"cosine search"| VS
    PE -->|"schedule / trigger"| MGR
    WA -->|"file events"| MGR
    AC -.->|"checkpoint reminder"| MCP
```

Более подробный разбор — модель данных, конечные автоматы, границы безопасности, эксплуатационные аспекты — в [docs/ru/architecture/overview.md](docs/ru/architecture/overview.md).

## Быстрый старт

```bash
git clone https://github.com/Korrnals/mnemos.git
cd mnemos
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
mnemos add --content "Первая запись — используй uv, не pip" \
           --tags project:mnemos agent:tech-writer gcw:learning
mnemos search "uv vs pip" --limit 5
```

Это весь цикл: установка, запись, поиск. Пошаговое руководство первого запуска — [docs/ru/user/getting-started.md](docs/ru/user/getting-started.md).

> **Варианты установки.** Установка из репозитория выше — быстрейший путь для разработки.
> Mnemos 1.1.3 также доступен как [готовый wheel](https://github.com/Korrnals/mnemos/releases/download/v1.1.3/mnemos-1.1.3-py3-none-any.whl) (`pip install <url>`) и как [контейнер](https://ghcr.io/korrnals/mnemos) (`ghcr.io/korrnals/mnemos:1.1.3`) — см. [docs/ru/admin/runbooks/container-deployment.md](docs/ru/admin/runbooks/container-deployment.md).

### Установка одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/install.sh | bash
```

Создаёт venv в `~/.mnemos-venv`, устанавливает свежий wheel с extra `[mcp]` и проверяет CLI `mnemos`. Опции: `--version 1.1.3`, `--extra mcp,ollama`, `--venv ~/custom`, `--no-venv`.

**Контейнер одной командой:**

```bash
export MNEMOS_API__TOTP_MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/install.sh | bash -s -- --container
```

Скачивает `ghcr.io/korrnals/mnemos:latest`, создаёт тома и запускает контейнер на порту 8787. Подробности — [container-deployment.md](docs/ru/admin/runbooks/container-deployment.md).
## Три поверхности, одно ядро

Одно и то же MemoryManager управляет всеми тремя интерфейсами — выберите подходящий.

| Поверхность | Когда использовать… | Документация |
|---------|--------------|-----------|
| **CLI** — `mnemos …` | Вы работаете в шелле, нужен быстрый ad-hoc add/search, или скрипты cron | [docs/ru/user/cli-reference.md](docs/ru/user/cli-reference.md) |
| **HTTP** — `mnemos serve` | У вас не-MCP клиент (веб-дашборд, мобильное приложение, CI runner) | [docs/ru/user/http-api.md](docs/ru/user/http-api.md) |
| **MCP** — `mnemos mcp-server` | Вы VS Code Copilot или любой MCP-aware агент; это путь семейства GCW | [docs/ru/user/mcp-tools.md](docs/ru/user/mcp-tools.md) |

MCP-поверхность также предоставляет **A2A Sessions API** (M16) — постоянный бэкенд для многошаговых разговоров агентов. Пять endpoints (`POST /v1/sessions`, append-turn, range-load, …) чтобы GCW переживал рестарты без потери контекста. См. [docs/ru/architecture/a2a-sessions.md](docs/ru/architecture/a2a-sessions.md).

## Документация

| Страница | Содержание |
|------|----------------|
| [docs/README.md](docs/README.md) | Главная страница документации — выбор языка (EN / RU) |
| [docs/ru/user/getting-started.md](docs/ru/user/getting-started.md) | Первый запуск: установка → первая запись → первый поиск → MCP / HTTP |
| [docs/ru/architecture/overview.md](docs/ru/architecture/overview.md) | Архитектура, модель данных, конечные автоматы, границы безопасности |
| [docs/ru/user/cli-reference.md](docs/ru/user/cli-reference.md) | Все подкоманды `mnemos` с флагами, значениями по умолчанию, примерами |
| [docs/ru/user/mcp-tools.md](docs/ru/user/mcp-tools.md) | Все инструменты `mnemos_*` для VS Code Copilot |
| [docs/ru/user/http-api.md](docs/ru/user/http-api.md) | Все HTTP endpoints (CRUD памяти + A2A Sessions, M16) |
| [docs/ru/architecture/a2a-sessions.md](docs/ru/architecture/a2a-sessions.md) | Контракт agent-to-agent разговоров (M16) |
| [docs/ru/user/tag-contract.md](docs/ru/user/tag-contract.md) | Схема `project:` / `agent:` / `gcw:`, обязательная для каждой записи |
| [docs/ru/admin/security.md](docs/ru/admin/security.md) | Модель угроз, SSRF-защита, FTS5 escape, пиннинг HF Hub |
| [docs/ru/admin/runbooks/](docs/ru/admin/runbooks/) | Установка, миграция, резервное копирование, обновление зависимостей |
| [docs/ru/admin/runbooks/container-deployment.md](docs/ru/admin/runbooks/container-deployment.md) | Сборка, push, compose, podman, Kubernetes, quadlet — полное руководство контейнерного деплоя |
| [docs/project/adr/](docs/project/adr/) | Архитектурные решения (ADR) — *почему* за каждым дизайном |
| [docs/project/milestones.md](docs/project/milestones.md) | Журнал milestones со статусами |
| [CHANGELOG.md](CHANGELOG.md) | Release notes — формат Keep a Changelog |

## Связь с семейством агентов GCW

Mnemos — автономное хранилище для senior-agent команды **GCW (GitHub Copilot Workflow)**. Репозиторий GCW содержит тонкий stub-плагин (`plugins/mnemos-integration`), который работает в деградированном файловом режиме, пока Mnemos недоступен; как только MCP-сервер поднят, stub прозрачно переключается на `mnemos_*` инструменты без изменения кода. Общий контракт — [схема тегов](docs/ru/user/tag-contract.md) — `project:<slug>`, `agent:<slug>` и хотя бы один `gcw:<subtype>` — которую должна нести каждая запись памяти.

## Исходный код, лицензия

- **Исходник**: этот репозиторий, [github.com/Korrnals/mnemos](https://github.com/Korrnals/mnemos).
- **Лицензия**: MIT (см. [pyproject.toml](pyproject.toml)).

## Участие

PR приветствуются. Прочитайте [PLAN.md](PLAN.md) для текущего roadmap и следуйте конвенциям в [docs/](docs/). Запустите `make verify` перед открытием PR.

Git-workflow этого репо: `feat/*` → `dev-<этап>` → `release/X.Y.Z` → `main`; `main` принимает только `release/*` и `hotfix/*` PR. Обязательны Conventional Commits.

---

> **Воспроизведите зелёное состояние**: `make verify` запускает полный quality gate (ruff + mypy --strict + bandit + pip-audit + 209 тестов). Если зелёно — изменение готово к публикации.