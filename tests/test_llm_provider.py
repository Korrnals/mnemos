"""Tests for LLMResponse fields, LLMExecutionError, and create_provider factory.

PR 1 covered the ``fallback_used`` field and ``LLMExecutionError`` type.
PR 2 replaces the ``create_provider`` stub with a real factory dispatching
to ``OllamaProvider`` / ``OpenAIProvider`` / ``AnthropicProvider``; these
tests verify the dispatch, import-guard behaviour, and error paths without
making any real network calls.
"""

from __future__ import annotations

import pytest

from mnemos.config import LLMConfig
from mnemos.llm.base import (
    LLMExecutionError,
    LLMProvider,
    LLMResponse,
    create_provider,
)

# ── LLMResponse.fallback_used ────────────────────────────────────────────────


def test_llm_response_defaults() -> None:
    """LLMResponse keeps its original defaults when fallback_used is unset."""
    resp = LLMResponse(text="hello", model="qwen2.5:3b")
    assert resp.text == "hello"
    assert resp.model == "qwen2.5:3b"
    assert resp.tokens_in == 0
    assert resp.tokens_out == 0
    assert resp.cached is False
    assert resp.fallback_used is False


def test_llm_response_fallback_used_false_explicit() -> None:
    """fallback_used can be explicitly set to False."""
    resp = LLMResponse(text="hi", model="qwen2.5:3b", fallback_used=False)
    assert resp.fallback_used is False


def test_llm_response_fallback_used_true() -> None:
    """fallback_used=True marks a graceful degradation from the primary."""
    resp = LLMResponse(text="hi", model="llama3:8b", fallback_used=True)
    assert resp.fallback_used is True


def test_llm_response_all_fields_set() -> None:
    """All fields can be set together (happy path for a fallback hit)."""
    resp = LLMResponse(
        text="synthesis",
        model="claude-3-opus",
        tokens_in=120,
        tokens_out=80,
        cached=False,
        fallback_used=True,
    )
    assert resp.fallback_used is True
    assert resp.tokens_in == 120
    assert resp.tokens_out == 80


# ── LLMExecutionError ────────────────────────────────────────────────────────


def test_llm_execution_error_is_exception() -> None:
    """LLMExecutionError is a subclass of Exception."""
    assert issubclass(LLMExecutionError, Exception)


def test_llm_execution_error_message() -> None:
    """The error message is preserved."""
    err = LLMExecutionError("provider timed out")
    assert "provider timed out" in str(err)


def test_llm_execution_error_provider_field() -> None:
    """The provider field identifies which provider failed."""
    err = LLMExecutionError("boom", provider="ollama")
    assert err.provider == "ollama"


def test_llm_execution_error_default_provider_empty() -> None:
    """provider defaults to an empty string when not supplied."""
    err = LLMExecutionError("boom")
    assert err.provider == ""


def test_llm_execution_error_chained_cause() -> None:
    """A cause exception is chained via __cause__."""
    root = TimeoutError("connection dropped")
    err = LLMExecutionError("provider timed out", provider="openai", cause=root)
    assert err.__cause__ is root


def test_llm_execution_error_raisable() -> None:
    """LLMExecutionError can be raised and caught as Exception."""
    with pytest.raises(LLMExecutionError) as exc_info:
        raise LLMExecutionError("auth rejected", provider="anthropic")
    assert exc_info.value.provider == "anthropic"


def test_llm_execution_error_catchable_as_exception() -> None:
    """LLMExecutionError is catchable via the base Exception type."""
    with pytest.raises(Exception):  # noqa: B017 — intentional broad catch test
        raise LLMExecutionError("boom")


# ── create_provider factory (PR 2) ───────────────────────────────────────────


def test_create_provider_ollama_returns_ollama_provider() -> None:
    """create_provider('ollama') returns an OllamaProvider instance."""
    from mnemos.llm.ollama import OllamaProvider

    config = LLMConfig(provider="ollama", model="qwen2.5:3b")
    provider = create_provider(config)
    assert isinstance(provider, OllamaProvider)
    assert provider.provider_name == "ollama"


