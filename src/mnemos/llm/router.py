"""LLMRouter — threshold-based routing between standard and RLM providers.

The router is the single entry point the rest of Mnemos uses to obtain LLM
completions. It owns two concerns:

1. **Token estimation** — a cheap ``len(prompt) // 4`` heuristic decides
   whether the prompt is large enough to warrant RLM decomposition.
2. **Provider selection** — below the threshold (or when RLM is disabled)
   the standard provider (Ollama / OpenAI / Anthropic) is used directly.
   At or above the threshold, when RLM is enabled, the RLM provider is
   dispatched. If the RLM call fails and ``fallback_on_failure`` is set,
   the router falls back to the standard provider with
   ``fallback_used=True`` so callers and observability can distinguish
   graceful degradation from a clean primary hit.

The router never makes a routing decision based on network state — only on
the prompt size and the configured threshold. This keeps the decision
deterministic and testable without any live provider.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mnemos.llm.base import (
    LLMExecutionError,
    LLMProvider,
    LLMResponse,
    create_provider,
)

if TYPE_CHECKING:
    from mnemos.config import LLMConfig, RLMSettings

logger = logging.getLogger(__name__)

# Cheap token estimate: ~4 chars per token for English/mixed prose. This is
# intentionally a heuristic — the router must not depend on a tokenizer
# download (supply-chain risk) or a network call. The real token count is
# reported by the provider in LLMResponse.tokens_in after the call.
_CHARS_PER_TOKEN: int = 4


class LLMRouter:
    """Threshold-based router between standard and RLM LLM providers.

    The standard provider is created lazily via :func:`create_provider` on
    first use (matching the ``MemoryManager.embedder`` pattern) so that
    constructing a router never pays the SDK import cost unless a
    completion is actually requested.

    The RLM provider is created lazily on the first above-threshold call
    when ``rlm_settings`` is non-``None``. This keeps the
    ``rlm_toolkit`` import cost off the hot path when RLM is configured
    but never triggered.

    ``rlm_settings`` is ``None`` when RLM is disabled
    (``rlm.enabled=False``) or the ``rlm_toolkit`` is not installed. When
    ``None`` the router always uses the standard provider.
    """

    def __init__(
        self,
        settings: LLMConfig,
        rlm_settings: RLMSettings | None = None,
    ) -> None:
        self._settings = settings
        self._rlm_settings = rlm_settings
        self._provider: LLMProvider | None = None
        self._rlm_provider: LLMProvider | None = None
        # Last selection: "standard" | "rlm" | "rlm-fallback". Used by the
        # provider_name property for observability. ``None`` until the first
        # complete() call — provider_name returns "router" before that.
        self._last_selection: str | None = None

    # ── internal helpers ─────────────────────────────────────────────────

    @property
    def _standard(self) -> LLMProvider:
        """Lazily instantiate the standard provider on first use."""
        if self._provider is None:
            self._provider = create_provider(self._settings)
        return self._provider

    def _rlm(self) -> LLMProvider:
        """Lazily instantiate the RLM provider on first above-threshold use.

        Imports ``RLMProvider`` here so the ``rlm_toolkit`` import cost is
        only paid when RLM is actually triggered. Raises ``ImportError``
        if the toolkit is not installed — the caller (complete) wraps that
        into a fallback when ``fallback_on_failure`` is set.
        """
        if self._rlm_provider is None:
            from mnemos.llm.rlm import RLMProvider

            assert self._rlm_settings is not None
            self._rlm_provider = RLMProvider(self._settings, self._rlm_settings)
        return self._rlm_provider

    @staticmethod
    def _estimate_tokens(prompt: str) -> int:
        """Cheap char-based token estimate (~4 chars/token)."""
        return len(prompt) // _CHARS_PER_TOKEN

    # ── public API ───────────────────────────────────────────────────────

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Route a completion request to the selected provider.

        Routing logic:
          * ``token_estimate = len(prompt) // 4``
          * if ``rlm_settings is None`` or ``token_estimate < threshold``:
            standard provider, ``fallback_used=False``.
          * if ``token_estimate >= threshold`` and RLM is configured:
            dispatch to the RLM provider. On ``LLMExecutionError`` with
            ``fallback_on_failure=True``, fall back to standard with
            ``fallback_used=True``. With ``fallback_on_failure=False``,
            the error propagates.
          * if the standard provider raises, ``LLMExecutionError``
            propagates (the router does not swallow provider errors).

        The returned ``LLMResponse`` always carries the routing outcome in
        its ``fallback_used`` flag.
        """
        token_estimate = self._estimate_tokens(prompt)
        threshold = (
            self._rlm_settings.threshold_tokens
            if self._rlm_settings is not None
            else None
        )

        use_rlm = (
            self._rlm_settings is not None
            and threshold is not None
            and token_estimate >= threshold
        )

        if use_rlm:
            assert self._rlm_settings is not None
            assert threshold is not None
            return await self._dispatch_rlm(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                token_estimate=token_estimate,
                threshold=threshold,
            )

        # Standard path — below threshold or RLM disabled.
        selected = "standard"
        logger.debug(
            "router: estimated=%d tokens, threshold=%s, selected=%s",
            token_estimate,
            threshold,
            selected,
        )
        self._last_selection = selected
        resp = await self._call_standard(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        resp.fallback_used = False
        return resp

    async def _dispatch_rlm(
        self,
        prompt: str,
        *,
        system: str | None,
        temperature: float,
        max_tokens: int,
        token_estimate: int,
        threshold: int,
    ) -> LLMResponse:
        """Dispatch to the RLM provider with optional fallback to standard.

        Called only when the prompt is at or above the RLM threshold and
        ``rlm_settings`` is non-``None``.
        """
        assert self._rlm_settings is not None
        try:
            rlm_provider = self._rlm()
        except ImportError as exc:
            # rlm_toolkit not installed — fall back or propagate.
            if not self._rlm_settings.fallback_on_failure:
                raise
            logger.warning(
                "router: RLM selected but rlm_toolkit not installed "
                "(estimated=%d tokens, threshold=%d): %s — falling back "
                "to standard",
                token_estimate,
                threshold,
                exc,
            )
            return await self._fallback_to_standard(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        try:
            resp = await rlm_provider.complete(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._last_selection = "rlm"
            resp.fallback_used = False
            logger.debug(
                "router: RLM dispatch succeeded (estimated=%d tokens, "
                "threshold=%d)",
                token_estimate,
                threshold,
            )
            return resp
        except LLMExecutionError as exc:
            if not self._rlm_settings.fallback_on_failure:
                raise
            logger.warning(
                "router: RLM call failed (estimated=%d tokens, "
                "threshold=%d): %s — falling back to standard",
                token_estimate,
                threshold,
                exc,
            )
            return await self._fallback_to_standard(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    async def _fallback_to_standard(
        self,
        prompt: str,
        *,
        system: str | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Fall back to the standard provider, marking fallback_used=True."""
        self._last_selection = "rlm-fallback"
        resp = await self._call_standard(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        resp.fallback_used = True
        return resp

    async def _call_standard(
        self,
        prompt: str,
        *,
        system: str | None,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Invoke the standard provider and let LLMExecutionError propagate."""
        try:
            return await self._standard.complete(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMExecutionError:
            # Already typed — re-raise unchanged so the caller sees the
            # original provider and cause.
            raise
        except Exception as exc:  # pragma: no cover — defensive
            # Wrap any unexpected error in LLMExecutionError so callers
            # only have to handle one error type.
            raise LLMExecutionError(
                f"router: standard provider failed: {exc}",
                provider="router",
                cause=exc,
            ) from exc

    @property
    def provider_name(self) -> str:
        """Provider identifier reflecting the last routing decision.

        Returns ``"router:standard"`` after a standard-path call,
        ``"router:rlm"`` after a real RLM call, and
        ``"router:rlm-fallback"`` when RLM was selected but fell back to
        standard (RLM failure or toolkit not installed). Before any call
        has been made returns ``"router"``.
        """
        if self._last_selection is None:
            return "router"
        if self._last_selection == "standard":
            return "router:standard"
        if self._last_selection == "rlm-fallback":
            return "router:rlm-fallback"
        if self._last_selection == "rlm":
            return "router:rlm"
        return "router"
