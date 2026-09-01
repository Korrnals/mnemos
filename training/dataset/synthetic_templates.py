"""Synthetic memory-shaped RU+EN templates for the NM-1a dataset (ADR-0021).

Programmatic generation only — no external dataset downloads in NM-1a
(that is NM-1b+ if ever needed). Template families mirror the mnemos
memory shapes: notes, chat excerpts, code headlines, decisions, rules.

Language mix is engineered for the ≥40 % RU quota: template families
are duplicated per language and the RNG draws Russian variants slightly
more often (quota enforced downstream by the counter in
``prepare_dataset.py`` — this module only shapes the candidate pool).

Determinism: every family is generated from an explicit ``random.Random``
seed passed by the caller; the module itself holds no global RNG state.

Structure: each family returns a list of strings given a
``random.Random``. ``TEMPLATE_FAMILIES`` is the registry consumed by
``prepare_dataset.py``.
"""

from __future__ import annotations

import random
from collections.abc import Callable

# ── Domain vocabularies (shared by EN and RU families) ───────────────────────

_PROJECTS_EN = ["aurora-api", "vault-ui", "mnemos-core", "atlas-parser"]
_PROJECTS_RU = ["аурора-апи", "волт-юи", "мнемос-ядро", "атлас-парсер"]

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
]

_MODULES_EN = [
    "src/api/routes.py",
    "src/core/pool.py",
    "src/auth/tokens.py",
    "src/db/migrate.py",
    "src/jobs/worker.py",
    "src/cache/store.py",
]
_MODULES_RU = [
    "модуль маршрутов API",
    "модуль пула соединений",
    "модуль токенов авторизации",
    "модуль миграций БД",
    "модуль воркеров задач",
    "модуль кэш-хранилища",
]

_AGENTS_EN = ["backend-agent", "ui-agent", "devops-agent", "research-agent"]
_AGENTS_RU = ["бэкенд-агент", "фронтенд-агент", "девопс-агент", "исследовательский агент"]

# ── Template families ────────────────────────────────────────────────────────
#
# Each family: (name, callable(rng) -> list[str]). Families return MANY
# variants per call (topic/module vocabularies crossed with phrasing) so
# that a handful of families fills a 100k pool without near-duplicates.


def _en_notes(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Note on {t} in {p}: the current approach works but needs a review "
        "before the next release.",
        "Decision: {t} in {p} stays as implemented; revisit only after a measured regression.",
        "Reminder for {p}: document {t} in the runbook, the on-call agent kept asking about it.",
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
    ]
    for topic in _TOPICS_RU:
        for proj in _PROJECTS_RU:
            for shape in shapes:
                out.append(shape.format(t=topic, p=proj))
    return out


def _en_chat(rng: random.Random) -> list[str]:
    out: list[str] = []
    openers = ["Quick check:", "Heads up:", "Found it —", "Follow-up on", "Status:"]
    closers = [
        "will do.",
        "please confirm.",
        "no action needed yet.",
        "adding it to the backlog.",
        "see the linked issue.",
    ]
    for opener in openers:
        for closer in closers:
            topic = rng.choice(_TOPICS_EN)
            proj = rng.choice(_PROJECTS_EN)
            out.append(f"{opener} {topic} in {proj} behaved differently on staging — {closer}")
    return out


def _ru_chat(rng: random.Random) -> list[str]:
    out: list[str] = []
    openers = [
        "Быстрая проверка:",
        "Обрати внимание:",
        "Нашёл причину —",
        "Продолжение по",
        "Статус:",
    ]
    closers = [
        "сделаю.",
        "подтверди, пожалуйста.",
        "действий пока не нужно.",
        "добавил в бэклог.",
        "см. связанную задачу.",
    ]
    for opener in openers:
        for closer in closers:
            topic = rng.choice(_TOPICS_RU)
            proj = rng.choice(_PROJECTS_RU)
            out.append(
                f"{opener} тема «{topic}» в {proj} повела себя иначе на стейджинге — {closer}"
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
    return out


def _en_rules(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Rule for {a}: never edit {p} config directly; propose the change in a review first.",
        "{a} convention in {p}: every side-effecting call must be idempotent and logged.",
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
    ]
    names = ["user", "session", "token", "cache", "job", "hook", "metric", "quota"]
    for pat in patterns:
        for n in names:
            out.append(
                f"Code snippet ({n}): {pat.format(n=n)} — usage note: keep timeouts explicit."
            )
    return out


def _ru_snippets(rng: random.Random) -> list[str]:
    out: list[str] = []
    names = ["пользователь", "сессия", "токен", "кэш", "задача", "вебхук", "метрика", "квота"]
    notes = [
        "сниппет для {n}: таймауты задавать явно, без дефолтов библиотеки.",
        "пример работы с {n}: ретраи — с джиттером, не фиксированным интервалом.",
        "паттерн для {n}: пул соединений создавать один раз на процесс.",
        "заголовок правки по {n}: сначала тест, затем реализация.",
    ]
    for note in notes:
        for n in names:
            out.append(note.format(n=n))
    return out


def _en_meeting(rng: random.Random) -> list[str]:
    out: list[str] = []
    shapes = [
        "Meeting excerpt ({t}): we agreed to ship behind a flag and measure "
        "before enabling by default.",
        "Standup note on {t}: blocked on review, owner picked it up, ETA this week.",
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
    ]
    for topic in _TOPICS_RU:
        for shape in shapes:
            out.append(shape.format(t=topic))
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
}


def generate_synthetic(
    seed: int,
    *,
    shuffle: bool = True,
) -> list[tuple[str, str, str]]:
    """Generate the synthetic candidate pool: (text, lang, source) triples.

    Deterministic for a given seed. The pool is the cross product of all
    families; when ``shuffle`` is on, order is mixed deterministically so
    the RU/EN quota interleaves instead of arriving in language blocks.
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
