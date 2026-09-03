"""Synthetic memory-shaped RU+EN templates for the NM-1a dataset (ADR-0021).

Programmatic generation only — no external dataset downloads in NM-1a
(that is NM-1b+ if ever needed). Template families mirror the mnemos
memory shapes: notes, chat excerpts, code headlines, decisions, rules.

Round-2 expansion: the pilot pool (~650 unique texts repeated 130x toward
the 100k cap) starved the student of variety. Every family is now a full
deterministic cross-product over placeholder vocabularies (projects,
topics, modules, versions, dates, severities, statuses, names...), so the
pool carries thousands of genuinely unique rows. New families cover
memory shapes the pilot lacked: versioned tech notes, bug reports, commit
messages, config snippets (yaml/json/env), docstrings, agent-human
dialogs, paper digests, and RU-mixed code comments.

Language mix: families are duplicated per language with symmetric
vocabularies, so the candidate pool lands at ~50 % RU (quota enforced
downstream by the counter in ``prepare_dataset.py`` — this module only
shapes the candidate pool).

Determinism: family content is a fixed cross-product (no RNG draws at
all); the caller-supplied ``random.Random`` seed drives only the pool
shuffle. Same seed → byte-identical pool. The module holds no global RNG
state.

Structure: each family returns a list of strings given a
``random.Random``. ``TEMPLATE_FAMILIES`` is the registry consumed by
``prepare_dataset.py``; ``collect_synthetic`` is the collector-named
entry point used by the round-2 verification tooling.

Uniqueness invariant: every loop axis (vocabulary value) is interpolated
into every template shape of its family, so cross-products never emit
duplicate texts.
"""

from __future__ import annotations

import random
from collections.abc import Callable

# ── Domain vocabularies (EN/RU lists are length-symmetric by design) ─────────

_PROJECTS_EN = [
    "aurora-api",
    "vault-ui",
    "mnemos-core",
    "atlas-parser",
    "helios-index",
    "orbis-gateway",
    "nova-search",
    "tessa-worker",
]
_PROJECTS_RU = [
    "аурора-апи",
    "волт-юи",
    "мнемос-ядро",
    "атлас-парсер",
    "гелиос-индекс",
    "орбис-шлюз",
    "нова-поиск",
    "тесса-воркер",
]

_TOPICS_EN = [
    "rate limiting",
    "connection pooling",
    "database migrations",
    "health probes",
    "token refresh",
    "cache eviction",
    "retry backoff",
    "schema validation",
    "log rotation",
    "deployment runbook",
    "auth token lifecycle",
    "webhook delivery",
    "background jobs",
    "error budgets",
    "config reload",
    "idempotency keys",
    "graceful shutdown",
    "circuit breaker",
    "pagination cursors",
    "audit logging",
    "zero-downtime rollout",
    "backpressure",
    "schema versioning",
    "secret rotation",
]
_TOPICS_RU = [
    "ограничение частоты запросов",
    "пул соединений",
    "миграции базы данных",
    "проверки здоровья",
    "обновление токенов",
    "вытеснение из кэша",
    "повторные попытки с задержкой",
    "валидация схемы",
    "ротация логов",
    "инструкция развёртывания",
    "жизненный цикл токенов доступа",
    "доставка вебхуков",
    "фоновые задачи",
    "бюджеты ошибок",
    "перезагрузка конфигурации",
    "ключи идемпотентности",
    "корректное завершение работы",
    "размыкатель цепи",
    "курсоры пагинации",
    "аудит-журналирование",
    "бесшовное обновление",
    "обратное давление",
    "версионирование схемы",
    "ротация секретов",
]

_MODULES_EN = [
    "src/api/routes.py",
    "src/core/pool.py",
    "src/auth/tokens.py",
    "src/db/migrate.py",
    "src/jobs/worker.py",
    "src/cache/store.py",
    "src/search/indexer.py",
    "src/queue/consumer.py",
    "src/webhooks/delivery.py",
    "src/metrics/export.py",
]
_MODULES_RU = [
    "модуль маршрутов API",
    "модуль пула соединений",
    "модуль токенов авторизации",
    "модуль миграций БД",
    "модуль воркеров задач",
    "модуль кэш-хранилища",
    "модуль индексации поиска",
    "модуль консьюмера очереди",
    "модуль доставки вебхуков",
    "модуль экспорта метрик",
]

