"""LLM provider interface for Mnemos.

All providers must implement this interface for use in the synthesis
pipeline (M4) and context filter (M10).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mnemos.config import LLMConfig


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


def create_provider(config: LLMConfig) -> LLMProvider:
    """Factory: instantiate the configured LLM provider.

    Maps ``config.provider`` to a concrete ``LLMProvider`` subclass. SDK
    imports are lazy (inside this function) so importing ``mnemos.llm``
    does not pull in every optional provider dependency — only the one
    actually requested pays the import cost.

    Supported providers:
      * ``"ollama"``    → :class:`mnemos.llm.ollama.OllamaProvider`
      * ``"openai"``    → :class:`mnemos.llm.openai.OpenAIProvider`
      * ``"anthropic"`` → :class:`mnemos.llm.anthropic.AnthropicProvider`
      * ``"rlm"``       → :class:`mnemos.llm.rlm.RLMProvider`

    Raises:
        ImportError:  the selected provider's SDK is not installed.
        ValueError:   ``config.provider`` is not a recognised provider name.
    """
    name = config.provider

    if name == "ollama":
        from mnemos.llm.ollama import OLLAMA_AVAILABLE, OllamaProvider

        if not OLLAMA_AVAILABLE:
            raise ImportError(
                "ollama SDK not installed. Run: pip install mnemos[ollama]"
            )
        return OllamaProvider(config)

    if name == "openai":
        from mnemos.llm.openai import OPENAI_AVAILABLE, OpenAIProvider

        if not OPENAI_AVAILABLE:
            raise ImportError(
                "openai SDK not installed. Run: pip install mnemos[openai]"
            )
        return OpenAIProvider(config)

    if name == "anthropic":
        from mnemos.llm.anthropic import ANTHROPIC_AVAILABLE, AnthropicProvider

        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic SDK not installed. Run: pip install mnemos[anthropic]"
            )
        return AnthropicProvider(config)

    if name == "rlm":
        from mnemos.llm.rlm import RLM_AVAILABLE, RLMProvider

        if not RLM_AVAILABLE:
            raise ImportError(
                "rlm-toolkit not installed. Run: pip install mnemos[rlm]"
            )
        return RLMProvider(config, config.rlm)

    raise ValueError(
        f"Unknown LLM provider: {name!r}. "
        "Supported: 'ollama', 'openai', 'anthropic', 'rlm'."
    )
