"""Tests for LLMRouter — threshold-based routing, mocked providers, no network.

PR 4 wires the RLM provider into the router. When the prompt is at or above
the threshold and ``rlm.enabled=True``, the router dispatches to a real
``RLMProvider`` (mocked here). On RLM failure with
``fallback_on_failure=True``, it falls back to the standard provider with
``fallback_used=True``. With ``fallback_on_failure=False``, the
``LLMExecutionError`` propagates. These tests verify:

  * small prompts → standard provider, ``fallback_used=False``
  * large prompts with RLM disabled → standard, ``fallback_used=False``
  * large prompts with RLM enabled → RLM provider called, ``fallback_used=False``
  * RLM failure with fallback → standard, ``fallback_used=True``
  * RLM failure without fallback → ``LLMExecutionError`` propagates
  * standard provider failure → ``LLMExecutionError`` propagates
  * token estimation heuristic (``len(prompt) // 4``)
  * threshold boundary behaviour
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from mnemos.config import LLMConfig, RLMSettings
from mnemos.llm.base import LLMExecutionError, LLMProvider, LLMResponse
from mnemos.llm.router import LLMRouter

# ── test doubles ─────────────────────────────────────────────────────────────


class _MockStandardProvider(LLMProvider):
    """Deterministic in-memory provider — no network, no SDK import.

    Records every call so tests can assert routing decisions. Raises
    ``LLMExecutionError`` when ``fail`` is set so the router's error
    propagation path can be exercised.
    """

    def __init__(
        self,
        *,
        text: str = "mock-response",
        fail: bool = False,
    ) -> None:
        self._text = text
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self._fail:
            raise LLMExecutionError(
                "mock standard provider failure",
                provider="mock",
            )
        return LLMResponse(
            text=self._text,
            model="mock-model",
            tokens_in=len(prompt) // 4,
            tokens_out=len(self._text) // 4,
            cached=False,
            fallback_used=False,
        )

    @property
    def provider_name(self) -> str:
        return "mock"


class _MockRLMProvider(LLMProvider):
    """Mock RLM provider — records calls, optionally fails."""

    def __init__(
        self,
        *,
        text: str = "rlm-response",
        fail: bool = False,
    ) -> None:
        self._text = text
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self._fail:
            raise LLMExecutionError("mock RLM failure", provider="rlm")
        return LLMResponse(
            text=self._text,
            model="rlm:mock",
            tokens_in=len(prompt) // 4,
            tokens_out=len(self._text) // 4,
            cached=False,
            fallback_used=False,
        )

    @property
    def provider_name(self) -> str:
        return "rlm"


def _make_router(
    *,
    provider: _MockStandardProvider,
    rlm_settings: RLMSettings | None = None,
    rlm_provider: _MockRLMProvider | None = None,
) -> LLMRouter:
    """Build a router whose standard provider is pre-injected (no factory).

    When ``rlm_provider`` is given, it is injected directly so the router
    does not try to construct a real ``RLMProvider`` (which would need
    rlm_toolkit).
    """
    config = LLMConfig(provider="ollama", model="qwen2.5:3b")
    router = LLMRouter(config, rlm_settings)
    # Bypass create_provider — inject the mock directly.
    router._provider = provider
    if rlm_provider is not None:
        router._rlm_provider = rlm_provider
    return router


# ── token estimation ─────────────────────────────────────────────────────────


def test_router_token_estimation() -> None:
    """Token estimate is len(prompt) // 4 (the documented heuristic)."""
    assert LLMRouter._estimate_tokens("") == 0
    assert LLMRouter._estimate_tokens("a" * 4) == 1
    assert LLMRouter._estimate_tokens("a" * 40) == 10
    assert LLMRouter._estimate_tokens("hello world!") == 3  # 12 chars // 4


# ── small context → standard ─────────────────────────────────────────────────


async def test_router_small_context_uses_standard() -> None:
    """Prompt below threshold → standard provider, fallback_used=False."""
    provider = _MockStandardProvider(text="ok")
    rlm = RLMSettings(enabled=True, threshold_tokens=10_000)
    router = _make_router(provider=provider, rlm_settings=rlm)

    resp = await router.complete("small prompt")

    assert len(provider.calls) == 1
    assert provider.calls[0]["prompt"] == "small prompt"
    assert resp.text == "ok"
    assert resp.fallback_used is False
    assert router.provider_name == "router:standard"


# ── large context, RLM disabled → standard ──────────────────────────────────


async def test_router_large_context_uses_standard_when_rlm_disabled() -> None:
    """Prompt above threshold but rlm.enabled=False → standard, no fallback."""
    provider = _MockStandardProvider(text="ok")
    # rlm_settings is None because RLM is disabled — matches MemoryManager.llm
    router = _make_router(provider=provider, rlm_settings=None)

    big_prompt = "x" * 80_000  # 20_000 tokens, well above any threshold
    resp = await router.complete(big_prompt)

    assert len(provider.calls) == 1
    assert resp.fallback_used is False
    assert router.provider_name == "router:standard"