_AGENTS_EN = [
    "backend-agent",
    "ui-agent",
    "devops-agent",
    "research-agent",
    "qa-agent",
    "data-agent",
]
_AGENTS_RU = [
    "бэкенд-агент",
    "фронтенд-агент",
    "девопс-агент",
    "исследовательский агент",
    "qa-агент",
    "агент данных",
]

_NAMES_EN = ["Alex", "Marta", "Dana", "Igor", "Priya", "Victor"]
_NAMES_RU = ["Алексей", "Мария", "Дана", "Игорь", "Прия", "Виктор"]

# Shared (language-neutral) vocabularies — versions, dates, ids, keys.

_VERSIONS = ["1.4.2", "2.0.0-rc3", "2.3.1", "3.11.4", "5.7.29", "8.2.0", "0.9.1", "23.4.0"]

_DATES = [
    "2024-11-04",
    "2025-01-16",
    "2025-03-08",
    "2025-05-21",
    "2025-07-02",
    "2025-08-19",
    "2025-09-30",
    "2026-01-12",
]

_SEVERITIES_EN = ["blocker", "critical", "major", "minor"]
_SEVERITIES_RU = ["блокирующий", "критический", "существенный", "незначительный"]

_BUG_STATUSES_EN = [
    "open",
    "in progress",
    "blocked on review",
    "fixed — awaiting deploy",
    "cannot reproduce",
]
_BUG_STATUSES_RU = [
    "открыт",
    "в работе",
    "заблокирован ревью",
    "исправлен — ждёт выката",
    "не воспроизводится",
]

_COMMIT_TYPES = ["feat", "fix", "refactor", "test", "docs", "chore"]
_COMMIT_SCOPES = [
    "api",
    "pool",
    "auth",
    "db",
    "jobs",
    "cache",
    "search",
    "queue",
    "webhooks",
    "metrics",
]

_ENV_KEYS = [
    "MAX_RETRIES",
    "POOL_SIZE",
    "TIMEOUT_S",
    "RATE_LIMIT_RPS",
    "CACHE_TTL_S",
    "WORKERS",
    "TOKEN_TTL_S",
    "BATCH_SIZE",
]
_ENV_VALUES = ["1", "3", "10", "30", "60", "300"]

_VENUES = ["NeurIPS", "ICLR", "ACL", "KDD", "VLDB"]

_FUNCS = ["fetch", "index", "validate", "refresh", "compress", "merge", "rank", "watch"]
_PARAMS = ["limit", "timeout_s", "batch_size", "retries", "cursor", "min_score"]

_SNIPPET_NAMES_EN = [
    "user",
    "session",
    "token",
    "cache",
    "job",
    "hook",
    "metric",
    "quota",
    "index",
    "stream",
]
_SNIPPET_NAMES_RU = [
    "пользователь",
    "сессия",
    "токен",
    "кэш",
    "задача",
    "вебхук",
    "метрика",
    "квота",
    "индекс",
    "поток",
]

_MIXED_TERMS = [
    "rate limiter",
    "token bucket",
    "exponential backoff",
    "dead-letter queue",
    "idempotency key",
    "circuit breaker",
    "read replica",
    "write-ahead log",
]
_MIXED_ENTITIES = ["documents", "embeddings", "queries", "batches", "shards", "snapshots"]

# ── Template families ────────────────────────────────────────────────────────
#
# Each family: (name, callable(rng) -> list[str]). Families materialise the
# full cross-product of their vocabularies (every axis appears in every
# shape), so the pool carries thousands of unique rows without RNG draws;
# the rng argument is kept for registry compatibility and is used only by
# the pool shuffle in generate_synthetic().


def _en_notes(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Note on {t} in {p}: the current approach works but needs a review "
        "before the next release.",
        "Decision: {t} in {p} stays as implemented; revisit only after a measured regression.",
        "Reminder for {p}: document {t} in the runbook, the on-call agent kept asking about it.",
        "Follow-up on {p}: the {t} checklist is updated, link lives in the wiki.",
    ]
    for topic in _TOPICS_EN:
        for proj in _PROJECTS_EN:
            for shape in shapes:
                out.append(shape.format(t=topic, p=proj))
    return out


