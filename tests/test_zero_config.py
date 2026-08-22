"""Issue #123 — Phase 0: zero-config loopback profile (ADR-0017, D6).

Acceptance criteria (authoritative, from the issue):
  1. ``mnemos serve`` with zero config edits works on loopback.
  2. Non-loopback bind without auth+TOTP+TLS still refuses to start
     (guard regression test — the zero-config profile must not weaken it).
  3. First add/search roundtrip from a clean install, with FTS5 lexical
     recall active and no embedding provider available (offline / model
     not downloaded).

The zero-config profile is the built-in default set: loopback-only bind
(127.0.0.1:8787), storage auto-created under ``~/.mnemos/``, FTS5 recall
always on (the vector leg degrades non-fatally when embeddings are
unavailable).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mnemos.api.main as api_main
import mnemos.manager as manager_module
from mnemos.api.main import _check_non_loopback_auth, app
from mnemos.cli import _manager as cli_manager_module
from mnemos.cli.main import app as cli_app
from mnemos.config import ApiConfig, find_config_file, load_settings
from mnemos.manager import MemoryManager
from mnemos.models import MemoryCreate, MemorySource
from mnemos.scanner_runtime import reset_scanner

_VALID_TAGS = ["project:demo", "agent:user", "mnemos:learning"]


def _clear_mnemos_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every MNEMOS_* env var so tests start from a clean slate.

    pydantic-settings maps ``MNEMOS_API__HOST`` etc. onto ``Settings``; a
    leftover variable from the developer shell would silently change the
    defaults under test.
    """
    for key in list(os.environ):
        if key.startswith("MNEMOS_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def clean_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Simulate a clean install: fresh HOME, empty cwd, no MNEMOS_* env.

    Resets the CLI / API manager singletons and the scanner singleton so
    each test constructs its own stores under the temporary home.
    """
    _clear_mnemos_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # no ./config.yaml in cwd
    api_main._manager = None
    cli_manager_module._manager = None
    reset_scanner()
    yield tmp_path
    api_main._manager = None
    cli_manager_module._manager = None
    reset_scanner()


@pytest.fixture
def no_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate 'no embedding provider available' on a clean install.

    The default provider (chromadb ONNX MiniLM) needs to download an
    ~80 MB model on first use; offline (or before the download) every
    embed call fails. Both the add path and the search vector leg must
    treat this as non-fatal — FTS5 lexical recall stays active.
    """

    def _broken(_cfg: object) -> object:
        raise RuntimeError("embedding provider unavailable (clean install, offline)")

    monkeypatch.setattr(manager_module, "create_embedding_provider", _broken)


# ---------------------------------------------------------------------------
# Acceptance 2 (+ D6): defaults are loopback; the guard is not weakened
# ---------------------------------------------------------------------------


class TestZeroConfigDefaults:
    def test_default_api_config_is_loopback(self):
        """D6 regression: the built-in default bind is loopback ONLY.

        If someone flips this default to 0.0.0.0, the zero-config profile
        silently becomes network-exposed — this test must fail first.
        """
        cfg = ApiConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8787
        assert cfg.auth_enabled is False
        assert cfg.totp_enabled is False

    def test_load_settings_without_config_uses_safe_defaults(self, clean_home):
        """No config file anywhere → pure defaults, storage under $HOME/.mnemos."""
        assert find_config_file() is None, "fixture must guarantee a config-free env"
        settings = load_settings()
        assert settings.api.host == "127.0.0.1"
        assert settings.api.auth_enabled is False
        assert settings.mnemos.vault_path == clean_home / ".mnemos" / "vault"
        assert settings.mnemos.data_dir == clean_home / ".mnemos" / "data"
        assert settings.db_path == clean_home / ".mnemos" / "data" / "mnemos.db"

    def test_find_config_file_explicit_path(self, clean_home, tmp_path):
        cfg_file = tmp_path / "my.yaml"
        cfg_file.write_text("api:\n  port: 9999\n", encoding="utf-8")
        assert find_config_file(cfg_file) == cfg_file
        settings = load_settings(cfg_file)
        assert settings.api.port == 9999

    def test_zero_config_defaults_pass_startup_guard(self, clean_home):
        """Zero-config = loopback bind → startup guard passes by design."""
        _check_non_loopback_auth(load_settings().api)  # must not raise

    def test_non_loopback_env_override_still_refuses(self, clean_home):
        """Acceptance 2: env override to a non-loopback bind + no auth → refuse.

        ``MNEMOS_API__HOST`` is exactly how ``mnemos serve --host 0.0.0.0``
        propagates the bind into the app process; the guard must fire.
        """
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("MNEMOS_API__HOST", "0.0.0.0")
        try:
            settings = load_settings()
            assert settings.api.host == "0.0.0.0"
            with pytest.raises(SystemExit) as exc_info:
                _check_non_loopback_auth(settings.api)
            assert exc_info.value.code == 1
        finally:
            monkeypatch.undo()


# ---------------------------------------------------------------------------
# Acceptance 3: first add/search roundtrip from a clean install
# ---------------------------------------------------------------------------


class TestFirstRun:
    def test_first_run_creates_storage_layout(self, clean_home):
        """First run auto-creates vault/, data/ and (on first store access) the DB.

        SQLiteStore opens the connection lazily on the first query — the
        same warm-up ``serve`` performs at startup — so the DB file appears
        on the first store access, not at construction.
        """
        assert not (clean_home / ".mnemos").exists()
        mgr = MemoryManager(load_settings())
        try:
            assert (clean_home / ".mnemos" / "vault").is_dir()
            assert (clean_home / ".mnemos" / "data").is_dir()
            assert not (clean_home / ".mnemos" / "data" / "mnemos.db").exists()
            mgr.stats()  # first store access — the serve warm-up equivalent
            assert (clean_home / ".mnemos" / "data" / "mnemos.db").exists()
        finally:
            mgr.close()

    def test_add_search_roundtrip_without_embedding_provider(self, clean_home, no_embedder):
        """Acceptance 3 (manager level): add → FTS search works with no embedder.

        The add path must not fail when the embedding provider is
        unavailable (non-fatal), and the search vector leg must degrade to
        FTS-only — the memory is still found via the lexical leg.
        """
        mgr = MemoryManager(load_settings())
        try:
            memory = mgr.add(
                MemoryCreate(
                    content="Zero-config loopback profile: first memory works offline",
                    tags=list(_VALID_TAGS),
                    source=MemorySource.CLI,
                ),
                project="demo",
                agent="user",
            )
            assert memory.id

            # Strict default (published/processed only): a raw memory is
            # intentionally invisible to agents — that contract is unchanged.
            strict = mgr.search("zero-config loopback")
            assert strict == []

            # include_raw widens to raw+processing+processed+published —
            # this is what the CLI default now passes, and the memory is
            # found via the FTS leg with the vector leg degraded.
            results = mgr.search("zero-config loopback", include_raw=True)
            assert len(results) == 1
            assert results[0].memory.id == memory.id
            assert results[0].search_type == "fts_only"
        finally:
            mgr.close()

    def test_cli_add_search_roundtrip(self, clean_home, no_embedder):
        """Acceptance 3 (CLI level): `mnemos add` then `mnemos search` finds it.

        End-to-end over the real Typer CLI with a clean HOME and no
        embedding provider — the documented 5-minute first-run journey.
        """
        runner = CliRunner()
        added = runner.invoke(
            cli_app,
            ["add", "hello from the zero-config quickstart", "-T", ",".join(_VALID_TAGS)],
        )
        assert added.exit_code == 0, added.output
        assert "Saved" in added.output

        found = runner.invoke(cli_app, ["search", "zero-config quickstart"])
        assert found.exit_code == 0, found.output
        assert "No results found" not in found.output
        # Rich wraps table cells across lines — assert on wrap-resistant
        # fragments rather than the full title string.
        assert "hello from" in found.output
        assert "quickstart" in found.output
        assert "raw" in found.output  # status column shows it is not yet published

        # The escape hatch back to agent-grade strictness still works.
        strict = runner.invoke(cli_app, ["search", "zero-config quickstart", "--published-only"])
        assert strict.exit_code == 0, strict.output
        assert "No results found" in strict.output


# ---------------------------------------------------------------------------
# Acceptance 1: `mnemos serve` with zero config works on loopback
# ---------------------------------------------------------------------------


class TestServeZeroConfig:
    def test_serve_app_starts_and_serves_loopback(self, clean_home, no_embedder):
        """The real app (lifespan incl. guard) boots on zero-config defaults."""
        with TestClient(app) as tc:
            r = tc.get("/health")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}

    def test_serve_http_add_search_roundtrip(self, clean_home, no_embedder):
        """HTTP surface roundtrip: POST /memories → POST /search finds it.

        The HTTP surface keeps the strict published-only default (documented
        agent contract), so the roundtrip uses include_raw=true — the
        background processor would publish the raw memory within one cycle
        (~120 s) in a long-running server; the test must not depend on that
        timing.
        """
        with TestClient(app) as tc:
            created = tc.post(
                "/memories",
                json={
                    "content": "served memory from a zero-config loopback install",
                    "tags": list(_VALID_TAGS),
                },
            )
            assert created.status_code == 201, created.text
            memory_id = created.json()["id"]

            found = tc.post("/search", json={"query": "zero-config loopback", "include_raw": True})
            assert found.status_code == 200, found.text
            hits = found.json()
            assert any(h["id"] == memory_id for h in hits)
            assert all(h["search_type"] == "fts_only" for h in hits)
