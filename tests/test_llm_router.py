"""Tests for LLMRouter — threshold-based routing, mocked providers, no network.

PR 3 wires the router with standard-provider routing only. The RLM provider
is not yet implemented (PR 4); when RLM is "selected" (prompt at or above
threshold and ``rlm.enabled=True``) the router falls back to the standard
provider with ``fallback_used=True`` and a debug log. These tests verify:

  * small prompts → standard provider, ``fallback_used=False``
  * large prompts with RLM disabled → standard, ``fallback_used=False``
  * large prompts with RLM enabled but not implemented → standard,
    ``fallback_used=True``
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


def _make_router(
    *,
    provider: _MockStandardProvider,
    rlm_settings: RLMSettings | None = None,
) -> LLMRouter:
    """Build a router whose standard provider is pre-injected (no factory)."""
    config = LLMConfig(provider="ollama", model="qwen2.5:3b")
    router = LLMRouter(config, rlm_settings)
    # Bypass create_provider — inject the mock directly.
    router._provider = provider
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


# ── large context, RLM enabled but not implemented → fallback ───────────────


async def test_router_large_context_fallback_when_rlm_not_implemented(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Prompt >= threshold, rlm.enabled=True, RLM provider not wired (PR 3).

    Router falls back to standard with fallback_used=True and emits a debug
    log explaining the fallback.
    """
    provider = _MockStandardProvider(text="fallback-ok")
    rlm = RLMSettings(enabled=True, threshold_tokens=100)
    router = _make_router(provider=provider, rlm_settings=rlm)

    big_prompt = "x" * 800  # 200 tokens, above threshold=100
    with caplog.at_level(logging.DEBUG, logger="mnemos.llm.router"):
        resp = await router.complete(big_prompt)

    assert len(provider.calls) == 1
    assert resp.text == "fallback-ok"
    assert resp.fallback_used is True
    assert router.provider_name == "router:rlm-fallback"
    # Debug log mentions RLM not implemented
    assert any(
        "RLM provider not yet implemented" in rec.message
        for rec in caplog.records
    )


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
    """Prompt exactly at threshold → standard (RLM would trigger at >=)."""
    provider = _MockStandardProvider(text="boundary")
    rlm = RLMSettings(enabled=True, threshold_tokens=10)
    router = _make_router(provider=provider, rlm_settings=rlm)

    # Exactly at threshold: 40 chars → 10 tokens == threshold.
    # The router uses >= so this would route to RLM (fallback in PR 3).
    at_threshold = "a" * 40
    resp = await router.complete(at_threshold)

    assert len(provider.calls) == 1
    # At threshold with RLM enabled → RLM selected → fallback to standard.
    assert resp.fallback_used is True
    assert router.provider_name == "router:rlm-fallback"


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