def _ru_notes(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Заметка о теме «{t}» в проекте {p}: текущий подход работает, но требует "
        "ревью перед следующим релизом.",
        "Решение: {t} в {p} оставляем как реализовано; пересмотреть только после "
        "измеренной деградации.",
        "Напоминание по {p}: описать «{t}» в инструкции дежурному — агент постоянно спрашивает.",
        "Продолжение по {p}: чек-лист по теме «{t}» обновлён, ссылка в вики.",
    ]
    for topic in _TOPICS_RU:
        for proj in _PROJECTS_RU:
            for shape in shapes:
                out.append(shape.format(t=topic, p=proj))
    return out


def _en_chat(rng: random.Random) -> list[str]:
    out: list[str] = []
    openers = ["Quick check:", "Heads up:", "Found it —", "Follow-up on", "Status:", "FYI:"]
    closers = [
        "will do.",
        "please confirm.",
        "no action needed yet.",
        "adding it to the backlog.",
        "see the linked issue.",
        "ping me after the deploy.",
    ]
    for opener in openers:
        for topic in _TOPICS_EN[:12]:
            for closer in closers:
                out.append(
                    f"{opener} {topic} on staging behaved differently after the rollout — {closer}"
                )
    return out


def _ru_chat(rng: random.Random) -> list[str]:
    out: list[str] = []
    openers = [
        "Быстрая проверка:",
        "Обрати внимание:",
        "Нашёл причину —",
        "Продолжение по",
        "Статус:",
        "Для сведения:",
    ]
    closers = [
        "сделаю.",
        "подтверди, пожалуйста.",
        "действий пока не нужно.",
        "добавил в бэклог.",
        "см. связанную задачу.",
        "напиши после выката.",
    ]
    for opener in openers:
        for topic in _TOPICS_RU[:12]:
            for closer in closers:
                out.append(
                    f"{opener} тема «{topic}» на стейджинге повела себя "
                    f"иначе после выката — {closer}"
                )
    return out


def _en_code_headlines(rng: random.Random) -> list[str]:
    out: list[str] = []
    for module in _MODULES_EN:
        out.append(
            f"{module}: extract the retry helper into a shared module; three copies already exist."
        )
        out.append(
            f"Refactor {module} — split the read path from the write path, unit tests first."
        )
        out.append(f"Fix in {module}: off-by-one in the pagination cursor, regression test added.")
        out.append(
            f"{module}: replace bare except with typed errors, keep the log line on failure."
        )
        out.append(f"Perf note on {module}: the hot path allocates per request — reuse the buffer.")
        out.append(
            f"Coverage gap in {module}: the error branch has no test, add one before release."
        )
    return out


def _ru_code_headlines(rng: random.Random) -> list[str]:
    out: list[str] = []
    for module in _MODULES_RU:
        out.append(f"{module}: вынести хелпер повторов в общий модуль — уже три копии.")
        out.append(f"Рефакторинг {module}: разделить чтение и запись, сначала юнит-тесты.")
        out.append(
            f"Исправление в {module}: ошибка на единицу в курсоре пагинации, добавлен регресс-тест."
        )
        out.append(
            f"{module}: заменить голый except на типизированные ошибки, лог при сбое сохранить."
        )
        out.append(
            f"Про производительность {module}: горячий путь аллоцирует на каждый "
            "запрос — переиспользовать буфер."
        )
        out.append(f"Пробел в покрытии {module}: ветка ошибок без теста, добавить до релиза.")
    return out


def _en_rules(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Rule for {a}: never edit {p} config directly; propose the change in a review first.",
        "{a} convention in {p}: every side-effecting call must be idempotent and logged.",
        "{a} guardrail for {p}: any schema change ships with a rollback note.",
    ]
    for agent in _AGENTS_EN:
        for proj in _PROJECTS_EN:
            for shape in shapes:
                out.append(shape.format(a=agent, p=proj))
    return out


def _ru_rules(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Правило для {a}: не править конфиг {p} напрямую; сначала предложить изменение в ревью.",
        "Конвенция {a} в {p}: каждый побочный вызов должен быть идемпотентным и логироваться.",
        "Ограничение {a} для {p}: любое изменение схемы едет с заметкой об откате.",
    ]
    for agent in _AGENTS_RU:
        for proj in _PROJECTS_RU:
            for shape in shapes:
                out.append(shape.format(a=agent, p=proj))
    return out


