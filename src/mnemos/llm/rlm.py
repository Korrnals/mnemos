"""RLM (Recursive Language Model) provider adapter.

Wraps ``rlm_toolkit.RLM`` so the synthesis pipeline can delegate large
context synthesis to recursive sub-LLM calls inside a sandboxed Python
REPL (see ADR 0008 for the GCW↔RLM pattern mapping).

The toolkit is an optional dependency; install it with
``pip install mnemos[rlm]``. When absent, ``RLM_AVAILABLE`` is ``False``
and instantiating :class:`RLMProvider` raises ``ImportError`` with a
helpful install hint.

Context/query split
-------------------
RLM's ``run()`` / ``arun()`` take a ``context`` (the bulk data to
decompose) and a ``query`` (the instruction). Mnemos callers pass a
single ``prompt`` string, so the provider splits it on a delimiter
before dispatch:

    <context bytes>
    --- Synthesis ---
    <query bytes>

If the delimiter is absent the entire prompt is treated as the ``query``
with an empty ``context`` (degraded mode — no token savings, but still
functional).

Security
--------
``RLMResult.history`` contains the chain-of-thought trace. Per the
sensitive-data policy it is **never** stored on the provider or returned
to the caller. Only aggregate metrics (iterations, subcall_count,
total_cost, trace_id) are retained for observability enrichment.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any

from mnemos.llm.base import LLMExecutionError, LLMProvider, LLMResponse

try:
    from rlm_toolkit import RLM, RLMConfig, RLMResult

    RLM_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    RLM_AVAILABLE = False
    RLM = None
    RLMConfig = None
    RLMResult = None

if TYPE_CHECKING:
    from mnemos.config import LLMConfig, RLMSettings

logger = logging.getLogger(__name__)

# Delimiter that separates context from query inside a single prompt.
# The synthesis pipeline joins accumulated memory entries with the
# synthesis instruction using this marker.
_CONTEXT_QUERY_DELIMITERS: tuple[str, ...] = (
    "\n--- Synthesis ---\n",
    "\n--- QUERY ---\n",
)

# Backends supported by rlm_toolkit.RLM factory methods.
_SUPPORTED_BACKENDS: tuple[str, ...] = ("ollama", "openai", "anthropic")


class RLMProvider(LLMProvider):
    """LLM provider backed by ``rlm_toolkit.RLM``.

    The RLM instance is constructed eagerly in ``__init__`` so that
    configuration errors (bad backend, missing model) surface at
    construction time rather than mid-synthesis. The actual LLM calls
    happen lazily on :meth:`complete`.

    Args:
        config:     The base ``LLMConfig`` (unused except for future
                    provider-level overrides; kept for interface parity).
        rlm_config: The ``RLMSettings`` block describing backend, model,
                    resource bounds, and sandbox guard.

    Raises:
        ImportError:  ``rlm_toolkit`` is not installed.
        ValueError:   ``rlm_config.backend`` is not a supported backend.
    """

    def __init__(
        self,
        config: LLMConfig,
        rlm_config: RLMSettings,
    ) -> None:
        if not RLM_AVAILABLE:
            raise ImportError(
                "rlm-toolkit not installed. Run: pip install mnemos[rlm]"
            )

        self._config = config
        self._rlm_config = rlm_config

        # Build the RLMConfig from our settings. ``allowed_imports`` is a
        # list[str] in RLMSettings but a set[str] in RLMConfig — convert.
        rlm_cfg = RLMConfig(
            max_iterations=rlm_config.max_iterations,
            max_subcalls=rlm_config.max_subcalls,
            max_cost=rlm_config.max_cost,
            max_depth=rlm_config.max_depth,
            max_execution_time=float(rlm_config.max_execution_time),
            max_memory_mb=rlm_config.max_memory_mb,
            sandbox=rlm_config.sandbox,
            allowed_imports=set(rlm_config.allowed_imports),
            truncate_output=rlm_config.truncate_output,
            # recovery: leave as RLM default (None) — no custom recovery yet.
            use_infiniretri=rlm_config.use_infiniretri,
            infiniretri_threshold=rlm_config.infiniretri_threshold,
        )

        backend = rlm_config.backend
        if backend not in _SUPPORTED_BACKENDS:
            raise ValueError(
                f"Unsupported RLM backend: {backend!r}. "
                f"Supported: {_SUPPORTED_BACKENDS}."
            )

        if backend == "ollama":
            self._rlm = RLM.from_ollama(
                model=rlm_config.model,
                sub_model=rlm_config.sub_model,
                resilient=rlm_config.resilient,
                config=rlm_cfg,
            )
        elif backend == "openai":
            self._rlm = RLM.from_openai(
                root_model=rlm_config.model,
                sub_model=rlm_config.sub_model or "gpt-4o-mini",
                resilient=rlm_config.resilient,
                config=rlm_cfg,
            )
        else:  # backend == "anthropic"
            self._rlm = RLM.from_anthropic(
                root_model=rlm_config.model,
                sub_model=rlm_config.sub_model or "claude-haiku",
                resilient=rlm_config.resilient,
                config=rlm_cfg,
            )

        # Detect whether arun is a true coroutine. rlm_toolkit's arun is
        # declared async, but some versions may block the event loop
        # internally. We check once at construction and dispatch
        # accordingly in complete().
        self._arun_is_coroutine: bool = inspect.iscoroutinefunction(
            self._rlm.arun
        )

        # Last-run metrics for observability enrichment. Populated by
        # complete() and read by the router / TraceRecorder. history is
        # deliberately NOT stored here (security policy).
        self._last_iterations: int = 0
        self._last_subcall_count: int = 0
        self._last_total_cost: float = 0.0
        self._last_trace_id: str | None = None

    # ── context/query split ──────────────────────────────────────────────

    @staticmethod
    def _split_context_query(prompt: str) -> tuple[str, str]:
        """Split a single prompt into (context, query) for RLM dispatch.

        Scans for the first recognised delimiter. Everything before it is
        the context (bulk data); everything after is the query
        (instruction). When no delimiter is present the entire prompt
        becomes the query with an empty context — degraded mode, no token
        savings, but the call still works.
        """
        for delim in _CONTEXT_QUERY_DELIMITERS:
            idx = prompt.find(delim)
            if idx != -1:
                context = prompt[:idx]
                query = prompt[idx + len(delim) :]
                return context, query
        return "", prompt

    # ── public API ───────────────────────────────────────────────────────

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Dispatch a completion to the RLM engine.

        Splits the prompt into context/query, calls ``RLM.arun`` (or
        ``RLM.run`` via a thread when ``arun`` is not a true coroutine),
        and maps the result to :class:`LLMResponse`.

        Raises:
            LLMExecutionError: RLM returned a non-success status, or the
                underlying call raised.
        """
        context, query = self._split_context_query(prompt)

        try:
            if self._arun_is_coroutine:
                result: RLMResult = await self._rlm.arun(
                    context=context,
                    query=query,
                    system_prompt=system,
                )
            else:
                # arun is not async — run the sync ``run`` in a thread to
                # avoid blocking the event loop.
                result = await _run_in_thread(
                    self._rlm.run,
                    context=context,
                    query=query,
                    system_prompt=system,
                )
        except LLMExecutionError:
            raise
        except Exception as exc:
            raise LLMExecutionError(
                f"RLM call failed: {exc}",
                provider="rlm",
                cause=exc,
            ) from exc

        # Persist aggregate metrics for observability. history is NEVER
        # stored (chain-of-thought — security policy).
        self._last_iterations = result.iterations
        self._last_subcall_count = result.subcall_count
        self._last_total_cost = result.total_cost
        self._last_trace_id = result.trace_id

        if result.status != "success":
            raise LLMExecutionError(
                f"RLM failed: status={result.status}",
                provider="rlm",
            )

        # Token estimate: word count (whitespace split). RLM does not
        # expose per-call token counts, so we approximate from the
        # context + query input and the answer output.
        tokens_in = len(context.split()) + len(query.split())
        tokens_out = len(result.answer.split())

        return LLMResponse(
            text=result.answer,
            model=f"rlm:{self._rlm_config.model}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached=False,
            fallback_used=False,
        )

    @property
    def provider_name(self) -> str:
        return "rlm"

    # ── observability enrichment ─────────────────────────────────────────

    @property
    def last_iterations(self) -> int:
        """Iteration count from the most recent complete() call."""
        return self._last_iterations

    @property
    def last_subcall_count(self) -> int:
        """Sub-LLM call count from the most recent complete() call."""
        return self._last_subcall_count

    @property
    def last_total_cost(self) -> float:
        """Aggregate cost from the most recent complete() call."""
        return self._last_total_cost

    @property
    def last_trace_id(self) -> str | None:
        """Trace ID from the most recent complete() call (may be None)."""
        return self._last_trace_id


# ── helpers ──────────────────────────────────────────────────────────────────


async def _run_in_thread(
    fn: Any,
    /,
    **kwargs: Any,
) -> RLMResult:
    """Run a sync callable in a worker thread, awaiting the result.

    Used when ``RLM.arun`` is not a true coroutine (some rlm_toolkit
    versions declare ``arun`` async but block internally). Falls back to
    dispatching the sync ``run`` via :func:`asyncio.to_thread`.
    """
    import asyncio

    return await asyncio.to_thread(fn, **kwargs)
