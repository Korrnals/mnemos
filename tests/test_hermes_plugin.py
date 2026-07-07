"""Unit tests for the Hermes MnemosMemoryProvider plugin.

The plugin at ``integrations/hermes/__init__.py`` imports two Hermes-internal
modules that are not available in the Mnemos test environment:

  - ``agent.memory_provider.MemoryProvider``  — the Hermes ABC
  - ``tools.registry.tool_error``              — Hermes tool-error helper

We install minimal stubs into ``sys.modules`` (mirroring how
``conftest.py`` stubs the optional ``mcp`` package) so the plugin can be
imported and its pure-Python surface tested without a running Hermes
installation or a running Mnemos server.

Coverage:

  1. ``register()`` exists and ``MnemosMemoryProvider`` is importable.
  2. ``get_tool_schemas()`` returns exactly 15 schemas.
  3. Every schema name starts with ``mnemos_``.
  4. ``_load_config()`` defaults (port 8000, not 8787).
  5. Circuit breaker opens after 5 failures and closes after cooldown.
  6. ``is_available()`` returns False when the server is unreachable.
  7. ``get_config_schema()`` returns 7 fields with the expected keys.
  8. ``save_config()`` writes to ``memory.mnemos`` (not ``plugins.mnemos``).
  9. ``sync_turn`` significance filter — only syncs when the user message
     exceeds 50 chars or on every Nth turn.
"""

from __future__ import annotations

import json
import os
import sys
import types
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Stub the Hermes-internal imports the plugin depends on.
# ---------------------------------------------------------------------------


class _StubMemoryProvider:
    """Minimal stand-in for ``agent.memory_provider.MemoryProvider``.

    The real ABC defines a handful of hooks (prefetch, sync_turn, …) with
    default no-op implementations. Our stub is a plain base class so the
    plugin's ``class MnemosMemoryProvider(MemoryProvider)`` declaration
    succeeds and isinstance/issubclass checks behave.
    """


def _stub_tool_error(msg: str) -> str:
    """Mirror ``tools.registry.tool_error`` — returns a JSON error string."""
    return json.dumps({"error": msg})


def _install_hermes_stubs() -> None:
    """Inject ``agent.memory_provider`` and ``tools.registry`` stubs.

    Only installed when the real modules are absent (the guard mirrors
    conftest.py's ``if "mcp" not in sys.modules`` pattern).
    """
    if "agent.memory_provider" not in sys.modules:
        agent_pkg = types.ModuleType("agent")
        agent_pkg.__path__ = []  # mark as package
        mp_mod = types.ModuleType("agent.memory_provider")
        mp_mod.MemoryProvider = _StubMemoryProvider
        sys.modules["agent"] = agent_pkg
        sys.modules["agent.memory_provider"] = mp_mod

    if "tools.registry" not in sys.modules:
        tools_pkg = types.ModuleType("tools")
        tools_pkg.__path__ = []
        reg_mod = types.ModuleType("tools.registry")
        reg_mod.tool_error = _stub_tool_error
        sys.modules["tools"] = tools_pkg
        sys.modules["tools.registry"] = reg_mod


_install_hermes_stubs()

# Make ``integrations`` importable as a package. The repo root holds the
# ``integrations/hermes/`` directory; pytest is invoked from the repo root
# (``testpaths = ["tests"]``) so the cwd is on sys.path, but the
# ``integrations`` namespace package may not be registered yet.
_REPO_ROOT = None
for _p in sys.path:
    if _p and os.path.isdir(os.path.join(_p, "integrations", "hermes")):
        _REPO_ROOT = _p
        break