def _en_snippets(rng: random.Random) -> list[str]:
    out: list[str] = []
    patterns = [
        "async def fetch_{n}(client, key): return await client.get(f'/v1/{n}/{{key}}')",
        "def retry_{n}(fn, attempts=3): return backoff(fn, attempts, jitter=True)",
        "class {n}Store: def __init__(self, dsn): self._pool = create_pool(dsn)",
        "@route('POST', '/{n}') def handle_{n}(req): return validate(req.schema)",
        "async def stream_{n}(ws): async for msg in ws: yield decode(msg)",
        "def validate_{n}(payload, schema): return schema.match(payload) or reject(payload)",
    ]
    for pat in patterns:
        for n in _SNIPPET_NAMES_EN:
            out.append(
                f"Code snippet ({n}): {pat.format(n=n)} — usage note: keep timeouts explicit."
            )
    return out


def _ru_snippets(rng: random.Random) -> list[str]:
    out: list[str] = []
    notes = [
        "сниппет для {n}: таймауты задавать явно, без дефолтов библиотеки.",
        "пример работы с {n}: ретраи — с джиттером, не фиксированным интервалом.",
        "паттерн для {n}: пул соединений создавать один раз на процесс.",
        "заголовок правки по {n}: сначала тест, затем реализация.",
        "стриминг для {n}: медленного потребителя обрабатывать явно, не копить буфер.",
        "валидация для {n}: схему закрепить константой, не собирать на лету.",
    ]
    for note in notes:
        for n in _SNIPPET_NAMES_RU:
            out.append(note.format(n=n))
    return out


def _en_meeting(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Meeting excerpt ({t}): we agreed to ship behind a flag and measure "
        "before enabling by default.",
        "Standup note on {t}: blocked on review, owner picked it up, ETA this week.",
        "Retro on {t}: the fix worked, but the alert fired too late — tighten the SLO.",
    ]
    for topic in _TOPICS_EN:
        for shape in shapes:
            out.append(shape.format(t=topic))
    return out


def _ru_meeting(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Выдержка из встречи ({t}): договорились выкатить за флагом и измерить "
        "до включения по умолчанию.",
        "Заметка со стендапа по «{t}»: заблокировано ревью, ответственный взял "
        "задачу, срок — на этой неделе.",
        "Ретро по «{t}»: исправление сработало, но алерт пришёл поздно — ужесточить SLO.",
    ]
    for topic in _TOPICS_RU:
        for shape in shapes:
            out.append(shape.format(t=topic))
    return out


# ── New families (round 2) ───────────────────────────────────────────────────


def _en_tech_notes(rng: random.Random) -> list[str]:
    out: list[str] = []
    for i, ver in enumerate(_VERSIONS):
        old, new = 180 - 5 * i, 90 + 4 * i
        rps, cpu, conn = 800 + 150 * i, 40 + 4 * i, 20 + 3 * i
        fixes, flags, days = 3 + i % 4, 1 + i % 3, 2 + i % 5
        shapes = [
            f"Upgrade note: {{p}} moved to v{ver}; p99 on reads went from "
            f"{old} ms to {new} ms after the swap.",
            f"Capacity check on {{p}} (build {ver}): sustained {rps} rps for 30 min, "
            f"CPU at {cpu} %, zero errors.",
            f"Regression in {{p}} after {ver}: pool exhaustion at {conn} connections, "
            "fixed by raising the ceiling.",
            f"Changelog entry for {{p}} {ver}: {fixes} fixes, {flags} flags added, "
            f"rollout over {days} days.",
        ]
        for proj in _PROJECTS_EN:
            for shape in shapes:
                out.append(shape.format(p=proj))
    return out


def _ru_tech_notes(rng: random.Random) -> list[str]:
    out: list[str] = []
    for i, ver in enumerate(_VERSIONS):
        old, new = 180 - 5 * i, 90 + 4 * i
        rps, cpu, conn = 800 + 150 * i, 40 + 4 * i, 20 + 3 * i
        fixes, flags, days = 3 + i % 4, 1 + i % 3, 2 + i % 5
        shapes = [
            f"Заметка об обновлении: {{p}} переехал на версию {ver}; p99 на чтениях "
            f"упал с {old} мс до {new} мс после замены.",
            f"Проверка ёмкости {{p}} (сборка {ver}): держали {rps} rps 30 минут, "
            f"CPU на {cpu} %, ошибок нет.",
            f"Регрессия в {{p}} после {ver}: исчерпание пула на {conn} соединениях, "
            "вылечили поднятым лимитом.",
            f"Запись в ченджлоге {{p}} {ver}: исправлений — {fixes}, флагов "
            f"добавлено — {flags}, раскатка {days} дн.",
        ]
        for proj in _PROJECTS_RU:
            for shape in shapes:
                out.append(shape.format(p=proj))
    return out


