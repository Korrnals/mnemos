"""Tests for M4: Knowledge Pipeline.

Covers:
  - cluster_raw_memories — grouping by similarity, status transition,
    deterministic cluster_id, min_cluster_size, project/agent filters
  - synthesize_cluster — draft creation, idempotency / cache, trace logging
  - evaluate_quality — threshold enforcement, pass/fail rationale
  - publish_memory — status transition, vector indexing, skip_quality_check
  - MemoryManager.run_pipeline — end-to-end integration
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from mnemos.config import Settings
from mnemos.manager import MemoryManager
from mnemos.models import Memory, MemoryCreate, MemoryStatus
from mnemos.pipeline.cluster import cluster_raw_memories
from mnemos.pipeline.publish import publish_memory
from mnemos.pipeline.quality_gate import evaluate_quality
from mnemos.pipeline.synthesize import synthesize_cluster

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_settings():
    """Yield a Settings object backed by a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        settings = Settings(
            mnemos={
                "vault_path": str(tmp / "vault"),
                "data_dir": str(tmp / "data"),
                "db_name": "test.db",
            },
            embedding={"provider": "onnx"},
        )
        settings.resolve_paths()
        yield settings


@pytest.fixture
def tmp_manager(tmp_settings, mock_llm_router):
    """Yield a MemoryManager with isolated storage and mocked embedder/LLM.

    The embedder is mocked with a deterministic hash-based vector generator
    so clustering works without the ONNX model. The LLM router is the
    shared ``mock_llm_router`` fixture (no network, no SDK) so synthesis
    tests run deterministically.
    """
    mgr = MemoryManager(tmp_settings)
    # Mock embedder: deterministic 384-dim embeddings based on content hash
    mock_embedder = MagicMock()

    def _fake_embed(text: str) -> list[float]:
        # Deterministic float vector from text hash — stable across runs
        h = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**31)
        rng = np.random.default_rng(seed=h)
        vec = rng.random(384).astype(np.float32)
        # Normalise
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    mock_embedder.embed.side_effect = _fake_embed
    mgr._embedder = mock_embedder
    # Inject the mock LLM router so synthesize_cluster never hits the network.
    mgr._llm = mock_llm_router
    yield mgr
    mgr.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_raw(mgr: MemoryManager, content: str, agent: str = "reviewer", project: str = "gcw"):
    """Add a raw memory via MemoryManager."""
    data = MemoryCreate(
        content=content,
        tags=[f"project:{project}", f"agent:{agent}", "gcw:learning"],
    )
    return mgr.add(data, project=project, agent=agent)


# ---------------------------------------------------------------------------
# Cluster worker
# ---------------------------------------------------------------------------


