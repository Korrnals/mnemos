"""Tests for FastAPI REST API endpoints."""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ai_brain.config import BrainConfig, EmbeddingConfig, Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        brain=BrainConfig(
            vault_path=tmp_path / "vault",
            data_dir=tmp_path / "data",
        ),
        embedding=EmbeddingConfig(provider="chromadb"),
    )
    s.resolve_paths()
    return s


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with patch("ai_brain.api.get_settings", return_value=settings):
        from ai_brain.api import app

        with TestClient(app) as c:
            yield c


def test_health(client: TestClient):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stats(client: TestClient):
    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_memories" in data


def test_create_and_get_memory(client: TestClient):
    resp = client.post("/api/v1/memories", json={
        "content": "Test API memory",
        "title": "API Test",
        "tags": ["test", "api"],
    })
    assert resp.status_code == 201
    memory = resp.json()
    assert memory["content"] == "Test API memory"
    assert memory["title"] == "API Test"
    memory_id = memory["id"]

    resp = client.get(f"/api/v1/memories/{memory_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == memory_id


def test_list_memories(client: TestClient):
    for i in range(3):
        client.post("/api/v1/memories", json={"content": f"Memory {i}"})

    resp = client.get("/api/v1/memories")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_update_memory(client: TestClient):
    resp = client.post("/api/v1/memories", json={"content": "Original"})
    memory_id = resp.json()["id"]

    resp = client.put(f"/api/v1/memories/{memory_id}", json={
        "content": "Updated content",
        "tags": ["updated"],
    })
    assert resp.status_code == 200
    assert resp.json()["content"] == "Updated content"
    assert resp.json()["tags"] == ["updated"]


def test_delete_memory(client: TestClient):
    resp = client.post("/api/v1/memories", json={"content": "To delete"})
    memory_id = resp.json()["id"]

    resp = client.delete(f"/api/v1/memories/{memory_id}")
    assert resp.status_code == 204

    resp = client.get(f"/api/v1/memories/{memory_id}")
    assert resp.status_code == 404


def test_search(client: TestClient):
    client.post("/api/v1/memories", json={
        "content": "Python asyncio event loop",
        "tags": ["python"],
    })
    client.post("/api/v1/memories", json={
        "content": "JavaScript promises and callbacks",
        "tags": ["javascript"],
    })

    resp = client.post("/api/v1/search", json={"query": "python async"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) >= 1


def test_tags(client: TestClient):
    client.post("/api/v1/memories", json={
        "content": "A", "tags": ["python", "dev"],
    })
    client.post("/api/v1/memories", json={
        "content": "B", "tags": ["python"],
    })

    resp = client.get("/api/v1/tags")
    assert resp.status_code == 200
    tags = resp.json()
    assert tags["python"] == 2
    assert tags["dev"] == 1


def test_get_nonexistent(client: TestClient):
    resp = client.get("/api/v1/memories/nonexistent-id")
    assert resp.status_code == 404


def test_ui_page(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"AI-Brain" in resp.content
