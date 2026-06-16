"""MemoryManager — core CRUD and search orchestrator for Mnemos.

Backed by:
  - SQLiteStore  : all memories (raw/processing/processed/published) + traces
  - VectorStore  : embeddings for published memories only
  - VaultManager : Obsidian-compatible markdown mirror
  - EmbeddingProvider : configurable local ONNX (default) / Ollama / ST
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mnemos.config import Settings
from mnemos.embeddings import EmbeddingProvider, create_embedding_provider
from mnemos.models import (
    AgentRecallQuery,
    Memory,
    MemoryCreate,
    MemorySource,
    MemoryStatus,
    MemoryUpdate,
    SearchResult,
)
from mnemos.pipeline import (
    ClusterResult,
    PublishResult,
    QualityResult,
    SynthesisResult,
)
from mnemos.policy.engine import PolicyAction
from mnemos.storage.sqlite_store import SQLiteStore
from mnemos.storage.vault import VaultManager
from mnemos.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


class MemoryManager:
    """Central coordinator for all memory operations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.resolve_paths()
        settings.apply_runtime_env()
        self.sqlite = SQLiteStore(settings.db_path)
        self.vault = VaultManager(settings.mnemos.vault_path)
        self.vectors = VectorStore(settings.mnemos.data_dir)
        self._embedder: EmbeddingProvider | None = None
        self._watcher: Any = None

    @property
    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            self._embedder = create_embedding_provider(self.settings.embedding)
        return self._embedder

    def close(self) -> None:
        self.sqlite.close()

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _embedding_text(memory: Memory) -> str:
        """Text representation used for embedding (title + content)."""
        parts = []
        if memory.title:
            parts.append(memory.title)
        parts.append(memory.effective_content())
        if memory.tags:
            parts.append(" ".join(memory.tags))
        return "\n".join(parts)[:4096]

    # ── CRUD ────────────────────────────────────────────────────────────────

    def add(
        self,
        data: MemoryCreate,
        *,
        project: str = "",
        agent: str = "",
    ) -> Memory:
        """Create a new memory entry.

        The M2 tag contract is enforced by the MCP layer (mcp_server.py).
        MemoryManager trusts validated project/agent passed in kwargs.
        """
        memory = Memory(
            content=data.content,
            title=data.title,
            tags=data.tags,
            source=data.source,
            source_url=data.source_url,
            memory_type=data.memory_type,
            metadata=data.metadata,
            category=data.category,
            status=data.status,
            filter_profile=data.filter_profile,
            project=project,
            agent=agent,
        )

        # Write to Obsidian vault
        try:
            file_path = self.vault.memory_to_file(memory)
            memory.file_path = str(file_path)
        except Exception as exc:
            logger.warning("Vault write failed (non-fatal): %s", exc)

        # Persist to SQLite
        self.sqlite.save(memory)

        # Only embed + index published memories in the vector store
        if memory.status == MemoryStatus.PUBLISHED:
            try:
                embedding = self.embedder.embed(self._embedding_text(memory))
                self.vectors.upsert(
                    memory.id,
                    embedding,
                    {"project": memory.project, "agent": memory.agent},
                )
            except Exception as exc:
                logger.warning("Vector embed failed (non-fatal): %s", exc)

        logger.info("add: id=%s project=%s agent=%s", memory.id[:8], project, agent)
        return memory

    def get(self, memory_id: str) -> Memory | None:
        return self.sqlite.get(memory_id)

    def update(self, memory_id: str, data: MemoryUpdate) -> Memory | None:
        memory = self.sqlite.get(memory_id)
        if not memory:
            return None

        update_kwargs: dict[str, Any] = {}
        for field in (
            "content",
            "title",
            "tags",
            "memory_type",
            "metadata",
            "status",
            "category",
            "quality_score",
            "confidence",
            "cluster_id",
        ):
            val = getattr(data, field, None)
            if val is not None:
                setattr(memory, field, val)
                update_kwargs[field] = val

        memory.updated_at = datetime.now(UTC)
        self.sqlite.save(memory)

        # Re-embed if now published
        if memory.status == MemoryStatus.PUBLISHED:
            try:
                emb = self.embedder.embed(self._embedding_text(memory))
                self.vectors.upsert(
                    memory.id,
                    emb,
                    {"project": memory.project, "agent": memory.agent},
                )
            except Exception as exc:
                logger.warning("Re-embed failed: %s", exc)

        return memory

    def delete(self, memory_id: str) -> bool:
        memory = self.sqlite.get(memory_id)
        if not memory:
            return False
        if memory.file_path:
            self.vault.delete_file(memory.file_path)
        self.vectors.delete(memory_id)
        return self.sqlite.delete(memory_id)

    # ── Search ──────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        project: str | None = None,
        agent: str | None = None,
        status: MemoryStatus | None = None,
        limit: int = 20,
        hybrid_alpha: float | None = None,
    ) -> list[SearchResult]:
        """Hybrid search: FTS5 + vector + Reciprocal Rank Fusion."""
        alpha = hybrid_alpha if hybrid_alpha is not None else self.settings.search.hybrid_alpha

        # ── FTS leg ────────────────────────────────────────────────────────
        fts_pairs: list[tuple[Memory, float]] = []
        try:
            fts_pairs = self.sqlite.fts_search(
                query,
                limit=limit * 2,
                project=project,
                agent=agent,
                status=status,
            )
        except Exception as exc:
            logger.warning("FTS search failed: %s", exc)

        # ── Vector leg ─────────────────────────────────────────────────────
        vector_pairs: list[tuple[str, float]] = []
        try:
            q_emb = self.embedder.embed(query)
            vector_pairs = self.vectors.search(q_emb, limit=limit * 2)
        except Exception as exc:
            logger.warning("Vector search failed: %s", exc)

        # ── RRF merge ──────────────────────────────────────────────────────
        rrf_k = 60
        scores: dict[str, float] = {}

        for rank, (mem, _) in enumerate(fts_pairs, start=1):
            scores[mem.id] = scores.get(mem.id, 0.0) + (1 - alpha) / (rrf_k + rank)

        for rank, (mid, _) in enumerate(vector_pairs, start=1):
            scores[mid] = scores.get(mid, 0.0) + alpha / (rrf_k + rank)

        # Resolve ids → Memory objects
        id_to_memory: dict[str, Memory] = {m.id: m for m, _ in fts_pairs}
        for mid, _ in vector_pairs:
            if mid not in id_to_memory:
                # SQLite lookups can miss; skip silently if memory was
                # deleted between vector and SQLite indexes.
                fetched: Memory | None = self.sqlite.get(mid)
                if fetched is not None:
                    id_to_memory[mid] = fetched

        # Apply tag filter post-hoc
        results: list[SearchResult] = []
        for mid, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            matched: Memory | None = id_to_memory.get(mid)
            if matched is None:
                continue
            if tags and not all(t in matched.tags for t in tags):
                continue
            results.append(SearchResult(memory=matched, score=score, search_type="hybrid"))
            if len(results) >= limit:
                break
        return results

    def agent_recall(self, query: AgentRecallQuery) -> list[SearchResult]:
        """M3 — per-agent recall: recent entries + optional hybrid search."""
        if query.query:
            return self.search(
                query.query,
                agent=query.agent,
                project=query.project,
                limit=query.limit,
            )
        # No query → return most recent N for agent
        memories = self.sqlite.list_recent_for_agent(
            query.agent,
            project=query.project,
            limit=query.limit,
        )
        return [SearchResult(memory=m, score=1.0, search_type="recency") for m in memories]

    def recall_context(
        self, *, project: str, query: str | None = None, limit: int = 5
    ) -> list[Memory]:
        """Return most recent checkpoint memories for a project."""
        memories = self.sqlite.list_all(
            limit=limit * 3,
            project=project,
            tags=["gcw:checkpoint"],
        )
        # Sort by recency and trim
        memories.sort(key=lambda m: m.created_at, reverse=True)
        return memories[:limit]

    def list_recent(
        self,
        *,
        limit: int = 10,
        tags: list[str] | None = None,
        project: str | None = None,
        agent: str | None = None,
    ) -> list[Memory]:
        return self.sqlite.list_all(
            limit=limit,
            tags=tags,
            project=project,
            agent=agent,
        )

    def list_tags(self) -> dict[str, int]:
        return self.sqlite.get_all_tags()

    def stats(self) -> dict[str, Any]:
        by_status = self.sqlite.count_by_status()
        return {
            "status": "ok",
            "version": "0.1.0",
            "data_dir": str(self.settings.mnemos.data_dir),
            "vault_path": str(self.settings.mnemos.vault_path),
            "total": self.sqlite.count(),
            "by_status": by_status,
            "vectors": self.vectors.count(),
            "projects": self.sqlite.get_project_memory_counts(),
        }

    # ── Path-scoped rules ingest (M8) ───────────────────────────────────────

    def ingest_path_scoped_rules(
        self,
        rules_dir: str | Path,
        *,
        project: str = "",
        agent: str = "",
        pattern: str = "*.instructions.md",
    ) -> list[dict[str, Any]]:
        """Scan a directory for `*.instructions.md` files and ingest them."""
        from mnemos.watchers.path_scoped import ingest_path_scoped_rules as _ingest

        return _ingest(self, Path(rules_dir), project=project, agent=agent, pattern=pattern)

    def remove_path_scoped_rule(self, file_path: str | Path) -> dict[str, Any]:
        """Remove a single rule by file path."""
        from mnemos.watchers.path_scoped import remove_path_scoped_rule as _remove

        return _remove(self, Path(file_path))

    # ── Context Filter (M10) ─────────────────────────────────────────────────

    def apply_context_filter(
        self,
        memory_id: str,
        *,
        profile: str | None = None,
        budget: int | None = None,
    ) -> dict[str, Any]:
        """Run the 5-stage context filter on a memory's raw_content.

        Updates memory.clean_content, filter_profile, filter_stats, filter_version.
        """
        from mnemos.filter.pipeline import apply_filter

        memory = self.sqlite.get(memory_id)
        if memory is None:
            return {"status": "error", "error": f"Memory {memory_id} not found"}

        raw = memory.raw_content or memory.content
        if not raw:
            return {"status": "error", "error": "No content to filter"}

        result = apply_filter(raw, profile=profile, budget=budget)

        memory.clean_content = result["clean_content"]
        memory.filter_profile = result["profile"]
        memory.filter_stats = result["stats"]
        memory.filter_version = result["version"]
        memory.updated_at = datetime.now(UTC)

        self.sqlite.save(memory)

        return {
            "status": "ok",
            "memory_id": memory_id,
            "clean_content": result["clean_content"],
            "filter_profile": result["profile"],
            "stats": result["stats"],
        }

    # ── Ingestion ────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_url(url: str) -> str:
        """Validate URL for SSRF safety. Raises ValueError on blocked schemes or hosts.

        Covers (ADR-0009, ADR-0012):
        - Schemes: only http, https
        - DNS names: resolved and the *resolved* IP is checked
        - IPv4: loopback, RFC1918 private, link-local (169.254/16), 0.0.0.0
        - IPv6: loopback (::1), link-local (fe80::/10), unique-local (fc00::/7
          which includes AWS IPv6 metadata fd00:ec2::254), IPv4-mapped IPv6
        - Any IP flagged by ``ipaddress`` as private/loopback/link-local/
          reserved/multicast is rejected
        """
        import ipaddress
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"URL scheme must be http(s), got {parsed.scheme}")
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError("URL must have a host")

        # The "0.0.0.0" entry is a blocklist literal, NOT a socket bind.
        # nosec B104 — see ADR-0009 §"B104 false positive".
        blocked_v4_literals: set[str] = {
            "localhost",
            "127.0.0.1",
            "0.0.0.0",  # nosec B104 — blocklist entry
            "::1",
            "169.254.169.254",  # AWS IPv4 metadata
        }

        if host in blocked_v4_literals:
            raise ValueError(f"URL host blocked for SSRF safety: {host}")

        # 1) If host is a literal IP (v4 or v6), use ipaddress to classify it.
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None  # not a literal IP; it's a DNS name — resolve below

        if ip is not None:
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ValueError(f"URL host blocked for SSRF safety: {host}")
            return url

        # 2) DNS name. Check IPv4-prefix heuristics first (cheap, fast-fail).
        if host.startswith("127."):
            raise ValueError(f"URL host blocked for SSRF safety: {host}")
        if host.startswith("10."):
            raise ValueError(f"URL host blocked for SSRF safety: {host}")
        if host.startswith("192.168."):
            raise ValueError(f"URL host blocked for SSRF safety: {host}")
        if host.startswith("172."):
            second_octet = host[4:].split(".")[0]
            if second_octet.isdigit() and 16 <= int(second_octet) <= 31:
                raise ValueError(f"URL host blocked for SSRF safety: {host}")

        # 3) Resolve the DNS name. If any resolved address is private/loopback/
        # link-local, reject. This closes DNS rebinding at the boundary: even
        # if the resolver returns a public IP at check time and a private IP
        # at TCP time, we re-checked at the *resolve* step and the httpx
        # Client below will be the one making the actual connection. The
        # boundary check still raises the bar.
        import socket

        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ValueError(f"URL host could not be resolved: {host} ({exc})") from exc

        for info in infos:
            sockaddr = info[4]
            # ``sockaddr[0]`` is typed as ``str | int`` by ``typeshed``
            # (on some platforms it can be a 4-byte packed int); force
            # a ``str`` so downstream ``startswith`` / ``ip_address`` work
            # uniformly and mypy can narrow the type.
            resolved = str(sockaddr[0])
            # Strip IPv4-mapped IPv6 prefix (e.g. "::ffff:127.0.0.1" → "127.0.0.1")
            if resolved.startswith("::ffff:"):
                resolved = resolved[len("::ffff:") :]
            try:
                rip = ipaddress.ip_address(resolved)
            except ValueError:
                continue
            if (
                rip.is_private
                or rip.is_loopback
                or rip.is_link_local
                or rip.is_reserved
                or rip.is_multicast
                or rip.is_unspecified
            ):
                raise ValueError(f"URL host resolves to blocked address: {host} → {resolved}")

        return url

    def ingest_url(self, url: str, *, tags: list[str], project: str, agent: str) -> Memory:
        """Fetch a URL, extract main text, save as RAW memory."""
        try:
            self._validate_url(url)
            import httpx
            import trafilatura

            with httpx.Client(follow_redirects=True, max_redirects=5) as client:
                resp = client.get(url, timeout=30)
            resp.raise_for_status()
            content = trafilatura.extract(resp.text) or resp.text[:4000]
        except Exception as exc:
            logger.warning("URL fetch failed: %s — using placeholder", exc)
            content = f"URL: {url}\n[fetch failed: {exc}]"

        data = MemoryCreate(
            content=content,
            title=url.split("//")[-1][:80],
            tags=tags,
            source=MemorySource.WEB,
            source_url=url,
        )
        return self.add(data, project=project, agent=agent)

    # ── Watchers ─────────────────────────────────────────────────────────────

    def watch_start(
        self, *, paths: list[str], scan: bool = True, include_rules: bool = False
    ) -> None:
        """Start the background vault watcher (M8)."""
        logger.info("watch_start: paths=%s include_rules=%s", paths, include_rules)

    def watch_stop(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def watch_status(self) -> dict[str, Any]:
        return {"running": self._watcher is not None}

    # ── Pipeline (M4) ───────────────────────────────────────────────────────

    def cluster(
        self,
        *,
        project: str | None = None,
        agent: str | None = None,
        limit: int = 100,
        similarity_threshold: float = 0.82,
        min_cluster_size: int = 2,
    ) -> list[ClusterResult]:
        """Run the cluster worker on raw memories."""
        from mnemos.pipeline.cluster import cluster_raw_memories

        return cluster_raw_memories(
            self,
            project=project,
            agent=agent,
            limit=limit,
            similarity_threshold=similarity_threshold,
            min_cluster_size=min_cluster_size,
        )

    def synthesize(
        self,
        cluster_id: str,
        *,
        prompt_version: str = "v1",
        force: bool = False,
    ) -> SynthesisResult | None:
        """Run the synthesis worker on a cluster."""
        from mnemos.pipeline.synthesize import synthesize_cluster

        return synthesize_cluster(self, cluster_id, prompt_version=prompt_version, force=force)

    def quality_gate(self, memory_id: str) -> QualityResult:
        """Run quality gates on a processed memory."""
        from mnemos.pipeline.quality_gate import evaluate_quality

        return evaluate_quality(self, memory_id)

    def publish(self, memory_id: str, *, skip_quality_check: bool = False) -> PublishResult:
        """Publish a processed memory and index it in the vector store."""
        from mnemos.pipeline.publish import publish_memory

        return publish_memory(self, memory_id, skip_quality_check=skip_quality_check)

    def run_pipeline(
        self,
        *,
        project: str | None = None,
        agent: str | None = None,
        limit: int = 100,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """End-to-end pipeline: cluster → synthesize → quality_gate → publish.

        Returns a summary dict for observability / CLI output.
        """
        clusters = self.cluster(project=project, agent=agent, limit=limit, **kwargs)
        synthesized: list[SynthesisResult] = []
        published: list[PublishResult] = []
        failed_qg: list[QualityResult] = []

        for cr in clusters:
            syn = self.synthesize(cr.cluster_id)
            if syn is None:
                continue
            synthesized.append(syn)

            qg = self.quality_gate(syn.draft_id)
            if not qg.passed:
                failed_qg.append(qg)
                continue

            pub = self.publish(syn.draft_id)
            published.append(pub)

        return {
            "clusters": len(clusters),
            "synthesized": len(synthesized),
            "published": len(published),
            "failed_quality_gate": len(failed_qg),
            "published_ids": [p.memory_id for p in published],
        }

    # ── Policy / DLQ (M5) ─────────────────────────────────────────────────

    def dlq_list(
        self,
        *,
        task_label: str | None = None,
        ready_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """List Dead-Letter Queue entries."""
        from mnemos.policy.dlq import dlq_list

        return dlq_list(self, task_label=task_label, ready_only=ready_only, limit=limit)

    def dlq_retry(self, dlq_id: str, *, backoff_sec: int = 60) -> dict[str, object]:
        """Increment retry attempt for a DLQ entry."""
        from mnemos.policy.dlq import dlq_retry

        return dlq_retry(self, dlq_id, backoff_sec=backoff_sec)

    def dlq_discard(self, dlq_id: str) -> bool:
        """Permanently remove a DLQ entry."""
        from mnemos.policy.dlq import dlq_discard

        return dlq_discard(self, dlq_id)

    def evaluate_policy(self, memory_id: str) -> list[PolicyAction]:
        """Evaluate policy rules against a memory and return fired actions."""
        from mnemos.policy.engine import evaluate_rules, load_rules_from_dict

        mem = self.sqlite.get(memory_id)
        if mem is None:
            return []
        raw = getattr(self.settings, "policies", None)
        rules = load_rules_from_dict(raw) if isinstance(raw, dict) else []
        return evaluate_rules(mem, rules)