def test_create_provider_openai_returns_openai_provider() -> None:
    """create_provider('openai') returns an OpenAIProvider instance."""
    from mnemos.llm.openai import OpenAIProvider

    config = LLMConfig(
        provider="openai", model="gpt-4o", openai_api_key="sk-test"
    )
    provider = create_provider(config)
    assert isinstance(provider, OpenAIProvider)
    assert provider.provider_name == "openai"


def test_create_provider_anthropic_returns_anthropic_provider() -> None:
    """create_provider('anthropic') returns an AnthropicProvider instance."""
    from mnemos.llm.anthropic import AnthropicProvider

    config = LLMConfig(
        provider="anthropic", model="claude-3-opus", anthropic_api_key="sk-ant-test"
    )
    provider = create_provider(config)
    assert isinstance(provider, AnthropicProvider)
    assert provider.provider_name == "anthropic"


def test_create_provider_rlm_import_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """When rlm_toolkit is absent, create_provider('rlm') raises ImportError.

    PR 4 replaced the previous NotImplementedError with a real RLMProvider.
    When the toolkit is not installed, the import guard fires with a
    helpful install hint.
    """
    from mnemos.llm import rlm as rlm_mod

    monkeypatch.setattr(rlm_mod, "RLM_AVAILABLE", False)
    config = LLMConfig(provider="rlm")
    with pytest.raises(ImportError, match="rlm-toolkit not installed"):
        create_provider(config)


def test_create_provider_unknown_raises_value_error() -> None:
    """An unrecognised provider name raises ValueError."""
    config = LLMConfig(provider="totally-fake")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_provider(config)


def test_create_provider_ollama_import_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the ollama SDK is absent, create_provider('ollama') raises ImportError."""
    from mnemos.llm import ollama as ollama_mod

    monkeypatch.setattr(ollama_mod, "OLLAMA_AVAILABLE", False)
    config = LLMConfig(provider="ollama")
    with pytest.raises(ImportError, match="ollama SDK not installed"):
        create_provider(config)


def test_create_provider_openai_import_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the openai SDK is absent, create_provider('openai') raises ImportError."""
    from mnemos.llm import openai as openai_mod

    monkeypatch.setattr(openai_mod, "OPENAI_AVAILABLE", False)
    config = LLMConfig(provider="openai", openai_api_key="sk-test")
    with pytest.raises(ImportError, match="openai SDK not installed"):
        create_provider(config)


def test_create_provider_anthropic_import_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the anthropic SDK is absent, create_provider('anthropic') raises ImportError."""
    from mnemos.llm import anthropic as anthropic_mod

    monkeypatch.setattr(anthropic_mod, "ANTHROPIC_AVAILABLE", False)
    config = LLMConfig(provider="anthropic", anthropic_api_key="sk-ant-test")
    with pytest.raises(ImportError, match="anthropic SDK not installed"):
        create_provider(config)


def test_create_provider_returns_llm_provider_subclass() -> None:
    """Every factory result is an LLMProvider instance (contract check)."""
    config = LLMConfig(provider="ollama", model="qwen2.5:3b")
    provider = create_provider(config)
    assert isinstance(provider, LLMProvider)


# ── LLMProvider abstract contract ────────────────────────────────────────────


def test_llm_provider_is_abstract() -> None:
    """LLMProvider cannot be instantiated directly (abstract base)."""
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


def test_llm_provider_subclass_must_implement_methods() -> None:
    """A subclass missing complete/provider_name still cannot instantiate."""

    class _Incomplete(LLMProvider):
        async def complete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096):  # type: ignore[no-untyped-def]
            ...

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_llm_provider_full_subclass_instantiates() -> None:
    """A fully-implemented subclass can be instantiated."""

    class _Complete(LLMProvider):
        async def complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            temperature: float = 0.3,
            max_tokens: int = 4096,
        ) -> LLMResponse:
            return LLMResponse(text="ok", model="test")

        @property
        def provider_name(self) -> str:
            return "test"

    provider = _Complete()
    assert provider.provider_name == "test"
