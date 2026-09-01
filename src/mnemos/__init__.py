"""Mnemos — standalone memory & knowledge server for AI agents.

Productionised for the GitHub Copilot Workflow agent family and Hermes Agent.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # PyPI distribution name (pyproject [project].name); the import package
    # stays `mnemos` — only the installable name changed.
    __version__ = _pkg_version("mnemos-memory-server")
except PackageNotFoundError:  # pragma: no cover — source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
