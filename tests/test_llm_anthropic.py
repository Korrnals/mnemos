"""Tests for AnthropicProvider — mocked SDK, no network calls.

The ``anthropic.AsyncAnthropic`` client is replaced with a mock whose
``messages.create`` async method returns a canned response with a
``content`` list of text blocks and ``usage.input_tokens`` /
``usage.output_tokens``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mnemos.config import LLMConfig
from mnemos.llm.anthropic import AnthropicProvider
from mnemos.llm.base import LLMExecutionError, LLMResponse


def _make_response(
    text: str = "hello there",
    input_tokens: int = 12,
    output_tokens: int = 4,
) -> SimpleNamespace:
    """Build a fake Anthropic messages response."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def _make_client(response: SimpleNamespace) -> SimpleNamespace:
    """Build a fake AsyncAnthropic client with a chained messages.create."""
    create = AsyncMock(return_value=response)
    messages = SimpleNamespace(create=create)
    return SimpleNamespace(messages=messages)


# ── construction ─────────────────────────────────────────────────────────────


def test_anthropic_provider_name() -> None:
    """provider_name is 'anthropic'."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic", model="claude-3-opus", anthropic_api_key="sk-ant-test"
        )
    )
    assert provider.provider_name == "anthropic"


def test_anthropic_provider_reads_api_key_from_config() -> None:
    """The API key is read from config.anthropic_api_key when present."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic",
            model="claude-3-opus",
            anthropic_api_key="sk-ant-from-config",
        )
    )
    assert provider._api_key == "sk-ant-from-config"


def test_anthropic_provider_reads_api_key_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When config.anthropic_api_key is empty, ANTHROPIC_API_KEY env var is used."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    provider = AnthropicProvider(LLMConfig(provider="anthropic", model="claude-3-opus"))
    assert provider._api_key == "sk-ant-from-env"


def test_anthropic_provider_missing_api_key_raises() -> None:
    """No key in config or env raises LLMExecutionError at construction."""
    import os

    config = LLMConfig(
        provider="anthropic", model="claude-3-opus", anthropic_api_key=""
    )
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(LLMExecutionError) as exc_info:
            AnthropicProvider(config)
        assert exc_info.value.provider == "anthropic"
        assert "API key" in str(exc_info.value)
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


# ── complete() happy path ────────────────────────────────────────────────────


async def test_anthropic_complete_returns_llm_response() -> None:
    """complete() maps the SDK response to an LLMResponse with real usage."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic",
            model="claude-3-opus",
            anthropic_api_key="sk-ant-test",
        )
    )
    fake_client = _make_client(_make_response("greetings", 25, 7))

    with patch(
        "mnemos.llm.anthropic.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        resp = await provider.complete("hi", system="be brief")

    assert isinstance(resp, LLMResponse)
    assert resp.text == "greetings"
    assert resp.model == "claude-3-opus"
    assert resp.tokens_in == 25
    assert resp.tokens_out == 7
    fake_client.messages.create.assert_awaited_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-3-opus"
    assert kwargs["system"] == "be brief"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


async def test_anthropic_complete_forwards_params() -> None:
    """temperature and max_tokens are forwarded to the create call."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic",
            model="claude-3-opus",
            anthropic_api_key="sk-ant-test",
        )
    )
    fake_client = _make_client(_make_response())

    with patch(
        "mnemos.llm.anthropic.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        await provider.complete("hi", temperature=0.9, max_tokens=300)

    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["temperature"] == 0.9
    assert kwargs["max_tokens"] == 300


async def test_anthropic_complete_without_system() -> None:
    """When system is None, the SDK's NOT_GIVEN sentinel is forwarded."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic",
            model="claude-3-opus",
            anthropic_api_key="sk-ant-test",
        )
    )
    fake_client = _make_client(_make_response())

    with patch(
        "mnemos.llm.anthropic.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        await provider.complete("hi")

    kwargs = fake_client.messages.create.call_args.kwargs
    # system defaults to anthropic.NOT_GIVEN when None
    assert "system" in kwargs


# ── content block parsing ────────────────────────────────────────────────────


async def test_anthropic_complete_multi_block_content() -> None:
    """Multiple text blocks in the content list are concatenated."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic",
            model="claude-3-opus",
            anthropic_api_key="sk-ant-test",
        )
    )
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="part one "),
            SimpleNamespace(type="text", text="part two"),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=8),
    )
    fake_client = _make_client(response)

    with patch(
        "mnemos.llm.anthropic.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        resp = await provider.complete("hi")

    assert resp.text == "part one part two"
    assert resp.tokens_out == 8


# ── error handling ───────────────────────────────────────────────────────────


async def test_anthropic_complete_wraps_sdk_error() -> None:
    """A raised SDK exception is wrapped in LLMExecutionError with provider set."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic",
            model="claude-3-opus",
            anthropic_api_key="sk-ant-test",
        )
    )
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(side_effect=RuntimeError("auth rejected"))
        )
    )

    with (
        patch(
            "mnemos.llm.anthropic.anthropic.AsyncAnthropic", return_value=fake_client
        ),
        pytest.raises(LLMExecutionError) as exc_info,
    ):
        await provider.complete("hi")

    assert exc_info.value.provider == "anthropic"
    assert "auth rejected" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


async def test_anthropic_complete_missing_usage_defaults_zero() -> None:
    """When usage is absent, token counts default to 0."""
    provider = AnthropicProvider(
        LLMConfig(
            provider="anthropic",
            model="claude-3-opus",
            anthropic_api_key="sk-ant-test",
        )
    )
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hi")],
        usage=None,
    )
    fake_client = _make_client(response)

    with patch(
        "mnemos.llm.anthropic.anthropic.AsyncAnthropic", return_value=fake_client
    ):
        resp = await provider.complete("hi")

    assert resp.tokens_in == 0
    assert resp.tokens_out == 0
    assert resp.text == "hi"
