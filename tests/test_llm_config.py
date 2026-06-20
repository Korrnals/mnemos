"""Tests for the RLMSettings config schema (PR 1).

Covers validation rules, defaults, env-var overrides, and bounds — the
contract Tech Lead approved for the RLM integration plan.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mnemos.config import LLMConfig, RLMSettings, Settings

# ── Defaults ─────────────────────────────────────────────────────────────────


def test_rlm_defaults_offline() -> None:
    """RLM is disabled and InfiniRetri is off by default (Tech Lead decision #3)."""
    rlm = RLMSettings()
    assert rlm.enabled is False
    assert rlm.use_infiniretri is False


def test_rlm_default_threshold_tokens() -> None:
    """threshold_tokens defaults to 10_000."""
    assert RLMSettings().threshold_tokens == 10_000


def test_rlm_default_allowed_imports() -> None:
    """allowed_imports defaults to the 6 safe stdlib modules (no numpy)."""
    rlm = RLMSettings()
    assert rlm.allowed_imports == ["re", "json", "math", "datetime", "collections", "itertools"]
    assert "numpy" not in rlm.allowed_imports


def test_rlm_default_bounds() -> None:
    """Resource bounds have sane defaults."""
    rlm = RLMSettings()
    assert rlm.max_cost == 0.50
    assert rlm.max_depth == 3
    assert rlm.max_execution_time == 120


def test_rlm_default_sandbox_true() -> None:
    """sandbox defaults to True."""
    assert RLMSettings().sandbox is True


def test_llm_config_has_rlm_section() -> None:
    """LLMConfig embeds an RLMSettings with the offline defaults."""
    cfg = LLMConfig()
    assert isinstance(cfg.rlm, RLMSettings)
    assert cfg.rlm.enabled is False
    assert cfg.rlm.use_infiniretri is False


def test_settings_has_rlm_defaults() -> None:
    """Top-level Settings exposes llm.rlm with offline defaults."""
    settings = Settings()
    assert settings.llm.rlm.enabled is False
    assert settings.llm.rlm.use_infiniretri is False


# ── sandbox=False is rejected ────────────────────────────────────────────────


def test_sandbox_false_rejected() -> None:
    """sandbox=False must raise ValidationError — host code execution risk."""
    with pytest.raises(ValidationError) as exc_info:
        RLMSettings(sandbox=False)
    assert "sandbox" in str(exc_info.value).lower()


def test_sandbox_false_rejected_via_llm_config() -> None:
    """sandbox=False is rejected even when nested under llm.rlm."""
    with pytest.raises(ValidationError):
        LLMConfig(rlm=RLMSettings(sandbox=False))


def test_sandbox_false_message_mentions_execution() -> None:
    """The rejection message explains the risk so operators understand why."""
    with pytest.raises(ValidationError) as exc_info:
        RLMSettings(sandbox=False)
    msg = str(exc_info.value)
    assert "execution" in msg.lower() or "sandbox" in msg.lower()


# ── Bounds validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("tokens", [0, -1, 1_000_001])
def test_threshold_tokens_out_of_bounds(tokens: int) -> None:
    """threshold_tokens must be within [1, 1_000_000]."""
    with pytest.raises(ValidationError):
        RLMSettings(threshold_tokens=tokens)


@pytest.mark.parametrize("cost", [-0.01, 100.01, -1.0])
def test_max_cost_out_of_bounds(cost: float) -> None:
    """max_cost must be within [0.0, 100.0]."""
    with pytest.raises(ValidationError):
        RLMSettings(max_cost=cost)


@pytest.mark.parametrize("depth", [0, -1, 11])
def test_max_depth_out_of_bounds(depth: int) -> None:
    """max_depth must be within [1, 10]."""
    with pytest.raises(ValidationError):
        RLMSettings(max_depth=depth)


@pytest.mark.parametrize("seconds", [0, -1, 3601])
def test_max_execution_time_out_of_bounds(seconds: int) -> None:
    """max_execution_time must be within [1, 3600]."""
    with pytest.raises(ValidationError):
        RLMSettings(max_execution_time=seconds)


# ── allowed_imports customization ───────────────────────────────────────────


def test_allowed_imports_can_be_extended() -> None:
    """Operators can extend allowed_imports (e.g. add numpy explicitly)."""
    rlm = RLMSettings(allowed_imports=["re", "json", "numpy"])
    assert "numpy" in rlm.allowed_imports


def test_allowed_imports_can_be_empty() -> None:
    """An empty allowed_imports list is valid (most restrictive)."""
    rlm = RLMSettings(allowed_imports=[])
    assert rlm.allowed_imports == []


# ── env var overrides ───────────────────────────────────────────────────────


def test_env_var_enables_rlm(monkeypatch: pytest.MonkeyPatch) -> None:
    """MNEMOS_LLM__RLM__ENABLED=true flips the enabled flag via env override."""
    monkeypatch.setenv("MNEMOS_LLM__RLM__ENABLED", "true")
    settings = Settings()
    assert settings.llm.rlm.enabled is True


def test_env_var_use_infiniretri(monkeypatch: pytest.MonkeyPatch) -> None:
    """MNEMOS_LLM__RLM__USE_INFINIRETRI=true flips the InfiniRetri flag."""
    monkeypatch.setenv("MNEMOS_LLM__RLM__USE_INFINIRETRI", "true")
    settings = Settings()
    assert settings.llm.rlm.use_infiniretri is True


def test_env_var_threshold_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """MNEMOS_LLM__RLM__THRESHOLD_TOKENS overrides the default threshold."""
    monkeypatch.setenv("MNEMOS_LLM__RLM__THRESHOLD_TOKENS", "5000")
    settings = Settings()
    assert settings.llm.rlm.threshold_tokens == 5000


def test_env_var_does_not_enable_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env override, RLM stays disabled (offline default holds)."""
    monkeypatch.delenv("MNEMOS_LLM__RLM__ENABLED", raising=False)
    settings = Settings()
    assert settings.llm.rlm.enabled is False


# ── YAML round-trip ──────────────────────────────────────────────────────────


def test_rlm_section_loads_from_yaml_dict() -> None:
    """RLM settings can be supplied via the YAML config dict path."""
    cfg = LLMConfig(
        provider="ollama",
        rlm=RLMSettings(enabled=True, use_infiniretri=False, threshold_tokens=8000),
    )
    assert cfg.rlm.enabled is True
    assert cfg.rlm.use_infiniretri is False
    assert cfg.rlm.threshold_tokens == 8000


def test_rlm_partial_override_keeps_other_defaults() -> None:
    """Overriding one RLM field leaves the others at their defaults."""
    rlm = RLMSettings(enabled=True)
    assert rlm.enabled is True
    assert rlm.use_infiniretri is False  # default preserved
    assert rlm.threshold_tokens == 10_000  # default preserved
    assert rlm.sandbox is True  # default preserved