def _en_bug_reports(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Bug #{id} [{sev}] in {p}: the pagination cursor skips every 11th row on descending sort.",
        "Issue #{id} ({sev}) — {p} drops the Authorization header on retry; "
        "repro is two curl lines.",
        "Ticket #{id} / {sev}: {p} double-fires the webhook on a 302; the dedup key is missing.",
        "Defect #{id}, severity {sev}: {p} config reload leaks one file handle per SIGHUP.",
    ]
    for i, proj in enumerate(_PROJECTS_EN):
        for j, sev in enumerate(_SEVERITIES_EN):
            bug_id = 1000 + 37 * i + 11 * j
            date = _DATES[(2 * i + j) % len(_DATES)]
            status = _BUG_STATUSES_EN[(i + j) % len(_BUG_STATUSES_EN)]
            for shape in shapes:
                out.append(
                    f"{shape.format(id=bug_id, sev=sev, p=proj)} Reported {date}, status: {status}."
                )
    return out


def _ru_bug_reports(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Баг #{id} [{sev}] в {p}: курсор пагинации пропускает каждый 11-й ряд "
        "при сортировке убыванием.",
        "Инцидент #{id} ({sev}) — {p} теряет заголовок Authorization при повторе; "
        "репро на две строки curl.",
        "Тикет #{id} / {sev}: {p} дважды доставляет вебхук на 302; нет ключа дедупликации.",
        "Дефект #{id}, серьёзность {sev}: перезагрузка конфига {p} течёт дескриптором "
        "на каждый SIGHUP.",
    ]
    for i, proj in enumerate(_PROJECTS_RU):
        for j, sev in enumerate(_SEVERITIES_RU):
            bug_id = 1000 + 37 * i + 11 * j
            date = _DATES[(2 * i + j) % len(_DATES)]
            status = _BUG_STATUSES_RU[(i + j) % len(_BUG_STATUSES_RU)]
            for shape in shapes:
                out.append(
                    f"{shape.format(id=bug_id, sev=sev, p=proj)} "
                    f"Зарегистрирован {date}, статус: {status}."
                )
    return out


def _en_commits(rng: random.Random) -> list[str]:
    out: list[str] = []
    descs = [
        "add exponential backoff with jitter",
        "split the read path from the write path",
        "pin the transitive dep that broke CI",
        "replace bare except with typed errors",
        "cover the boundary case with tests",
    ]
    for i, typ in enumerate(_COMMIT_TYPES):
        for j, scope in enumerate(_COMMIT_SCOPES):
            for k, desc in enumerate(descs):
                ticket = 400 + 50 * i + 5 * j + k
                out.append(f"{typ}({scope}): {desc} (refs #{ticket})")
    return out


def _ru_commits(rng: random.Random) -> list[str]:
    out: list[str] = []
    descs = [
        "добавить экспоненциальный бэкофф с джиттером",
        "разделить путь чтения и путь записи",
        "закрепить транзитивную зависимость, сломавшую CI",
        "заменить голый except на типизированные ошибки",
        "покрыть граничный случай тестами",
    ]
    for i, typ in enumerate(_COMMIT_TYPES):
        for j, scope in enumerate(_COMMIT_SCOPES):
            for k, desc in enumerate(descs):
                ticket = 400 + 50 * i + 5 * j + k
                out.append(f"{typ}({scope}): {desc} (refs #{ticket})")
    return out


