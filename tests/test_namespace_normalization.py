"""Regression tests for project namespace case normalization.

Covers the bug where PascalCase project slugs (e.g. ``Project-Umbra``)
split the namespace from the canonical lowercase form
(``project-umbra``). The split happened because:

- ``_detect_project()`` returned the raw folder name (PascalCase).
- ``mnemos_save_context`` built tags manually without validation.
- Read paths (``search``, ``recall``, ``list_recent``) passed the
  ``project`` filter without normalizing.

These tests verify every entry/exit point normalizes via
``normalize_project_slug`` so namespaces never diverge on case.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from mnemos.models import normalize_project_slug

# ── Unit: normalize_project_slug ────────────────────────────────────────────


class TestNormalizeProjectSlug:
    def test_lowercases(self) -> None:
        assert normalize_project_slug("Project-Umbra") == "project-umbra"

    def test_replaces_spaces(self) -> None:
        assert normalize_project_slug("My Project") == "my-project"

    def test_strips_whitespace(self) -> None:
        assert normalize_project_slug("  Project-Umbra  ") == "project-umbra"

    def test_preserves_lowercase(self) -> None:
        assert normalize_project_slug("project-umbra") == "project-umbra"

    def test_preserves_hyphens_and_digits(self) -> None:
        assert normalize_project_slug("M2-Release-v2") == "m2-release-v2"

    def test_empty_string(self) -> None:
        assert normalize_project_slug("") == ""


# ── MCP dispatch: save_context normalizes ───────────────────────────────────


@pytest.fixture
def manager() -> object:  # type: ignore[name-defined]
    import tempfile

    os.environ["MNEMOS_MNEMOS__DATA_DIR"] = tempfile.mkdtemp()
    from mnemos.config import Settings
    from mnemos.manager import MemoryManager

    return MemoryManager(Settings())


@pytest.mark.asyncio
async def test_save_context_normalizes_pascalcase_project(manager: object) -> None:
    """mnemos_save_context must store lowercase project + tag."""
    from mnemos.mcp_server import _dispatch

    # Patch the module-level manager getter to return our fixture.
    with mock.patch("mnemos.mcp_server.get_manager", return_value=manager):
        result = await _dispatch(
            "mnemos_save_context",
            {"project": "Project-Umbra", "goals": "test normalization"},
        )
        assert "Context saved" in str(result)

        # Read back the stored memory.
        mgr = manager  # type: ignore[assignment]
        mem = mgr.sqlite.list_all(project="project-umbra", limit=5)  # type: ignore[attr-defined]
        assert any(
            "project:project-umbra" in m.tags and m.project == "project-umbra" for m in mem
        ), (
            "Expected lowercase project-umbra, got "
            f"projects={[m.project for m in mem]}, "
            f"tags={[m.tags for m in mem]}"
        )


@pytest.mark.asyncio
async def test_save_context_auto_detect_normalizes(manager: object) -> None:
    """_detect_project via cwd must return lowercase."""
    from mnemos.mcp_server import _detect_project

    with mock.patch("os.getcwd", return_value="/tmp/Project-Umbra"):
        assert _detect_project() == "project-umbra"


# ── MCP dispatch: read paths normalize the filter ───────────────────────────


@pytest.mark.asyncio
async def test_search_normalizes_project_filter(manager: object) -> None:
    """Searching with PascalCase filter must match lowercase entries."""
    from mnemos.mcp_server import _dispatch
    from mnemos.models import MemoryCreate, MemorySource

    mgr = manager  # type: ignore[assignment]
    mgr.add(  # type: ignore[attr-defined]
        MemoryCreate(
            content="lowercase-namespace-entry",
            tags=["project:project-umbra", "agent:qa", "gcw:learning"],
            source=MemorySource.MCP,
        ),
        project="project-umbra",
        agent="qa",
    )

    with mock.patch("mnemos.mcp_server.get_manager", return_value=manager):
        result = await _dispatch(
            "mnemos_search",
            {"query": "lowercase-namespace-entry", "project": "Project-Umbra", "include_raw": True},
        )
        assert "lowercase-namespace-entry" in str(result), (
            "PascalCase filter failed to match lowercase entry"
        )


@pytest.mark.asyncio
async def test_agent_recall_normalizes_agent_and_project(manager: object) -> None:
    """agent_recall must normalize both agent and project filters."""
    from mnemos.mcp_server import _dispatch
    from mnemos.models import MemoryCreate, MemorySource

    mgr = manager  # type: ignore[assignment]
    mgr.add(  # type: ignore[attr-defined]
        MemoryCreate(
            content="agent-recall-normalization-test",
            tags=["project:project-umbra", "agent:gcw-tech-lead", "gcw:learning"],
            source=MemorySource.MCP,
        ),
        project="project-umbra",
        agent="gcw-tech-lead",
    )

    with mock.patch("mnemos.mcp_server.get_manager", return_value=manager):
        # PascalCase agent + PascalCase project.
        result = await _dispatch(
            "mnemos_agent_recall",
            {"agent": "GCW-Tech-Lead", "project": "Project-Umbra"},
        )
        assert "agent-recall-normalization-test" in str(result), (
            "PascalCase filters failed to match lowercase entries"
        )


@pytest.mark.asyncio
async def test_list_recent_normalizes_project_filter(manager: object) -> None:
    """list_recent must normalize the project filter."""
    from mnemos.mcp_server import _dispatch
    from mnemos.models import MemoryCreate, MemorySource

    mgr = manager  # type: ignore[assignment]
    mgr.add(  # type: ignore[attr-defined]
        MemoryCreate(
            content="list-recent-normalization",
            tags=["project:project-umbra", "agent:qa", "gcw:learning"],
            source=MemorySource.MCP,
        ),
        project="project-umbra",
        agent="qa",
    )

    with mock.patch("mnemos.mcp_server.get_manager", return_value=manager):
        result = await _dispatch("mnemos_list_recent", {"project": "Project-Umbra", "limit": 10})
        assert "list-recent-normalization" in str(result), (
            "PascalCase filter failed to match lowercase entry"
        )


@pytest.mark.asyncio
async def test_recall_context_normalizes_project(manager: object) -> None:
    """recall_context must normalize the project filter."""
    from mnemos.mcp_server import _dispatch

    with mock.patch("mnemos.mcp_server.get_manager", return_value=manager):
        # First save with lowercase.
        await _dispatch(
            "mnemos_save_context",
            {"project": "project-umbra", "goals": "recall test"},
        )

        # Now recall with PascalCase — must find it.
        result = await _dispatch("mnemos_recall_context", {"project": "Project-Umbra"})
        assert "recall test" in str(result), (
            "PascalCase recall filter failed to match lowercase checkpoint"
        )
