# Документация Mnemos (Русский)

**🌐 Language / Язык:** [English](../en/index.md) · Русский

> Mnemos — автономный MCP-сервер памяти и знаний для LLM/GCW-агентов. Даёт каждому агенту настоящую долгосрочную память: структурированную, доступную для поиска, управляемую строгим контрактом тегов — данные сохраняются между сессиями, перезапусками и сжатием контекста.

---

## Подключить mnemos как MCP-сервер

**MCP — основная точка интеграции.** Именно через неё VS Code Copilot и GCW-агенты взаимодействуют с Mnemos.

### Что реализовано

| Свойство | Значение |
|----------|---------|
| Протокол | MCP поверх **stdio JSON-RPC 2.0** |
| Имя сервера | `mnemos` |
| Транспорт | stdio — без TCP-порта |
| Префикс инструментов | `mnemos_` |
| Исходный код | `src/mnemos/mcp_server.py` |

### Установка MCP-расширения

Пакет `mcp` — **опциональная зависимость**, её нет в базовой или `[dev]`-установке:

```bash
pip install -e ".[mcp]"
# или
uv pip install -e ".[mcp]"
```

### Запуск сервера

```bash
mnemos mcp-server
```

Процесс блокируется на stdin/stdout. Остановить через `Ctrl+C` или EOF на stdin.

### Сниппет для `mcp.json` в VS Code

Добавить в VS Code **User** или **Workspace** `mcp.json`:

```jsonc
{
  "servers": {
    "mnemos": {
      "type": "stdio",
      "command": "mnemos",
      "args": ["mcp-server"],
      "env": {
        "MNEMOS_DATA_DIR": "/home/youruser/.mnemos",
        "MNEMOS_VAULT__VAULT_PATH": "/home/youruser/mnemos-vault"
      }
    }
  }
}
```

После сохранения инструменты `mnemos_*` появятся в панели "tools" Copilot Chat.

### Режим автосбора

Установите `MNEMOS_AUTO_COLLECT=1` в блоке `env` выше, чтобы Mnemos напоминал агенту вызывать `mnemos_save_context` каждые ~6 вызовов инструментов (проактивные чекпоинты). Подробнее: [mcp-tools.md#auto-collect-mode](user/mcp-tools.md#auto-collect-mode) *(Wave 2)*.

### 13 MCP-инструментов (префикс `mnemos_`)

| Инструмент | Назначение |
|-----------|-----------|
| `mnemos_search` | Гибридный поиск FTS5 + вектор (опубликованные записи) |
| `mnemos_add` | Создать запись памяти — **соблюдает контракт тегов GCW** |
| `mnemos_agent_recall` | Recall по агенту (M3) — фильтр по slug агента |
| `mnemos_save_context` | Сохранить чекпоинт сессии |
| `mnemos_recall_context` | Восстановить последний чекпоинт проекта |
| `mnemos_list_recent` | Список последних записей |
| `mnemos_list_tags` | Список тегов с количеством записей |
| `mnemos_ingest_url` | Скачать веб-страницу и сохранить в память |
| `mnemos_watch_start` | Запустить фоновый watcher файлов |
| `mnemos_watch_stop` | Остановить watcher |
| `mnemos_watch_status` | Статус watcher |
| `mnemos_auto_collect_status` | Вектор сигналов уплотнения контекста (M7) |
| `mnemos_stats` | Счётчики здоровья и ключевые пути |

Полный каталог со схемами ввода и примерами: **[user/mcp-tools.md](user/mcp-tools.md)** *(Wave 2)*

Пошаговое подключение в VS Code: **[user/getting-started.md#run-the-mcp-server](user/getting-started.md#run-the-mcp-server)** *(Wave 2)*

---

## Быстрый старт

| Если вы… | Читайте |
|----------|---------|
| Устанавливаете Mnemos впервые | [user/getting-started.md](user/getting-started.md) *(Wave 2)* |
| Подключаете Mnemos к VS Code Copilot | [user/getting-started.md#run-the-mcp-server](user/getting-started.md#run-the-mcp-server) *(Wave 2)* |
| Ищете конкретную команду / флаг | [user/cli-reference.md](user/cli-reference.md) *(Wave 2)* |
| Ищете конкретный MCP-инструмент | [user/mcp-tools.md](user/mcp-tools.md) *(Wave 2)* |
| Разрабатываете HTTP-клиент | [user/http-api.md](user/http-api.md) *(Wave 2)* |
| Разбираетесь в устройстве системы | [architecture/overview.md](architecture/overview.md) *(Wave 2)* |
| Диагностируете проблему | [admin/runbooks/install.md](admin/runbooks/install.md) *(Wave 2)* |

> **Wave 2**: полный перевод пользовательской документации запланирован. Ссылки выше ведут на корректные пути в `ru/`-дереве — файлы появятся в следующем волне.

---

## Документация для пользователей

- [Начало работы](user/getting-started.md) — установка → первая запись → первый поиск → MCP / HTTP *(Wave 2)*
- [Справочник MCP-инструментов](user/mcp-tools.md) — все инструменты `mnemos_*` *(Wave 2)*
- [Справочник HTTP API](user/http-api.md) — все эндпоинты, тела запросов и ответов *(Wave 2)*
- [Справочник CLI](user/cli-reference.md) — все подкоманды `mnemos` *(Wave 2)*
- [Контракт тегов](user/tag-contract.md) — схема M2: `project:`, `agent:`, `gcw:` *(Wave 2)*

---

## Администрирование / Эксплуатация

- [Runbook: Установка](admin/runbooks/install.md) *(Wave 2)*
- [Runbook: Контейнерное развёртывание](admin/runbooks/container-deployment.md) — сборка, залитие, compose, podman, Kubernetes, quadlet.
- [Runbook: Миграция из ai-brain](admin/runbooks/migrate.md) *(Wave 2)*
- [Runbook: Резервное копирование и восстановление](admin/runbooks/backup-restore.md) *(Wave 2)*
- [Runbook: Обновление зависимостей](admin/runbooks/dependency-updates.md) *(Wave 2)*
- [Runbook: CI/CD](admin/runbooks/ci-cd.md) *(Wave 2)*
- [Модель безопасности](admin/security.md) — модель угроз, SSRF-защита, аутентификация *(Wave 2)*

---

## Архитектура

- [Обзор системы](architecture/overview.md) — слоёная архитектура, модель данных, автоматы состояний *(Wave 2)*
- [Конвейер знаний](architecture/overview.md#state-machines) — `raw` → `processing` → `processed` → `published` (M4) *(Wave 2)*
- [A2A Sessions](architecture/a2a-sessions.md) — контракт агент-агент (M16) *(Wave 2)*

---

## Исторические артефакты проекта (только EN)

- [Architecture Decision Records](../project/adr/README.md) — 14 ADR по эволюции M1 → M16
- [Milestones](../project/milestones.md) — журнал вех с легендой статусов
- [Code Review 2026-06](../project/code-review-2026-06.md) — итоги финального код-ревью

---

## Корень репозитория

- [README](../../README.md) — главная страница проекта
- [CHANGELOG](../../CHANGELOG.md) — история релизов
- [PLAN](../../PLAN.md) — план реализации (M1 → M15)

---

_Последнее обновление: 2026-06-17_
