# AI-Brain — Архитектура системы

## Обзор

AI-Brain — гибридная система долговременной памяти: личная база знаний + RAG-хранилище для AI-агентов. Единый «мозг», доступный из CLI, Web UI, API, MCP-сервера, Telegram-бота и Obsidian.

## Ключевые принципы

- **Markdown-first**: человеко-читаемые заметки в формате Obsidian (YAML frontmatter + markdown)
- **Семантический поиск**: vector embeddings поверх текстовых данных
- **Гибридный поиск**: full-text search + vector similarity, ранжирование по релевантности
- **Модульность**: ядро отделено от интерфейсов, каждый интерфейс — тонкий адаптер
- **Local-first**: всё работает локально, без обязательных облачных зависимостей
- **Расширяемость**: плагинная система для источников данных и интерфейсов

---

## Архитектура (слои)

```
┌─────────────────────────────────────────────────────────┐
│                    ИНТЕРФЕЙСЫ                            │
│  ┌─────┐ ┌───────┐ ┌──────┐ ┌─────┐ ┌────────────────┐ │
│  │ CLI │ │Web UI │ │ API  │ │ MCP │ │ Telegram Bot   │ │
│  │Typer│ │Svelte │ │ REST │ │Srv  │ │ aiogram        │ │
│  └──┬──┘ └───┬───┘ └──┬───┘ └──┬──┘ └───────┬────────┘ │
│     │        │        │        │             │          │
├─────┴────────┴────────┴────────┴─────────────┴──────────┤
│                    FastAPI (Core API)                     │
│            GET/POST /memories, /search, /ingest          │
├──────────────────────────────────────────────────────────┤
│                    ЯДРО (brain_core)                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐ │
│  │MemoryManager │ │ SearchEngine │ │IngestionPipeline │ │
│  │  CRUD ops    │ │ hybrid search│ │  parse & embed   │ │
│  └──────┬───────┘ └──────┬───────┘ └────────┬─────────┘ │
│         │                │                   │           │
├─────────┴────────────────┴───────────────────┴──────────┤
│                    ХРАНИЛИЩЕ                              │
│  ┌────────────────┐  ┌───────────────┐  ┌─────────────┐ │
│  │ Obsidian Vault │  │  ChromaDB     │  │  SQLite     │ │
│  │ (markdown)     │  │  (vectors)    │  │  (metadata) │ │
│  └────────────────┘  └───────────────┘  └─────────────┘ │
├──────────────────────────────────────────────────────────┤
│                    EMBEDDING                              │
│  sentence-transformers (local) / Ollama / OpenAI API     │
└──────────────────────────────────────────────────────────┘
```

---

## Компоненты

### 1. Хранилище (Storage Layer)

| Компонент | Назначение | Технология |
| --- | --- | --- |
| Obsidian Vault | Человеко-читаемые заметки, markdown + frontmatter | Файловая система |
| ChromaDB | Векторные эмбеддинги для семантического поиска | ChromaDB (persistent) |
| SQLite | Метаданные, теги, связи, история, кэш | SQLite + aiosqlite |

**Obsidian-совместимость**:
- Каждая «память» — markdown-файл с YAML frontmatter (tags, source, created, etc.)
- Поддержка `[[wiki-links]]` и тегов `#tag`
- Vault-директория настраивается в конфиге
- Файловый watcher отслеживает изменения и переиндексирует

### 2. Embedding Layer

- **По умолчанию**: `sentence-transformers/all-MiniLM-L6-v2` (быстро, ~80MB)
- **Для русского**: `intfloat/multilingual-e5-base` или `cointegrated/rubert-tiny2`
- **Опционально**: Ollama embeddings, OpenAI API
- Embedding-провайдер настраивается через конфиг
- Кэширование эмбеддингов для избежания повторных вычислений

### 3. Ядро (brain_core)

#### MemoryManager
- CRUD для записей памяти (create, read, update, delete)
- Автоматическая генерация эмбеддингов при создании/обновлении
- Синхронизация: markdown-файл ↔ ChromaDB ↔ SQLite
- Теги, категории, приоритеты, TTL (время жизни записи)

#### SearchEngine
- **Семантический поиск**: vector similarity через ChromaDB
- **Полнотекстовый поиск**: FTS5 через SQLite
- **Гибридный поиск**: RRF (Reciprocal Rank Fusion) для объединения результатов
- Фильтрация по тегам, датам, источникам, типам

#### IngestionPipeline
- Парсинг входящих данных из разных источников
- Чанкинг длинных документов (RecursiveCharacterTextSplitter)
- Дедупликация (по хешу контента + cosine similarity)
- Автоматическое извлечение тегов и метаданных

### 4. Источники данных (Ingestors)

