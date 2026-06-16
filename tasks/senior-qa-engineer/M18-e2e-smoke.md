# Task M18 — E2E smoke tests + coverage enforcement

> **Task ID**: QA-M18
> **Specialist**: GCW Senior QA Engineer
> **Priority**: P1 (после M15 + M16)
> **Status**: ⏳ pending assignment
> **Created**: 2026-06-15
> **Source**: [tasks/AUDIT.md §6](../AUDIT.md)

---

## Goal

Довести покрытие тестами до ≥ 80%, добавить E2E smoke test, формализовать test strategy.

## Background

Текущее состояние:
- 209 tests pass
- `make coverage` target существует (`--cov-fail-under=80`), но не enforced в CI
- Coverage не измеряется автоматически
- E2E тестов через MCP-клиент нет (только unit + integration через fixtures)

## Acceptance criteria

- [ ] `pytest --cov=src/mnemos --cov-fail-under=80` проходит
- [ ] Coverage report показывает критичные модули (manager, mcp_server, sessions/*) ≥ 85%
- [ ] E2E test запускает Mnemos в subprocess, подключается через MCP stdio, выполняет add→search→recall
- [ ] Integration test для A2A sessions API (из M16) — full round-trip через FastAPI TestClient
- [ ] Flaky test detection: запустить pytest 10 раз подряд, 0 failures
- [ ] Test report генерируется в `tests/reports/coverage.html`

## Coverage strategy

Текущее покрытие (нужно измерить первым делом):
```bash
pytest --cov=src/mnemos --cov-report=term-missing tests/ -q
```

**Целевые модули** (≥ 85%):
- `mnemos/manager.py` — critical
- `mnemos/mcp_server.py` — critical
- `mnemos/sessions/*` — новый код из M16
- `mnemos/filter/*` — уже 32/32 тестов, должно быть хорошо
- `mnemos/pipeline/*` — 24/24 тестов

**Приемлемо < 85%**:
- `mnemos/embeddings/*` — external API mocking
- `mnemos/cli/migrate.py` — I/O heavy

## Новые тесты

### 1. E2E через MCP stdio (`tests/test_e2e_mcp.py`)

```python
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
async def mnemos_subprocess(tmp_path: Path):
    """Start mnemos mcp server in subprocess with isolated data dir."""
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    data_dir.mkdir()
    vault.mkdir()

    proc = subprocess.Popen(
        [sys.executable, "-m", "mnemos.mcp_server", "--data-dir", str(data_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"MNEMOS_DATA_DIR": str(data_dir), "MNEMOS_VAULT": str(vault)},
    )
    yield proc
    proc.terminate()
    proc.wait(timeout=5)


async def test_mcp_add_search_recall_roundtrip(mnemos_subprocess):
    """Full E2E: add → search → agent_recall."""
    # 1. Send mnemos_add
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "mnemos_add",
            "arguments": {
                "content": "Test memory about A2A sessions",
                "tags": ["project:gcw", "agent:qa-engineer", "gcw:test"],
            },
        },
    }
    mnemos_subprocess.stdin.write((json.dumps(request) + "\n").encode())
    mnemos_subprocess.stdin.flush()
    response = json.loads(mnemos_subprocess.stdout.readline())
    assert "result" in response, response
    memory_id = response["result"]["id"]

    # 2. Search
    request["id"] = 2
    request["params"] = {"name": "mnemos_search", "arguments": {"query": "A2A"}}
    mnemos_subprocess.stdin.write((json.dumps(request) + "\n").encode())
    mnemos_subprocess.stdin.flush()
    response = json.loads(mnemos_subprocess.stdout.readline())
    assert any(r["id"] == memory_id for r in response["result"])

    # 3. Agent recall
    request["id"] = 3
    request["params"] = {
        "name": "mnemos_agent_recall",
        "arguments": {"agent": "qa-engineer", "limit": 10},
    }
    mnemos_subprocess.stdin.write((json.dumps(request) + "\n").encode())
    mnemos_subprocess.stdin.flush()
    response = json.loads(mnemos_subprocess.stdout.readline())
    assert any(r["id"] == memory_id for r in response["result"])


async def test_mcp_concurrent_adds(mnemos_subprocess):
    """Verify thread-safety: 10 concurrent adds don't lose data."""
    # Send 10 mnemos_add requests rapidly
    # Verify all 10 are saved
    pass
```

### 2. A2A sessions integration (`tests/test_a2a_integration.py`)

```python
from fastapi.testclient import TestClient
from mnemos.api.main import app

client = TestClient(app)


def test_a2a_full_roundtrip():
    """Full session lifecycle: create → 3 turns → load range."""
    # 1. Create session
    resp = client.post(
        "/v1/sessions",
        json={"user_id": "abyss", "metadata": {"test": True}},
    )
    assert resp.status_code == 201
    session_id = resp.json()["session_id"]

    # 2. Write 3 turns
    for i in range(3):
        resp = client.post(
            f"/v1/sessions/{session_id}/turns",
            json={
                "role": "a2a_message",
                "from": "gcw-test",
                "to": "gcw-test-target",
                "message_id": f"msg-{i}",
                "content": f"Test turn {i} with some content",
                "outcome": "delivered",
                "tags": ["test"],
            },
        )
        assert resp.status_code == 201

    # 3. Load range
    resp = client.post(
        f"/v1/sessions/{session_id}/turns/range",
        json={"from_step": 1, "to_step": 3, "mode": "summary"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["turns"]) == 3


def test_a2a_idempotency():
    """POST with same message_id twice returns same turn."""
    # Create session
    session_id = create_test_session()

    # First write
    resp1 = client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "role": "a2a_message",
            "from": "gcw-test",
            "to": "gcw-test",
            "message_id": "msg-dup",
            "content": "First",
            "outcome": "delivered",
            "tags": [],
        },
    )

    # Second write (same message_id)
    resp2 = client.post(
        f"/v1/sessions/{session_id}/turns",
        json={
            "role": "a2a_message",
            "from": "gcw-test",
            "to": "gcw-test",
            "message_id": "msg-dup",
            "content": "Second (should be ignored)",
            "outcome": "delivered",
            "tags": [],
        },
    )

    assert resp1.json()["turn_id"] == resp2.json()["turn_id"]


def test_a2a_atomic_write():
    """Verify crash mid-write doesn't leave half-state."""
    # Hard to test directly, but can verify:
    # - transaction wraps insert
    # - rollback on error
    # - no partial turns visible
    pass
```

### 3. Concurrency tests (`tests/test_concurrency.py`)

```python
import asyncio
import pytest
from mnemos.manager import MemoryManager
from mnemos.config import Settings


async def test_concurrent_writes_no_lost_data():
    """10 concurrent writes should result in 10 memories saved."""
    settings = Settings(data_dir=tmp_path)
    manager = MemoryManager(settings)

    async def add_memory(i: int) -> None:
        m = MemoryCreate(
            content=f"Concurrent test {i}",
            tags=[f"agent:test", "project:concurrency", "gcw:test"],
        )
        await manager.create(m)

    await asyncio.gather(*[add_memory(i) for i in range(10)])
    memories = await manager.list_all(limit=20)
    concurrent_count = sum(1 for m in memories if "project:concurrency" in m.tags)
    assert concurrent_count == 10


async def test_concurrent_search_consistency():
    """Search results stable under concurrent writes."""
    pass
```

### 4. Regression suite (`tests/test_regression.py`)

Зафиксировать известные баги из audit'а как тесты, чтобы не регрессировали:
- `test_vault_read_narrowed_exception` — `vault.py` `except (ValueError, TypeError, KeyError, OSError)` (было `except Exception`)
- `test_ttlcache_size_narrowed_exception` — `sqlite_store.py:60` то же
- `test_ssrf_blocked_in_ingest_url` — `_validate_url` работает

## Flaky test detection

```bash
# Run 10x and check stability
for i in {1..10}; do
  pytest tests/ -q --tb=line 2>&1 | tail -3
done
```

Если какие-то тесты flaky — выделить в отдельный файл `tests/test_flaky.py` с `@pytest.mark.flaky(reruns=3)` и `@pytest.mark.skip(reason="GH-XXXX: flaky root cause TBD")` — НЕ `@pytest.mark.skip` без issue-ссылки (запрещено по `lint-and-validate.instructions.md`).

## Coverage enforcement в CI

(Делается в M17 — SRE). В M18 только замерить и зафиксировать baseline.

## Files to touch

| Файл | Действие |
|---|---|
| `tests/test_e2e_mcp.py` | create (~200 строк) |
| `tests/test_a2a_integration.py` | create (~150 строк, после M16) |
| `tests/test_concurrency.py` | create (~100 строк) |
| `tests/test_regression.py` | create (~80 строк) |
| `tests/test_security.py` | extend (если ещё не покрыто в M15.2) |
| `tests/conftest.py` | edit — add fixtures (temp manager, fastapi client) |
| `pyproject.toml` | edit — add `pytest-cov` to dev deps (если нет) |

## Verification

```bash
cd /var/home/abyss/LABs/AI/mnemos
source .venv/bin/activate

# 1. Coverage
pytest --cov=src/mnemos --cov-report=term-missing --cov-fail-under=80 tests/ -q
# MUST: ≥ 80%, иначе FAIL

# 2. E2E
pytest tests/test_e2e_mcp.py -v
# MUST: pass

# 3. A2A integration (после M16)
pytest tests/test_a2a_integration.py -v
# MUST: pass

# 4. Concurrency
pytest tests/test_concurrency.py -v
# MUST: pass

# 5. Flakiness
for i in {1..5}; do
  pytest tests/ -q 2>&1 | tail -1
done
# MUST: consistent pass count
```

## Commit strategy

Несколько commits:
1. `test(m18): E2E MCP smoke test`
2. `test(m18): A2A sessions integration suite` (после M16)
3. `test(m18): concurrency tests`
4. `test(m18): regression suite for audit findings`

## Out of scope

- ❌ Performance benchmarks (отдельная фаза)
- ❌ Load testing (отдельная фаза, нужен k6/wrk)
- ❌ Property-based testing (hypothesis) — nice to have, не critical
- ❌ Mutation testing (cosmic ray, mutmut) — overkill для v1

## Hand-off

Report back to `@GCW: Tech Lead` with:
- Coverage report (term-missing)
- Количество новых тестов + общий счёт
- Список flaky tests (если есть) с issue-ссылками
- E2E smoke test output

## Coordination

- Зависит от M15 (working tree должен быть чистый)
- Зависит от M16 (A2A integration tests)
- Параллельно с M17 (CI) — coverage enforcement там
