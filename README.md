# AI-Brain

Гибридная система долговременной памяти: личная база знаний + RAG-хранилище для AI-агентов.

Единый «мозг», доступный из CLI, REST API, MCP-сервера (Copilot), Telegram-бота и Obsidian.

## Возможности

- **Obsidian-совместимое хранилище** — заметки в markdown с YAML frontmatter
- **Семантический поиск** — vector embeddings через sentence-transformers / Ollama
- **Полнотекстовый поиск** — FTS5 через SQLite
- **Гибридный поиск** — Reciprocal Rank Fusion (RRF) для объединения результатов
- **Ingestion pipeline** — импорт из URL, PDF, DOCX, plain text
- **CLI** — быстрая работа из терминала
- **REST API** — интеграция с любыми сервисами
- **MCP-сервер** — нативная интеграция с GitHub Copilot и другими LLM

## Быстрый старт

```bash
# Установка
cd ai-brain
python -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"

# Конфигурация
cp config.example.yaml config.yaml
# Отредактируйте config.yaml при необходимости

# Добавить заметку
brain add "Python: list comprehension быстрее цикла for в 2-3 раза" --tags python,performance

# Поиск
brain search "оптимизация python"

# Импорт URL
brain add --url https://docs.python.org/3/tutorial/datastructures.html --tags python,docs

# Синхронизация Obsidian vault
brain sync

# Статистика
brain stats
```

## CLI-команды

| Команда | Описание |
| --- | --- |
| `brain add` | Добавить заметку (текст, файл, URL, stdin) |
| `brain search` | Гибридный поиск |
| `brain list` | Список последних записей |
| `brain get <id>` | Получить запись по ID |
| `brain delete <id>` | Удалить запись |
| `brain tags` | Список тегов |
| `brain sync` | Переиндексация Obsidian vault |
| `brain stats` | Статистика |
| `brain serve` | Запуск REST API сервера |

## REST API

Запуск: `brain serve` (по умолчанию `http://127.0.0.1:8787`)

```bash
# Добавить
curl -X POST http://localhost:8787/api/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"content": "Важный факт", "tags": ["test"]}'

# Поиск
curl -X POST http://localhost:8787/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "важный"}'
```

Swagger UI: `http://127.0.0.1:8787/docs`

## MCP-сервер (Copilot)

Добавьте в настройки VS Code (`.vscode/mcp.json`):

```json
{
  "servers": {
    "ai-brain": {
      "command": "python",
      "args": ["-m", "ai_brain.mcp_server"],
      "cwd": "/path/to/ai-brain"
    }
  }
}
```

Доступные инструменты для LLM:
- `brain_search` — поиск по памяти
- `brain_add` — добавить запись
- `brain_get` — получить запись по ID
- `brain_list_tags` — список тегов
- `brain_stats` — статистика
- `brain_ingest_url` — загрузить веб-страницу

## Архитектура

```
Interfaces: CLI | Web UI | REST API | MCP | Telegram
                    ↓
            FastAPI (Core API)
                    ↓
            brain_core (MemoryManager)
                    ↓
    ┌───────────────┼───────────────┐
    Obsidian Vault  ChromaDB        SQLite
    (markdown)      (vectors)       (metadata+FTS)
                    ↓
            Embedding Layer
    (sentence-transformers / Ollama)
```

Подробнее: [docs/architecture.md](docs/architecture.md)

## Структура проекта

```
ai-brain/
├── src/ai_brain/
│   ├── __init__.py
│   ├── config.py          # Конфигурация (YAML + env vars)
│   ├── models.py          # Pydantic-модели данных
│   ├── manager.py         # MemoryManager — ядро системы
│   ├── embedding.py       # Embedding-провайдеры
│   ├── ingestion.py       # Pipeline импорта данных
│   ├── api.py             # FastAPI REST API
│   ├── cli.py             # Typer CLI
│   ├── mcp_server.py      # MCP-сервер для Copilot
│   └── storage/
│       ├── sqlite_store.py  # SQLite + FTS5
│       ├── vector_store.py  # ChromaDB
│       └── vault.py         # Obsidian vault
├── docs/
│   └── architecture.md
├── config.example.yaml
├── pyproject.toml
└── README.md
```

## Лицензия

MIT
