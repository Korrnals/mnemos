"""Tests for OllamaProvider — mocked SDK, no network calls.

The ``ollama`` SDK's ``AsyncClient.chat`` is replaced with an async mock
that returns a canned response object mimicking the real SDK shape
(``message.content`` + ``prompt_eval_count`` / ``eval_count`` stats).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mnemos.config import LLMConfig
from mnemos.llm.base import LLMExecutionError, LLMResponse
from mnemos.llm.ollama import OllamaProvider


def _make_response(
    text: str = "hello there",
    prompt_eval_count: int | None = 12,
    eval_count: int | None = 4,
) -> SimpleNamespace:
    """Build a fake ollama chat response with the SDK's attribute shape."""
    return SimpleNamespace(
        message=SimpleNamespace(content=text),
        prompt_eval_count=prompt_eval_count,
        eval_count=eval_count,
    )


# ── construction ─────────────────────────────────────────────────────────────


def test_ollama_provider_name() -> None:
    """provider_name is 'ollama'."""
    provider = OllamaProvider(LLMConfig(provider="ollama", model="qwen2.5:3b"))
    assert provider.provider_name == "ollama"


def test_ollama_provider_stores_config() -> None:
    """Constructor reads model, host, temperature, max_tokens from config."""
    config = LLMConfig(
        provider="ollama",
        model="llama3:8b",
        ollama_url="http://ollama-host:11434",
        temperature=0.1,
        max_tokens=512,
    )
    provider = OllamaProvider(config)
    assert provider._model == "llama3:8b"
    assert provider._host == "http://ollama-host:11434"


# ── complete() happy path ────────────────────────────────────────────────────


async def test_ollama_complete_returns_llm_response() -> None:
    """complete() maps the SDK response to an LLMResponse with real token counts."""
    provider = OllamaProvider(LLMConfig(provider="ollama", model="qwen2.5:3b"))
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(return_value=_make_response("greetings", 15, 5))

    with patch("mnemos.llm.ollama.ollama.AsyncClient", return_value=fake_client):
        resp = await provider.complete("hi", system="be brief")

    assert isinstance(resp, LLMResponse)
    assert resp.text == "greetings"
    assert resp.model == "qwen2.5:3b"
    assert resp.tokens_in == 15
    assert resp.tokens_out == 5
    fake_client.chat.assert_awaited_once()
    # system message prepended when supplied
    sent_messages = fake_client.chat.call_args.kwargs["messages"]
    assert sent_messages[0]["role"] == "system"
    assert sent_messages[0]["content"] == "be brief"
    assert sent_messages[-1]["role"] == "user"
    assert sent_messages[-1]["content"] == "hi"


async def test_ollama_complete_without_system() -> None:
    """When system is None, only the user message is sent."""
    provider = OllamaProvider(LLMConfig(provider="ollama", model="qwen2.5:3b"))
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(return_value=_make_response())

    with patch("mnemos.llm.ollama.ollama.AsyncClient", return_value=fake_client):
        await provider.complete("hi")

    sent_messages = fake_client.chat.call_args.kwargs["messages"]
    assert len(sent_messages) == 1
    assert sent_messages[0]["role"] == "user"


async def test_ollama_complete_forwards_options() -> None:
    """temperature and max_tokens are forwarded via the options dict."""
    provider = OllamaProvider(LLMConfig(provider="ollama", model="qwen2.5:3b"))
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(return_value=_make_response())

    with patch("mnemos.llm.ollama.ollama.AsyncClient", return_value=fake_client):
        await provider.complete("hi", temperature=0.7, max_tokens=100)

    opts = fake_client.chat.call_args.kwargs["options"]
    assert opts["temperature"] == 0.7
    assert opts["num_predict"] == 100


# ── token fallback ───────────────────────────────────────────────────────────


async def test_ollama_complete_token_fallback_word_count() -> None:
    """When eval_count/prompt_eval_count are missing, word count is used."""
    provider = OllamaProvider(LLMConfig(provider="ollama", model="qwen2.5:3b"))
    fake_client = AsyncMock()
    # No stats fields → fallback path
    fake_client.chat = AsyncMock(
        return_value=SimpleNamespace(
            message=SimpleNamespace(content="one two three four"),
            prompt_eval_count=None,
            eval_count=None,
        )
    )

    with patch("mnemos.llm.ollama.ollama.AsyncClient", return_value=fake_client):
        resp = await provider.complete("alpha beta gamma")

    assert resp.tokens_in == 3  # "alpha beta gamma" → 3 words
    assert resp.tokens_out == 4  # "one two three four" → 4 words


# ── error handling ───────────────────────────────────────────────────────────


async def test_ollama_complete_wraps_sdk_error() -> None:
    """A raised SDK exception is wrapped in LLMExecutionError with provider set."""
    provider = OllamaProvider(LLMConfig(provider="ollama", model="qwen2.5:3b"))
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(side_effect=RuntimeError("connection refused"))

    with (
        patch("mnemos.llm.ollama.ollama.AsyncClient", return_value=fake_client),
        pytest.raises(LLMExecutionError) as exc_info,
    ):
        await provider.complete("hi")

    assert exc_info.value.provider == "ollama"
    assert "connection refused" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


async def test_ollama_complete_empty_response_text() -> None:
    """An empty content string yields an empty text and zero output tokens."""
    provider = OllamaProvider(LLMConfig(provider="ollama", model="qwen2.5:3b"))
    fake_client = AsyncMock()
    fake_client.chat = AsyncMock(
        return_value=SimpleNamespace(
            message=SimpleNamespace(content=""),
            prompt_eval_count=10,
            eval_count=0,
        )
    )

    with patch("mnemos.llm.ollama.ollama.AsyncClient", return_value=fake_client):
        resp = await provider.complete("hi")

    assert resp.text == ""
    assert resp.tokens_out == 0
    assert resp.tokens_in == 10
