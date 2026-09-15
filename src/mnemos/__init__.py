"""Compatibility shim: ``mnemos`` → ``vesmaro`` (dual-import period).

Rebrand ADR-0031: the canonical import package is ``vesmaro``; this shim
keeps every ``import mnemos.*`` / ``from mnemos.*`` statement alive so
existing harness bridges and user code survive the 5.0.0 window untouched.
Installs a meta-path finder that resolves ``mnemos.X.Y`` by importing
``vesmaro.X.Y`` and aliasing it in ``sys.modules`` (same module objects —
``mnemos.cli.main.app is vesmaro.cli.main.app``).

Retires no earlier than 6.0 (dual-prefix contract, archcom 2026-09-14).
Deprecation notice is emitted once per process on first import.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import warnings

__all__: list[str] = []

_WARNED = False


def _warn_once() -> None:
    global _WARNED
    if not _WARNED:
        _WARNED = True
        warnings.warn(
            "The 'mnemos' import name is deprecated — use 'vesmaro' instead. "
            "The compatibility shim retires no earlier than 6.0.",
            DeprecationWarning,
            stacklevel=3,
        )


_warn_once()


def __getattr__(name: str):
    """Proxy unknown attributes (e.g. ``__version__``) to ``vesmaro``."""
    return getattr(importlib.import_module("vesmaro"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(importlib.import_module("vesmaro"))))


class _VesmaroAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ``mnemos.<rest>`` by importing ``vesmaro.<rest>``.

    ``create_module`` returns the already-imported vesmaro module object, so
    ``sys.modules["mnemos.cli.main"] is sys.modules["vesmaro.cli.main"]`` —
    one module, two names (true aliasing, not a copy).
    """

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}

    def find_spec(self, fullname: str, path: object = None, target: object = None):
        if not fullname.startswith("mnemos."):
            return None
        if fullname == "mnemos":
            return None  # this __init__ already imported
        vesmaro_name = "vesmaro." + fullname[len("mnemos.") :]
        try:
            importlib.import_module(vesmaro_name)
        except ImportError as exc:  # pragma: no cover - passthrough of real gaps
            raise ImportError(
                f"mnemos shim: cannot resolve {fullname!r} "
                f"(vesmaro module {vesmaro_name!r} failed: {exc})"
            ) from exc
        vesmaro_mod = sys.modules[vesmaro_name]
        self._pending[fullname] = vesmaro_name
        return importlib.machinery.ModuleSpec(fullname, self)

    def create_module(self, spec):
        # Return the ORIGINAL vesmaro module object — import machinery will
        # register it under the mnemos name (true alias, same object).
        vesmaro_name = self._pending.pop(spec.name, None)
        if vesmaro_name is None:  # pragma: no cover - direct loader misuse
            raise ImportError(f"mnemos shim: unexpected spec {spec.name!r}")
        return sys.modules[vesmaro_name]

    def exec_module(self, module) -> None:
        return None  # already fully executed as vesmaro


if not any(isinstance(f, _VesmaroAliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _VesmaroAliasFinder())

_warn_once()