if _REPO_ROOT is None:
    # Fall back to a relative path from this test file.
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Import the plugin now that the stubs are in place.
import integrations.hermes as hermes_plugin  # noqa: E402
from integrations.hermes import (  # noqa: E402
    _BREAKER_THRESHOLD,
    _SYNC_MIN_USER_CHARS,
    MnemosMemoryProvider,
    _load_config,
    register,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(**overrides) -> MnemosMemoryProvider:
    """Build a provider with a config dict (skips env/yaml loading)."""
    cfg = {
        "base_url": "http://127.0.0.1:8000",
        "api_key": "",
        "project": "hermes",
        "agent": "hermes-default",
        "auto_sync": True,
        "prefetch_limit": 5,
        "sync_interval": 10,
    }
    cfg.update(overrides)
    return MnemosMemoryProvider(cfg)


# ---------------------------------------------------------------------------
# 1. Plugin imports correctly
# ---------------------------------------------------------------------------


class TestPluginImport:
    def test_register_function_exists(self):
        assert callable(register)

    def test_provider_class_exists(self):
        assert MnemosMemoryProvider is not None
        # It must subclass our stubbed MemoryProvider.
        from agent.memory_provider import MemoryProvider

        assert issubclass(MnemosMemoryProvider, MemoryProvider)

    def test_provider_name_property(self):
        p = _make_provider()
        assert p.name == "mnemos"


# ---------------------------------------------------------------------------
# 2. 15 tool schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_exactly_15_schemas(self):
        p = _make_provider()
        schemas = p.get_tool_schemas()
        assert len(schemas) == 15

    def test_all_names_start_with_mnemos(self):
        p = _make_provider()
        for s in p.get_tool_schemas():
            assert s["name"].startswith("mnemos_"), s["name"]

    def test_schema_names_unique(self):
        p = _make_provider()
        names = [s["name"] for s in p.get_tool_schemas()]
        assert len(names) == len(set(names))

    def test_schemas_have_parameters(self):
        p = _make_provider()
        for s in p.get_tool_schemas():
            assert "parameters" in s
            assert "type" in s["parameters"]


# ---------------------------------------------------------------------------
# 4. Config loading defaults
# ---------------------------------------------------------------------------


class TestConfigLoading:
    def test_default_base_url_port_8000(self, monkeypatch):
        """Default base_url must be port 8000, not 8787."""
        # Clear env overrides so the defaults are exercised.
        for var in (
            "MNEMOS_BASE_URL",
            "MNEMOS_API_KEY",
            "MNEMOS_PROJECT",
            "MNEMOS_AGENT",
            "MNEMOS_AUTO_SYNC",
            "MNEMOS_PREFETCH_LIMIT",
            "MNEMOS_SYNC_INTERVAL",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg = _load_config()
        assert cfg["base_url"] == "http://127.0.0.1:8000"
        assert ":8787" not in cfg["base_url"]

    def test_default_project_and_agent(self, monkeypatch):
        for var in ("MNEMOS_PROJECT", "MNEMOS_AGENT"):
            monkeypatch.delenv(var, raising=False)
        cfg = _load_config()
        assert cfg["project"] == "hermes"
        assert cfg["agent"] == "hermes-default"

    def test_default_sync_interval(self, monkeypatch):
        monkeypatch.delenv("MNEMOS_SYNC_INTERVAL", raising=False)
        cfg = _load_config()
        assert cfg["sync_interval"] == 10


# ---------------------------------------------------------------------------
# 5. Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_breaker_opens_after_threshold(self):
        """After _BREAKER_THRESHOLD failures the breaker is open."""
        p = _make_provider()
        assert p._is_breaker_open() is False
        for _ in range(_BREAKER_THRESHOLD):
            p._record_failure()
        assert p._is_breaker_open() is True

    def test_breaker_closes_after_cooldown(self):
        """Once the cooldown elapses the breaker resets to closed."""
        p = _make_provider()
        for _ in range(_BREAKER_THRESHOLD):
            p._record_failure()
        assert p._is_breaker_open() is True

        # Fast-forward past the cooldown.
        p._breaker_until = 0.0  # simulate elapsed cooldown
        # _is_breaker_open resets failures when cooldown expired.
        assert p._is_breaker_open() is False
        assert p._failures == 0

    def test_record_success_resets_failures(self):
        p = _make_provider()
        p._record_failure()
        p._record_failure()
        assert p._failures == 2
        p._record_success()
        assert p._failures == 0


# ---------------------------------------------------------------------------
# 6. is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_returns_false_when_unreachable(self):
        """is_available() returns False when /health cannot be reached."""
        p = _make_provider(base_url="http://127.0.0.1:1")  # closed port
        # Don't actually attempt a socket connection — patch _get_json.
        with patch.object(hermes_plugin, "_get_json", side_effect=Exception("boom")):
            assert p.is_available() is False

    def test_returns_true_when_healthy(self):
        p = _make_provider()
        with patch.object(hermes_plugin, "_get_json", return_value={"status": "ok"}):
            assert p.is_available() is True


# ---------------------------------------------------------------------------
# 7. Config schema
# ---------------------------------------------------------------------------


class TestConfigSchema:
    def test_seven_fields(self):
        p = _make_provider()
        schema = p.get_config_schema()
        assert len(schema) == 7

    def test_expected_keys(self):
        p = _make_provider()
        keys = {f["key"] for f in p.get_config_schema()}
        assert keys == {
            "base_url",
            "api_key",
            "project",
            "agent",
            "auto_sync",
            "prefetch_limit",
            "sync_interval",
        }

    def test_base_url_default_is_8000(self):
        p = _make_provider()
        base = next(f for f in p.get_config_schema() if f["key"] == "base_url")
        assert base["default"] == "http://127.0.0.1:8000"

    def test_api_key_marked_secret(self):
        p = _make_provider()
        api_key = next(f for f in p.get_config_schema() if f["key"] == "api_key")
        assert api_key.get("secret") is True


# ---------------------------------------------------------------------------
# 8. save_config writes to memory.mnemos
# ---------------------------------------------------------------------------


class TestSaveConfig:
    def test_writes_to_memory_mnemos(self, tmp_path):
        """save_config must write under memory.mnemos, not plugins.mnemos."""
        p = _make_provider()
        hermes_home = str(tmp_path)
        values = {"base_url": "http://localhost:9000", "project": "test"}

        # yaml is a dependency of the project, but guard just in case.
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        p.save_config(values, hermes_home)

        config_path = tmp_path / "config.yaml"
        assert config_path.exists()
        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert "memory" in data
        assert "mnemos" in data["memory"]
        assert data["memory"]["mnemos"] == values
        # Ensure we did NOT write to the legacy plugins.mnemos location.
        assert "plugins" not in data or "mnemos" not in data.get("plugins", {})


# ---------------------------------------------------------------------------
# 9. sync_turn significance filter
# ---------------------------------------------------------------------------


class TestSyncTurnSignificance:
    def test_short_message_not_synced(self):
        """A short user message (< 50 chars) on a non-Nth turn is skipped."""
        p = _make_provider(sync_interval=10)
        p.initialize("sess-1")
        with patch.object(hermes_plugin, "_post_json") as mock_post:
            p.sync_turn("hi", "hello back", session_id="sess-1")
            # Join any background thread so the mock assertion is reliable.
            if p._sync_thread:
                p._sync_thread.join(timeout=2.0)
            mock_post.assert_not_called()

    def test_long_message_synced(self):
        """A user message > 50 chars triggers a sync."""
        p = _make_provider(sync_interval=10)
        p.initialize("sess-1")
        long_msg = "x" * (_SYNC_MIN_USER_CHARS + 10)
        with patch.object(hermes_plugin, "_post_json", return_value={"id": "1"}) as mock_post:
            p.sync_turn(long_msg, "response", session_id="sess-1")
            if p._sync_thread:
                p._sync_thread.join(timeout=2.0)
            mock_post.assert_called_once()
            body = mock_post.call_args[0][1]
            assert body["content"].startswith("## User")

    def test_nth_turn_synced_even_if_short(self):
        """Every Nth turn syncs regardless of message length."""
        p = _make_provider(sync_interval=3)
        p.initialize("sess-1")
        with patch.object(hermes_plugin, "_post_json", return_value={"id": "1"}) as mock_post:
            # Turn 1 — short, not Nth → skipped.
            p.sync_turn("a", "b", session_id="sess-1")
            # Turn 2 — short, not Nth → skipped.
            p.sync_turn("a", "b", session_id="sess-1")
            # Turn 3 — short, but Nth (3 % 3 == 0) → synced.
            p.sync_turn("a", "b", session_id="sess-1")
            if p._sync_thread:
                p._sync_thread.join(timeout=2.0)
            assert mock_post.call_count == 1

    def test_auto_sync_disabled_skips(self):
        """When auto_sync is False, sync_turn never calls the API."""
        p = _make_provider(auto_sync=False, sync_interval=1)
        p.initialize("sess-1")
        with patch.object(hermes_plugin, "_post_json") as mock_post:
            p.sync_turn("x" * 200, "y", session_id="sess-1")
            if p._sync_thread:
                p._sync_thread.join(timeout=2.0)
            mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# handle_tool_call dispatch sanity (no real network)
# ---------------------------------------------------------------------------


class TestHandleToolCall:
    def test_unknown_tool_returns_error(self):
        p = _make_provider()
        result = p.handle_tool_call("mnemos_bogus", {})
        data = json.loads(result)
        assert "error" in data

    def test_breaker_open_returns_error(self):
        p = _make_provider()
        for _ in range(_BREAKER_THRESHOLD):
            p._record_failure()
        # Force the breaker to stay open.
        p._breaker_until = float("inf")
        result = p.handle_tool_call("mnemos_search", {"query": "x"})
        data = json.loads(result)
        assert "error" in data
        assert "circuit breaker" in data["error"].lower()
