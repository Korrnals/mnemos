# Контракт тегов

**🌐 Language / Язык:** [English](../../en/user/tag-contract.md) · Русский

Mnemos применяет структурированную схему тегов к каждой записи в памяти. Этот документ
описывает контракт, допустимые значения и руководство по миграции.

---

## Зачем нужен контракт тегов?

Записи без согласованной структуры превращаются в неискаемый шум.
Контракт тегов:

- Привязывает каждую запись ровно к **одному проекту** и **одному агенту**
- Классифицирует запись хотя бы **одним Mnemos-подтипом** (когнитивная категория)
- Обеспечивает per-agent recall (M3) и очистку в рамках проекта
- Предотвращает неоднозначные записи с двумя проектами (частый источник загрязнения контекста)

---

## Обязательные теги (должны присутствовать во всех новых записях)

| Тег | Формат | Кардинальность | Назначение |
|-----|--------|----------------|------------ |
| `project:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **ровно 1** | Привязывает запись к кодовой базе / инициативе |
| `agent:<slug>` | `[a-z0-9][a-z0-9\-_]*` | **ровно 1** | Агент, создавший запись |
| `mnemos:<subtype>` | см. таблицу ниже | **не менее 1** | Когнитивная категория |

### Mnemos-подтипы

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
    ["project:myproject", "agent:copilot", "mnemos:learning"],
    strict=True,
)

# Использование модели TagContract напрямую
tc = TagContract(tags=["project:myproject", "agent:copilot", "mnemos:decision"])
print(tc.project)       # "myproject"
print(tc.agent)         # "copilot"
print(tc.mnemos_subtypes)  # {"decision"}

# Передача тегов при создании Memory
from mnemos.models import Memory
m = Memory(
    content="Decided to use FTS5 over a dedicated search service.",
    tags=["project:mnemos", "agent:tech-lead", "mnemos:decision"],
    project="mnemos",
    agent="tech-lead",
)
```

---

## Использование через MCP

```
mnemos_add(
    content="Discovered timing issue in FTS5 query planner.",
    tags=["project:mnemos", "agent:copilot", "mnemos:bug-pattern"],
    project="mnemos",
    agent="copilot",
)
```

---

## Массовое переименование тегов (`gcw:` → `mnemos:` и другие смены префикса)

Команда `mnemos tags rename` (и эквивалентные MCP-инструмент `mnemos_tags_rename` /
HTTP-эндпоинт `POST /tags/rename`) массово переименовывает теги, соответствующие
исходному префиксу, в целевой префикс по всем существующим записям. Это безопасная
замена устаревшей команды `mnemos migrate tags`.

```bash
# Сначала dry-run — только предпросмотр, ничего не записывается (по умолчанию)
mnemos tags rename --from gcw: --to mnemos: --dry-run

# Применить переименование
mnemos tags rename --from gcw: --to mnemos: --no-dry-run

# Ограничить конкретными подтипами
mnemos tags rename --from gcw: --to mnemos: --subtypes decision --subtypes learning --no-dry-run

# Ограничить одним проектом / агентом
mnemos tags rename --from gcw: --to mnemos: --project mnemos --no-dry-run

# Неверные подтипы отправлять в <to_prefix>legacy вместо пропуска
mnemos tags rename --from gcw: --to mnemos: --invalid-to-legacy --no-dry-run
```

**Почему это безопасно:** переименование идёт через `SQLiteStore.update_fields`
(обычный `UPDATE`), поэтому триггер FTS5 `AFTER UPDATE` срабатывает и индекс
external-content остаётся согласованным — в отличие от старого пути `migrate tags`,
который использовал прямую запись в `sqlite3` и обходил триггер. Операция
**идемпотентна**: повторный запуск с теми же аргументами вернёт `renamed=0`,
пот что тегов с `from_prefix:` больше нет.

**Пере-эмбеддинг:** векторы индексируются по `memory_id`, а эмбеддируемый текст
строится из `title + content + tags`. Теги входят в эмбеддируемый текст, поэтому
переименование *технически* меняет вход эмбеддинга, но вклад тегов мал относительно
содержимого. Переименование намеренно **не** пере-эмбеддит — семантический поиск
продолжает работать, потому что хранящиеся векторы всё ещё указывают на те же
`memory_id`, а ветка FTS5 (которая отражает новые теги через триггер) обслуживает
запросы с фильтром по тегам. Если требуется точное выравнивание тег-вектор,
выполните `mnemos reindex` после переименования.

**Аудит-трейл:** каждый вызов записывает одну строку в таблицу трассировок с
`step="tags_rename"`, фиксируя префиксы, флаг dry-run и счётчики.

Возвращаемый отчёт (и вывод CLI) имеет вид:

```json
{
  "scanned": 42,
  "renamed": 18,
  "skipped_invalid": 0,
  "errors": [],
  "dry_run": false,
  "from_prefix": "gcw:",
  "to_prefix": "mnemos:"
}
```

---

## Руководство по миграции (с ai-brain)

В ai-brain обязательной схемы тегов не было. Процесс миграции:

1. Запустите `mnemos migrate-from-ai-brain` — копирует SQLite из ai-brain в хранилище Mnemos.
2. Существующие записи без `project:` / `agent:` получают добавленный тег `mnemos:legacy`
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
| `at least one mnemos:` | Нет тега `mnemos:` |
| `invalid mnemos: subtype` | Подтип не входит в допустимое множество |
| `invalid slug for project:` | Slug содержит заглавные буквы или спецсимволы |
| `invalid slug for agent:` | Slug содержит заглавные буквы или спецсимволы |
