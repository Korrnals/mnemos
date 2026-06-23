"""Test that __version__ is consistent with package metadata."""

from __future__ import annotations

from importlib.metadata import version as pkg_version

from mnemos import __version__


def test_version_is_string() -> None:
    """__version__ must be a non-empty string."""
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_version_matches_metadata() -> None:
    """__version__ must match the version in pyproject.toml (via metadata)."""
    metadata_version = pkg_version("mnemos")
    assert __version__ == metadata_version, (
        f"__version__ ({__version__}) != metadata ({metadata_version})"
    )


def test_version_not_unknown() -> None:
    """__version__ must not be the fallback '0.0.0+unknown'."""
    assert __version__ != "0.0.0+unknown"
