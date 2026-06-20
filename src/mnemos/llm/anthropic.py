"""Anthropic LLM provider — Claude models via the official ``anthropic`` SDK.

Uses ``anthropic>=0.25`` (the async client). The SDK is an optional
dependency; install it with ``pip install mnemos[anthropic]``.

API key resolution order:
1. ``config.anthropic_api_key`` (explicit config value).
2. ``ANTHROPIC_API_KEY`` environment variable.

We prefer the env var for production deployments so secrets never land in
config files on disk. The config field exists for development convenience.

Token accounting: the Anthropic messages response carries
``response.usage`` with ``input_tokens`` and ``output_tokens`` — we use
those directly.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

from mnemos.llm.base import LLMExecutionError, LLMProvider, LLMResponse

try:
    import anthropic

    ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    ANTHROPIC_AVAILABLE = False

if TYPE_CHECKING:
    from mnemos.config import LLMConfig


class AnthropicProvider(LLMProvider):
    """LLM provider backed by the Anthropic Messages API (Claude models)."""

    def __init__(self, config: LLMConfig) -> None:
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic SDK not installed. Run: pip install mnemos[anthropic]"
            )
        api_key = config.anthropic_api_key or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        if not api_key:
            raise LLMExecutionError(
                "Anthropic API key not configured: set config.anthropic_api_key "
                "or the ANTHROPIC_API_KEY environment variable.",
                provider="anthropic",
            )
        self._model = config.model
        self._api_key = api_key
        self._temperature = config.temperature
        self._max_tokens = config.max_tokens

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Call the Anthropic messages endpoint and map to ``LLMResponse``."""
        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        try:
            response: Any = await client.messages.create(
                model=self._model,
                system=cast(Any, system or anthropic.NOT_GIVEN),
                messages=cast(Any, [{"role": "user", "content": prompt}]),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMExecutionError(
                f"anthropic messages failed: {exc}",
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
    """Concatenate text blocks from an Anthropic messages response."""
    content = getattr(response, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
                continue
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "".join(parts)
    return ""


def _extract_usage(response: Any) -> tuple[int, int]:
    """Read input_tokens / output_tokens from ``response.usage``."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        return input_tokens, output_tokens
    if isinstance(usage, dict):
        it = usage.get("input_tokens")
        ot = usage.get("output_tokens")
        if isinstance(it, int) and isinstance(ot, int):
            return it, ot
    return 0, 0
