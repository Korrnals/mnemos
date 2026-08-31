"""Shared test setup and fixtures for the Mnemos test suite.

MCP stub
--------
The ``mcp`` package is an optional dependency (``[mcp]`` extra, not
installed in the standard dev environment). We inject minimal stubs into
``sys.modules`` here - before any test file imports ``mnemos.mcp_server`` -
so that the dispatch / routing tests can run without the real SDK.

The stubs replicate the MCP SDK 2.x contract (#185): ``Server`` registers
handlers via constructor kwargs (``on_list_tools`` / ``on_call_tool``) and
the wire types are plain attribute holders.

If the real ``mcp`` package is installed (e.g. via ``pip install -e .[mcp]``)
the guard ``if "mcp" not in sys.modules`` ensures the stubs are skipped and
the real implementation is used instead.

Rate-limiter reset
------------------
The ``reset_rate_limiter`` autouse fixture clears the in-process slowapi
storage before every test so one test's calls do not bleed into the next
test's quota (all TestClient requests share ``host="testclient"``).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Minimal MCP stubs - only installed when mcp is not already present
# ---------------------------------------------------------------------------

if "mcp" not in sys.modules:

    class _Server:
        """Stub replicating the MCP SDK 2.x Server constructor contract.

        Handlers are registered via the ``on_list_tools`` / ``on_call_tool``
        constructor kwargs (the 1.x runtime decorators were removed in
        SDK 2.0 — see #185). The stub keeps the same attribute surface the
        ported ``mnemos.mcp_server`` module relies on.
        """

        def __init__(
            self,
            name: str,
            *,
            version: str = "",
            on_list_tools=None,
            on_call_tool=None,
            **_kwargs,
        ) -> None:
            self.name = name
            self.version = version
            self.on_list_tools = on_list_tools
            self.on_call_tool = on_call_tool

        def create_initialization_options(self):
            return {}

    class _TextContent:
        """Stub for mcp.types.TextContent - supports attribute access on .text."""

        def __init__(self, *, type: str, text: str) -> None:
            self.type = type
            self.text = text

    class _Tool:
        """Stub for mcp.types.Tool - preserves name/description/input_schema.

        The SDK 2.x attribute is ``input_schema`` (the wire alias
        ``inputSchema`` is serialization-only). The stub mirrors that.
        """

        def __init__(
            self,
            *,
            name: str,
            description: str | None = None,
            input_schema: dict,  # canonical 2.x name (alias: inputSchema)
        ) -> None:
            self.name = name
            self.description = description
            self.input_schema = input_schema

    class _ListToolsResult:
        """Stub for mcp.types.ListToolsResult."""

        def __init__(self, *, tools: list) -> None:
            self.tools = tools

    class _CallToolResult:
        """Stub for mcp.types.CallToolResult."""

        def __init__(self, *, content: list, is_error: bool = False) -> None:
            self.content = content
            self.is_error = is_error

    class _CallToolRequestParams:
        """Stub for mcp.types.CallToolRequestParams."""

        def __init__(self, *, name: str, arguments: dict | None = None) -> None:
            self.name = name
            self.arguments = arguments

    class _PaginatedRequestParams:
        """Stub for mcp.types.PaginatedRequestParams."""

        def __init__(self, *, cursor: str | None = None) -> None:
            self.cursor = cursor

    _mcp_stub = MagicMock()

    _mcp_server_stub = MagicMock()
    _mcp_server_stub.Server = _Server

    _mcp_stdio_stub = MagicMock()

    _mcp_types_stub = MagicMock()
    _mcp_types_stub.TextContent = _TextContent
    _mcp_types_stub.Tool = _Tool
    _mcp_types_stub.ListToolsResult = _ListToolsResult
    _mcp_types_stub.CallToolResult = _CallToolResult
    _mcp_types_stub.CallToolRequestParams = _CallToolRequestParams
    _mcp_types_stub.PaginatedRequestParams = _PaginatedRequestParams

    sys.modules.update(
        {
            "mcp": _mcp_stub,
            "mcp.server": _mcp_server_stub,
            "mcp.server.stdio": _mcp_stdio_stub,
            "mcp.types": _mcp_types_stub,
        }
    )


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    """Reset the in-process rate-limiter storage before every test.

    The slowapi ``Limiter`` is a module-level singleton keyed by client host.
    Starlette's ``TestClient`` always presents ``host="testclient"``, so
    all test requests share the same bucket.  Resetting between tests
    prevents one test's calls from bleeding into the next test's quota.
    """
    from mnemos.api.rate_limit import limiter

    limiter._storage.reset()
    yield
