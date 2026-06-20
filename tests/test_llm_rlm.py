"""Tests for RLMProvider — mocked rlm_toolkit.RLM, no network calls.

Covers:
  * context/query split on the ``--- Synthesis ---`` delimiter
  * degraded mode when no delimiter is present
  * RLMResult → LLMResponse mapping (tokens, model, fallback flag)
  * non-success status → LLMExecutionError
  * backend selection (ollama / openai / anthropic factory calls)
  * RLM_AVAILABLE=False → ImportError on construction
  * chain-of-thought history is NEVER persisted
  * word-count token estimation
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mnemos.config import LLMConfig, RLMSettings
from mnemos.llm.base import LLMExecutionError, LLMResponse
from mnemos.llm.rlm import RLMProvider

# ── helpers ──────────────────────────────────────────────────────────────────


def _rlm_result(
    *,
    answer: str = "synthesized answer",
    status: str = "success",
    iterations: int = 3,
    subcall_count: int = 5,
    total_cost: float = 0.01,
    trace_id: str | None = "trace-abc",
    history: list[tuple[str, str]] | None = None,
) -> Any:
    """Build a mock RLMResult-like object."""
    if history is None:
        history = [("think", "step"), ("act", "result")]
    return MagicMock(
        answer=answer,
        status=status,
        iterations=iterations,
        subcall_count=subcall_count,
        total_cost=total_cost,
        trace_id=trace_id,
        history=history,
    )


def _make_mock_rlm(*, arun_is_coroutine: bool = True) -> MagicMock:
    """Build a mock RLM instance with an arun/run pair.

    ``arun_is_coroutine`` controls whether ``inspect.iscoroutinefunction``
    sees ``arun`` as a true coroutine. When True, ``arun`` is an
    ``AsyncMock``; when False, ``arun`` is a plain ``MagicMock`` and
    ``run`` is the one that gets called.
    """
    rlm = MagicMock()
    if arun_is_coroutine:
        rlm.arun = AsyncMock()
    else:
        rlm.arun = MagicMock()  # not a coroutine
        rlm.run = MagicMock()
    return rlm


def _make_provider(
    *,
    rlm_config: RLMSettings | None = None,
    arun_is_coroutine: bool = True,
    arun_result: Any | None = None,
    run_result: Any | None = None,
) -> tuple[RLMProvider, MagicMock]:
    """Construct an RLMProvider with a mocked rlm_toolkit.RLM.

    Returns (provider, mock_rlm_instance) so tests can assert on calls.
    """
    rlm_config = rlm_config or RLMSettings(
        enabled=True,
        backend="ollama",
        model="qwen2.5:3b",
    )
    config = LLMConfig(provider="rlm", model="qwen2.5:3b")

    mock_rlm = _make_mock_rlm(arun_is_coroutine=arun_is_coroutine)
    if arun_is_coroutine and arun_result is not None:
        mock_rlm.arun.return_value = arun_result
    if not arun_is_coroutine and run_result is not None:
        mock_rlm.run.return_value = run_result

    with (
        patch("mnemos.llm.rlm.RLM_AVAILABLE", True),
        patch("mnemos.llm.rlm.RLM") as mock_rlm_cls,
    ):
        mock_rlm_cls.from_ollama.return_value = mock_rlm
        mock_rlm_cls.from_openai.return_value = mock_rlm
        mock_rlm_cls.from_anthropic.return_value = mock_rlm
        # RLMConfig is a real dataclass — let it construct normally.
        provider = RLMProvider(config, rlm_config)

    return provider, mock_rlm


# ── context/query split ──────────────────────────────────────────────────────


async def test_rlm_provider_context_query_split() -> None:
    """Prompt with ``--- Synthesis ---`` delimiter → context/query split."""
    result = _rlm_result(answer="synth")
    provider, mock_rlm = _make_provider(arun_result=result)

    prompt = "memory entry 1\nmemory entry 2\n--- Synthesis ---\nSummarize."
    await provider.complete(prompt)

    mock_rlm.arun.assert_awaited_once()
    call_kwargs = mock_rlm.arun.await_args.kwargs
    assert call_kwargs["context"] == "memory entry 1\nmemory entry 2"
    assert call_kwargs["query"] == "Summarize."


async def test_rlm_provider_query_delimiter_split() -> None:
    """The ``--- QUERY ---`` delimiter is also recognised."""
    result = _rlm_result(answer="synth")
    provider, mock_rlm = _make_provider(arun_result=result)

    prompt = "ctx data\n--- QUERY ---\nWhat is the answer?"
    await provider.complete(prompt)

    call_kwargs = mock_rlm.arun.await_args.kwargs
    assert call_kwargs["context"] == "ctx data"
    assert call_kwargs["query"] == "What is the answer?"


async def test_rlm_provider_no_delimiter_degraded() -> None:
    """Prompt without delimiter → entire prompt as query, empty context."""
    result = _rlm_result(answer="synth")
    provider, mock_rlm = _make_provider(arun_result=result)

    prompt = "just a plain prompt with no delimiter"
    await provider.complete(prompt)

    call_kwargs = mock_rlm.arun.await_args.kwargs
    assert call_kwargs["context"] == ""
    assert call_kwargs["query"] == "just a plain prompt with no delimiter"


# ── result mapping ───────────────────────────────────────────────────────────


async def test_rlm_provider_arun_mapping() -> None:
    """RLMResult fields map correctly to LLMResponse."""
    result = _rlm_result(
        answer="final answer text",
        iterations=4,
        subcall_count=7,
        total_cost=0.02,
        trace_id="trace-xyz",
    )
    provider, _ = _make_provider(arun_result=result)

    prompt = "some context\n--- Synthesis ---\nquery words here"
    resp = await provider.complete(prompt)

    assert isinstance(resp, LLMResponse)
    assert resp.text == "final answer text"
    assert resp.model == "rlm:qwen2.5:3b"
    assert resp.cached is False
    assert resp.fallback_used is False
    # tokens_in = words in context + words in query
    assert resp.tokens_in == len(["some", "context"]) + len(
        ["query", "words", "here"]
    )
    # tokens_out = words in answer
    assert resp.tokens_out == len(["final", "answer", "text"])
    # observability enrichment
    assert provider.last_iterations == 4
    assert provider.last_subcall_count == 7
    assert provider.last_total_cost == 0.02
    assert provider.last_trace_id == "trace-xyz"


async def test_rlm_provider_token_estimation() -> None:
    """Token counts are word-based estimates (whitespace split)."""
    result = _rlm_result(answer="one two three")
    provider, _ = _make_provider(arun_result=result)

    # context: 2 words, query: 2 words → tokens_in = 4
    # answer: 3 words → tokens_out = 3
    resp = await provider.complete("ctx one\n--- Synthesis ---\nq two")
    assert resp.tokens_in == 4
    assert resp.tokens_out == 3


# ── failure handling ─────────────────────────────────────────────────────────


async def test_rlm_provider_failure_raises() -> None:
    """RLMResult with status != 'success' → LLMExecutionError."""
    result = _rlm_result(answer="", status="max_iterations")
    provider, _ = _make_provider(arun_result=result)

    with pytest.raises(LLMExecutionError) as exc_info:
        await provider.complete("ctx\n--- Synthesis ---\nq")

    assert exc_info.value.provider == "rlm"
    assert "max_iterations" in str(exc_info.value)


async def test_rlm_provider_underlying_exception_wrapped() -> None:
    """An exception from arun() is wrapped in LLMExecutionError."""
    provider, mock_rlm = _make_provider()
    mock_rlm.arun.side_effect = RuntimeError("ollama connection refused")

    with pytest.raises(LLMExecutionError) as exc_info:
        await provider.complete("ctx\n--- Synthesis ---\nq")

    assert exc_info.value.provider == "rlm"
    assert "ollama connection refused" in str(exc_info.value)


# ── backend selection ────────────────────────────────────────────────────────


def test_rlm_provider_ollama_backend() -> None:
    """backend='ollama' → RLM.from_ollama called with model/sub_model."""
    rlm_config = RLMSettings(
        enabled=True,
        backend="ollama",
        model="qwen2.5:3b",
        sub_model="qwen2.5:1.5b",
        resilient=False,
    )
    config = LLMConfig(provider="rlm")

    mock_rlm = _make_mock_rlm()
    with (
        patch("mnemos.llm.rlm.RLM_AVAILABLE", True),
        patch("mnemos.llm.rlm.RLM") as mock_rlm_cls,
    ):
        mock_rlm_cls.from_ollama.return_value = mock_rlm
        RLMProvider(config, rlm_config)

    mock_rlm_cls.from_ollama.assert_called_once()
    call_kwargs = mock_rlm_cls.from_ollama.call_args.kwargs
    assert call_kwargs["model"] == "qwen2.5:3b"
    assert call_kwargs["sub_model"] == "qwen2.5:1.5b"
    assert call_kwargs["resilient"] is False


def test_rlm_provider_openai_backend() -> None:
    """backend='openai' → RLM.from_openai called; default sub_model when None."""
    rlm_config = RLMSettings(
        enabled=True,
        backend="openai",
        model="gpt-5.2",
        sub_model=None,
    )
    config = LLMConfig(provider="rlm")

    mock_rlm = _make_mock_rlm()
    with (
        patch("mnemos.llm.rlm.RLM_AVAILABLE", True),
        patch("mnemos.llm.rlm.RLM") as mock_rlm_cls,
    ):
        mock_rlm_cls.from_openai.return_value = mock_rlm
        RLMProvider(config, rlm_config)

    mock_rlm_cls.from_openai.assert_called_once()
    call_kwargs = mock_rlm_cls.from_openai.call_args.kwargs
    assert call_kwargs["root_model"] == "gpt-5.2"
    assert call_kwargs["sub_model"] == "gpt-4o-mini"  # default fallback


def test_rlm_provider_anthropic_backend() -> None:
    """backend='anthropic' → RLM.from_anthropic called."""
    rlm_config = RLMSettings(
        enabled=True,
        backend="anthropic",
        model="claude-opus-4.5",
        sub_model="claude-haiku",
    )
    config = LLMConfig(provider="rlm")

    mock_rlm = _make_mock_rlm()
    with (
        patch("mnemos.llm.rlm.RLM_AVAILABLE", True),
        patch("mnemos.llm.rlm.RLM") as mock_rlm_cls,
    ):
        mock_rlm_cls.from_anthropic.return_value = mock_rlm
        RLMProvider(config, rlm_config)

    mock_rlm_cls.from_anthropic.assert_called_once()
    call_kwargs = mock_rlm_cls.from_anthropic.call_args.kwargs
    assert call_kwargs["root_model"] == "claude-opus-4.5"
    assert call_kwargs["sub_model"] == "claude-haiku"


def test_rlm_provider_unsupported_backend_raises() -> None:
    """An unknown backend → ValueError at construction."""
    rlm_config = RLMSettings(enabled=True, backend="ollama", model="x")
    rlm_config.backend = "groq"  # type: ignore[misc] — bypass validation
    config = LLMConfig(provider="rlm")

    mock_rlm = _make_mock_rlm()
    with (
        patch("mnemos.llm.rlm.RLM_AVAILABLE", True),
        patch("mnemos.llm.rlm.RLM") as mock_rlm_cls,
    ):
        mock_rlm_cls.from_ollama.return_value = mock_rlm
        with pytest.raises(ValueError, match="Unsupported RLM backend"):
            RLMProvider(config, rlm_config)


# ── not installed ────────────────────────────────────────────────────────────


def test_rlm_provider_not_installed() -> None:
    """RLM_AVAILABLE=False → ImportError with install hint."""
    config = LLMConfig(provider="rlm")
    rlm_config = RLMSettings(enabled=True, backend="ollama", model="x")

    with (
        patch("mnemos.llm.rlm.RLM_AVAILABLE", False),
        pytest.raises(ImportError, match="rlm-toolkit not installed"),
    ):
        RLMProvider(config, rlm_config)


# ── security: history never stored ───────────────────────────────────────────


async def test_rlm_provider_never_stores_history() -> None:
    """result.history (chain-of-thought) is never persisted on the provider."""
    secret_history = [("secret_thought", "secret_step")]
    result = _rlm_result(
        answer="answer",
        history=secret_history,
    )
    provider, _ = _make_provider(arun_result=result)

    await provider.complete("ctx\n--- Synthesis ---\nq")

    # The provider must not expose history via any attribute.
    for attr in vars(provider):
        assert "history" not in attr.lower(), (
            f"provider stores history-like attribute: {attr}"
        )
    # Observability attributes carry only aggregate metrics.
    assert provider.last_iterations == 3
    assert provider.last_subcall_count == 5
    assert provider.last_trace_id == "trace-abc"


# ── sync run via thread (arun not coroutine) ─────────────────────────────────


async def test_rlm_provider_sync_run_via_thread() -> None:
    """When arun is not a coroutine, run() is called via asyncio.to_thread."""
    result = _rlm_result(answer="threaded answer")
    provider, mock_rlm = _make_provider(
        arun_is_coroutine=False,
        run_result=result,
    )

    resp = await provider.complete("ctx\n--- Synthesis ---\nq")

    # arun (non-coroutine) must NOT be called; run must be called.
    mock_rlm.run.assert_called_once()
    mock_rlm.arun.assert_not_called()
    assert resp.text == "threaded answer"


# ── provider_name ────────────────────────────────────────────────────────────


def test_rlm_provider_name() -> None:
    """provider_name is always 'rlm'."""
    provider, _ = _make_provider()
    assert provider.provider_name == "rlm"


# ── RLMConfig construction ───────────────────────────────────────────────────


def test_rlm_provider_builds_rlm_config_from_settings() -> None:
    """RLMConfig is built from RLMSettings with correct field mapping."""
    rlm_config = RLMSettings(
        enabled=True,
        backend="ollama",
        model="qwen2.5:3b",
        max_iterations=25,
        max_subcalls=50,
        max_cost=0.25,
        max_depth=2,
        max_execution_time=60,
        max_memory_mb=256,
        truncate_output=5000,
        allowed_imports=["re", "json"],
        use_infiniretri=True,
        infiniretri_threshold=50_000,
    )
    config = LLMConfig(provider="rlm")

    captured_cfg: dict[str, Any] = {}

    mock_rlm = _make_mock_rlm()
    with (
        patch("mnemos.llm.rlm.RLM_AVAILABLE", True),
        patch("mnemos.llm.rlm.RLM") as mock_rlm_cls,
        patch("mnemos.llm.rlm.RLMConfig") as mock_rlm_cfg_cls,
    ):
        mock_rlm_cls.from_ollama.return_value = mock_rlm
        mock_rlm_cfg_cls.return_value = MagicMock()
        RLMProvider(config, rlm_config)
        captured_cfg = mock_rlm_cfg_cls.call_args.kwargs

    assert captured_cfg["max_iterations"] == 25
    assert captured_cfg["max_subcalls"] == 50
    assert captured_cfg["max_cost"] == 0.25
    assert captured_cfg["max_depth"] == 2
    assert captured_cfg["max_execution_time"] == 60.0
    assert captured_cfg["max_memory_mb"] == 256
    assert captured_cfg["truncate_output"] == 5000
    assert captured_cfg["sandbox"] is True
    assert captured_cfg["use_infiniretri"] is True
    assert captured_cfg["infiniretri_threshold"] == 50_000
    # allowed_imports converted list → set
    assert captured_cfg["allowed_imports"] == {"re", "json"}