def _en_config(rng: random.Random) -> list[str]:
    out: list[str] = []
    # yaml: project x replicas (2 shapes)
    yaml_shapes = [
        "config/{p}/deploy.yaml: replicas: {r}, strategy: canary, probe: /healthz, timeout_s: 30",
        "helm values for {p}: replicaCount: {r}, autoscaling on at 70 % cpu, maxReplicas: {rm}",
    ]
    for proj in _PROJECTS_EN:
        for r in (2, 4, 8):
            for shape in yaml_shapes:
                out.append(shape.format(p=proj, r=r, rm=r * 4))
    # json: project (3 shapes, numbers derived from the project index)
    for i, proj in enumerate(_PROJECTS_EN):
        rps, retries, interval = 100 + 50 * i, 1 + i % 4, 10 + 5 * i
        out.append(
            f'json — {{ "name": "{proj}", "liveness": {{ "path": "/healthz", '
            f'"interval_s": {interval} }} }}'
        )
        out.append(
            f'json limits for {proj}: {{"rps": {rps}, "burst": {rps * 2}, "retries": {retries}}}'
        )
        out.append(f'json rollout ({proj}): {{"canary": true, "steps": [1, 10, 50, 100]}}')
    # env: key x value (2 shapes)
    env_shapes = [
        "env: {k}={v}  # deploy override, revert after the rollout",
        "dotenv line: {k}={v} — set in the staging namespace only",
    ]
    for key in _ENV_KEYS:
        for val in _ENV_VALUES:
            for shape in env_shapes:
                out.append(shape.format(k=key, v=val))
    return out


def _ru_config(rng: random.Random) -> list[str]:
    out: list[str] = []
    yaml_shapes = [
        "конфиг {p}/deploy.yaml: реплики: {r}, стратегия: canary, проба: /healthz, таймаут: 30 с",
        "values для {p}: replicaCount: {r}, автоскейлинг от 70 % CPU, maxReplicas: {rm}",
    ]
    for proj in _PROJECTS_RU:
        for r in (2, 4, 8):
            for shape in yaml_shapes:
                out.append(shape.format(p=proj, r=r, rm=r * 4))
    for i, proj in enumerate(_PROJECTS_RU):
        rps, retries, interval = 100 + 50 * i, 1 + i % 4, 10 + 5 * i
        out.append(
            f'json — {{ "name": "{proj}", "liveness": {{ "path": "/healthz", '
            f'"interval_s": {interval} }} }}'
        )
        out.append(
            f'json лимиты для {proj}: {{"rps": {rps}, "burst": {rps * 2}, "retries": {retries}}}'
        )
        out.append(f'json раскатка ({proj}): {{"canary": true, "steps": [1, 10, 50, 100]}}')
    env_shapes = [
        "env: {k}={v}  # переопределение на выкат, откатить после релиза",
        "строка dotenv: {k}={v} — только в стейджинговом namespace",
    ]
    for key in _ENV_KEYS:
        for val in _ENV_VALUES:
            for shape in env_shapes:
                out.append(shape.format(k=key, v=val))
    return out


def _en_docstrings(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        '"""Validate {param} inside {f}(); raises ValueError on a bad shape — see tests."""',
        '"""{f}({param}): deprecated since 2.1, use {f}_v2; removal tracked in the backlog."""',
        '"""Async {f}: {param} bounds the fan-out; do not raise it above 64."""',
    ]
    for func in _FUNCS:
        for param in _PARAMS:
            for shape in shapes:
                out.append(shape.format(f=func, param=param))
    return out


def _ru_docstrings(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        '"""Валидирует {param} внутри {f}(); бросает ValueError при плохой форме — см. тесты."""',
        '"""{f}({param}): устарело с 2.1, используйте {f}_v2; удаление в бэклоге."""',
        '"""Асинхронный {f}: {param} ограничивает веер вызовов; выше 64 не поднимать."""',
    ]
    for func in _FUNCS:
        for param in _PARAMS:
            for shape in shapes:
                out.append(shape.format(f=func, param=param))
    return out


def _en_agent_chat(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Human: can we enable {t} today?\n"
        "{a}: yes — behind a flag, watch the error budget.\n"
        "Human: who rolls it back?\n"
        "{a}: on-call, the runbook is pinned.",
        "Human: {t} broke on staging.\n"
        "{a}: reproduced; the hotfix is in review, ETA an hour.\n"
        "Human: page the owner?\n"
        "{a}: already done.",
        "Human: quick question on {t}.\n"
        "{a}: the design note is in the wiki, section 4.\n"
        "Human: is it current?\n"
        "{a}: as of last week, yes.",
        "Human: status on {t}?\n"
        "{a}: measuring on 5 % traffic, curves look flat.\n"
        "Human: full rollout?\n"
        "{a}: after the SLO review.",
    ]
    for agent in _AGENTS_EN:
        for topic in _TOPICS_EN[:10]:
            for shape in shapes:
                out.append(shape.format(t=topic, a=agent))
    return out