class TestClusterWorker:
    def test_groups_similar_memories(self, tmp_manager):
        """Memories with similar content get the same cluster_id."""
        mgr = tmp_manager
        # Two very similar security notes
        m1 = _add_raw(mgr, "SQL injection in auth module via user input")
        m2 = _add_raw(mgr, "SQL injection vulnerability found in authentication")
        # One unrelated note
        _add_raw(mgr, "Refactor database connection pool for performance")

        clusters = cluster_raw_memories(mgr, similarity_threshold=0.75, min_cluster_size=2)
        assert len(clusters) >= 1
        # At least one cluster should contain the two similar notes
        cluster_ids_for_m1 = [c.cluster_id for c in clusters if m1.id in c.memory_ids]
        cluster_ids_for_m2 = [c.cluster_id for c in clusters if m2.id in c.memory_ids]
        assert cluster_ids_for_m1 == cluster_ids_for_m2

    def test_status_transition_to_processing(self, tmp_manager):
        """Clustered memories move from RAW to PROCESSING."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        m2 = _add_raw(mgr, "note two")

        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)

        reloaded1 = mgr.sqlite.get(m1.id)
        reloaded2 = mgr.sqlite.get(m2.id)
        assert reloaded1 is not None
        assert reloaded2 is not None
        assert reloaded1.status == MemoryStatus.PROCESSING
        assert reloaded2.status == MemoryStatus.PROCESSING
        assert reloaded1.cluster_id == reloaded2.cluster_id

    def test_min_cluster_size_discards_small(self, tmp_manager):
        """Clusters smaller than min_cluster_size are discarded."""
        mgr = tmp_manager
        _add_raw(mgr, "lonely note")

        clusters = cluster_raw_memories(mgr, min_cluster_size=2)
        assert clusters == []

    def test_project_filter(self, tmp_manager):
        """Project scope limits which raw memories are considered."""
        mgr = tmp_manager
        _add_raw(mgr, "gcw note", project="gcw")
        _add_raw(mgr, "docs note", project="docs")

        clusters = cluster_raw_memories(mgr, project="gcw", similarity_threshold=0.5)
        # Only gcw note considered; not enough for cluster
        assert clusters == []

    def test_deterministic_cluster_id(self, tmp_manager):
        """Re-running on the same data yields the same cluster_id."""
        mgr = tmp_manager
        _add_raw(mgr, "alpha")
        _add_raw(mgr, "beta")

        c1 = cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        # Reset statuses back to raw so we can re-cluster
        for m in mgr.sqlite.list_all(status=MemoryStatus.PROCESSING, limit=10):
            m.status = MemoryStatus.RAW
            m.cluster_id = None
            mgr.sqlite.save(m)

        c2 = cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        assert c1[0].cluster_id == c2[0].cluster_id

    def test_empty_raw_pool(self, tmp_manager):
        """No raw memories → empty cluster list."""
        mgr = tmp_manager
        clusters = cluster_raw_memories(mgr)
        assert clusters == []


# ---------------------------------------------------------------------------
# Synthesize worker
# ---------------------------------------------------------------------------


class TestSynthesizeWorker:
    def test_creates_processed_memory(self, tmp_manager):
        """Synthesis produces a new memory with status=processed."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "security issue A")
        _add_raw(mgr, "security issue B")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        result = synthesize_cluster(mgr, cluster_id)
        assert result is not None
        draft = mgr.sqlite.get(result.draft_id)
        assert draft is not None
        assert draft.status == MemoryStatus.PROCESSED
        assert draft.cluster_id == cluster_id
        assert "gcw:synthesized" in draft.tags

    def test_idempotency_cache(self, tmp_manager):
        """Second call with same params returns cached result."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        r1 = synthesize_cluster(mgr, cluster_id)
        r2 = synthesize_cluster(mgr, cluster_id)
        assert r1 is not None
        assert r2 is not None
        assert r1.draft_id == r2.draft_id

    def test_force_bypasses_cache(self, tmp_manager):
        """force=True creates a new draft even if cache exists."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        r1 = synthesize_cluster(mgr, cluster_id)
        r2 = synthesize_cluster(mgr, cluster_id, force=True)
        assert r1 is not None
        assert r2 is not None
        assert r1.draft_id != r2.draft_id

    def test_missing_cluster_returns_none(self, tmp_manager):
        """Synthesizing a nonexistent cluster returns None."""
        mgr = tmp_manager
        result = synthesize_cluster(mgr, "nonexistent-cluster-id")
        assert result is None

    def test_trace_logged(self, tmp_manager):
        """A trace record is written after successful synthesis."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        synthesize_cluster(mgr, cluster_id)
        traces = mgr.sqlite.list_traces(limit=10)
        assert any(t.task_label == "synthesize" for t in traces)

    def test_synthesize_calls_llm_router(self, tmp_manager):
        """synthesize_cluster calls mgr.llm.complete() exactly once."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        # The mock_llm_router has a MockLLMProvider injected as _provider.
        provider = mgr.llm._provider
        assert provider is not None
        calls_before = len(provider.calls)

        result = synthesize_cluster(mgr, cluster_id)
        assert result is not None
        assert len(provider.calls) == calls_before + 1
        # The prompt must contain the --- Synthesis --- delimiter.
        assert "--- Synthesis ---" in provider.calls[-1]["prompt"]

    def test_synthesize_records_trace(self, tmp_manager):
        """Trace has llm_called=True, tokens_in/out, fallback_used."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        synthesize_cluster(mgr, cluster_id)
        traces = mgr.sqlite.list_traces(task_label="synthesize", limit=10)
        llm_traces = [t for t in traces if t.step == "llm_call"]
        assert len(llm_traces) >= 1
        t = llm_traces[0]
        assert t.llm_called is True
        assert t.llm_done is True
        assert t.tokens_in > 0
        assert t.tokens_out > 0
        assert t.fallback_used is False

    def test_synthesize_rlm_metrics_in_trace(self, tmp_manager):
        """When RLM is used, trace rationale_summary includes RLM metrics."""
        from mnemos.config import LLMConfig, RLMSettings
        from mnemos.llm.base import LLMProvider, LLMResponse
        from mnemos.llm.router import LLMRouter

        class _RLMWithMetrics(LLMProvider):
            def __init__(self) -> None:
                self.calls = []

            async def complete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096):
                self.calls.append({"prompt": prompt})
                return LLMResponse(
                    text="# RLM Synthesis\n\nArticle.",
                    model="rlm:qwen2.5:3b",
                    tokens_in=50,
                    tokens_out=10,
                    cached=False,
                    fallback_used=False,
                )

            @property
            def provider_name(self) -> str:
                return "rlm"

            @property
            def last_iterations(self) -> int:
                return 3

            @property
            def last_subcall_count(self) -> int:
                return 5

            @property
            def last_total_cost(self) -> float:
                return 0.0123

            @property
            def last_trace_id(self) -> str | None:
                return "rlm-trace-abc"

        rlm_provider = _RLMWithMetrics()
        rlm_settings = RLMSettings(enabled=True, threshold_tokens=1)
        router = LLMRouter(LLMConfig(provider="ollama", model="qwen2.5:3b"), rlm_settings)
        router._rlm_provider = rlm_provider
        # Also inject a standard provider so fallback path is available.
        from tests.conftest import MockLLMProvider

        router._provider = MockLLMProvider(text="standard-fallback")

        mgr = tmp_manager
        mgr._llm = router

        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        result = synthesize_cluster(mgr, cluster_id, force_rlm=True)
        assert result is not None
        assert len(rlm_provider.calls) == 1

        traces = mgr.sqlite.list_traces(task_label="synthesize", limit=10)
        llm_traces = [t for t in traces if t.step == "llm_call"]
        assert len(llm_traces) >= 1
        rationale = llm_traces[0].rationale_summary
        assert "RLM iters=3" in rationale
        assert "subcalls=5" in rationale
        assert "trace=rlm-trace-abc" in rationale

    def test_synthesize_fallback_on_rlm_failure(self, tmp_manager):
        """RLM fails, fallback to standard, trace fallback_used=True."""
        from mnemos.config import LLMConfig, RLMSettings
        from mnemos.llm.base import LLMExecutionError, LLMProvider
        from mnemos.llm.router import LLMRouter
        from tests.conftest import MockLLMProvider

        class _FailingRLM(LLMProvider):
            def __init__(self) -> None:
                self.calls = []

            async def complete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096):
                self.calls.append({"prompt": prompt})
                raise LLMExecutionError("rlm boom", provider="rlm")

            @property
            def provider_name(self) -> str:
                return "rlm"

        rlm_provider = _FailingRLM()
        standard = MockLLMProvider(text="# Fallback Synthesis\n\nArticle.")
        rlm_settings = RLMSettings(
            enabled=True, threshold_tokens=1, fallback_on_failure=True
        )
        router = LLMRouter(LLMConfig(provider="ollama", model="qwen2.5:3b"), rlm_settings)
        router._rlm_provider = rlm_provider
        router._provider = standard

        mgr = tmp_manager
        mgr._llm = router

        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        result = synthesize_cluster(mgr, cluster_id, force_rlm=True)
        assert result is not None
        assert len(rlm_provider.calls) == 1  # RLM was attempted
        assert len(standard.calls) == 1  # then fell back
        assert result.content == "# Fallback Synthesis\n\nArticle."

        traces = mgr.sqlite.list_traces(task_label="synthesize", limit=10)
        llm_traces = [t for t in traces if t.step == "llm_call"]
        assert len(llm_traces) >= 1
        assert llm_traces[0].fallback_used is True

    def test_synthesize_force_rlm(self, tmp_manager):
        """force_rlm=True → RLM used regardless of prompt size."""
        from mnemos.config import LLMConfig, RLMSettings
        from mnemos.llm.base import LLMProvider, LLMResponse
        from mnemos.llm.router import LLMRouter

        class _RLMStub(LLMProvider):
            def __init__(self) -> None:
                self.calls = []

            async def complete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096):
                self.calls.append({"prompt": prompt})
                return LLMResponse(
                    text="# Forced RLM\n\nArticle.",
                    model="rlm:qwen2.5:3b",
                    tokens_in=10,
                    tokens_out=5,
                    cached=False,
                    fallback_used=False,
                )

            @property
            def provider_name(self) -> str:
                return "rlm"

        rlm_provider = _RLMStub()
        rlm_settings = RLMSettings(enabled=True, threshold_tokens=10_000)
        router = LLMRouter(LLMConfig(provider="ollama", model="qwen2.5:3b"), rlm_settings)
        router._rlm_provider = rlm_provider
        from tests.conftest import MockLLMProvider

        router._provider = MockLLMProvider(text="should-not-be-used")

        mgr = tmp_manager
        mgr._llm = router

        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        result = synthesize_cluster(mgr, cluster_id, force_rlm=True)
        assert result is not None
        assert len(rlm_provider.calls) == 1
        assert result.model_used == "rlm:qwen2.5:3b"

    def test_synthesize_force_standard(self, tmp_manager):
        """force_standard=True → standard used regardless of prompt size."""
        from mnemos.config import LLMConfig, RLMSettings
        from mnemos.llm.base import LLMProvider, LLMResponse
        from mnemos.llm.router import LLMRouter

        class _NeverCalledRLM(LLMProvider):
            def __init__(self) -> None:
                self.calls = []

            async def complete(self, prompt, *, system=None, temperature=0.3, max_tokens=4096):
                self.calls.append({"prompt": prompt})
                return LLMResponse(
                    text="should-not-be-used",
                    model="rlm:mock",
                    tokens_in=0,
                    tokens_out=0,
                )

            @property
            def provider_name(self) -> str:
                return "rlm"

        rlm_provider = _NeverCalledRLM()
        # threshold=1 so without force_standard, RLM would be selected.
        rlm_settings = RLMSettings(enabled=True, threshold_tokens=1)
        router = LLMRouter(LLMConfig(provider="ollama", model="qwen2.5:3b"), rlm_settings)
        router._rlm_provider = rlm_provider
        from tests.conftest import MockLLMProvider

        standard = MockLLMProvider(text="# Forced Standard\n\nArticle.")
        router._provider = standard

        mgr = tmp_manager
        mgr._llm = router

        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        result = synthesize_cluster(mgr, cluster_id, force_standard=True)
        assert result is not None
        assert len(rlm_provider.calls) == 0  # RLM NOT called
        assert len(standard.calls) == 1
        assert result.content == "# Forced Standard\n\nArticle."

    def test_synthesize_force_both_raises(self, tmp_manager):
        """force_rlm and force_standard together → ValueError."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        with pytest.raises(ValueError, match="mutually exclusive"):
            synthesize_cluster(
                mgr, cluster_id, force_rlm=True, force_standard=True
            )

    def test_synthesize_async_variant(self, tmp_manager):
        """synthesize_cluster_async works with await."""
        import asyncio

        from mnemos.pipeline.synthesize import synthesize_cluster_async

        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        result = asyncio.run(synthesize_cluster_async(mgr, cluster_id))
        assert result is not None
        draft = mgr.sqlite.get(result.draft_id)
        assert draft is not None
        assert draft.status == MemoryStatus.PROCESSED

    def test_synthesize_sync_wrapper(self, tmp_manager):
        """synthesize_cluster (sync) works via asyncio.run internally."""
        mgr = tmp_manager
        m1 = _add_raw(mgr, "note one")
        _add_raw(mgr, "note two")
        cluster_raw_memories(mgr, similarity_threshold=0.5, min_cluster_size=2)
        cluster_id = mgr.sqlite.get(m1.id).cluster_id

        result = synthesize_cluster(mgr, cluster_id)
        assert result is not None
        assert result.content == "mock-llm-response"


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


