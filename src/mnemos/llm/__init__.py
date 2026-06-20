"""LLM provider abstraction for Mnemos.

Submodules:
  base        — Provider interface (abstract base class) + ``create_provider`` factory
  anthropic   — Anthropic Claude (primary recommended for synthesis)
  openai      — OpenAI GPT (or any OpenAI-compatible gateway via ``base_url``)
  azure_openai — Azure OpenAI (uses openai SDK with azure config)
  ollama      — Local Ollama (privacy + offline)
  gemini      — Google Gemini

Provider selection order (recommended): Anthropic → Ollama → OpenAI → Azure → Gemini
Configured in ~/.mnemos/config.yaml or MNEMOS_LLM__PROVIDER env var.
"""

from __future__ import annotations

from mnemos.llm.base import (
    LLMExecutionError,
    LLMProvider,
    LLMResponse,
    create_provider,
)
from mnemos.llm.ollama import OllamaProvider

__all__ = [
    "AnthropicProvider",
    "LLMExecutionError",
    "LLMProvider",
    "LLMResponse",
    "OllamaProvider",
    "OpenAIProvider",
    "create_provider",
]


# OpenAI / Anthropic providers are imported lazily via ``create_provider`` to
# avoid pulling their optional SDKs at package import time. We still expose
# them for type-checking and direct instantiation when the SDK is present.
try:
    from mnemos.llm.anthropic import AnthropicProvider
except ImportError:  # pragma: no cover — anthropic SDK optional
    AnthropicProvider = None  # type: ignore[assignment,misc]

try:
    from mnemos.llm.openai import OpenAIProvider
except ImportError:  # pragma: no cover — openai SDK optional
    OpenAIProvider = None  # type: ignore[assignment,misc]
