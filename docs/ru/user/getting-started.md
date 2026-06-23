# Начало работы

**🌐 Language / Язык:** [English](../../en/user/getting-started.md) · Русский

> Полное руководство для первого запуска Mnemos — от установки до первого воспоминания, поиска и отзыва агентом.

Эта страница проведёт вас через рабочую установку Mnemos, создание первой записи, первый поиск и первый запуск MCP/HTTP-сервера. Каждая команда здесь выполнима на чистом Linux / macOS / WSL2.

Для общего контекста см. [обзор архитектуры](../architecture/overview.md). Для справки по каждому субкоманде CLI — [cli-reference.md](cli-reference.md). По каждому MCP-инструменту — [mcp-tools.md](mcp-tools.md). По каждому HTTP-эндпоинту — [http-api.md](http-api.md).

---

## Предварительные требования

Mnemos требует Python 3.11 или новее и `git`. Рекомендуем `uv` для быстрой изолированной установки.

| Инструмент | Версия | Зачем |
|------------|--------|-------|
| Python | ≥ 3.11 | Pydantic v2, современные type hints, StrEnum |
| `uv` | последняя | Быстрый, герметичный пакетный менеджер Python |
| `git` | любая | Клонирование репозитория (пропустить при установке из PyPI) |
| `make` | любая | Вспомогательные цели: `make verify`, `make test` |

> **Замечание об ОС.** Mnemos разрабатывается на Linux (Arch, Fedora, Ubuntu 22.04+) и регулярно проходит smoke-тест на macOS. Windows работает через WSL2. Юнит systemd в `contrib/systemd/` только для Linux.

> **Железо.** Стандартная ONNX-модель эмбеддингов (`all-MiniLM-L6-v2`) весит ~25 МБ и комфортно работает на одном ядре CPU. GPU не требуется. VM с 2 vCPU / 2 ГБ ОЗУ достаточно для личного использования.

---

## Установка

```bash
git clone https://github.com/Korrnals/mnemos.git
cd mnemos
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Если `uv` не установлен:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Экстра `[dev]` добавляет `pytest`, `ruff`, `mypy`, `bandit` и `pip-audit` для запуска полного набора проверок.

### Опции установки

`pip install -e ".[dev]"` — точка `.` и есть сам пакет (`mnemos`); `[dev]` и `[mcp]` — **опциональные экстры**. Имя пакета в команде не отсутствует.

| Метод | Команда |
|-------|--------|
| Editable dev (рекомендуется для контрибьюторов) | `pip install -e ".[dev,mcp]"` |
| Из исходников, версионированный | `pip install ".[mcp]"` |
| Released wheel | `pip install https://github.com/Korrnals/mnemos/releases/download/v1.1.1/mnemos-1.1.1-py3-none-any.whl` |
| Контейнер | `podman run -d -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 --env MNEMOS_API__TOTP_MASTER_KEY=<key> ghcr.io/korrnals/mnemos:1.1.1` — см. [container-deployment.md](../admin/runbooks/container-deployment.md) |

### Опциональные экстры LLM-провайдеров

Mnemos умеет вызывать внешние LLM для синтеза (M4) и контекстного фильтра (M10). Устанавливайте только нужное:

```bash
uv pip install -e ".[ollama]"     # локальный Ollama
uv pip install -e ".[openai]"     # OpenAI / Azure OpenAI
uv pip install -e ".[anthropic]"   # Anthropic Claude
uv pip install -e ".[gemini]"     # Google Gemini
```

