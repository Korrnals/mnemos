"""Tests for LLMResponse fields and LLMExecutionError (PR 1).

Covers the `fallback_used` field added to `LLMResponse` and the new
`LLMExecutionError` exception type. The `create_provider()` factory is
still a stub in PR 1 — its NotImplementedError is verified here too.
"""

from __future__ import annotations

import pytest

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


# ── create_provider stub (PR 1) ──────────────────────────────────────────────


def test_create_provider_raises_not_implemented() -> None:
    """PR 1: the factory is still a stub and raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        create_provider(object())


def test_create_provider_error_mentions_pr2() -> None:
    """The stub message points to PR 2 so callers know it is intentional."""
    with pytest.raises(NotImplementedError, match="PR 2"):
        create_provider(object())


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
