# Контракт тегов

**🌐 Language / Язык:** [English](../../en/user/tag-contract.md) · Русский

Mnemos применяет структурированную схему тегов к каждой записи в памяти. Этот документ
описывает контракт, допустимые значения и руководство по миграции.

---

## Зачем нужен контракт тегов?

Записи без согласованной структуры превращаются в неискаемый шум.
Контракт тегов:

- Привязывает каждую запись ровно к **одному проекту** и **одному агенту**
- Классифицирует запись хотя бы **одним GCW-подтипом** (когнитивная категория)
- Обеспечивает per-agent recall (M3) и очистку в рамках проекта
- Предотвращает неоднозначные записи с двумя проектами (частый источник загрязнения контекста)

---

## Обязательные теги (должны присутствовать во всех новых записях)

| Тег | Формат | Кардинальность | Назначение |
|-----|--------|----------------|------------ |
| `project:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **ровно 1** | Привязывает запись к кодовой базе / инициативе |
| `agent:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **ровно 1** | Агент, создавший запись |
| `gcw:<subtype>` | см. таблицу ниже | **не менее 1** | Когнитивная категория |

### GCW-подтипы

| Подтип | Когда использовать |
|--------|-------------------|
| `session` | Контрольные точки непрерывности сессии |
| `bug-pattern` | Повторяющиеся сбои, паттерны корневых причин |
| `learning` | Новые факты, усвоенные в ходе задачи |
| `decision` | Явные архитектурные / продуктовые решения |
| `rule` | Жёсткие ограничения и инварианты (из файлов инструкций и т.п.) |
| `open-question` | Нерешённые вопросы, требующие дальнейшего расследования |
| `checkpoint` | Промежуточные снимки для выживания после сжатия контекста |
| `legacy` | Перенесённые записи из ai-brain или хранилищ без контракта |

---

## Опциональные теги

Принимаются, но не обязательны. Неизвестные префиксы, не перечисленные здесь,
отклоняются в strict-режиме.

| Тег | Формат | Назначение |
|-----|--------|------------ |
| `source:<slug>` | любая строка | Происхождение записи (chat, file, url, …) |
| `applyTo:<glob>` | glob файлов | Ограничивает `rule` конкретными путями |
| `milestone:<id>` | любая строка | Связывает запись с этапом проекта |
| `domain:<slug>` | любая строка | Субклассификатор домена внутри проекта |

---

## Режимы валидации

### Strict-режим (`strict_tag_contract=True`, по умолчанию для новых установок)

- Все три обязательных семейства тегов должны присутствовать.
- `TagContractError` выбрасывается, если любой обязательный тег отсутствует, некорректен
  или дублируется.
- Используется в `mnemos_add` (MCP-инструмент) и `Memory(strict_tags=True)`.

### Lax-режим (`strict_tag_contract=False`, для миграций)

- Отсутствие обязательных тегов выдаёт предупреждение, но **не** вызывает исключение.
- Несколько тегов `project:` / `agent:` всё равно вызывают исключение (всегда неоднозначно).
- Используется командой `mnemos migrate-from-ai-brain`.

---

## Python API

```python
from mnemos.models import validate_tag_contract, TagContract, TagContractError

# Валидация списка тегов (strict, выбрасывает исключение при нарушениях)
clean_tags = validate_tag_contract(
    ["project:myproject", "agent:copilot", "gcw:learning"],
    strict=True,
)

# Использование модели TagContract напрямую
tc = TagContract(tags=["project:myproject", "agent:copilot", "gcw:decision"])
print(tc.project)       # "myproject"
print(tc.agent)         # "copilot"
print(tc.gcw_subtypes)  # {"decision"}

# Передача тегов при создании Memory
from mnemos.models import Memory
m = Memory(
    content="Decided to use FTS5 over a dedicated search service.",
    tags=["project:mnemos", "agent:tech-lead", "gcw:decision"],
    project="mnemos",
    agent="tech-lead",
)
```

---

## Использование через MCP

```
mnemos_add(
    content="Discovered timing issue in FTS5 query planner.",
    tags=["project:mnemos", "agent:copilot", "gcw:bug-pattern"],
    project="mnemos",
    agent="copilot",
)
```

---

## Руководство по миграции (с ai-brain)

В ai-brain обязательной схемы тегов не было. Процесс миграции:

1. Запустите `mnemos migrate-from-ai-brain` — копирует SQLite из ai-brain в хранилище Mnemos.
2. Существующие записи без `project:` / `agent:` получают добавленный тег `gcw:legacy`
   и сохраняются с `strict_tags=False`.
3. Запустите `mnemos tags-validate` для получения списка записей с неполным контрактом.
4. Отредактируйте записи вручную или выполните `mnemos tags-validate --auto-patch` для
   применения умолчаний по возможности (`project:unknown`, `agent:unknown`).
5. Переключите `strict_tag_contract=True` в `~/.mnemos/config.yaml` после очистки.

---

## Справка по TagContractError

```
mnemos.models.TagContractError
```

Выбрасывается функцией `validate_tag_contract(..., strict=True)` и `TagContract(strict=True)`.

Типичные сообщения:

| Фрагмент сообщения | Причина |
|--------------------|---------|
| `exactly one project:` | 0 или ≥2 тегов `project:` |
| `exactly one agent:` | 0 или ≥2 тегов `agent:` |
| `at least one gcw:` | Нет тега `gcw:` |
| `invalid gcw: subtype` | Подтип не входит в допустимое множество |
| `invalid slug for project:` | Slug содержит заглавные буквы или спецсимволы |
| `invalid slug for agent:` | Slug содержит заглавные буквы или спецсимволы |