Провайдер по умолчанию — `ollama`, указывающий на `http://localhost:11434`. Полную матрицу провайдеров см. в [обзоре архитектуры](../architecture/overview.md#llm-providers).

---

## Проверка

Запустите полный набор проверок. Все пять шагов должны завершиться зелёным.

```bash
make verify
```

Цель `verify` выполняет по порядку:

| Шаг | Инструмент | Что проверяет |
|-----|-----------|---------------|
| 1 | `ruff` | Линтинг (PEP-8, порядок импортов, типичные ошибки) |
| 2 | `pytest` | Набор тестов (unit + integration) |
| 3 | `bandit` | Security-линтинг (M9) |
| 4 | `pip-audit` | Сканирование CVE в зависимостях (M15) |
| 5 | напоминание | Выводит напоминание о закреплённых CVE |

Чистый прогон завершается строкой `✅ All verification checks passed`.

Если `pip-audit` сообщает о закреплённой CVE, см. [runbook по обновлению зависимостей](../admin/runbooks/dependency-updates.md) для еженедельного рабочего процесса.

---

## Первая запись (CLI)

CLI построен на Typer и выводит таблицы через Rich. Добавьте первую запись:

```bash
mnemos add --content "Hello world" --tags project:test agent:getting-started gcw:learning
```

Ожидаемый вывод:

```text
✓ Saved: Hello world (550e8400-e29b-41d4-a716-446655440000)
```

Mnemos автоматически:

1. **Записал запись в SQLite** по пути `~/.mnemos/data/mnemos.db`.
2. **Отразил её в Obsidian-vault** `~/.mnemos/vault/` как markdown-файл с YAML-фронтматером.
3. **Проверил контракт тегов** — `project:test` + `agent:getting-started` + `gcw:learning` — корректная тройка M2. Если пропустить один из тегов, вы получите `❌ Tag contract violation: ...` вместо подтверждения.

Контракт тегов описан в [tag-contract.md](tag-contract.md). Коротко: каждая запись требует **ровно одного** `project:<slug>`, **ровно одного** `agent:<slug>` и **хотя бы одного** `gcw:<subtype>` (например, `gcw:learning`, `gcw:bug-pattern`, `gcw:decision`).

> **Замечание.** Только что добавленные записи получают статус `raw`. Индекс векторного поиска включает только записи в статусе `published`. Чтобы перевести запись в `published`, запустите пайплайн (см. [runbook по установке](../admin/runbooks/install.md)) или воспользуйтесь HTTP API `POST /process` (см. [http-api.md](http-api.md)).

---

## Первый поиск

Гибридный поиск объединяет FTS5 SQLite с векторным сходством и объединяет ранжирование через Reciprocal Rank Fusion (RRF).

```bash
mnemos search "hello"
```

Ожидаемый вывод (таблица Rich):

```text
┏━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Score ┃ Title      ┃ Tags                                  ┃ Status   ┃
┡━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ 1.000 │ Hello world│ project:test, agent:getting-started, │ raw      │
│       │            │ gcw:learning                          │          │
└───────┴────────────┴──────────────────────────────────────┴──────────┘
```

Полезные флаги:

| Флаг | Действие |
|------|---------|
| `--limit N` / `-l N` | Максимум результатов (по умолчанию 10) |
| `--project P` / `-p P` | Ограничить одним проектом |

Для программного доступа с расширенными опциями (вес вектора, сырой контент, фильтр по тегам) используйте HTTP API — см. [http-api.md#search](http-api.md#search).

---

## Первый отзыв агентом

`recall` возвращает последние записи. Фильтр по агенту выбирает только то, что сохранил конкретный Copilot-агент (M3):

```bash
mnemos recall --agent getting-started
```

Ожидаемый вывод (список Rich):

```text
Hello world  (550e8400…)
  tags: project:test, agent:getting-started, gcw:learning
```

Комбинируйте с `--project` для дополнительного сужения:

```bash
mnemos recall --agent getting-started --project test --limit 5
```

Это те же данные, которые MCP-инструмент [`mnemos_agent_recall`](mcp-tools.md#mnemos_agent_recall) передаёт Copilot-агентам.

---

## Запуск MCP-сервера

MCP-сервер говорит на stdio JSON-RPC — VS Code Copilot взаимодействует с ним напрямую. **Это основная точка интеграции для GCW-агентов.**

> Пакет `mcp` — опциональная зависимость. Сначала установите её: `pip install -e ".[mcp]"` (или `uv pip install -e ".[mcp]"`).

```bash
mnemos mcp-server
```

Процесс блокируется на stdin/stdout; TCP-порт не занимает. Остановить через `Ctrl+C`.

### Сниппет для `mcp.json` в VS Code

Добавьте в `mcp.json` VS Code (User или Workspace):

```jsonc
{
  "servers": {
    "mnemos": {
      "type": "stdio",
      "command": "mnemos",
      "args": ["mcp-server"],
      "env": {
        "MNEMOS_DATA_DIR": "/home/youruser/.mnemos/data",
        "MNEMOS_VAULT__VAULT_PATH": "/home/youruser/.mnemos/vault"
      }
    }
  }
}
```

После сохранения VS Code отобразит инструменты `mnemos_*` в панели «tools» Copilot Chat. Полный каталог инструментов — в [mcp-tools.md](mcp-tools.md).