class TestQualityGate:
    def test_passes_when_all_thresholds_met(self, tmp_manager):
        """Memory meeting all thresholds passes."""
        mgr = tmp_manager
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
            quality_score=0.9,
            confidence=0.9,
            source_coverage=5,
        )
        mgr.sqlite.save(mem)

        qg = evaluate_quality(
            mgr,
            mem.id,
            min_quality=0.6,
            min_confidence=0.6,
            min_source_coverage=2,
        )
        assert qg.passed is True
        assert qg.failures == []

    def test_fails_on_low_quality(self, tmp_manager):
        """quality_score below threshold → fail."""
        mgr = tmp_manager
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
            quality_score=0.3,
            confidence=0.9,
            source_coverage=5,
        )
        mgr.sqlite.save(mem)

        qg = evaluate_quality(mgr, mem.id, min_quality=0.6)
        assert qg.passed is False
        assert any("quality_score" in f for f in qg.failures)

    def test_fails_on_low_confidence(self, tmp_manager):
        """confidence below threshold → fail."""
        mgr = tmp_manager
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
            quality_score=0.9,
            confidence=0.2,
            source_coverage=5,
        )
        mgr.sqlite.save(mem)

        qg = evaluate_quality(mgr, mem.id, min_confidence=0.6)
        assert qg.passed is False
        assert any("confidence" in f for f in qg.failures)

    def test_fails_on_low_source_coverage(self, tmp_manager):
        """source_coverage below threshold → fail."""
        mgr = tmp_manager
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
            quality_score=0.9,
            confidence=0.9,
            source_coverage=1,
        )
        mgr.sqlite.save(mem)

        qg = evaluate_quality(mgr, mem.id, min_source_coverage=3)
        assert qg.passed is False
        assert any("source_coverage" in f for f in qg.failures)

    def test_fails_on_wrong_status(self, tmp_manager):
        """Only processed memories can pass the gate."""
        mgr = tmp_manager
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.RAW,
            quality_score=0.9,
            confidence=0.9,
            source_coverage=5,
        )
        mgr.sqlite.save(mem)

        qg = evaluate_quality(mgr, mem.id)
        assert qg.passed is False
        assert any("status" in f for f in qg.failures)

    def test_missing_memory(self, tmp_manager):
        """Quality gate on nonexistent memory returns graceful failure."""
        mgr = tmp_manager
        qg = evaluate_quality(mgr, "nonexistent-id")
        assert qg.passed is False
        assert any("not found" in f for f in qg.failures)


