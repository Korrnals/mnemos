"""Basic tests for AI-Brain core."""

from pathlib import Path

import pytest

from ai_brain.config import Settings, BrainConfig, EmbeddingConfig
from ai_brain.models import Memory, MemoryCreate, MemorySource, MemoryType
from ai_brain.storage.sqlite_store import SQLiteStore


@pytest.fixture
def tmp_db(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.db")
    yield store
    store.close()


def test_memory_auto_title():
    m = Memory(content="Hello world\nSecond line")
    assert m.auto_title() == "Hello world"


def test_memory_auto_title_with_title():
    m = Memory(content="Hello", title="My Title")
    assert m.auto_title() == "My Title"


def test_sqlite_save_and_get(tmp_db: SQLiteStore):
    m = Memory(content="Test content", tags=["test"])
    tmp_db.save(m)
    fetched = tmp_db.get(m.id)
    assert fetched is not None
    assert fetched.content == "Test content"
    assert fetched.tags == ["test"]


def test_sqlite_delete(tmp_db: SQLiteStore):
    m = Memory(content="To delete")
    tmp_db.save(m)
    assert tmp_db.delete(m.id)
    assert tmp_db.get(m.id) is None


def test_sqlite_list(tmp_db: SQLiteStore):
    for i in range(5):
        tmp_db.save(Memory(content=f"Item {i}", tags=[f"tag{i}"]))
    items = tmp_db.list_all(limit=3)
    assert len(items) == 3


def test_sqlite_fts(tmp_db: SQLiteStore):
    tmp_db.save(Memory(content="Python list comprehension"))
    tmp_db.save(Memory(content="JavaScript arrow functions"))
    results = tmp_db.fts_search("python")
    assert len(results) >= 1
    assert "Python" in results[0][0].content


def test_sqlite_tags(tmp_db: SQLiteStore):
    tmp_db.save(Memory(content="A", tags=["python", "dev"]))
    tmp_db.save(Memory(content="B", tags=["python"]))
    tags = tmp_db.get_all_tags()
    assert tags["python"] == 2
    assert tags["dev"] == 1
