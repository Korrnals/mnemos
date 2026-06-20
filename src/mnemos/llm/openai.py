"""OpenAI LLM provider — GPT models via the official ``openai`` SDK.

Uses ``openai>=1.0`` (the async client). The SDK is an optional dependency;
install it with ``pip install mnemos[openai]``.

API key resolution order:
1. ``config.openai_api_key`` (explicit config value).
2. ``OPENAI_API_KEY`` environment variable.

We prefer the env var for production deployments so secrets never land in
config files on disk. The config field exists for development convenience
and for environments where env injection is awkward (e.g. some container
orchestrators that prefer mounted config).

``config.openai_base_url`` is forwarded as ``base_url`` when non-empty,
which lets operators point the OpenAI-compatible client at Azure OpenAI,
vLLM, LiteLLM, or any other OpenAI-compatible gateway.

Token accounting: the OpenAI chat response carries ``response.usage`` with
``prompt_tokens`` and ``completion_tokens`` — we use those directly.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

from mnemos.llm.base import LLMExecutionError, LLMProvider, LLMResponse

try:
    import openai

    OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    OPENAI_AVAILABLE = False

if TYPE_CHECKING:
    from mnemos.config import LLMConfig


class OpenAIProvider(LLMProvider):
    """LLM provider backed by the OpenAI API (or an OpenAI-compatible gateway)."""

    def __init__(self, config: LLMConfig) -> None:
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "openai SDK not installed. Run: pip install mnemos[openai]"
            )
        api_key = config.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise LLMExecutionError(
                "OpenAI API key not configured: set config.openai_api_key "
                "or the OPENAI_API_KEY environment variable.",
                provider="openai",
            )
        self._model = config.model
        self._api_key = api_key
        self._base_url = config.openai_base_url or None
        self._temperature = config.temperature
        self._max_tokens = config.max_tokens

    @property
    def provider_name(self) -> str:
        return "openai"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Call the OpenAI chat completions endpoint and map to ``LLMResponse``."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        client = openai.AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        try:
            response: Any = await client.chat.completions.create(
                model=self._model,
                messages=cast(Any, messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMExecutionError(
                f"openai chat failed: {exc}",
                provider=self.provider_name,
                cause=exc,
            ) from exc

        text = _extract_text(response)
        tokens_in, tokens_out = _extract_usage(response)
        return LLMResponse(
            text=text,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


def _extract_text(response: Any) -> str:
    """Pull the first choice's message content from an OpenAI chat response."""
    choices = getattr(response, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message")
            if isinstance(msg, dict):
                raw = msg.get("content")
                if isinstance(raw, str):
                    return raw
    return ""


def _extract_usage(response: Any) -> tuple[int, int]:
    """Read prompt_tokens / completion_tokens from ``response.usage``."""
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
        return prompt_tokens, completion_tokens
    if isinstance(usage, dict):
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if isinstance(pt, int) and isinstance(ct, int):
            return pt, ct
    return 0, 0