# ── large context, RLM enabled → RLM dispatch ───────────────────────────────


async def test_router_large_context_uses_rlm() -> None:
    """Prompt >= threshold, RLM enabled → RLM provider called, no fallback."""
    standard = _MockStandardProvider(text="standard-ok")
    rlm_provider = _MockRLMProvider(text="rlm-ok")
    rlm = RLMSettings(enabled=True, threshold_tokens=100)
    router = _make_router(
        provider=standard,
        rlm_settings=rlm,
        rlm_provider=rlm_provider,
    )

    big_prompt = "x" * 800  # 200 tokens, above threshold=100
    resp = await router.complete(big_prompt)

    assert len(rlm_provider.calls) == 1
    assert rlm_provider.calls[0]["prompt"] == big_prompt
    assert len(standard.calls) == 0  # standard NOT called
    assert resp.text == "rlm-ok"
    assert resp.fallback_used is False
    assert router.provider_name == "router:rlm"


# ── RLM failure with fallback → standard ────────────────────────────────────


async def test_router_rlm_failure_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RLM raises LLMExecutionError, fallback_on_failure=True → standard."""
    standard = _MockStandardProvider(text="fallback-ok")
    rlm_provider = _MockRLMProvider(fail=True)
    rlm = RLMSettings(
        enabled=True,
        threshold_tokens=100,
        fallback_on_failure=True,
    )
    router = _make_router(
        provider=standard,
        rlm_settings=rlm,
        rlm_provider=rlm_provider,
    )

    big_prompt = "x" * 800
    with caplog.at_level(logging.WARNING, logger="mnemos.llm.router"):
        resp = await router.complete(big_prompt)

    assert len(rlm_provider.calls) == 1  # RLM was attempted
    assert len(standard.calls) == 1  # then fell back to standard
    assert resp.text == "fallback-ok"
    assert resp.fallback_used is True
    assert router.provider_name == "router:rlm-fallback"
    assert any("RLM call failed" in rec.message for rec in caplog.records)


# ── RLM failure without fallback → error propagates ─────────────────────────


async def test_router_rlm_failure_no_fallback() -> None:
    """RLM raises, fallback_on_failure=False → LLMExecutionError propagates."""
    standard = _MockStandardProvider(text="should-not-reach")
    rlm_provider = _MockRLMProvider(fail=True)
    rlm = RLMSettings(
        enabled=True,
        threshold_tokens=100,
        fallback_on_failure=False,
    )
    router = _make_router(
        provider=standard,
        rlm_settings=rlm,
        rlm_provider=rlm_provider,
    )

    big_prompt = "x" * 800
    with pytest.raises(LLMExecutionError) as exc_info:
        await router.complete(big_prompt)

    assert exc_info.value.provider == "rlm"
    assert "mock RLM failure" in str(exc_info.value)
    assert len(standard.calls) == 0  # standard NOT called


# ── standard failure → LLMExecutionError ─────────────────────────────────────


async def test_router_standard_failure_raises_llm_execution_error() -> None:
    """When the standard provider raises LLMExecutionError, it propagates."""
    provider = _MockStandardProvider(fail=True)
    router = _make_router(provider=provider, rlm_settings=None)

    with pytest.raises(LLMExecutionError) as exc_info:
        await router.complete("anything")

    assert exc_info.value.provider == "mock"
    assert "mock standard provider failure" in str(exc_info.value)


# ── threshold boundary ───────────────────────────────────────────────────────


async def test_router_threshold_boundary() -> None:
    """Prompt exactly at threshold → RLM dispatch (>= triggers RLM)."""
    standard = _MockStandardProvider(text="standard")
    rlm_provider = _MockRLMProvider(text="rlm")
    rlm = RLMSettings(enabled=True, threshold_tokens=10)
    router = _make_router(
        provider=standard,
        rlm_settings=rlm,
        rlm_provider=rlm_provider,
    )

    # Exactly at threshold: 40 chars → 10 tokens == threshold.
    at_threshold = "a" * 40
    resp = await router.complete(at_threshold)

    assert len(rlm_provider.calls) == 1
    assert len(standard.calls) == 0
    assert resp.fallback_used is False
    assert router.provider_name == "router:rlm"


async def test_router_just_below_threshold_uses_standard() -> None:
    """Prompt one token below threshold → standard, fallback_used=False."""
    provider = _MockStandardProvider(text="below")
    rlm = RLMSettings(enabled=True, threshold_tokens=10)
    router = _make_router(provider=provider, rlm_settings=rlm)

    # 36 chars → 9 tokens, below threshold=10.
    below = "a" * 36
    resp = await router.complete(below)

    assert len(provider.calls) == 1
    assert resp.fallback_used is False
    assert router.provider_name == "router:standard"


# ── provider_name before any call ────────────────────────────────────────────


def test_router_provider_name_default() -> None:
    """Before any complete() call, provider_name is 'router'."""
    provider = _MockStandardProvider()
    router = _make_router(provider=provider, rlm_settings=None)
    assert router.provider_name == "router"
