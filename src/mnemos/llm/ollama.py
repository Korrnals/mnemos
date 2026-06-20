"""Ollama LLM provider — local, privacy-friendly, offline-capable.

Uses the official ``ollama`` Python SDK (``ollama>=0.3``). The SDK is an
optional dependency; install it with ``pip install mnemos[ollama]``.

Token accounting: the Ollama chat response exposes ``prompt_eval_count``
(input tokens) and ``eval_count`` (output tokens) when the backend
populates them. When those fields are absent (older server versions or
mocked responses), we fall back to a whitespace word-count estimate so
callers always get a non-zero signal for observability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mnemos.llm.base import LLMExecutionError, LLMProvider, LLMResponse

try:
    import ollama

    OLLAMA_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    OLLAMA_AVAILABLE = False

if TYPE_CHECKING:
    from mnemos.config import LLMConfig


class OllamaProvider(LLMProvider):
    """LLM provider backed by a local Ollama daemon."""

    def __init__(self, config: LLMConfig) -> None:
        if not OLLAMA_AVAILABLE:
            raise ImportError(
                "ollama SDK not installed. Run: pip install mnemos[ollama]"
            )
        self._model = config.model
        self._host = config.ollama_url
        self._temperature = config.temperature
        self._max_tokens = config.max_tokens

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Call the Ollama chat endpoint and map the result to ``LLMResponse``."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        client = ollama.AsyncClient(host=self._host)
        try:
            response: Any = await client.chat(
                model=self._model,
                messages=messages,
                options={
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            )
        except Exception as exc:
            raise LLMExecutionError(
                f"ollama chat failed: {exc}",
                provider=self.provider_name,
                cause=exc,
            ) from exc

        text = _extract_text(response)
        tokens_in = _extract_int(response, "prompt_eval_count") or len(prompt.split())
        tokens_out = _extract_int(response, "eval_count") or len(text.split())
        return LLMResponse(
            text=text,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


def _extract_text(response: Any) -> str:
    """Pull the assistant message text from an ollama chat response."""
    # ollama SDK returns a ChatResponse object with a ``message`` attribute.
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content
    # Fall back to dict-style access (mocked responses or older SDK shapes).
    if isinstance(response, dict):
        msg = response.get("message")
        if isinstance(msg, dict):
            raw = msg.get("content")
            if isinstance(raw, str):
                return raw
    return ""


def _extract_int(response: Any, key: str) -> int:
    """Read an integer stats field from an ollama chat response (0 if absent)."""
    value = getattr(response, key, None)
    if isinstance(value, int):
        return value
    if isinstance(response, dict):
        value = response.get(key)
        if isinstance(value, int):
            return value
    return 0
