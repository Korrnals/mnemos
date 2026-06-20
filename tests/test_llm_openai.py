"""Tests for OpenAIProvider — mocked SDK, no network calls.

The ``openai.AsyncOpenAI`` client is replaced with a mock whose
``chat.completions.create`` async method returns a canned response with
``choices[0].message.content`` and ``usage.prompt_tokens`` /
``usage.completion_tokens``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mnemos.config import LLMConfig
from mnemos.llm.base import LLMExecutionError, LLMResponse
from mnemos.llm.openai import OpenAIProvider


def _make_response(
    text: str = "hello there",
    prompt_tokens: int = 12,
    completion_tokens: int = 4,
) -> SimpleNamespace:
    """Build a fake OpenAI chat completion response."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def _make_client(response: SimpleNamespace) -> SimpleNamespace:
    """Build a fake AsyncOpenAI client with a chained chat.completions.create."""
    create = AsyncMock(return_value=response)
    completions = SimpleNamespace(create=create)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


# ── construction ─────────────────────────────────────────────────────────────


def test_openai_provider_name() -> None:
    """provider_name is 'openai'."""
    provider = OpenAIProvider(
        LLMConfig(provider="openai", model="gpt-4o", openai_api_key="sk-test")
    )
    assert provider.provider_name == "openai"


def test_openai_provider_reads_api_key_from_config() -> None:
    """The API key is read from config.openai_api_key when present."""
    provider = OpenAIProvider(
        LLMConfig(provider="openai", model="gpt-4o", openai_api_key="sk-from-config")
    )
    assert provider._api_key == "sk-from-config"


def test_openai_provider_reads_api_key_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When config.openai_api_key is empty, OPENAI_API_KEY env var is used."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    provider = OpenAIProvider(LLMConfig(provider="openai", model="gpt-4o"))
    assert provider._api_key == "sk-from-env"


def test_openai_provider_missing_api_key_raises() -> None:
    """No key in config or env raises LLMExecutionError at construction."""
    config = LLMConfig(provider="openai", model="gpt-4o", openai_api_key="")
    # Ensure env var is not set either
    import os

    monkeypatch_del = "OPENAI_API_KEY"
    saved = os.environ.pop(monkeypatch_del, None)
    try:
        with pytest.raises(LLMExecutionError) as exc_info:
            OpenAIProvider(config)
        assert exc_info.value.provider == "openai"
        assert "API key" in str(exc_info.value)
    finally:
        if saved is not None:
            os.environ[monkeypatch_del] = saved


def test_openai_provider_base_url_forwarded() -> None:
    """A non-empty openai_base_url is stored for the client."""
    provider = OpenAIProvider(
        LLMConfig(
            provider="openai",
            model="gpt-4o",
            openai_api_key="sk-test",
            openai_base_url="https://gateway.example.com/v1",
        )
    )
    assert provider._base_url == "https://gateway.example.com/v1"


# ── complete() happy path ────────────────────────────────────────────────────


async def test_openai_complete_returns_llm_response() -> None:
    """complete() maps the SDK response to an LLMResponse with real usage."""
    provider = OpenAIProvider(
        LLMConfig(provider="openai", model="gpt-4o", openai_api_key="sk-test")
    )
    fake_client = _make_client(_make_response("greetings", 20, 6))

    with patch("mnemos.llm.openai.openai.AsyncOpenAI", return_value=fake_client):
        resp = await provider.complete("hi", system="be brief")

    assert isinstance(resp, LLMResponse)
    assert resp.text == "greetings"
    assert resp.model == "gpt-4o"
    assert resp.tokens_in == 20
    assert resp.tokens_out == 6
    fake_client.chat.completions.create.assert_awaited_once()
    sent_messages = fake_client.chat.completions.create.call_args.kwargs["messages"]
    assert sent_messages[0]["role"] == "system"
    assert sent_messages[-1]["role"] == "user"


async def test_openai_complete_forwards_params() -> None:
    """temperature and max_tokens are forwarded to the create call."""
    provider = OpenAIProvider(
        LLMConfig(provider="openai", model="gpt-4o", openai_api_key="sk-test")
    )
    fake_client = _make_client(_make_response())

    with patch("mnemos.llm.openai.openai.AsyncOpenAI", return_value=fake_client):
        await provider.complete("hi", temperature=0.5, max_tokens=200)

    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["temperature"] == 0.5
    assert kwargs["max_tokens"] == 200
    assert kwargs["model"] == "gpt-4o"


# ── error handling ───────────────────────────────────────────────────────────


async def test_openai_complete_wraps_sdk_error() -> None:
    """A raised SDK exception is wrapped in LLMExecutionError with provider set."""
    provider = OpenAIProvider(
        LLMConfig(provider="openai", model="gpt-4o", openai_api_key="sk-test")
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("rate limited"))
            )
        )
    )

    with (
        patch("mnemos.llm.openai.openai.AsyncOpenAI", return_value=fake_client),
        pytest.raises(LLMExecutionError) as exc_info,
    ):
        await provider.complete("hi")

    assert exc_info.value.provider == "openai"
    assert "rate limited" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


async def test_openai_complete_missing_usage_defaults_zero() -> None:
    """When usage is absent, token counts default to 0."""
    provider = OpenAIProvider(
        LLMConfig(provider="openai", model="gpt-4o", openai_api_key="sk-test")
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=None,
    )
    fake_client = _make_client(response)

    with patch("mnemos.llm.openai.openai.AsyncOpenAI", return_value=fake_client):
        resp = await provider.complete("hi")

    assert resp.tokens_in == 0
    assert resp.tokens_out == 0
    assert resp.text == "hi"