| Источник | Метод | Формат |
| --- | --- | --- |
| Ручной ввод | CLI / API / Web | Текст / markdown |
| Obsidian vault | File watcher (watchdog) | Markdown + frontmatter |
| Веб-страницы | URL → trafilatura/BeautifulSoup | HTML → чистый текст |
| Файлы | Загрузка через API | PDF, TXT, MD, DOCX |
| Telegram | Бот (aiogram) → API | Сообщения, файлы |
| LLM-чаты | MCP / экспорт | Диалоги |

### 5. Интерфейсы

#### CLI (Typer)
```bash
brain add "Заметка о важном"                    # быстрое добавление
brain add --file ./document.pdf                  # из файла
brain search "как настроить nginx"               # семантический поиск
brain search --tags python,devops --limit 10     # фильтрация
brain ingest --url https://example.com           # парсинг URL
brain list --recent 20                           # последние записи
brain tags                                       # все теги
brain sync                                       # полная переиндексация
brain serve                                      # запуск API-сервера
```

#### REST API (FastAPI)
```
POST   /api/v1/memories          — создать запись
GET    /api/v1/memories           — список (с пагинацией)
GET    /api/v1/memories/{id}      — получить запись
PUT    /api/v1/memories/{id}      — обновить
DELETE /api/v1/memories/{id}      — удалить
POST   /api/v1/search             — гибридный поиск
POST   /api/v1/ingest             — загрузка/парсинг
GET    /api/v1/tags               — список тегов
POST   /api/v1/sync               — переиндексация
GET    /api/v1/health              — healthcheck
```

#### MCP-сервер
Инструменты для Copilot/LLM-агентов:
- `brain_search` — семантический поиск по памяти
- `brain_add` — добавить новую запись
- `brain_get` — получить запись по ID
- `brain_list_tags` — список тегов
- `brain_ingest_url` — загрузить веб-страницу

#### Telegram Bot (aiogram)
- Отправить текст → сохранить как заметку
- Отправить URL → парсить и сохранить
- Отправить файл → извлечь текст и сохранить
- `/search запрос` → семантический поиск
- `/tags` → список тегов
- `/recent` → последние записи

#### Web UI
- Будет реализован позже (Svelte/React)
- Dashboard: статистика, последние записи, облако тегов
- Поиск с фильтрами
- Редактор заметок

---

## Структура данных

### Memory (запись памяти)

```python
class Memory:
    id: str              # UUID
    content: str         # основной текст
    title: str | None    # заголовок (авто или ручной)
    tags: list[str]      # теги
    source: str          # источник: manual, telegram, web, file, mcp, obsidian
    source_url: str | None
    memory_type: str     # note, fact, snippet, bookmark, conversation
    created_at: datetime
    updated_at: datetime
    embedding: list[float] | None
    metadata: dict       # дополнительные данные
    file_path: str | None  # путь к markdown-файлу в vault
```

### Markdown-файл (Obsidian)

```markdown
---
id: 550e8400-e29b-41d4-a716-446655440000
title: Настройка nginx reverse proxy
tags: [nginx, devops, linux]
source: web
source_url: https://example.com/nginx-guide
memory_type: note
created: 2026-04-10T12:00:00
updated: 2026-04-10T12:00:00
---

# Настройка nginx reverse proxy

Основной контент заметки...
```

---

## Конфигурация

```yaml
# config.yaml
brain:
  vault_path: ~/brain-vault          # Obsidian vault
  data_dir: ~/.ai-brain              # ChromaDB + SQLite

embedding:
  provider: sentence-transformers    # sentence-transformers | ollama | openai
  model: intfloat/multilingual-e5-base
  # ollama_url: http://localhost:11434
  # openai_api_key: ...

search:
  default_limit: 20
  hybrid_alpha: 0.7                  # вес семантического поиска (0=FTS, 1=vector)

api:
  host: 0.0.0.0
  port: 8787

telegram:
  bot_token: ...
  allowed_users: []                  # пустой = все

mcp:
  transport: stdio                   # stdio | sse
```

---

## Путь развития

### Фаза 1 — MVP ✦ (текущая)
- [x] Архитектура и модели данных
- [ ] Core: MemoryManager + ChromaDB + SQLite
- [ ] Embedding layer (sentence-transformers)
- [ ] Гибридный поиск
- [ ] CLI (add, search, list, tags)
- [ ] Obsidian vault sync (read/write)
- [ ] REST API (FastAPI)

### Фаза 2 — Интеграции
- [ ] MCP-сервер для Copilot
- [ ] Telegram-бот
- [ ] Web scraping (ingest URLs)
- [ ] PDF/DOCX парсинг

### Фаза 3 — Продвинутые фичи
- [ ] Web UI
- [ ] Автокатегоризация (LLM-powered)
- [ ] Граф связей между записями
- [ ] Автосаммаризация длинных документов
- [ ] Периодическая консолидация (merge похожих записей)
- [ ] Экспорт/импорт

### Фаза 4 — Масштабирование
- [ ] Миграция на PostgreSQL + pgvector (опционально)
- [ ] Multi-user support
- [ ] Шифрование хранилища
