"""Version guard (#204) — VERSION file and pyproject.toml must agree.

Catches version drift between the two release markers BEFORE it reaches a
tag or a PyPI upload. Fail-loud, no fallbacks.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_version_file_matches_pyproject() -> None:
    """VERSION file must equal pyproject [project].version exactly."""
    version_file = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject_version = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert version_file == pyproject_version, (
        f"version drift: VERSION={version_file!r} != "
        f"pyproject.version={pyproject_version!r} — bump both synchronously (#204)"
    )