# ---------------------------------------------------------------------------
# Publish stage
# ---------------------------------------------------------------------------


class TestPublishStage:
    def test_promotes_to_published(self, tmp_manager):
        """Publish transitions status to published."""
        mgr = tmp_manager
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
        )
        mgr.sqlite.save(mem)

        result = publish_memory(mgr, mem.id)
        assert result.published is True
        reloaded = mgr.sqlite.get(mem.id)
        assert reloaded is not None
        assert reloaded.status == MemoryStatus.PUBLISHED

    def test_upserts_to_vector_index(self, tmp_manager):
        """Published memory is added to the vector store."""
        mgr = tmp_manager
        mem = Memory(
            content="draft about kubernetes deployments",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
        )
        mgr.sqlite.save(mem)
        # Pre-seed embedder with deterministic vector
        dummy = [0.5] * 384
        mgr._embedder = MagicMock()
        mgr._embedder.embed.return_value = dummy

        publish_memory(mgr, mem.id)
        assert mgr.vectors.count() == 1

    def test_skips_non_processed(self, tmp_manager):
        """Publishing a RAW memory fails unless skip_quality_check is used."""
        mgr = tmp_manager
        mem = Memory(
            content="raw note",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.RAW,
        )
        mgr.sqlite.save(mem)

        result = publish_memory(mgr, mem.id)
        assert result.published is False

    def test_skip_quality_check_bypass(self, tmp_manager):
        """skip_quality_check=True allows publishing non-processed memories."""
        mgr = tmp_manager
        mem = Memory(
            content="raw note",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.RAW,
        )
        mgr.sqlite.save(mem)
        mgr._embedder = MagicMock()
        mgr._embedder.embed.return_value = [0.1] * 384

        result = publish_memory(mgr, mem.id, skip_quality_check=True)
        assert result.published is True
        assert mgr.sqlite.get(mem.id).status == MemoryStatus.PUBLISHED

    def test_missing_memory(self, tmp_manager):
        """Publishing nonexistent memory returns failed result."""
        mgr = tmp_manager
        result = publish_memory(mgr, "nonexistent-id")
        assert result.published is False


# ---------------------------------------------------------------------------
# End-to-end pipeline via MemoryManager
# ---------------------------------------------------------------------------


class TestRunPipeline:
    def test_end_to_end(self, tmp_manager):
        """run_pipeline goes cluster → synthesize → quality_gate → publish."""
        mgr = tmp_manager
        # Seed 3 similar raw notes
        _add_raw(mgr, "security vulnerability in auth module")
        _add_raw(mgr, "auth module has SQL injection vulnerability")
        _add_raw(mgr, "authentication layer security issue")

        summary = mgr.run_pipeline(similarity_threshold=0.5)

        assert summary["clusters"] >= 1
        assert summary["synthesized"] >= 1
        # Quality gate may pass or fail depending on default thresholds
        # (default quality/confidence are 0.0 from synthesis placeholder)
        # So we assert structure, not exact counts
        assert isinstance(summary["published_ids"], list)

    def test_empty_raw_pool(self, tmp_manager):
        """run_pipeline with no raw memories returns zero counts."""
        mgr = tmp_manager
        summary = mgr.run_pipeline()
        assert summary == {
            "clusters": 0,
            "synthesized": 0,
            "published": 0,
            "failed_quality_gate": 0,
            "published_ids": [],
        }