def _ru_agent_chat(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Человек: можно включить «{t}» уже сегодня?\n"
        "{a}: да — за флагом, следить за бюджетом ошибок.\n"
        "Человек: кто откатывает?\n"
        "{a}: дежурный, инструкция закреплена.",
        "Человек: «{t}» сломалось на стейджинге.\n"
        "{a}: воспроизвёл; хотфикс в ревью, через час.\n"
        "Человек: пейджить владельца?\n"
        "{a}: уже сделал.",
        "Человек: короткий вопрос по «{t}».\n"
        "{a}: проектная заметка в вики, раздел 4.\n"
        "Человек: она актуальна?\n"
        "{a}: на прошлой неделе — да.",
        "Человек: статус по «{t}»?\n"
        "{a}: меряем на 5 % трафика, кривые ровные.\n"
        "Человек: полная раскатка?\n"
        "{a}: после ревью SLO.",
    ]
    for agent in _AGENTS_RU:
        for topic in _TOPICS_RU[:10]:
            for shape in shapes:
                out.append(shape.format(t=topic, a=agent))
    return out


def _en_science(rng: random.Random) -> list[str]:
    out: list[str] = []
    findings = [
        "contrastive fine-tuning beats pure keyword recall on the RU mix",
        "int8 quantisation keeps over 99 % of cosine ranking quality",
        "matryoshka embeddings shrink the index 4x at a 2 % recall cost",
        "hard negatives improve slot alignment more than extra epochs",
        "cross-encoder reranking recovers most of the lost recall@10",
        "domain shift degrades short-query embeddings twice as fast",
        "a deterministic seed matters more than batch size below 10k pairs",
        "late chunking beats sentence pooling on long notes",
        "hybrid BM25+dense lifts nDCG on rare entities",
        "distillation preserves teacher geometry best with cosine MSE",
    ]
    measures = ["n=4k, p<0.01", "n=12k, p<0.05", "n=40k, p<0.001"]
    for venue in _VENUES:
        for finding in findings:
            for measure in measures:
                out.append(f"Paper digest ({venue}): {finding} — {measure}.")
    return out


def _ru_science(rng: random.Random) -> list[str]:
    out: list[str] = []
    findings = [
        "контрастивная донастройка бьёт чисто ключевой поиск на русскоязычной смеси",
        "int8-квантизация сохраняет свыше 99 % качества косинусного ранжирования",
        "матрёшкины эмбеддинги дают индекс в 4 раза меньше ценой 2 % полноты",
        "жёсткие негативы улучшают выравнивание слотов сильнее, чем лишние эпохи",
        "переранжирование кросс-энкодером возвращает большую часть потерянного recall@10",
        "сдвиг домена ухудшает эмбеддинги коротких запросов вдвое быстрее",
        "детерминизм seed'а важнее размера батча на выборках до 10 тыс. пар",
        "поздняя нарезка обгоняет пословное усреднение на длинных заметках",
        "гибрид BM25+dense поднимает nDCG на редких сущностях",
        "дистилляция лучше всего сохраняет геометрию учителя при MSE по косинусу",
    ]
    measures = ["n=4 тыс., p<0,01", "n=12 тыс., p<0,05", "n=40 тыс., p<0,001"]
    for venue in _VENUES:
        for finding in findings:
            for measure in measures:
                out.append(f"Выжимка из статьи ({venue}): {finding} — {measure}.")
    return out


def _en_mixed(rng: random.Random) -> list[str]:
    out: list[str] = []
    # code one-liners with English inline comments
    snippets = [
        "result = [x for x in {e} if x.ok]  # drop invalid ones, count goes to the log",
        "for item in {e}: process(item)  # one at a time: keep memory flat",
        "merged = dedupe({e})  # hash-based dedup, order preserved",
        "chunk = len({e}) // 8  # split into eight parts, remainder joins the last batch",
        "cache.update({e})  # refresh the cache after commit, not before",
        'log.debug("{e}: %d items", len({e}))  # debug level, not info',
    ]
    for snippet in snippets:
        for entity in _MIXED_ENTITIES:
            out.append(snippet.format(e=entity))
    # prose mixing English narration with the same technical terms
    sentences = [
        "Traced a regression in the {term} path — added a metric and an alert.",
        "On the {term}: behaviour diverges from the docs, filed an issue.",
        "Debugged the {term}: root cause is the default timeout, fix is in review.",
        "Reminder: the {term} moves to the new schema on Thursday.",
        "Checkpoint passed for the {term}: limits hold, no alerts fired.",
    ]
    for sentence in sentences:
        for term in _MIXED_TERMS:
            out.append(sentence.format(term=term))
    return out


