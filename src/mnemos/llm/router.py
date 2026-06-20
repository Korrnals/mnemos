"""LLMRouter — threshold-based routing between standard and RLM providers.

The router is the single entry point the rest of Mnemos uses to obtain LLM
completions. It owns two concerns:

1. **Token estimation** — a cheap ``len(prompt) // 4`` heuristic decides
   whether the prompt is large enough to warrant RLM decomposition.
2. **Provider selection** — below the threshold (or when RLM is disabled)
   the standard provider (Ollama / OpenAI / Anthropic) is used directly.
   At or above the threshold, when RLM is enabled, the RLM provider would
   be used — but the RLM provider is wired in PR 4. Until then the router
   logs the routing decision and falls back to the standard provider with
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

    ``rlm_settings`` is ``None`` when RLM is disabled
    (``rlm.enabled=False``) or the ``rlm_toolkit`` is not installed. When
    ``None`` the router always uses the standard provider. When non-``None``
    the router consults ``rlm_settings.threshold_tokens`` to decide whether
    to route to RLM — but the RLM provider itself is wired in PR 4; until
    then an above-threshold prompt falls back to standard with
    ``fallback_used=True`` and a debug log.
    """

    def __init__(
        self,
        settings: LLMConfig,
        rlm_settings: RLMSettings | None = None,
    ) -> None:
        self._settings = settings
        self._rlm_settings = rlm_settings
        self._provider: LLMProvider | None = None
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
          * if ``token_estimate >= threshold`` and RLM is configured: the
            RLM provider would be used, but it is not wired until PR 4 —
            fall back to standard with ``fallback_used=True`` and a debug
            log explaining the fallback.
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
            # PR 4 wires the RLM provider. Until then we fall back to the
            # standard provider and mark the response as a fallback so
            # callers/observability can distinguish graceful degradation
            # from a clean primary hit.
            logger.debug(
                "router: RLM routing selected but RLM provider not yet "
                "implemented, using standard (estimated=%d tokens, "
                "threshold=%d)",
                token_estimate,
                threshold,
            )
            self._last_selection = "rlm-fallback"
            resp = await self._call_standard(
                prompt,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            resp.fallback_used = True
            return resp

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
        ``"router:rlm"`` after a real RLM call (PR 4), and
        ``"router:rlm-fallback"`` when RLM was selected but fell back to
        standard because the RLM provider is not yet wired. Before any
        call has been made returns ``"router"``.
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
