"""Harness-only deterministic memory ids (TL decision 2026-09-14, #280).

Why this exists
---------------
Cache contract Phase-1 added the deterministic tiebreak to
``MemoryManager.search``: equal fused scores order by ``id`` ascending.
The production contract is PER-STORE determinism ("same store ⇒ same
bytes") — exactly what the KV-cache stability goal requires, and what
the tiebreak delivers. But ``Memory.id`` is minted ``uuid4`` (random
per row, ``mnemos/models.py``), so two FRESH stores built by the same
harness procedure get different id draws, and the tie order inside
equal-score groups differs run-to-run. Test harnesses that assert
cross-run byte-identity of two fresh-store measurements therefore
broke (golden determinism, S1 measurement determinism, the lanes
flag-off sha256 fixture).

The fix is a HARNESS property, not a production one (TL decision
2026-09-14, option (a)): for the duration of a measurement, replace
``uuid.uuid4`` with a counter-keyed ``uuid.uuid5`` sequence seeded by
the caller. Each ``with`` block restarts the counter, so two runs of
the same procedure mint the SAME id sequence ⇒ identical tie order ⇒
byte-identical snapshots — while every ranking, invariant and fixture
assertion keeps its full catching power (nothing is loosened; the ids
are merely a fixed draw instead of a random one).

Never use outside test harnesses — the production uuid4 path is
deliberately untouched.
"""

from __future__ import annotations

import contextlib
import itertools
import uuid
from collections.abc import Iterator
from unittest import mock

#: Fixed namespace for the seeded sequence — any constant UUID works;
#: uuid5 is RFC-4122 SHA-1, stable across Python versions and platforms.
_NAMESPACE = uuid.UUID("5f0d8a94-7bb0-4d56-9d0a-1c2b3e4f5a6c")


@contextlib.contextmanager
def seeded_memory_ids(seed: str) -> Iterator[None]:
    """Mint deterministic uuid4 substitutes for the duration of the block.

    ``uuid.uuid4`` is swapped (process-wide, restored on exit — the
    stdlib ``uuid`` module is shared by every importer) for a factory
    returning ``uuid5(NAMESPACE, "<seed>:<n>")`` with ``n`` counting
    from 0 inside THIS block. Same ``seed`` + same call order ⇒ same id
    sequence on every run; different ``seed`` values keep independent
    harness procedures from colliding on ids.
    """
    counter = itertools.count()

    def _seeded_uuid4() -> uuid.UUID:
        return uuid.uuid5(_NAMESPACE, f"{seed}:{next(counter)}")

    with mock.patch.object(uuid, "uuid4", _seeded_uuid4):
        yield