def _ru_mixed(rng: random.Random) -> list[str]:
    out: list[str] = []
    # code one-liners with Cyrillic inline comments
    snippets = [
        "result = [x for x in {e} if x.ok]  # отбрасываем невалидные, счётчик пишем в лог",
        "for item in {e}: process(item)  # по одному за раз: держим память плоской",
        "merged = dedupe({e})  # дедупликация по хэшу, порядок сохраняем",
        "chunk = len({e}) // 8  # делим на восемь частей, остаток в последний батч",
        "cache.update({e})  # кэш обновляем после коммита, не до",
        'log.debug("{e}: %d items", len({e}))  # отладочный уровень, не info',
    ]
    for snippet in snippets:
        for entity in _MIXED_ENTITIES:
            out.append(snippet.format(e=entity))
    # RU-EN mix: Russian narration around English technical terms
    sentences = [
        "Зафиксировал деградацию {term} на стейджинге — добавил метрику и алерт.",
        "По {term}: поведение отличается от документации, завёл задачу.",
        "Разобрался с {term}: причина в дефолтном таймауте, исправление в ревью.",
        "Напоминание: {term} переключаем на новую схему в четверг.",
        "Итог по {term}: контрольная точка пройдена, лимиты не превышены.",
    ]
    for sentence in sentences:
        for term in _MIXED_TERMS:
            out.append(sentence.format(term=term))
    return out


TEMPLATE_FAMILIES: dict[str, Callable[[random.Random], list[str]]] = {
    "synthetic-en-notes": _en_notes,
    "synthetic-ru-notes": _ru_notes,
    "synthetic-en-chat": _en_chat,
    "synthetic-ru-chat": _ru_chat,
    "synthetic-en-code": _en_code_headlines,
    "synthetic-ru-code": _ru_code_headlines,
    "synthetic-en-rules": _en_rules,
    "synthetic-ru-rules": _ru_rules,
    "synthetic-en-snippets": _en_snippets,
    "synthetic-ru-snippets": _ru_snippets,
    "synthetic-en-meeting": _en_meeting,
    "synthetic-ru-meeting": _ru_meeting,
    "synthetic-en-tech": _en_tech_notes,
    "synthetic-ru-tech": _ru_tech_notes,
    "synthetic-en-bugs": _en_bug_reports,
    "synthetic-ru-bugs": _ru_bug_reports,
    "synthetic-en-commits": _en_commits,
    "synthetic-ru-commits": _ru_commits,
    "synthetic-en-config": _en_config,
    "synthetic-ru-config": _ru_config,
    "synthetic-en-docstrings": _en_docstrings,
    "synthetic-ru-docstrings": _ru_docstrings,
    "synthetic-en-agentchat": _en_agent_chat,
    "synthetic-ru-agentchat": _ru_agent_chat,
    "synthetic-en-science": _en_science,
    "synthetic-ru-science": _ru_science,
    "synthetic-en-mixed": _en_mixed,
    "synthetic-ru-mixed": _ru_mixed,
}


def generate_synthetic(
    seed: int,
    *,
    shuffle: bool = True,
) -> list[tuple[str, str, str]]:
    """Generate the synthetic candidate pool: (text, lang, source) triples.

    Deterministic for a given seed: family content is a fixed cross-product
    and only the shuffle consumes the seeded RNG, so the same seed yields a
    byte-identical pool. When ``shuffle`` is on, order is mixed so the
    RU/EN quota interleaves instead of arriving in language blocks.
    """
    rng = random.Random(seed)
    texts: list[tuple[str, str, str]] = []
    for name, family in TEMPLATE_FAMILIES.items():
        lang = "ru" if "-ru-" in name else "en"
        for text in family(rng):
            texts.append((text, lang, name))
    if shuffle:
        rng.shuffle(texts)
    return texts


def collect_synthetic(seed: int) -> list[tuple[str, str, str]]:
    """Collector-named entry point (mirrors ``prepare_dataset.collect_synthetic``).

    Round-2 verification tooling imports the collector straight from this
    module; it is a thin alias so both entry points stay in lockstep.
    """
    return generate_synthetic(seed)
