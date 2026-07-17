"""MemoryManager — core CRUD and search orchestrator for Mnemos.

Backed by:
  - SQLiteStore  : all memories (raw/processing/processed/published) + traces
  - VectorStore  : embeddings for published memories only
  - VaultManager : Obsidian-compatible markdown mirror
  - EmbeddingProvider : configurable local ONNX (default) / Ollama / ST
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mnemos import __version__
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

# Hard cap on redirect hops for per-hop SSRF re-validation (v2 posture).
# Each hop is validated by _validate_url before the next request is issued.
_MAX_REDIRECTS: int = 5


class _SSRFRejectionError(Exception):
    """Internal sentinel wrapping a ``ValueError`` from ``_validate_url``.

    Distinguishes an SSRF guard rejection (must be re-raised, never stored
    in memory) from operational ``ValueError``s raised inside the fetch
    loop (too-many-redirects, redirect-loop, missing Location) which are
    legitimate network errors and degrade to placeholder content.
    """

    def __init__(self, original: ValueError) -> None:
        super().__init__(str(original))
        self.original = original


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
        # In-memory search instrumentation (resets on restart).
        # Accepted trade-off for the dashboard: not persisted, no history.
        self._search_stats: dict[str, Any] = {
            "requests_total": 0,
            "latency_samples_ms": [],
            "results_counts": [],
        }
        self._search_stats_lock = threading.Lock()
        self._processor_thread: threading.Thread | None = None
        self._processor_stop: threading.Event | None = None
        # P1-5/T3: CCR cleanup cycle counter — cleanup runs every
        # `ccr_cleanup_interval_sec` (tracked in wall-clock time), not
        # every processor cycle, to avoid scanning the cache table
        # every `interval_sec` (default 120s).
        self._ccr_cleanup_last_ts: float = 0.0

    @property
    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            self._embedder = create_embedding_provider(self.settings.embedding)
        return self._embedder

    def close(self) -> None:
        self.stop_background_processor()
        self.sqlite.close()
        self.vectors.close()

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

    @staticmethod
    def _scan_and_tag(tags: list[str], content: str) -> list[str]:
        """Run the secrets scanner on ``content`` and auto-add no-federate.

        Federation defence-in-depth (Layer 1, ArchCom 2026-07-17 §2.2.1):
        if :func:`detect_secrets` finds a secret pattern AND the tag
        ``mnemos:no-federate`` is not already in ``tags``, it is appended so
        the record is excluded from all external exchange (batch sync +
        pull). Idempotent — a re-scan with the same secret does not
        duplicate the tag. Only pattern names and counts are logged;
        raw matched values never enter the log.

        Non-fatal: a scanner error returns the tags unchanged so the
        caller's write is never blocked by the scanner (Layer 2
        background scanner will catch it later).

        Args:
            tags: current tags list (not mutated; a new list is returned).
            content: text to scan; empty/None → tags returned unchanged.

        Returns:
            The (possibly augmented) tags list.
        """
        if not content:
            return list(tags)
        result = list(tags)
        try:
            from mnemos.models import NO_FEDERATE_TAG
            from mnemos.secrets_detector import detect_secrets, findings_by_pattern

            findings = detect_secrets(content)
            if findings and NO_FEDERATE_TAG not in result:
                result.append(NO_FEDERATE_TAG)
                logger.info(
                    "auto-tagged record with mnemos:no-federate "
                    "(patterns: %s) — raw values not logged",
                    findings_by_pattern(findings),
                )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Secrets scanner failed (non-fatal): %s", exc)
        return result

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

        Federation defence-in-depth (Layer 1, ArchCom 2026-07-17 §2.2.1):
        the write-path secrets scanner runs on ``data.content`` before
        persistence. If a secret is detected AND the record does not
        already carry ``mnemos:no-federate``, the tag is auto-added so the
        record is excluded from all external exchange (batch sync + pull).
        Idempotent: a re-add with the same secret does not duplicate the
        tag (the check is "already present → skip"). Only pattern names
        and counts are logged; raw matched values never enter the log.
        """
        # ── Layer 1: write-path secrets scanner ───────────────────────────
        # Run before Memory construction so the tag is part of the persisted
        # record from the first write (no second UPDATE needed). Non-fatal:
        # a scanner error must NOT block the write — the memory is still
        # saved, just without the no-federate marker (Layer 2 background
        # scanner will catch it later).
        tags = self._scan_and_tag(list(data.tags), data.content)

        memory = Memory(
            content=data.content,
            title=data.title,
            tags=tags,
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

        # M10: auto-filter on ingest if enabled. Non-fatal: on failure the
        # memory is still saved with raw content (clean_content stays None).
        if self.settings.mnemos.auto_filter and memory.content:
            try:
                self.apply_context_filter(memory.id, profile=data.filter_profile)
                reloaded = self.sqlite.get(memory.id)
                if reloaded is not None:
                    memory = reloaded
            except Exception as exc:
                logger.warning("Auto-filter failed (non-fatal) for %s: %s", memory.id, exc)

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

        # ── Layer 1: write-path secrets scanner (update path) ──────────────
        # Re-run the scanner when the update payload includes new content so
        # the path-scoped re-ingest path (.instructions.md edited → update
        # with new content) does not bypass the scanner. Idempotent: if the
        # tag is already present, _scan_and_tag does not duplicate it.
        if "content" in update_kwargs:
            memory.tags = self._scan_and_tag(memory.tags, memory.content)

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
        include_raw: bool = False,
    ) -> list[SearchResult]:
        """Hybrid search: FTS5 + vector + Reciprocal Rank Fusion.

        Status filtering precedence:
          1. Explicit ``status`` — always wins (caller knows what they want).
          2. ``include_raw=True`` — all statuses EXCEPT ``archived`` are
             returned. ``archived`` means "intentionally hidden from normal
             search" and is excluded unless the caller passes
             ``status=MemoryStatus.ARCHIVED`` explicitly.
          3. Default (``include_raw=False``, no ``status``) — only
             ``published`` and ``processed`` memories surface, preserving the
             documented "Only searches 'published' knowledge units by default"
             contract.
        """
        alpha = hybrid_alpha if hybrid_alpha is not None else self.settings.search.hybrid_alpha
        _t0 = time.monotonic()

        # Resolve the status filter applied to the FTS leg.
        # fts_search treats status=None as "no filter", so we only pass a
        # value when an explicit status was given. The include_raw/default
        # gating is applied post-hoc below (fts_search does not accept a
        # list of allowed statuses, and widening its signature is out of
        # scope for this fix).
        fts_status = status  # explicit status always wins

        # ── FTS leg ────────────────────────────────────────────────────────
        fts_pairs: list[tuple[Memory, float]] = []
        try:
            fts_pairs = self.sqlite.fts_search(
                query,
                limit=limit * 2,
                project=project,
                agent=agent,
                status=fts_status,
            )
        except Exception as exc:
            logger.warning("FTS search failed: %s", exc)

        # Default gating: when no explicit status was requested, restrict
        # FTS hits to the allowed set. ``include_raw`` widens the set to all
        # statuses except ``archived`` (archived = intentionally hidden from
        # normal search). An explicit ``status`` skips this post-hoc filter
        # entirely — ``fts_search`` already filtered on it.
        if status is None and not include_raw:
            # Default: only published + processed
            allowed: set[MemoryStatus] | None = {
                MemoryStatus.PUBLISHED,
                MemoryStatus.PROCESSED,
            }
        elif status is None and include_raw:
            # include_raw: all except archived (archived = intentionally hidden)
            allowed = {
                MemoryStatus.RAW,
                MemoryStatus.PROCESSING,
                MemoryStatus.PROCESSED,
                MemoryStatus.PUBLISHED,
            }
        else:
            allowed = None  # explicit status, no post-hoc filter

        if allowed is not None:
            fts_pairs = [(m, s) for m, s in fts_pairs if m.status in allowed]

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
                    # Filter vector results by the same status policy as the
                    # FTS leg. The vector store only holds published memories
                    # in normal operation, but a non-published memory that
                    # somehow entered the store could surface here.
                    if status is not None and fetched.status != status:
                        continue
                    if allowed is not None and fetched.status not in allowed:
                        continue
                    id_to_memory[mid] = fetched

        # Search mode: "hybrid" when the vector leg actually contributed a
        # result that survived status filtering and is not already covered by
        # the FTS leg. Vector leg failure (embeddings down) degrades
        # gracefully — RRF still ranks FTS-only results, but callers can see
        # the mode. Tracking contribution (not just raw output) prevents
        # reporting "hybrid" when all vector pairs were filtered out.
        fts_ids = {m.id for m, _ in fts_pairs}
        vector_contributed = any(
            mid in id_to_memory and mid not in fts_ids for mid, _ in vector_pairs
        )
        search_type = "hybrid" if vector_contributed else "fts_only"

        # Apply tag filter post-hoc
        results: list[SearchResult] = []
        for mid, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            matched: Memory | None = id_to_memory.get(mid)
            if matched is None:
                continue
            if tags and not all(t in matched.tags for t in tags):
                continue
            results.append(SearchResult(memory=matched, score=score, search_type=search_type))
            if len(results) >= limit:
                break
        # Record search instrumentation (in-memory, resets on restart).
        latency_ms = (time.monotonic() - _t0) * 1000.0
        with self._search_stats_lock:
            self._search_stats["requests_total"] = int(self._search_stats["requests_total"]) + 1
            samples: list[float] = self._search_stats["latency_samples_ms"]
            samples.append(latency_ms)
            # Cap samples to avoid unbounded growth in long-running processes.
            if len(samples) > 1000:
                del samples[: len(samples) - 1000]
            counts: list[int] = self._search_stats["results_counts"]
            counts.append(len(results))
            if len(counts) > 1000:
                del counts[: len(counts) - 1000]
        return results

    def agent_recall(self, query: AgentRecallQuery) -> list[SearchResult]:
        """M3 — per-agent recall: recent entries + optional hybrid search.

        Agent recall is about "what has this agent stored", not "what is
        published knowledge" — so the query path passes ``include_raw=True``
        to surface recently-added entries regardless of pipeline status.
        The recency path (no query) already has no status filter.
        """
        if query.query:
            return self.search(
                query.query,
                agent=query.agent,
                project=query.project,
                limit=query.limit,
                include_raw=True,
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
        """Return most recent checkpoint memories for a project.

        When ``query`` is provided, a hybrid search scoped to
        ``mnemos:checkpoint`` tags is used to rank checkpoints by relevance,
        then the top ``limit`` are returned. When ``query`` is omitted,
        checkpoints are returned by recency only.
        """
        if query:
            results = self.search(
                query=query,
                tags=["mnemos:checkpoint"],
                project=project,
                limit=limit,
            )
            return [r.memory for r in results]

        memories = self.sqlite.list_all(
            limit=limit * 3,
            project=project,
            tags=["mnemos:checkpoint"],
        )
        # Sort by recency and trim
        memories.sort(key=lambda m: m.created_at, reverse=True)
        return memories[:limit]

    def list_recent(
        self,
        *,
        limit: int = 10,
        offset: int = 0,
        tags: list[str] | None = None,
        project: str | None = None,
        agent: str | None = None,
        status: MemoryStatus | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[Memory]:
        return self.sqlite.list_all(
            limit=limit,
            offset=offset,
            tags=tags,
            project=project,
            agent=agent,
            status=status,
            since=since,
            until=until,
        )

    def list_tags(self) -> dict[str, int]:
        return self.sqlite.get_all_tags()

    def remove_no_federate(
        self,
        memory_id: str,
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Remove the ``mnemos:no-federate`` tag from a record.

        Per ArchCom 2026-07-17 federation contract §4 КП-6, removing the
        tag re-enables external exchange for the record. Because the tag
        is typically auto-added by the Layer 1 secrets scanner when a
        secret was detected in the content, removing it blindly could
        expose a real secret to federation. This method therefore
        requires explicit confirmation (``confirm=True``).

        If ``confirm`` is False, the method returns a ``"requires_confirmation"``
        report WITHOUT mutating the record. The caller (CLI / HTTP / MCP)
        is responsible for surfacing the warning and re-calling with
        ``confirm=True`` after the user has acknowledged the risk.

        Re-scans the content after removal: if a secret is still present,
        the tag is re-added automatically and the report records
        ``"re_detected"=True``. The owner must redact the content first
        (see ``secrets_detector.redact_content``) before the tag can be
        permanently removed.
        """
        from mnemos.models import NO_FEDERATE_TAG
        from mnemos.secrets_detector import detect_secrets, findings_by_pattern

        report: dict[str, Any] = {
            "memory_id": memory_id,
            "removed": False,
            "re_detected": False,
            "requires_confirmation": not confirm,
            "patterns_present": {},
        }

        memory = self.sqlite.get(memory_id)
        if memory is None:
            report["error"] = f"Memory {memory_id} not found"
            return report

        if NO_FEDERATE_TAG not in memory.tags:
            report["note"] = "Record does not carry mnemos:no-federate"
            return report

        if not confirm:
            # Surface the risk without mutating.
            findings = detect_secrets(memory.content) if memory.content else []
            report["patterns_present"] = findings_by_pattern(findings) if findings else {}
            report["warning"] = (
                "Removing mnemos:no-federate re-enables external exchange. "
                "If a secret is still in the content, it will be exported. "
                "Re-call with confirm=True to proceed."
            )
            return report

        new_tags = [t for t in memory.tags if t != NO_FEDERATE_TAG]

        # Re-scan content: if a secret is still present, re-add the tag.
        re_detected_findings = detect_secrets(memory.content) if memory.content else []
        if re_detected_findings:
            new_tags.append(NO_FEDERATE_TAG)
            report["re_detected"] = True
            report["patterns_present"] = findings_by_pattern(re_detected_findings)
            report["warning"] = (
                "Secret still present in content — mnemos:no-federate re-added. "
                "Redact the content first (see secrets_detector.redact_content) "
                "to permanently remove the tag."
            )
        else:
            report["removed"] = True

        # Persist the new tag list (update_fields avoids INSERT OR REPLACE
        # FTS5 drift — see sqlite_store.update_fields).
        self.sqlite.update_fields(memory_id, tags=new_tags)
        return report

    def tags_rename(
        self,
        from_prefix: str,
        to_prefix: str,
        *,
        subtypes: list[str] | None = None,
        dry_run: bool = True,
        project: str | None = None,
        agent: str | None = None,
        invalid_subtypes_to_legacy: bool = False,
    ) -> dict[str, Any]:
        """Bulk rename tags matching ``from_prefix:<subtype>`` → ``to_prefix:<subtype>``.

        Replaces the unsafe ``mnemos migrate tags`` path (which used raw
        ``sqlite3`` writes and bypassed the FTS5 ``AFTER UPDATE`` trigger).
        This method goes through ``SQLiteStore.update_fields`` (a plain
        ``UPDATE``), so the FTS5 external-content index stays consistent —
        the ``memories_au`` trigger fires on UPDATE, exactly like
        ``tags_normalize``.

        Args:
            from_prefix: Source prefix without the subtype (e.g. ``"gcw:"``).
            to_prefix: Target prefix without the subtype (e.g. ``"mnemos:"``).
            subtypes: Optional whitelist — only rename these subtypes.
                ``None`` means "all subtypes present on matching tags".
            dry_run: When ``True`` (default) nothing is written; the report
                describes what *would* happen. When ``False`` the rename is
                applied via ``update_fields``.
            project: Scope the scan to a single project slug (pre-filters
                via ``list_all(project=...)`` to reduce rows inspected).
            agent: Scope the scan to a single agent slug.
            invalid_subtypes_to_legacy: When ``False`` (default) a tag whose
                subtype is not in ``MNEMOS_TAG_SUBTYPES`` is skipped and
                counted in ``skipped_invalid``. When ``True`` it is renamed
                to ``<to_prefix>legacy`` instead.

        Returns:
            ``{"scanned": N, "renamed": N, "skipped_invalid": N,
            "errors": [...]}``. In dry-run mode ``renamed`` reflects what
            *would* be renamed; nothing is written.

        Idempotency:
            A second run with the same arguments returns ``renamed=0``
            because the ``from_prefix:`` tags no longer exist.

        Vector store:
            The vector index is keyed by ``memory_id`` (see
            ``VectorStore.upsert``) and the embedded text is derived from
            ``title + content + tags`` (see ``_embedding_text``). Tags ARE
            part of the embedded text, so renaming tags *technically*
            changes the embedding input. However, re-embedding on every
            tag rename is expensive and the tag contribution to semantic
            similarity is small relative to content. We deliberately do
            NOT re-embed here — semantic search continues to work because
            the stored vectors still point to the same memory ids and the
            FTS5 leg (which DOES reflect the new tags via the AFTER UPDATE
            trigger) carries tag-filtered queries. If exact tag-vector
            alignment is required, run ``mnemos reindex`` afterwards.
        """
        from mnemos.models import MNEMOS_TAG_SUBTYPES, validate_tag_contract
        from mnemos.traces import TraceRecorder

        report: dict[str, Any] = {
            "scanned": 0,
            "renamed": 0,
            "skipped_invalid": 0,
            "errors": [],
            "dry_run": dry_run,
            "from_prefix": from_prefix,
            "to_prefix": to_prefix,
        }

        # Validate prefix shapes early — must end with ":" so we don't
        # accidentally match `project:` when the caller means `gcw:`.
        if not from_prefix.endswith(":") or not to_prefix.endswith(":"):
            report["errors"].append("prefixes must end with ':' (e.g. 'gcw:', 'mnemos:')")
            return report

        subtype_filter = set(subtypes) if subtypes else None
        page_size = 500
        offset = 0
        # Trace the rename as a single audit row (one per call, not per row).
        recorder = TraceRecorder(store=self.sqlite)

        while True:
            batch = self.sqlite.list_all(
                limit=page_size, offset=offset, project=project, agent=agent
            )
            if not batch:
                break
            offset += len(batch)

            for mem in batch:
                report["scanned"] += 1
                new_tags: list[str] = []
                modified = False
                for tag in mem.tags:
                    if tag.startswith(from_prefix):
                        subtype = tag[len(from_prefix) :]
                        # Apply optional subtype whitelist.
                        if subtype_filter is not None and subtype not in subtype_filter:
                            new_tags.append(tag)
                            continue
                        # Decide target subtype.
                        if subtype in MNEMOS_TAG_SUBTYPES:
                            target = to_prefix + subtype
                        elif invalid_subtypes_to_legacy:
                            target = to_prefix + "legacy"
                        else:
                            report["skipped_invalid"] += 1
                            new_tags.append(tag)
                            continue
                        new_tags.append(target)
                        if target != tag:
                            modified = True
                    else:
                        new_tags.append(tag)

                if not modified:
                    continue

                # Re-validate the resulting tag set — must pass the contract.
                try:
                    validate_tag_contract(new_tags, strict=False)
                except Exception as exc:  # report, don't crash the batch
                    report["errors"].append(f"{mem.id}: {exc}")
                    continue

                report["renamed"] += 1
                if dry_run:
                    continue

                # Re-derive denormalised project/agent from the new tag set.
                # For gcw:→mnemos: these are unchanged (project:/agent: are
                # not prefixed by gcw:), but the method is generic — a
                # prefix change that touched project:/agent: must update the
                # denormalised columns too, otherwise per-project / per-agent
                # queries drift from the tags.
                new_project = next(
                    (t[len("project:") :] for t in new_tags if t.startswith("project:")),
                    mem.project,
                )
                new_agent = next(
                    (t[len("agent:") :] for t in new_tags if t.startswith("agent:")),
                    mem.agent,
                )
                try:
                    self.sqlite.update_fields(
                        mem.id,
                        tags=new_tags,
                        project=new_project,
                        agent=new_agent,
                    )
                except Exception as exc:  # record, continue batch
                    report["errors"].append(f"{mem.id}: {exc}")

        # Audit trail — one trace row per rename call.
        with recorder.record(
            task_label="tags_rename",
            project=project or "*",
            step="tags_rename",
        ) as trace:
            trace.rationale_summary = (
                f"{from_prefix}→{to_prefix} dry_run={dry_run} "
                f"renamed={report['renamed']} skipped={report['skipped_invalid']}"
            )[:200]

        return report

    def search_stats(self) -> dict[str, Any]:
        """Return in-memory search instrumentation (resets on restart)."""
        with self._search_stats_lock:
            samples: list[float] = list(self._search_stats["latency_samples_ms"])
            counts: list[int] = list(self._search_stats["results_counts"])
        avg_latency_ms = round(sum(samples) / len(samples), 2) if samples else 0.0
        avg_results = round(sum(counts) / len(counts), 2) if counts else 0.0
        return {
            "requests_total": int(self._search_stats["requests_total"]),
            "avg_latency_ms": avg_latency_ms,
            "avg_results": avg_results,
        }

    def dashboard_stats(self) -> dict[str, Any]:
        """Structured JSON for the mnemos-eyes dashboard.

        Aggregates volume, filter, pipeline, search, vectors, sessions.
        """
        by_status = self.sqlite.count_by_status()
        filter_stats = self.sqlite.get_filter_stats()
        s_stats = self.search_stats()
        sessions = self.sqlite.count_sessions()
        # Pipeline counts derived from status + DLQ.
        processed_total = int(by_status.get("processed", 0)) + int(by_status.get("published", 0))
        return {
            "version": __version__,
            "timestamp": datetime.now(UTC).isoformat(),
            "volume": {
                "memories_total": self.sqlite.count(),
                "by_status": by_status,
                "by_project": self.sqlite.get_project_memory_counts(),
                "by_agent": self.sqlite.count_by_agent(),
                "by_type": self.sqlite.count_by_type(),
            },
            "filter": {
                "auto_filter": self.settings.mnemos.auto_filter,
                "filtered_total": filter_stats["filtered"],
                "unfiltered_total": filter_stats["unfiltered"],
                "avg_reduction_pct": filter_stats["avg_reduction_pct"],
                "by_profile": filter_stats["by_profile"],
            },
            "pipeline": {
                "processed_total": processed_total,
                "failed_total": self.sqlite.dlq_count(),
                "dlq_depth": self.sqlite.dlq_count(),
                "last_run": None,
            },
            "search": {
                "requests_total": s_stats["requests_total"],
                "avg_latency_ms": s_stats["avg_latency_ms"],
                "avg_results": s_stats["avg_results"],
            },
            "vectors": {
                "indexed_total": self.vectors.count(),
            },
            "sessions": {
                "active": sessions["active"],
                "total": sessions["total"],
            },
        }

    def timeseries(
        self,
        *,
        metric: str = "memories_added",
        days: int = 30,
        granularity: str = "day",
    ) -> dict[str, Any]:
        """Temporal data for dashboard charts.

        Currently supports ``memories_added`` (daily counts from SQLite).
        Other metrics return an empty series with a note.
        """
        if metric == "memories_added":
            points = self.sqlite.count_by_date(days=days, granularity=granularity)
        else:
            points = []
        return {
            "granularity": granularity,
            "range": f"{days}d",
            "series": [
                {
                    "metric": metric,
                    "points": points,
                }
            ],
        }

    def stats(self) -> dict[str, Any]:
        by_status = self.sqlite.count_by_status()
        filter_stats = self.sqlite.get_filter_stats()
        vector_count = self.vectors.count()
        published_count = int(by_status.get("published", 0))
        # Degraded: published memories exist but none are embedded —
        # vector search is silently unavailable, search degrades to FTS-only.
        degraded = vector_count == 0 and published_count > 0
        queue_depth = int(by_status.get("raw", 0)) + int(by_status.get("processing", 0))
        return {
            "status": "ok",
            "version": __version__,
            "data_dir": str(self.settings.mnemos.data_dir),
            "vault_path": str(self.settings.mnemos.vault_path),
            "total": self.sqlite.count(),
            "by_status": by_status,
            "vectors": vector_count,
            "projects": self.sqlite.get_project_memory_counts(),
            "filter": {
                "auto_filter": self.settings.mnemos.auto_filter,
                "filtered_count": filter_stats["filtered"],
                "unfiltered_count": filter_stats["unfiltered"],
                "avg_reduction_pct": filter_stats["avg_reduction_pct"],
                "by_profile": filter_stats["by_profile"],
            },
            "embedding_status": {
                "provider": self.settings.embedding.provider,
                "vectors_indexed": vector_count,
                "degraded": degraded,
            },
            "processor": {
                "queue_depth": queue_depth,
                # Pipeline runs record their finish time in the meta table;
                # None means the pipeline has never run yet.
                "last_processed_at": self.sqlite.get_meta("pipeline_last_run"),
            },
            "search_health": {
                "fts_available": True,  # FTS5 is always available (SQLite built-in)
                "vector_available": vector_count > 0,
                "mode": "hybrid" if vector_count > 0 else "fts_only",
                # Orphaned vectors: embeddings exist but no published memories
                # — indicates the vector store drifted out of sync with SQLite
                # (e.g. memories were deleted but vectors were not removed).
                "orphaned_vectors": vector_count > 0 and published_count == 0,
            },
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

        # Use targeted UPDATE (not save()/INSERT OR REPLACE) to avoid
        # changing the rowid — FTS5 external-content tables lose sync
        # when INSERT OR REPLACE fires delete+insert triggers.
        # updated_at is set automatically by update_fields().
        self.sqlite.update_fields(
            memory.id,
            clean_content=memory.clean_content,
            filter_profile=memory.filter_profile,
            filter_stats=memory.filter_stats,
            filter_version=memory.filter_version,
        )

        return {
            "status": "ok",
            "memory_id": memory_id,
            "clean_content": result["clean_content"],
            "filter_profile": result["profile"],
            "stats": result["stats"],
        }

    def filter_all(
        self,
        *,
        profile: str | None = None,
        budget: int | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Re-apply the context filter to all (or a batch of) memories.

        Used by `mnemos filter --all`. Iterates memories in batches via
        ``sqlite.list_all`` and calls ``apply_context_filter`` on each.
        Failures on individual memories are non-fatal and counted.

        Returns aggregate stats: total, filtered, failed, skipped.
        """
        total = self.sqlite.count()
        offset = 0
        filtered = 0
        failed = 0
        skipped = 0
        seen = 0
        while seen < total:
            batch = self.sqlite.list_all(limit=limit, offset=offset)
            if not batch:
                break
            for memory in batch:
                seen += 1
                if not (memory.raw_content or memory.content):
                    skipped += 1
                    continue
                try:
                    result = self.apply_context_filter(memory.id, profile=profile, budget=budget)
                    if result.get("status") == "ok":
                        filtered += 1
                    else:
                        failed += 1
                except Exception as exc:
                    logger.warning("filter_all: failed %s: %s", memory.id, exc)
                    failed += 1
            offset += len(batch)
            if len(batch) < limit:
                break
        return {
            "status": "ok",
            "total": total,
            "filtered": filtered,
            "failed": failed,
            "skipped": skipped,
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
        """Fetch a URL, extract main text, save as RAW memory.

        Redirects (3xx) are followed manually with a hard cap of ``_MAX_REDIRECTS``
        hops. Every redirect target is passed through ``_validate_url`` before
        the next request is issued (per-hop SSRF guard, v2). This closes the
        open-redirect pivot where a public host returns 30x to an internal or
        metadata endpoint that would otherwise bypass the initial URL check.

        ``httpx.Client(follow_redirects=False)`` is retained so the library
        never follows a redirect without our guard running first.

        SSRF rejections (``ValueError`` from ``_validate_url``) are re-raised
        as hard errors — the blocked URL is NOT stored in memory. Only
        network/operational errors (connection, timeout, HTTP error status,
        too-many-redirects, redirect-loop) degrade to a placeholder.
        """
        # Initial SSRF validation — reject before any fetch attempt.
        # A ValueError here must NOT be swallowed into placeholder content.
        self._validate_url(url)

        try:
            from urllib.parse import urljoin

            import httpx
            import trafilatura

            # Per-hop SSRF re-validation (v2): follow redirects manually so
            # every Location target is checked by _validate_url before the
            # next request is issued. follow_redirects=False on the client
            # ensures httpx never silently skips the guard.
            current_url = url
            visited: set[str] = set()
            redirects = 0

            with httpx.Client(follow_redirects=False) as client:
                resp = client.get(current_url, timeout=30)
                visited.add(current_url)

                while resp.status_code in {301, 302, 303, 307, 308}:
                    redirects += 1
                    if redirects > _MAX_REDIRECTS:
                        raise ValueError(
                            f"Too many redirects fetching {url} (max {_MAX_REDIRECTS})"
                        )
                    location = resp.headers.get("location", "")
                    if not location:
                        raise ValueError(f"Redirect from {current_url} missing Location header")
                    next_url = urljoin(current_url, location)
                    # Core per-hop guard: validate the redirect target BEFORE
                    # following. Catches the pivot: public host -> 169.254.x
                    # or any private/loopback/metadata endpoint. A ValueError
                    # here is an SSRF rejection — wrap it so the outer
                    # ``except Exception`` does not swallow it into a placeholder.
                    try:
                        self._validate_url(next_url)
                    except ValueError as exc:
                        raise _SSRFRejectionError(exc) from exc
                    if next_url in visited:
                        raise ValueError(f"Redirect loop detected at {next_url}")
                    visited.add(next_url)
                    current_url = next_url
                    resp = client.get(current_url, timeout=30)

            resp.raise_for_status()
            content = trafilatura.extract(resp.text) or resp.text[:4000]
        except _SSRFRejectionError as exc:
            # SSRF guard rejected a URL — do NOT store it in memory.
            raise ValueError(f"URL rejected for security reasons: {exc.original}") from exc.original
        except Exception as exc:
            logger.warning("URL fetch failed: %s - using placeholder", exc)
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

        Single-memory passthrough: memories that don't form a cluster
        (min_cluster_size=2) are promoted individually via a lightweight
        synthesis so they still reach published + vector index. This
        prevents the queue from growing unbounded when most memories are
        unique (P0-1 fix).

        Returns a summary dict for observability / CLI output.
        """
        clusters = self.cluster(project=project, agent=agent, limit=limit, **kwargs)
        synthesized: list[SynthesisResult] = []
        published: list[PublishResult] = []
        failed_qg: list[QualityResult] = []

        # Track which raw memory ids were consumed by clustering
        clustered_ids: set[str] = set()
        for cr in clusters:
            clustered_ids.update(cr.memory_ids)

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

        # Single-memory passthrough: promote raw memories that were NOT
        # consumed by any cluster. Each becomes its own "synthesis" with
        # quality_score=0.5 (placeholder) so it can pass the gate and reach
        # published + vector index.
        single_promoted = 0
        raw_remaining = self.sqlite.list_all(
            limit=limit,
            status=MemoryStatus.RAW,
            project=project,
            agent=agent,
        )
        for mem in raw_remaining:
            if mem.id in clustered_ids:
                continue
            promoted = self._promote_single_memory(mem.id)
            if promoted is not None and promoted.published:
                published.append(promoted)
                single_promoted += 1

        # Also rescue memories stuck in "processing" status (clustered but
        # never synthesized — e.g. prior runs crashed mid-pipeline). These
        # are promoted directly to published since their cluster may be
        # orphaned and re-synthesizing would create a duplicate draft.
        stuck_processing = self.sqlite.list_all(
            limit=limit,
            status=MemoryStatus.PROCESSING,
            project=project,
            agent=agent,
        )
        stuck_rescued = 0
        for mem in stuck_processing:
            rescued = self._promote_single_memory(mem.id, from_status=MemoryStatus.PROCESSING)
            if rescued is not None and rescued.published:
                published.append(rescued)
                stuck_rescued += 1

        # Record when the pipeline last finished so stats() / dashboards can
        # detect a stuck pipeline (queue growing but last_processed_at stale).
        self.sqlite.set_meta("pipeline_last_run", datetime.now(UTC).isoformat())

        return {
            "clusters": len(clusters),
            "synthesized": len(synthesized),
            "published": len(published),
            "failed_quality_gate": len(failed_qg),
            "single_promoted": single_promoted,
            "stuck_rescued": stuck_rescued,
            "published_ids": [p.memory_id for p in published],
        }

    def _promote_single_memory(
        self,
        memory_id: str,
        *,
        from_status: MemoryStatus = MemoryStatus.RAW,
    ) -> PublishResult | None:
        """Promote a single memory directly to published.

        Used when a memory doesn't form a cluster (single-memory passthrough)
        or is stuck in processing. The memory is transitioned to processed
        with placeholder quality scores, then published + vector-indexed.

        This is the graceful fallback for when no real LLM synthesis is
        configured — the memory's own content becomes the "synthesized"
        article (P0-1 fix).
        """
        memory = self.sqlite.get(memory_id)
        if memory is None:
            return None
        if memory.status not in (from_status, MemoryStatus.PROCESSED):
            return None

        # If already processed, just publish
        if memory.status != MemoryStatus.PROCESSED:
            memory.status = MemoryStatus.PROCESSED
            memory.quality_score = 0.5
            memory.confidence = 0.5
            memory.source_coverage = 1
            memory.updated_at = datetime.now(UTC)
            self.sqlite.save(memory)

        return self.publish(memory.id, skip_quality_check=True)

    def rebuild_vector_index(self, *, batch_size: int = 100) -> dict[str, Any]:
        """Rebuild the vector index for all published memories.

        Re-embeds every published memory and upserts into the vector store.
        Used when the embedding pipeline was broken and vectors are missing
        (P0-2 fix). Idempotent: safe to run repeatedly.
        """
        published = self.sqlite.list_all(
            limit=10000,
            status=MemoryStatus.PUBLISHED,
        )
        indexed = 0
        failed = 0
        for i in range(0, len(published), batch_size):
            batch = published[i : i + batch_size]
            for mem in batch:
                try:
                    emb = self.embedder.embed(self._embedding_text(mem))
                    self.vectors.upsert(
                        mem.id,
                        emb,
                        {"project": mem.project, "agent": mem.agent},
                    )
                    indexed += 1
                except Exception as exc:
                    logger.warning("rebuild_vector_index: failed for %s: %s", mem.id[:8], exc)
                    failed += 1

        logger.info(
            "rebuild_vector_index: indexed=%d failed=%d total=%d",
            indexed,
            failed,
            len(published),
        )
        return {
            "total": len(published),
            "indexed": indexed,
            "failed": failed,
        }

    # ── Background processor ──────────────────────────────────────────────

    def start_background_processor(self, interval_sec: int = 120) -> None:
        """Start a background thread that periodically runs the pipeline.

        The processor drains the raw/processing queue by running
        cluster → synthesize → quality_gate → publish.  It runs every
        ``interval_sec`` seconds (default 120 = 2 min).

        The default was 300s (5 min) which was too slow to keep up with
        ingest rate, causing the queue to grow unbounded (P0-1 fix).

        Safe to call multiple times — if already running, does nothing.
        """
        if self._processor_thread is not None:
            return
        self._processor_stop = threading.Event()
        self._processor_thread = threading.Thread(
            target=self._processor_loop,
            args=(interval_sec,),
            daemon=True,
            name="mnemos-processor",
        )
        self._processor_thread.start()
        logger.info("Background processor started (interval=%ds)", interval_sec)

    def stop_background_processor(self) -> None:
        """Stop the background processor thread."""
        if self._processor_thread is None or self._processor_stop is None:
            return
        self._processor_stop.set()
        self._processor_thread.join(timeout=10)
        self._processor_thread = None
        self._processor_stop = None
        logger.info("Background processor stopped")

    def _processor_loop(self, interval_sec: int) -> None:
        """Background loop: run pipeline + CCR cleanup periodically.

        Processes in batches of up to 200 memories per cycle to drain
        large backlogs faster (P0-1 fix). The previous default limit=100
        per cycle was insufficient when ingest rate exceeded processing
        rate.

        CCR cleanup (T3): TTL expiry + LRU eviction runs on its own
        interval (``ccr_cleanup_interval_sec``, default 1200s = 20 min)
        — not every cycle — to avoid scanning the cache table every
        ``interval_sec``. Cleanup is guarded by ``ccr.enabled`` and
        wrapped in a try/except so a cleanup failure never crashes the
        processor loop.
        """
        if self._processor_stop is None:
            return
        while not self._processor_stop.is_set():
            try:
                stats = self.stats()
                queue_depth = stats.get("processor", {}).get("queue_depth", 0)
                if queue_depth > 0:
                    logger.info(
                        "Processor: queue_depth=%d, running pipeline (batch=200)",
                        queue_depth,
                    )
                    result = self.run_pipeline(limit=200)
                    logger.info(
                        "Processor: cycle done — published=%d single=%d stuck=%d",
                        result.get("published", 0),
                        result.get("single_promoted", 0),
                        result.get("stuck_rescued", 0),
                    )
                # CCR cleanup tick — runs on its own interval, not every cycle.
                self._maybe_run_ccr_cleanup()
            except Exception:
                logger.exception("Background processor error")
            self._processor_stop.wait(timeout=interval_sec)

    def _maybe_run_ccr_cleanup(self) -> None:
        """Run CCR TTL/LRU cleanup if enough wall-clock time has elapsed.

        Guarded by ``settings.ccr.enabled``. Exceptions are caught and
        logged so the processor loop never crashes on a cleanup failure.
        """
        if not self.settings.ccr.enabled:
            return
        interval = self.settings.ccr.ccr_cleanup_interval_sec
        now = time.monotonic()
        if self._ccr_cleanup_last_ts and (now - self._ccr_cleanup_last_ts) < interval:
            return
        self._ccr_cleanup_last_ts = now
        try:
            result = self.ccr_cleanup()
            if result["ttl_deleted"] or result["lru_evicted"]:
                logger.info(
                    "CCR cleanup: ttl_deleted=%d lru_evicted=%d",
                    result["ttl_deleted"],
                    result["lru_evicted"],
                )
        except Exception:
            logger.exception("CCR cleanup failed (non-fatal)")

    @property
    def processor_running(self) -> bool:
        """Whether the background processor thread is active."""
        return self._processor_thread is not None and self._processor_thread.is_alive()

    # ── CacheAligner (P1-5) ────────────────────────────────────────────────

    def align_prefix(self, text: str, *, profile: str | None = None) -> dict[str, Any]:
        """Relocate dynamic content to the end of ``text`` for prefix stability.

        Wraps :func:`mnemos.cache_aligner.align`. When CacheAligner is
        disabled in config, the text is returned unchanged with an empty
        extracted list.

        Per-kind toggles on ``CacheAlignerConfig`` (``extract_timestamps``,
        ``extract_uuids``, ``extract_session_ids``, ``extract_dates``,
        ``extract_tokens``) are honoured: a kind whose toggle is ``False``
        is added to the skip set and stays in-place. These toggles merge
        (union) with the profile's own skip set — disabling a kind in
        config widens what a profile already skips.

        Args:
            text: System-prompt-like text to stabilize.
            profile: Optional filter profile (``"code"``, ``"docs"``)
                that toggles which dynamic kinds are extracted.

        Returns:
            ``{"aligned_text","extracted","prefix_stabilized","moved_chars"}``.
        """
        from mnemos.cache_aligner import align

        if not self.settings.cache_aligner.enabled:
            return {
                "aligned_text": text,
                "extracted": [],
                "prefix_stabilized": False,
                "moved_chars": 0,
            }
        cfg = self.settings.cache_aligner
        skip_kinds: set[str] = set()
        if not cfg.extract_timestamps:
            skip_kinds.add("timestamp")
        if not cfg.extract_uuids:
            skip_kinds.add("uuid")
        if not cfg.extract_session_ids:
            skip_kinds.add("session_id")
        if not cfg.extract_dates:
            skip_kinds.add("date")
        if not cfg.extract_tokens:
            skip_kinds.add("token")
        return align(text, profile=profile, skip_kinds=skip_kinds or None)

    # ── CCR (P1-4) ─────────────────────────────────────────────────────────

    def compress_content(
        self,
        text: str,
        *,
        profile: str | None = None,
        project: str = "",
    ) -> dict[str, Any]:
        """Compress ``text`` via CCR and cache the original in SQLite.

        Returns the CCR result dict (see ``mnemos.ccr.compress``).
        """
        from mnemos.ccr import compress

        if not self.settings.ccr.enabled:
            return {
                "compressed_text": text,
                "hash": "",
                "original_size": len(text),
                "compressed_size": len(text),
                "reduction_pct": 0.0,
                "marker": "",
                "cached": False,
                "profile": "disabled",
            }
        return compress(
            text,
            store=self.sqlite,
            config=self.settings.ccr,
            profile=profile,
            project=project,
        )

    def retrieve_content(
        self,
        h: str,
        *,
        query: str | None = None,
        snippet_count: int | None = None,
    ) -> dict[str, Any]:
        """Retrieve a CCR-cached original (or FTS5 snippets if ``query``)."""
        from mnemos.ccr import retrieve

        return retrieve(
            h,
            store=self.sqlite,
            config=self.settings.ccr,
            query=query,
            snippet_count=snippet_count,
        )

    def ccr_cleanup(self) -> dict[str, int]:
        """Run CCR TTL expiry + LRU eviction. Returns removal counts."""
        from mnemos.ccr import cleanup

        return cleanup(store=self.sqlite, config=self.settings.ccr)

    def ccr_stats(self) -> dict[str, Any]:
        """Return CCR cache statistics."""
        return {
            "enabled": self.settings.ccr.enabled,
            "entries": self.sqlite.ccr_count(),
            "ttl_days": self.settings.ccr.ttl_days,
            "max_entries": self.settings.ccr.max_entries,
            "min_size_chars": self.settings.ccr.min_size_chars,
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
