"""LLM provider interface for Mnemos.

All providers must implement this interface for use in the synthesis
pipeline (M4) and context filter (M10).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMExecutionError(Exception):
    """Raised when an LLM provider call fails after all retries/fallbacks.

    Carries enough context for the caller to decide whether the failure is
    retryable at a higher level (e.g. transient network) or terminal (e.g.
    auth rejected, content policy). The ``provider`` field identifies which
    provider in a fallback chain produced the failure.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.__cause__ = cause


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached: bool = False
    # True when the response came from a fallback model/provider rather than
    # the primary. Lets callers and observability distinguish graceful
    # degradation from a clean primary hit.
    fallback_used: bool = False


class LLMProvider(ABC):
    """Abstract base for all LLM providers."""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a completion request and return the response."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. 'anthropic', 'ollama')."""
        ...


def create_provider(config: object) -> LLMProvider:
    """Factory: instantiate the configured LLM provider.

    Implemented in PR 2 (standard providers: Ollama + OpenAI + Anthropic).
    PR 1 ships only the config schema and the ``LLMResponse``/
    ``LLMExecutionError`` types, so the factory still raises.
    """
    raise NotImplementedError("LLM provider factory not yet implemented (PR 2)")
