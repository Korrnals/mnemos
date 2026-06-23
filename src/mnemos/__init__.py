"""Mnemos — standalone memory & knowledge server for GCW agents.

Productionised for the GCW (GitHub Copilot Workflow) agent family.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("mnemos")
except PackageNotFoundError:  # pragma: no cover — source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
