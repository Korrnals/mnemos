"""Smoke tests for MCP server dispatch routing.

Validates three contracts:
- test_routing_all_tools_recognized: every tool returned by list_tools() is
  recognized by _dispatch (routing never falls through to "Unknown tool: ...").
- test_dispatch_unknown_tool_returns_error_string: unregistered names produce
  the expected "Unknown tool: ..." sentinel string.
- test_call_tool_unknown_wraps_error_in_text_content: call_tool() negative path.
- test_list_tools_contract: list_tools() returns a non-empty list and each Tool
  carries name, description, and inputSchema (MCP schema contract).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mnemos.mcp_server import _dispatch, call_tool, list_tools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimum valid arguments per registered tool.
# Tags for mnemos_add / mnemos_ingest_url include the required
# project:/agent:/gcw: trio so validate_tag_contract (mocked in routing tests)
# does not need real validation logic.
_TOOL_ARGS: dict[str, dict] = {
    "mnemos_add": {
        "content": "smoke content",
        "tags": ["project:smoke", "agent:qa", "gcw:decision"],
    },
    "mnemos_agent_recall": {"agent": "qa-agent"},
    "mnemos_auto_collect_status": {},
    "mnemos_ingest_url": {
        "url": "https://example.com",
        "tags": ["project:smoke", "agent:qa", "gcw:decision"],
    },
    "mnemos_list_recent": {},
    "mnemos_list_tags": {},
    "mnemos_recall_context": {"project": "smoke"},
    "mnemos_save_context": {"project": "smoke", "goals": "smoke goals"},
    "mnemos_search": {"query": "smoke test"},
    "mnemos_stats": {},
    "mnemos_watch_start": {},
    "mnemos_watch_status": {},
    "mnemos_watch_stop": {},
}

# ---------------------------------------------------------------------------
# Routing assertions map (mcp-3 finding)
# tool_name -> (expected_manager_method, [forbidden_manager_methods])
# Covers all tools with a unique 1:1 manager method.
# mnemos_save_context (shares mgr.add) and mnemos_auto_collect_status
# (no manager data method) are handled in dedicated tests below.
# ---------------------------------------------------------------------------
_ROUTING_MAP: dict[str, tuple[str, list[str]]] = {
    "mnemos_add": ("add", ["search", "list_recent", "recall_context"]),
    "mnemos_search": ("search", ["add", "list_recent", "agent_recall"]),
    "mnemos_agent_recall": ("agent_recall", ["search", "add", "recall_context"]),
    "mnemos_recall_context": ("recall_context", ["search", "add", "agent_recall"]),
    "mnemos_list_recent": ("list_recent", ["search", "add", "list_tags"]),
    "mnemos_list_tags": ("list_tags", ["search", "list_recent", "stats"]),
    "mnemos_stats": ("stats", ["search", "list_tags", "list_recent"]),
    "mnemos_ingest_url": ("ingest_url", ["add", "search", "list_recent"]),
    "mnemos_watch_start": ("watch_start", ["watch_stop", "watch_status", "search"]),
    "mnemos_watch_stop": ("watch_stop", ["watch_start", "watch_status", "search"]),
    "mnemos_watch_status": ("watch_status", ["watch_start", "watch_stop", "search"]),
}


def _make_mock_manager() -> MagicMock:
    """Return a MagicMock MemoryManager with safe stub return values for all methods."""
    mock_memory = MagicMock()
    mock_memory.id = "smoke-id-1"
    mock_memory.auto_title.return_value = "Smoke Memory"
    mock_memory.status = "published"

    mgr = MagicMock()
    mgr.settings.mnemos.strict_tag_contract = False
    mgr.add.return_value = mock_memory
    mgr.search.return_value = []
    mgr.agent_recall.return_value = []
    mgr.recall_context.return_value = []
    mgr.list_recent.return_value = []
    mgr.list_tags.return_value = {}
    mgr.stats.return_value = {"total": 0}
    mgr.ingest_url.return_value = mock_memory
    mgr.watch_start.return_value = None
    mgr.watch_stop.return_value = None
    mgr.watch_status.return_value = "watching: 0 paths"
    return mgr


# ---------------------------------------------------------------------------
# Test 1 - routing coverage (parametrized per tool)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", sorted(_TOOL_ARGS.keys()))
async def test_routing_all_tools_recognized(tool_name: str) -> None:
    """_dispatch must route every registered tool - must NOT return 'Unknown tool: ...'."""
    mock_mgr = _make_mock_manager()
    with (
        patch("mnemos.mcp_server.get_manager", return_value=mock_mgr),
        patch(
            "mnemos.mcp_server.validate_tag_contract",
            side_effect=lambda tags, **_kw: tags,
        ),
    ):
        result = await _dispatch(tool_name, _TOOL_ARGS[tool_name])

    assert not (isinstance(result, str) and result.startswith("Unknown tool:")), (
        f"Tool {tool_name!r} was not recognized by _dispatch - routing is broken"
    )


# ---------------------------------------------------------------------------
# Test 2 - negative path via _dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_unknown_tool_returns_error_string() -> None:
    """_dispatch with an unregistered name must return the 'Unknown tool: ...' sentinel."""
    mock_mgr = _make_mock_manager()
    with patch("mnemos.mcp_server.get_manager", return_value=mock_mgr):
        result = await _dispatch("nonexistent_tool", {})

    assert isinstance(result, str), "Expected str return for unknown tool"
    assert "Unknown tool:" in result
    assert "nonexistent_tool" in result


# ---------------------------------------------------------------------------
# Test 2b - negative path via call_tool (full stack)
# ---------------------------------------------------------------------------


async def test_call_tool_unknown_wraps_error_in_text_content() -> None:
    """call_tool() with an unregistered name returns TextContent with 'Unknown tool: ...'."""
    mock_mgr = _make_mock_manager()
    with patch("mnemos.mcp_server.get_manager", return_value=mock_mgr):
        contents = await call_tool("nonexistent_tool", {})

    assert len(contents) == 1
    assert "Unknown tool:" in contents[0].text
    assert "nonexistent_tool" in contents[0].text


# ---------------------------------------------------------------------------
# Test 3 - MCP Tool schema contract
# ---------------------------------------------------------------------------


async def test_list_tools_contract() -> None:
    """list_tools() returns a non-empty list; each Tool satisfies MCP schema contract."""
    tools = await list_tools()

    assert len(tools) > 0, "list_tools() must return at least one tool"
    for tool in tools:
        assert tool.name, f"Tool missing 'name': {tool!r}"
        assert tool.description, f"Tool {tool.name!r} missing 'description'"
        assert isinstance(tool.inputSchema, dict), (
            f"Tool {tool.name!r} inputSchema must be a dict, got {type(tool.inputSchema)}"
        )

    # Every tool defined in _TOOL_ARGS must appear in list_tools() output
    registered = {t.name for t in tools}
    for expected_name in _TOOL_ARGS:
        assert expected_name in registered, (
            f"Tool {expected_name!r} defined in _TOOL_ARGS but missing from list_tools()"
        )


# ---------------------------------------------------------------------------
# Test 4 - positive routing: each tool invokes the correct manager method (mcp-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", sorted(_ROUTING_MAP.keys()))
async def test_routing_invokes_correct_manager_method(tool_name: str) -> None:
    """_dispatch must call the expected manager method and not a sibling (mcp-3)."""
    expected_method, forbidden_methods = _ROUTING_MAP[tool_name]
    mock_mgr = _make_mock_manager()
    with (
        patch("mnemos.mcp_server.get_manager", return_value=mock_mgr),
        patch(
            "mnemos.mcp_server.validate_tag_contract",
            side_effect=lambda tags, **_kw: tags,
        ),
    ):
        await _dispatch(tool_name, _TOOL_ARGS[tool_name])

    getattr(mock_mgr, expected_method).assert_called_once()
    for forbidden in forbidden_methods:
        getattr(mock_mgr, forbidden).assert_not_called()


# ---------------------------------------------------------------------------
# Test 5 - save_context -> mgr.add edge (not search / recall_context) (mcp-3)
# ---------------------------------------------------------------------------


async def test_save_context_routes_to_add_not_search() -> None:
    """mnemos_save_context must route to mgr.add - not mgr.search or mgr.recall_context."""
    mock_mgr = _make_mock_manager()
    with (
        patch("mnemos.mcp_server.get_manager", return_value=mock_mgr),
        patch(
            "mnemos.mcp_server.validate_tag_contract",
            side_effect=lambda tags, **_kw: tags,
        ),
    ):
        await _dispatch("mnemos_save_context", _TOOL_ARGS["mnemos_save_context"])

    mock_mgr.add.assert_called_once()
    mock_mgr.search.assert_not_called()
    mock_mgr.recall_context.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6 - auto_collect_status reads no manager data methods (mcp-3)
# ---------------------------------------------------------------------------


async def test_auto_collect_status_touches_no_manager_data_method() -> None:
    """mnemos_auto_collect_status must read only module-level state - zero mgr data method calls."""
    mock_mgr = _make_mock_manager()
    with patch("mnemos.mcp_server.get_manager", return_value=mock_mgr):
        await _dispatch("mnemos_auto_collect_status", _TOOL_ARGS["mnemos_auto_collect_status"])

    data_methods = [
        "add",
        "search",
        "agent_recall",
        "recall_context",
        "list_recent",
        "list_tags",
        "stats",
        "ingest_url",
    ]
    for method_name in data_methods:
        getattr(mock_mgr, method_name).assert_not_called()
