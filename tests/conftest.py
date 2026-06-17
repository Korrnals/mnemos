"""Shared test setup for the Mnemos test suite.

MCP stub
--------
The ``mcp`` package is an optional dependency (``[mcp]`` extra, not
installed in the standard dev environment). We inject minimal stubs into
``sys.modules`` here - before any test file imports ``mnemos.mcp_server`` -
so that the dispatch / routing tests can run without the real SDK.

If the real ``mcp`` package is installed (e.g. via ``pip install -e .[mcp]``)
the guard ``if "mcp" not in sys.modules`` ensures the stubs are skipped and
the real implementation is used instead.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Minimal MCP stubs - only installed when mcp is not already present
# ---------------------------------------------------------------------------

if "mcp" not in sys.modules:

    class _Server:
        """Stub replicating the MCP Server decorator contract.

        The decorators ``list_tools()`` and ``call_tool()`` register handlers
        and return the original function unchanged - which is exactly what the
        real SDK does.
        """

        def __init__(self, name: str) -> None:
            self.name = name

        def list_tools(self):
            def _dec(func):
                return func

            return _dec

        def call_tool(self):
            def _dec(func):
                return func

            return _dec

        def create_initialization_options(self):
            return {}

    class _TextContent:
        """Stub for mcp.types.TextContent - supports attribute access on .text."""

        def __init__(self, *, type: str, text: str) -> None:
            self.type = type
            self.text = text

    class _Tool:
        """Stub for mcp.types.Tool - preserves name/description/inputSchema."""

        def __init__(
            self,
            *,
            name: str,
            description: str | None = None,
            inputSchema: dict,  # noqa: N803 - upstream SDK uses camelCase
        ) -> None:
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    _mcp_stub = MagicMock()

    _mcp_server_stub = MagicMock()
    _mcp_server_stub.Server = _Server

    _mcp_stdio_stub = MagicMock()

    _mcp_types_stub = MagicMock()
    _mcp_types_stub.TextContent = _TextContent
    _mcp_types_stub.Tool = _Tool

    sys.modules.update(
        {
            "mcp": _mcp_stub,
            "mcp.server": _mcp_server_stub,
            "mcp.server.stdio": _mcp_stdio_stub,
            "mcp.types": _mcp_types_stub,
        }
    )
