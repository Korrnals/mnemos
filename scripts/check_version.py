#!/usr/bin/env python
"""check-version gate: __version__ must match the installed dist metadata.

Tries the current distribution name first (`mnemos-memory-server`, #122),
falls back to the legacy `mnemos` name for pre-rename environments. A
missing metadata is a hard error — the gate is meaningless without an
editable/installed dist.
"""

from __future__ import annotations

from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version

from mnemos import __version__


def _installed(name: str) -> str | None:
    with suppress(PackageNotFoundError):
        return version(name)
    return None


def main() -> None:
    v = _installed("mnemos-memory-server") or _installed("mnemos")
    if v is None:
        raise SystemExit(
            "check-version: no distribution metadata found for "
            "mnemos-memory-server (or legacy mnemos) — install with `pip install -e .`"
        )
    assert __version__ == v, f"mismatch: __init__={__version__}, metadata={v}"
    print(f"✓ version {v} consistent")


if __name__ == "__main__":
    main()
