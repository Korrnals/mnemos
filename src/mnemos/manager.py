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
import uuid
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from mnemos import __version__
from mnemos.config import Settings
from mnemos.embeddings import EmbeddingProvider, create_embedding_provider
from mnemos.models import (
    CONTEXT_ADMISSIBLE_STATUSES,
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
from mnemos.storage.sqlite_store import (
    FTS_SNIPPET_ELLIPSIS,
    FTS_SNIPPET_END_MARK,
    FTS_SNIPPET_START_MARK,
    SQLiteStore,
)
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


def _lock_is_stale(locked_at_iso: str, threshold_hours: int) -> bool:
    """Return True if a workflow lock is older than ``threshold_hours``.

    Used by guardrail 2 (stale-lock auto-release). Tolerant of malformed
    timestamps — a value that fails to parse is treated as NOT stale so a
    corrupt ``locked_at`` cannot enable a silent takeover.
    """
    try:
        locked_at = datetime.fromisoformat(locked_at_iso)
    except ValueError:
        return False
    if locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=UTC)
    age = datetime.now(UTC) - locked_at
    return age > timedelta(hours=threshold_hours)


# ADR-0018 P1-b (m2) — artificial insertions FTS5's snippet() places in a
# snippet: highlight markers around query-matched tokens and the ellipsis
# between non-contiguous fragments. All three can split a multi-token
# secret (e.g. a JWT whose payload segment matched the query), so the
# issuance scan runs on a copy with them removed. Sourced from the
# store's own FTS_SNIPPET_* constants (single source of truth).
_SNIPPET_STRIP_MARKS: Final[tuple[str, ...]] = (
    FTS_SNIPPET_START_MARK,
    FTS_SNIPPET_END_MARK,
    FTS_SNIPPET_ELLIPSIS,
)


def _snippet_scan_text(snippet: str) -> str:
    """Return a copy of an FTS5 snippet stripped of highlight markers.

    ``ccr_search`` snippets wrap query-matched tokens in ``>>>``/``<<<``
    and join non-contiguous fragments with ``' ... '``; those insertions
    break multi-token secret patterns, so ``detect_secrets`` on the raw
    snippet misses them. The stripped copy is used for DETECTION only —
    its character offsets do not map back to the marked snippet (every
    span after the first marker shifts), so callers must not use finding
    offsets to redact the marked text (see ``retrieve_content``).
    """
    for mark in _SNIPPET_STRIP_MARKS:
        snippet = snippet.replace(mark, "")
    return snippet


@dataclass(frozen=True, slots=True)
class IssuanceScan:
    """Outcome of an issuance-boundary secret scan (ADR-0018 P1-b M1).

    ``text`` is the string safe to issue: the redacted copy when
    secrets were found (and refuse mode is off), the input unchanged
    when clean, and ``""`` when ``refused`` — a refused outcome NEVER
    carries content, so callers branch on ``refused`` first.
    """

    text: str
    refused: bool
    # Machine-readable refusal cause: "secret detected" | "scanner error" | None.
    reason: str | None
    redactions: int
    redacted_patterns: dict[str, int]


@dataclass(frozen=True, slots=True)
class IssuanceItemScan:
    """Per-item outcome for ALL strings one result item echoes (P1-b F1).

    Security-review round F1: ``auto_title()`` derives from the first
    line of raw content (or echoes an explicitly-set title) and was
    returned unscanned next to redacted content — the title is now
    scanned as part of the same item. ``content``/``title`` are the
    strings safe to issue (``""`` when refused or when the item echoes
    no such field); ``redactions``/``redacted_patterns`` are the MERGED
    counts across both fields.
    """

    content: str
    title: str
    refused: bool
    reason: str | None
    redactions: int
    redacted_patterns: dict[str, int]


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
        # ADR-0017 D1 (#125) — bounded registry for assemble_context
        # mode="async" results (handle -> (ContextBlock dict, session)).
        # In-memory, single-tenant, capped by
        # mnemos.assemble.ASYNC_REGISTRY_CAP with oldest-first eviction;
        # entries are session-bound (review F1, CWE-863 — only the
        # assembling session may redeem a handle); a restart drops
        # pending handles (the harness re-asks — async is a latency
        # optimization, not storage).
        self._assemble_async: dict[str, tuple[dict[str, Any], str]] = {}
        self._assemble_async_lock: threading.Lock = threading.Lock()

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

    def embed_for(self, memory: Memory) -> list[float]:
        """Public helper — embed a memory into a vector.

        Thin wrapper over :meth:`_embedding_text` + :attr:`embedder` so
        callers (e.g. ``cli.sync.run_sync_import``) do not reach into
        the private ``_embedding_text`` symbol. A future refactor of
        the embedding-text shape only needs to update this method, not
        every call site.
        """
        return self.embedder.embed(self._embedding_text(memory))

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
        trusted_rewrite_provenance: bool = False,
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

        ``trusted_rewrite_provenance`` (C10 review round) is set ONLY by
        the rewrite-event path (``context_rewrite``): it lets
        ``SQLiteStore.save`` derive the denormalised
        ``rewrite_source``/``rewrite_session`` quota columns from
        ``data.metadata``. The generic create surfaces (REST/MCP/CLI)
        keep the default ``False`` — client-controlled metadata must
        never mint rewrite quota counters.
        """
        # ── Layer 1: write-path secrets scanner ───────────────────────────
        # Run before Memory construction so the tag is part of the persisted
        # record from the first write (no second UPDATE needed). Non-fatal:
        # a scanner error must NOT block the write — the memory is still
        # saved, just without the no-federate marker (Layer 2 background
        # scanner will catch it later).
        tags = self._scan_and_tag(list(data.tags), data.content)

        # ADR-0017 D1 / #125 ArchCom addendum 1 — contentType metadata
        # captured at ingest: detect_profile runs once here and the binary
        # partition ("code" vs everything-else "prose") is persisted in
        # metadata so assemble_context(mode=code|prose) filters recall
        # candidates by the stored value instead of re-detecting on the
        # hot path. Non-fatal: on failure the memory is saved without the
        # key and recall falls back to on-the-fly detection (documented in
        # mnemos.assemble).
        ingest_metadata: dict[str, Any] = dict(data.metadata)
        try:
            from mnemos.filter.pipeline import detect_profile

            ingest_metadata["content_type"] = (
                "code" if detect_profile(data.content) == "code" else "prose"
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("content_type ingest capture failed (non-fatal): %s", exc)

        memory = Memory(
            content=data.content,
            title=data.title,
            tags=tags,
            source=data.source,
            source_url=data.source_url,
            memory_type=data.memory_type,
            metadata=ingest_metadata,
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

        # Persist to SQLite (trusted_rewrite_provenance gates the C10
        # rewrite-column derivation — see the add docstring).
        self.sqlite.save(memory, trusted_rewrite_provenance=trusted_rewrite_provenance)

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

    # ── Workflow lifecycle (mnemos #96) ────────────────────────────────────
    #
    # Server-side enforcement of the workflow state machine. The MCP tool
    # (mnemos_workflow) and the REST endpoints (/memories/{id}/workflow) are
    # thin wrappers over these three methods — the validation MUST live here
    # so no caller can bypass the state machine or the 5 guardrails:
    #   1. Audit log       — every transition recorded in memory_workflow_history
    #   2. Stale-lock      — a lock older than the threshold auto-releases
    #   3. Idempotent      — to == current is a no-op (skip, not error)
    #   4. Force-unlock    — force=true overrides another actor's lock
    #   5. Rate limit      — max N transitions per memory per minute
    #
    # Lock model: a lock (locked_by + locked_at) is acquired on transition
    # to in-progress, persists through blocked/resolved (same actor still
    # owns the work), and is released on transition to open/done/withdrawn.
    # A different actor must either wait for stale-lock release or use force.

    def workflow_set(
        self,
        memory_id: str,
        to: str,
        *,
        actor: str,
        reason: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        """Transition a memory's workflow status, enforcing the state machine.

        Args:
            memory_id: Target memory.
            to: Target ``WorkflowStatus`` value (validated against the enum).
            actor: Free-form actor id (Phase 1 weak identity — NO authn/authz).
            reason: Optional human-readable reason; **required when
                ``force=True``** (guardrail 4).
            force: When True, override a lock held by another actor
                (guardrail 4). ``force_used=1`` is recorded in the audit log.

        Returns:
            Result dict describing the transition (see ``_workflow_result``).

        Raises:
            ValueError: On any guardrail violation (unknown status, locked by
                another actor without force, force without reason, rate-limit
                exceeded) or when the memory does not exist (re-checked at the
                current-state read to close the get()/get_workflow_status()
                TOCTOU window). The caller (MCP / REST) maps these to the
                appropriate client error.

        Audit note:
            Rejected transitions (forbidden edge, lock conflict, rate-limit,
            force-without-reason) write **no** audit row — the audit log
            records state changes, not attempts. Only recorded transitions
            (and the idempotent no-op short-circuit, which also writes
            nothing) touch the history table.
        """
        from mnemos.workflow import WorkflowStatus, validate_transition

        # ── Input validation ──────────────────────────────────────────────
        if not actor or not actor.strip():
            raise ValueError("actor is required (Phase 1 weak identity — free-form string)")
        actor = actor.strip()
        try:
            to_status = WorkflowStatus(to)
        except ValueError as exc:
            valid = sorted(s.value for s in WorkflowStatus)
            raise ValueError(f"invalid workflow status {to!r}. Valid: {valid}") from exc

        if force and not (reason and reason.strip()):
            raise ValueError("reason is required when force=True (guardrail 4: force-unlock)")

        # ── Memory existence ──────────────────────────────────────────────
        if self.sqlite.get(memory_id) is None:
            raise ValueError(f"memory {memory_id!r} not found")

        # ── Guardrail 5: rate limit ───────────────────────────────────────
        rate_limit = self.settings.mnemos.workflow_rate_limit_per_minute
        minute_ago = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        recent = self.sqlite.count_workflow_transitions_since(memory_id, minute_ago)
        if recent >= rate_limit:
            raise ValueError(
                f"rate limit exceeded: {recent} transitions on memory "
                f"{memory_id!r} in the last minute (limit: {rate_limit}/min, "
                f"guardrail 5). Wait or raise workflow_rate_limit_per_minute."
            )

        # ── Current state ─────────────────────────────────────────────────
        # NOTE: re-checked here (not asserted) because a concurrent delete
        # between the existence check above and this call would otherwise
        # surface as AssertionError (a 500 in REST) instead of the
        # documented ValueError (404/409). An assert would also be stripped
        # under ``python -O``, turning the race into a TypeError.
        current = self.sqlite.get_workflow_status(memory_id)
        if current is None:
            raise ValueError(f"memory {memory_id!r} not found")
        from_str = current["workflow_status"]
        from_status = WorkflowStatus(from_str) if from_str else None
        locked_by = current["locked_by"]
        locked_at_iso = current["locked_at"]

        # ── Guardrail 3: idempotent transitions ───────────────────────────
        # to == current is a no-op: we SKIP (do not write, do not record in
        # history). The audit log stays clean of polluting no-op polls; the
        # returned idempotent=True tells the caller nothing changed.
        if from_status is not None and from_status == to_status:
            return self._workflow_result(
                memory_id=memory_id,
                from_status=from_status,
                to_status=to_status,
                actor=actor,
                locked_by=locked_by,
                locked_at_iso=locked_at_iso,
                reason=reason,
                force_used=False,
                stale_lock_released=False,
                idempotent=True,
                recorded=False,
            )

        # ── State machine enforcement ─────────────────────────────────────
        # validate_transition raises WorkflowTransitionError (a ValueError
        # subclass) on a forbidden edge — e.g. blocked → done, or any edge out
        # of a terminal state. We let it propagate: WorkflowTransitionError IS
        # a ValueError, so the manager's ValueError contract (caught by the
        # MCP tool / REST layer) is honoured and the precise message survives.
        validate_transition(from_status, to_status)

        # ── Lock guardrails (2 + 4) ───────────────────────────────────────
        stale_threshold_h = self.settings.mnemos.workflow_stale_lock_threshold_hours
        force_used = False
        stale_lock_released = False
        previous_locked_by = locked_by
        lock_held_by_other = locked_by is not None and locked_by != actor
        if lock_held_by_other:
            # Guardrail 2: stale-lock auto-release. A lock older than the
            # threshold is treated as releasable — a different actor can
            # take over WITHOUT force. We log a warning and proceed.
            if locked_at_iso is not None and _lock_is_stale(locked_at_iso, stale_threshold_h):
                stale_lock_released = True
                logger.warning(
                    "workflow stale-lock release: memory %s locked by %r at %s "
                    "(>%dh), taken over by %r",
                    memory_id,
                    locked_by,
                    locked_at_iso,
                    stale_threshold_h,
                    actor,
                )
            # Guardrail 4: force-unlock. Explicit override; reason required
            # (validated above). force_used is recorded in the audit log.
            elif force:
                force_used = True
                logger.info(
                    "workflow force-unlock: memory %s lock held by %r overridden by %r (reason=%r)",
                    memory_id,
                    locked_by,
                    actor,
                    reason,
                )
            else:
                raise ValueError(
                    f"memory {memory_id!r} is locked by {locked_by!r}. "
                    f"Use force=True (with a reason) to override, or wait for "
                    f"stale-lock release (>{stale_threshold_h}h). "
                    f"(guardrails 2 + 4)"
                )

        # ── Compute new lock projection ───────────────────────────────────
        new_locked_by, new_locked_at = self._compute_lock_projection(
            to_status=to_status,
            actor=actor,
            current_locked_by=locked_by,
            current_locked_at_iso=locked_at_iso,
            force_used=force_used,
            stale_lock_released=stale_lock_released,
        )

        # ── Persist: columns + audit row ──────────────────────────────────
        now_iso = datetime.now(UTC).isoformat()
        self.sqlite.set_workflow_status(memory_id, to_status.value, new_locked_by, new_locked_at)
        self.sqlite.add_workflow_history(
            {
                "id": str(uuid.uuid4()),
                "memory_id": memory_id,
                "from_status": from_status.value if from_status else None,
                "to_status": to_status.value,
                "actor": actor,
                "reason": reason.strip(),
                "force_used": 1 if force_used else 0,
                "created_at": now_iso,
            }
        )
        logger.info(
            "workflow transition: memory=%s %s->%s actor=%s force=%s stale=%s",
            memory_id,
            from_status.value if from_status else "(unset)",
            to_status.value,
            actor,
            force_used,
            stale_lock_released,
        )

        return self._workflow_result(
            memory_id=memory_id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            locked_by=new_locked_by,
            locked_at_iso=new_locked_at,
            reason=reason,
            force_used=force_used,
            stale_lock_released=stale_lock_released,
            previous_locked_by=previous_locked_by,
            idempotent=False,
            recorded=True,
        )

    def workflow_get(self, memory_id: str) -> dict[str, Any] | None:
        """Return the current workflow projection for a memory.

        Returns ``None`` when the memory does not exist. The ``workflow_status``
        is normalised to ``open`` when the memory has never had its workflow
        set (legacy / freshly created), so callers always see a valid state.
        """
        current = self.sqlite.get_workflow_status(memory_id)
        if current is None:
            return None
        status = current["workflow_status"]
        return {
            "memory_id": memory_id,
            "workflow_status": status if status is not None else "open",
            "locked_by": current["locked_by"],
            "locked_at": current["locked_at"],
        }

    def workflow_history(self, memory_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return the workflow transition audit log for a memory (newest first).

        Returns an empty list if the memory has no recorded transitions
        (including when the memory does not exist — history is a projection
        of past events, not a memory existence check).
        """
        return self.sqlite.get_workflow_history(memory_id, limit=limit)

    # ── Workflow helpers ─────────────────────────────────────────────────

    @staticmethod
    def _compute_lock_projection(
        *,
        to_status: Any,
        actor: str,
        current_locked_by: str | None,
        current_locked_at_iso: str | None,
        force_used: bool,
        stale_lock_released: bool,
    ) -> tuple[str | None, str | None]:
        """Compute the new (locked_by, locked_at) for a target status.

        - in-progress acquires the lock (sets owner to ``actor``).
        - blocked / resolved keep the lock (same actor still owns the work);
          on a takeover (force / stale) the owner becomes ``actor`` and the
          timestamp is refreshed so the stale-lock clock restarts.
        - open / done / withdrawn release the lock.

        Returns explicit values for ``set_workflow_status`` to write — never
        relies on the DB to "keep" the old value, because
        ``set_workflow_status`` overwrites both columns unconditionally.
        """
        from mnemos.workflow import WorkflowStatus

        now_iso = datetime.now(UTC).isoformat()
        if to_status == WorkflowStatus.IN_PROGRESS:
            return actor, now_iso
        if to_status in (WorkflowStatus.BLOCKED, WorkflowStatus.RESOLVED):
            if force_used or stale_lock_released:
                return actor, now_iso
            return current_locked_by, current_locked_at_iso
        # open / done / withdrawn → release
        return None, None

    @staticmethod
    def _workflow_result(
        *,
        memory_id: str,
        from_status: Any,
        to_status: Any,
        actor: str,
        locked_by: str | None,
        locked_at_iso: str | None,
        reason: str,
        force_used: bool,
        stale_lock_released: bool,
        idempotent: bool,
        recorded: bool,
        previous_locked_by: str | None = None,
    ) -> dict[str, Any]:
        """Build the uniform result dict returned by workflow_set."""
        from mnemos.workflow import is_terminal

        return {
            "memory_id": memory_id,
            "from_status": from_status.value if from_status else None,
            "to_status": to_status.value,
            "actor": actor,
            "previous_locked_by": previous_locked_by,
            "locked_by": locked_by,
            "locked_at": locked_at_iso,
            "stale_lock_released": stale_lock_released,
            "force_used": force_used,
            "idempotent": idempotent,
            "recorded": recorded,
            "reason": reason.strip() if reason else "",
            "terminal": is_terminal(to_status),
        }

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
            # Default: only published + processed (ADR-0018 entry invariant
            # gate — CONTEXT_ADMISSIBLE_STATUSES is the single constant all
            # content-surfacing paths consult).
            allowed: set[MemoryStatus] | None = set(CONTEXT_ADMISSIBLE_STATUSES)
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

    def _commit_tags(
        self,
        mem: Memory,
        new_tags: list[str],
        *,
        dry_run: bool,
        strict: bool = False,
    ) -> tuple[bool, str | None]:
        """Validate and (unless ``dry_run``) persist a new tag set for one memory.

        Shared commit path for ``tags_rename`` / ``tags_remove`` / ``tags_add``
        (the grouped ``mnemos_tags`` MCP tool). Keeping the contract check and
        the ``update_fields`` write in one place guarantees every tag mutation
        goes through the same FTS5-safe ``UPDATE`` (the ``memories_au`` trigger
        fires) and the same ``validate_tag_contract`` gate.

        Args:
            mem: The memory row currently being processed.
            new_tags: The desired tag list for ``mem``.
            dry_run: When ``True`` nothing is written; the caller still counts
                the memory as "would change" so the report reflects intent.
            strict: Contract-validation mode for the resulting tag set.

                ``False`` (default, used by ``tags_rename``) — *lax*: a missing
                ``project:`` / ``agent:`` / ``mnemos:`` tag, an invalid
                ``mnemos:`` subtype, or a malformed slug is auto-patched in the
                returned list rather than rejected. Rename is a prefix swap
                (``gcw:`` → ``mnemos:``) that preserves required tags, so lax is
                the correct, non-corrupting mode there.

                ``True`` (used by ``tags_remove`` / ``tags_add``) — *strict*:
                any contract violation (including the soft ones lax would
                patch) raises ``TagContractError``, which this method reports as
                a per-memory error and skips the write. ``remove`` / ``add`` are
                explicit mutations, so a contract-breaking result (e.g. removing
                the last ``project:``, or adding an invalid ``mnemos:`` subtype)
                is rejected per memory instead of corrupting the store.

        Returns:
            ``(changed, error)``. ``changed`` is ``True`` when ``new_tags``
            differs from ``mem.tags`` AND passes the contract gate (and, when
            not ``dry_run``, the ``UPDATE`` succeeded). ``error`` is the
            ``"<id>: <reason>"`` string to append to the report's ``errors``
            list when validation or the write raised; ``None`` otherwise.

        Note:
            Re-derives the denormalised ``project`` / ``agent`` columns from
            the new tag set so a prefix change that touched ``project:`` /
            ``agent:`` keeps the denormalised columns aligned with the tags
            (otherwise per-project / per-agent queries drift). For the common
            ``gcw:`` → ``mnemos:`` rename these are unchanged.
        """
        from mnemos.models import validate_tag_contract

        if new_tags == mem.tags:
            return False, None
        try:
            validate_tag_contract(new_tags, strict=strict)
        except Exception as exc:  # report, don't crash the batch
            return False, f"{mem.id}: {exc}"
        if dry_run:
            return True, None
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
            return False, f"{mem.id}: {exc}"
        return True, None

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
            ``{"scanned": N, "renamed": N, "changed": N, "skipped_invalid": N,
            "errors": [...]}``. ``changed`` mirrors ``renamed`` so every
            ``mnemos_tags`` action (rename/remove/add) exposes a ``changed``
            key for a uniform report shape; ``renamed`` is kept for back-compat
            with existing ``mnemos_tags_rename`` callers. In dry-run mode
            ``renamed`` reflects what *would* be renamed; nothing is written.

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
        from mnemos.models import MNEMOS_TAG_SUBTYPES
        from mnemos.traces import TraceRecorder

        report: dict[str, Any] = {
            "action": "rename",
            "scanned": 0,
            "renamed": 0,
            "changed": 0,  # alias of renamed for a uniform report shape
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

                # Shared commit: contract check + FTS5-safe UPDATE. Both the
                # grouped ``mnemos_tags`` tool (action=rename alias) and the
                # legacy ``mnemos_tags_rename`` tool route through here, so
                # the behaviour is byte-identical.
                changed, err = self._commit_tags(mem, new_tags, dry_run=dry_run)
                if err:
                    report["errors"].append(err)
                if changed:
                    report["renamed"] += 1

        # ``changed`` mirrors ``renamed`` so the grouped ``mnemos_tags`` tool
        # exposes a uniform ``changed`` key across rename/remove/add.
        report["changed"] = report["renamed"]

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

    def tags_remove(
        self,
        tags: list[str],
        *,
        wildcard: bool = False,
        dry_run: bool = True,
        project: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Remove tags from memories. Explicit removal — never a magic empty target.

        Backs the ``mnemos_tags`` MCP tool with ``action="remove"``. Each tag
        in ``tags`` is matched against every memory's tag set; matches are
        dropped. With ``wildcard=False`` (default) the match is exact; with
        ``wildcard=True`` each entry is treated as a prefix and any tag
        starting with it is removed (e.g. ``["gcw:"]`` strips every ``gcw:*``
        tag without rewriting them).

        Args:
            tags: Tags to remove. Exact match by default; prefix match when
                ``wildcard=True``.
            wildcard: Prefix match (``True``) vs exact match (``False``, default).
            dry_run: Preview only when ``True`` (default).
            project / agent: Scope the scan to a single project / agent slug.

        Returns:
            ``{action, scanned, changed, removed_tags, wildcard, errors,
            dry_run}``. ``changed`` counts memories whose tag set actually
            changed and (when not ``dry_run``) was written.

        Safety:
            Goes through ``_commit_tags`` → ``SQLiteStore.update_fields``
            (plain ``UPDATE``), so the FTS5 ``AFTER UPDATE`` trigger fires and
            the external-content index stays consistent — same path as
            ``tags_rename``. The resulting tag set is validated in **strict**
            mode: removing the last ``project:`` / ``agent:`` / ``mnemos:`` tag
            (or otherwise breaking the contract) is rejected per memory with an
            error entry instead of corrupting the store. Idempotent: a second
            run reports ``changed=0``.
        """
        from mnemos.traces import TraceRecorder

        report: dict[str, Any] = {
            "action": "remove",
            "scanned": 0,
            "changed": 0,
            "removed_tags": list(tags),
            "wildcard": wildcard,
            "errors": [],
            "dry_run": dry_run,
        }
        if not tags:
            report["errors"].append("tags must be a non-empty list")
            return report

        matchers = list(tags)
        page_size = 500
        offset = 0
        recorder = TraceRecorder(store=self.sqlite)

        def _is_match(tag: str) -> bool:
            if wildcard:
                return any(tag.startswith(m) for m in matchers)
            return tag in matchers

        while True:
            batch = self.sqlite.list_all(
                limit=page_size, offset=offset, project=project, agent=agent
            )
            if not batch:
                break
            offset += len(batch)
            for mem in batch:
                report["scanned"] += 1
                new_tags = [t for t in mem.tags if not _is_match(t)]
                # Strict gate: removing the last project:/agent:/mnemos: tag
                # (or otherwise breaking the contract) is rejected per memory
                # with an error entry instead of corrupting the store. The
                # write is skipped for that memory; the batch continues.
                changed, err = self._commit_tags(mem, new_tags, dry_run=dry_run, strict=True)
                if err:
                    report["errors"].append(err)
                if changed:
                    report["changed"] += 1

        with recorder.record(
            task_label="tags_remove",
            project=project or "*",
            step="tags_remove",
        ) as trace:
            trace.rationale_summary = (
                f"remove tags={tags} wildcard={wildcard} dry_run={dry_run} "
                f"changed={report['changed']}"
            )[:200]
        return report

    def tags_add(
        self,
        tags: list[str],
        *,
        dry_run: bool = True,
        project: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Append tags to every memory matching the project/agent filter.

        Backs the ``mnemos_tags`` MCP tool with ``action="add"``. Each tag in
        ``tags`` is appended (if not already present) to every memory returned
        by the ``project`` / ``agent`` filter. When neither filter is set the
        operation spans all memories — callers should scope it deliberately.

        Args:
            tags: Tags to append. Each must have a prefix shape (contain
                ``":"``); the resulting full tag set is re-validated per
                memory via ``_commit_tags`` so adding a duplicate ``project:``
                (or any contract-breaking tag) errors on that memory instead
                of corrupting the store.
            dry_run: Preview only when ``True`` (default).
            project / agent: Scope the scan to a single project / agent slug.

        Returns:
            ``{action, scanned, changed, added_tags, errors, dry_run}``.
            ``changed`` counts memories whose tag set actually changed and
            (when not ``dry_run``) was written.

        Safety:
            Same ``_commit_tags`` → ``update_fields`` path as rename/remove,
            so FTS5 stays consistent. The full resulting set is validated in
            **strict** mode before any write: an added tag that breaks the
            contract (e.g. an invalid ``mnemos:`` subtype or a malformed slug)
            is rejected per memory with an error entry instead of corrupting
            the store.
        """
        from mnemos.traces import TraceRecorder

        report: dict[str, Any] = {
            "action": "add",
            "scanned": 0,
            "changed": 0,
            "added_tags": list(tags),
            "errors": [],
            "dry_run": dry_run,
        }
        if not tags:
            report["errors"].append("tags must be a non-empty list")
            return report

        to_add = [t for t in tags if t]
        # Light structural pre-flight: each added tag must carry a prefix.
        # The full per-memory contract check (exactly one project:/agent:,
        # etc.) is delegated to _commit_tags on the resulting set.
        for t in to_add:
            if ":" not in t:
                report["errors"].append(f"tag must have a prefix (contain ':'): {t!r}")
                return report

        page_size = 500
        offset = 0
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
                new_tags = list(mem.tags)
                for t in to_add:
                    if t not in new_tags:
                        new_tags.append(t)
                # Strict gate: a tag whose addition breaks the contract (e.g.
                # an invalid mnemos: subtype, a malformed slug, or a tag that
                # leaves the set without exactly one project:/agent:) is
                # rejected per memory with an error entry; the write is skipped
                # for that memory and the batch continues.
                changed, err = self._commit_tags(mem, new_tags, dry_run=dry_run, strict=True)
                if err:
                    report["errors"].append(err)
                if changed:
                    report["changed"] += 1

        with recorder.record(
            task_label="tags_add",
            project=project or "*",
            step="tags_add",
        ) as trace:
            trace.rationale_summary = (
                f"add tags={tags} dry_run={dry_run} changed={report['changed']}"
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

    # ── ADR-0017 D1: assemble_context provider contract (#125) ─────────────

    def assemble_context(
        self,
        *,
        session: str,
        project: str,
        file: str | None = None,
        budget: int = 2048,
        mode: str = "sync",
        expand_ccr: bool = False,
        async_handle: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Assemble the model-facing context block (ADR-0017 D1 contract).

        Thin delegation to :func:`mnemos.assemble.assemble_context` — the
        fixed pipeline (recall → CCR → filter → scan → align → budget)
        lives in that module; this method keeps the one-core-over-three-
        surfaces pattern (MCP / REST / future SDK all call the manager).
        ``agent`` (A2 review F2) pairs with ``session`` as the issuer
        context for the strict-mode CCR expansion gate. Raises
        ``ValueError`` on invalid ``session`` / ``project`` / ``mode`` /
        ``budget`` or an unknown ``async_handle``.
        """
        from mnemos.assemble import assemble_context as _assemble

        return _assemble(
            self,
            session=session,
            project=project,
            file=file,
            budget=budget,
            mode=mode,
            expand_ccr=expand_ccr,
            async_handle=async_handle,
            agent=agent,
        )

    # ── ADR-0018: on_context_rewrite lifecycle event (#125, Wave 2) ────────

    def context_rewrite(
        self,
        *,
        content: str,
        project: str,
        agent: str,
        session: str | None = None,
        supersedes: str | None = None,
        diff: str | None = None,
        include_marker: bool = False,
    ) -> dict[str, Any]:
        """Handle one ``on_context_rewrite`` event (idempotent, version-less).

        Thin delegation to :func:`mnemos.context_rewrite.context_rewrite` —
        the event logic lives in that module; this method keeps the
        one-core-over-three-surfaces pattern (MCP / REST / future SDK all
        call the manager). The original enters LTM through the NORMAL
        knowledge-pipeline ``add`` path; rehydrate is the existing
        scanned/gated issuance channels. Raises ``ValueError`` on invalid
        input (incl. size caps), tag-contract violations, or a
        ``supersedes`` target not found in the caller's project; raises
        :class:`mnemos.context_rewrite.ContextRewriteRateLimitError` when the
        per-(project, session) stored-event quota is exhausted (W2
        review F1 — REST maps it to 429).
        """
        from mnemos.context_rewrite import context_rewrite as _event

        return _event(
            self,
            content=content,
            project=project,
            agent=agent,
            session=session,
            supersedes=supersedes,
            diff=diff,
            include_marker=include_marker,
        )

    # ── CCR (P1-4) ─────────────────────────────────────────────────────────

    def compress_content(
        self,
        text: str,
        *,
        profile: str | None = None,
        project: str = "",
        agent: str | None = None,
        session: str | None = None,
    ) -> dict[str, Any]:
        """Compress ``text`` via CCR and cache the original in SQLite.

        A2 (ArchCom 2026-08-27) — issuer ledger: ``agent``/``session``
        record the caller identity on the cache row so strict marker
        validation (see :meth:`retrieve_content`) can later prove the
        marker was minted in the redeemer's own context. Callers without
        identity context pass ``None`` (stored NULL → the row's markers
        are unverifiable and strict mode refuses them).

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
            issuer_agent=agent,
            issuer_session=session,
        )

    def validate_marker(
        self,
        h: str,
        *,
        project: str | None,
        original_chars: int | None,
        trusted_issuers: AbstractSet[tuple[str, str | None]],
    ) -> dict[str, Any]:
        """A2 (ArchCom 2026-08-27) — validate a CCR marker before issuance.

        Strong-form gate for the W3 automation rehydrate channel
        (committee decision ``archcom-2026-08-27-deferrals-triage``):
        existence-only validation does NOT catch same-project seeding,
        so all three checks run:

        * **existence** — the row must exist under ``(project, hash)``
          (project-scoped after A1). Strict validation REQUIRES a
          project scope: an unscoped lookup would redeem a marker
          against the first-stored copy of any project.
        * **integrity** — the marker's ``original_chars`` (the N parsed
          from ``[compressed: <hash> | N→M chars | …]``) must equal the
          character length of the stored original. ``None`` fails
          (fail-closed: an unverifiable dimension is a failed
          dimension).
        * **provenance** — the row's issuer ledger
          (``issuer_agent``/``issuer_session``, recorded at store time)
          must match the caller's trusted issuer context: the stored
          ``(agent, session)`` pair must be a member of
          ``trusted_issuers``. The minimal sound spec for W3 automation
          is exactly one pair — its OWN ``(agent, session)`` — so a
          hook redeems only markers minted in its own context; an
          explicit allowlist is the same predicate with more pairs. A
          spec session of ``None`` matches only NULL issuer sessions
          (component-wise equality, never wildcards). Rows stored
          without identity (legacy migration or identity-less callers)
          are UNVERIFIABLE and fail with the distinct reason
          ``unverifiable legacy marker``; an empty spec fails with
          ``no trusted issuer context``.

        Residual (ADR-0018 residual register, accepted, A2 review round
        wording): with strict mode enabled, marker redemption is
        ADVERSARY-RESISTANT for issuer-stamped rows (post-A2 stores carry
        the ledger) — refusal reasons are FIXED non-oracle strings (A2
        review F1: a reason echoing the stored length or issuer pair is a
        two-call oracle that defeats provenance). Legacy NULL-issuer rows
        are unverifiable by construction: full-shape validation refuses
        them, hash-only retrieval under strict mode stays ALLOWED with a
        WARNING (refusing would brick all pre-A2 caches for zero
        marginal adversary resistance — see retrieve_content F2). The
        same-project seeding residual is unchanged: a trusted harness
        with compress access can still seed content inside its own
        project and redeem from the same identity; single-operator
        threat model, revisit on the first multi-principal trigger.

        Args:
            h: SHA-256 hash from the marker (validated by existence).
            project: Project scope; ``None``/empty fails existence
                (strict validation requires the scope).
            original_chars: N from the marker; ``None`` fails integrity.
            trusted_issuers: Set of ``(agent, session | None)`` pairs
                allowed to have minted the marker. Components are
                stripped and empty sessions normalised to ``None``
                (mirroring the ``ccr_store`` issuer normalisation — A2
                review F4); agents must be non-empty strings
                (``ValueError`` otherwise — the caller builds this set
                from its own identity).

        Returns:
            ``{"valid": bool, "reason": str | None, "check": str | None}``
            where ``check`` names the failed dimension (``existence`` /
            ``integrity`` / ``provenance``) for the refusal reason.
            Reasons are FIXED strings carrying no stored values (F1).
            The read is unbumped (``bump=False``): a failed validation
            must not LRU-pin the entry (P1-b review F4 semantics).
        """
        # F4 — normalise spec components exactly like ccr_store stores
        # them, so a padded spec matches the stripped ledger row.
        normalized: set[tuple[str, str | None]] = set()
        for agent_i, session_i in trusted_issuers:
            if not isinstance(agent_i, str) or not agent_i.strip():
                raise ValueError(
                    f"trusted_issuers agents must be non-empty slugs (got {agent_i!r})"
                )
            if session_i is not None and not isinstance(session_i, str):
                raise ValueError(
                    f"trusted_issuers sessions must be strings or None (got {session_i!r})"
                )
            session_n = (session_i.strip() or None) if session_i else None
            normalized.add((agent_i.strip(), session_n))

        def _verdict(valid: bool, check: str | None, reason: str | None) -> dict[str, Any]:
            return {"valid": valid, "reason": reason, "check": check}

        # (a) existence — project-scoped (strict mode requires a scope).
        if not project:
            return _verdict(False, "existence", "project scope required for marker validation")
        entry = self.sqlite.ccr_get(h, project=project, bump=False)
        if entry is None:
            return _verdict(False, "existence", f"hash not in cache under project {project!r}")

        # (b) integrity — marker N vs stored character length. F1: the
        # reason is a FIXED string — echoing marker/stored lengths turns
        # the refusal into a two-call oracle (read the true N, re-call).
        if original_chars is None:
            return _verdict(False, "integrity", "original_chars not provided by the caller")
        if original_chars != len(entry["original"]):
            return _verdict(False, "integrity", "original_chars mismatch")

        # (c) provenance — issuer ledger vs the trusted context. F1: the
        # reason never echoes the stored issuer pair (same oracle class).
        if not normalized:
            return _verdict(False, "provenance", "no trusted issuer context (agent required)")
        issuer_agent = entry.get("issuer_agent")
        if not issuer_agent:
            return _verdict(False, "provenance", "unverifiable legacy marker")
        issuer_pair = (issuer_agent, entry.get("issuer_session"))
        if issuer_pair not in normalized:
            return _verdict(False, "provenance", "issuer mismatch")
        return _verdict(True, None, None)

    def scan_issuance(self, text: str, *, context: str) -> IssuanceScan:
        """Scan one issuance-boundary string and redact/refuse (P1-b M1).

        Single helper for every content-echoing path — MCP
        ``mnemos_search`` / ``mnemos_agent_recall`` / ``mnemos_recall_context``
        and REST ``/search`` / ``/recall/agent`` — so no channel can drift
        from the P0 ``retrieve_content`` semantics: matched spans become
        ``<REDACTED:<pattern>>`` in the returned copy (zero-loss storage —
        the caller's stored model is never mutated), or the whole string
        is refused (no content) when ``ccr.retrieve_refuse_on_secret`` is
        on. A scanner exception is NEVER allowed to degrade into issuing
        unscanned content (fail-closed): it maps to ``refused=True`` with
        ``reason="scanner error"`` (P1-b m5). The ``context`` label names
        the calling channel in log lines (never the content).

        Cost control: scan runs on exactly the string being issued (per
        item, once) — titles/tags/metadata are the caller's business.
        """
        from mnemos.secrets_detector import (
            detect_secrets,
            findings_by_pattern,
            redact_content,
        )

        try:
            findings = detect_secrets(text)
            if not findings:
                return IssuanceScan(
                    text=text,
                    refused=False,
                    reason=None,
                    redactions=0,
                    redacted_patterns={},
                )
            pattern_counts = findings_by_pattern(findings)
            if self.settings.ccr.retrieve_refuse_on_secret:
                logger.warning(
                    "Issuance refused (%s): redactions=%d patterns=%s — raw values not logged",
                    context,
                    len(findings),
                    pattern_counts,
                )
                return IssuanceScan(
                    text="",
                    refused=True,
                    reason="secret detected",
                    redactions=len(findings),
                    redacted_patterns=pattern_counts,
                )
            redacted = redact_content(text, findings)
            logger.info(
                "Issuance redacted (%s): redactions=%d patterns=%s",
                context,
                len(findings),
                pattern_counts,
            )
            return IssuanceScan(
                text=redacted,
                refused=False,
                reason=None,
                redactions=len(findings),
                redacted_patterns=pattern_counts,
            )
        except Exception as exc:
            # Fail-closed (P1-b m5): any scanner failure refuses the
            # issuance rather than echoing unscanned content. Broad catch
            # is deliberate at this security boundary; the error is
            # logged (context label + exception, never the content).
            logger.error("Issuance scanner error (%s): %s", context, exc)
            return IssuanceScan(
                text="",
                refused=True,
                reason="scanner error",
                redactions=0,
                redacted_patterns={},
            )

    def scan_issuance_item(
        self,
        text: str | None,
        *,
        title: str | None = None,
        context: str,
    ) -> IssuanceItemScan:
        """Scan every string ONE result item echoes (P1-b review F1).

        Composite over :meth:`scan_issuance` for items that echo both a
        content field and a title (``auto_title()`` derives from the
        first line of raw content OR echoes an explicitly-set title —
        either can carry a secret, so scanning only the content leaks
        the title verbatim in the same response). ``text``/``title`` are
        ``None`` when the item does not echo that field (e.g. title-only
        ``mnemos_list_recent``); at least one must be given. Refuse mode
        refuses the item when EITHER field trips; ``redactions`` /
        ``redacted_patterns`` are merged across both fields. The
        ``context`` label should carry the item id (F3 forensics); the
        scanned field is appended here (``<context>:content`` /
        ``<context>:title``).
        """
        content_scan = (
            self.scan_issuance(text, context=f"{context}:content") if text is not None else None
        )
        title_scan = (
            self.scan_issuance(title, context=f"{context}:title") if title is not None else None
        )
        scans = [s for s in (content_scan, title_scan) if s is not None]
        refused = any(s.refused for s in scans)
        reason = next((s.reason for s in scans if s.refused), None)
        redactions = sum(s.redactions for s in scans)
        patterns: dict[str, int] = {}
        for s in scans:
            for name, count in s.redacted_patterns.items():
                patterns[name] = patterns.get(name, 0) + count
        return IssuanceItemScan(
            content=content_scan.text if content_scan is not None and not refused else "",
            title=title_scan.text if title_scan is not None and not refused else "",
            refused=refused,
            reason=reason,
            redactions=redactions,
            redacted_patterns=patterns,
        )

    def retrieve_content(
        self,
        h: str,
        *,
        query: str | None = None,
        snippet_count: int | None = None,
        project: str | None = None,
        validate_marker: bool | None = None,
        original_chars: int | None = None,
        agent: str | None = None,
        session: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve a CCR-cached original (or FTS5 snippets if ``query``).

        A2 (ArchCom 2026-08-27) — strict marker validation. The request
        is MARKER-SHAPED when it carries any of ``original_chars`` /
        ``agent`` / ``session`` (the metadata a harness parses out of a
        ``[compressed: <hash> | N→M chars | …]`` marker plus its own
        identity). When strict mode is on (per-call ``validate_marker``
        override, else the ``ccr.validate_markers`` knob) a marker-shaped
        request must first pass :meth:`validate_marker` — existence
        (project-scoped), ``original_chars`` integrity, and provenance
        against the caller's own ``(agent, session)`` issuer context —
        and any failed check returns the refused shape with
        ``reason="marker validation failed: <check>: <detail>"`` and NO
        content (fail-closed; reasons are FIXED non-oracle strings — A2
        review F1). Review F2 closes the strip-the-args bypass: in
        strict mode a HASH-ONLY retrieve of an ISSUER-STAMPED row is
        refused with ``reason="marker validation required"`` (no
        content); legacy NULL-issuer rows stay redeemable hash-only
        with a WARNING (unverifiable by construction — refusing would
        brick pre-A2 caches; the register wording lives in
        :meth:`validate_marker`). An explicit ``validate_marker=False``
        disables both gates (operator escape hatch). A refused
        validation never bumps the retrieval counter (F4 semantics
        preserved).

        ADR-0018 P0 — issuance secret scan. Every retrieval is scanned
        with ``detect_secrets`` (patterns evolve and stored records age,
        so a store-time verdict alone would go stale): matched spans in
        the RETURNED payload — full original or FTS5 snippets — are
        replaced with ``<REDACTED:<pattern>>``. The stored original is
        never mutated (zero-loss storage). The response carries
        ``redactions`` (number of redacted spans; ``0`` when clean) and,
        when non-zero, ``redacted_patterns`` (log-safe per-pattern
        counts — raw matched values never leave storage). When
        ``ccr.retrieve_refuse_on_secret`` is enabled, a detection
        returns ``refused=True`` with no content instead of a redacted
        copy. ``found=False`` responses are returned unchanged.

        ADR-0018 P1-a — project scoping: ``project`` scopes the cache
        lookup to that project's entries; a hash cached under another
        project returns ``found=False`` (cross-session marker redemption
        denied, fail-closed). The manager holds no ambient project
        context, so the default is ``None`` = unscoped lookup (legacy
        behavior preserved for callers without project context); the
        explicit parameter is the override. The scan-at-store verdict on
        the row is observability only and never fast-paths this scan.

        ADR-0018 P1-b — issuance hardening:

        * CWE-668 ergonomics (Security findings 1+4): retrieving a
          project-scoped entry with ``project=None`` logs a WARNING; with
          ``ccr.require_project_match`` (default False) the issuance is
          denied instead (``refused=True``, ``reason`` names the scope
          requirement).
        * m2 — FTS5 snippets are scanned on a marker-stripped copy
          (highlight markers split multi-token secrets); on a hit the
          WHOLE snippet is withheld (``<REDACTED:snippet>``) because
          stripped-copy offsets do not map back to the marked snippet.
        * m5 — a scanner exception maps to the refused shape with
          ``reason="scanner error"`` (fail-closed, observable) instead
          of propagating as a 500 / MCP error.
        * review F4 — the retrieval counter is bumped only when content
          is actually ISSUED (``ccr_touch`` after the decision; the
          entry is read via ``bump=False``): refused/denied issuances no
          longer inflate ``retrieval_count`` or LRU-pin the entry.
        """
        from mnemos.ccr import retrieve
        from mnemos.secrets_detector import (
            detect_secrets,
            findings_by_pattern,
        )

        # ── A2 (ArchCom 2026-08-27): strict marker validation gate ──────
        # Fail-closed BEFORE any content is read for issuance: a failed
        # check returns the refused shape with no content and never
        # bumps the retrieval counter (the validation read itself is
        # unbumped — validate_marker uses bump=False).
        strict = self.settings.ccr.validate_markers if validate_marker is None else validate_marker
        marker_shaped = original_chars is not None or agent is not None or session is not None
        if strict and marker_shaped:
            trusted: set[tuple[str, str | None]] = set()
            if agent and agent.strip():
                trusted.add((agent.strip(), (session.strip() or None) if session else None))
            verdict = self.validate_marker(
                h,
                project=project,
                original_chars=original_chars,
                trusted_issuers=trusted,
            )
            if not verdict["valid"]:
                refusal_reason = (
                    f"marker validation failed: {verdict['check']}: {verdict['reason']}"
                )
                logger.warning(
                    "CCR issuance refused (marker validation): hash=%s check=%s "
                    "project=%s agent=%s — no content issued",
                    h,
                    verdict["check"],
                    project,
                    agent,
                )
                return {
                    "hash": h,
                    "found": verdict["check"] != "existence",
                    "refused": True,
                    "reason": refusal_reason,
                    "redactions": 0,
                    "redacted_patterns": {},
                }

        result = retrieve(
            h,
            store=self.sqlite,
            config=self.settings.ccr,
            query=query,
            snippet_count=snippet_count,
            project=project,
            bump=False,
        )
        if not result.get("found"):
            return result

        # ── A2 review F2: strict-mode hash-only closure ────────────────
        # Strict deployments are automation contexts by design: an
        # ISSUER-STAMPED row must be redeemed WITH marker metadata so the
        # validation gate above actually runs — a hash-only retrieve
        # would strip the optional args and bypass it. Legacy NULL-issuer
        # rows stay redeemable (unverifiable by construction; refusing
        # would brick all pre-A2 caches for zero marginal adversary
        # resistance) with a WARNING for audit visibility. The explicit
        # per-call validate_marker=False override disables this gate
        # together with the rest of strict mode (operator escape hatch).
        if strict and not marker_shaped:
            if result.get("issuer_agent"):
                logger.warning(
                    "CCR issuance refused (marker validation required): hash=%s "
                    "project=%s — issuer-stamped entry redeemed without marker "
                    "metadata; no content issued",
                    h,
                    project,
                )
                return {
                    "hash": h,
                    "found": True,
                    "refused": True,
                    "reason": "marker validation required",
                    "redactions": 0,
                    "redacted_patterns": {},
                }
            logger.warning(
                "Unvalidated hash-only CCR retrieval of an unverifiable legacy "
                "entry under strict marker validation (A2 review F2): hash=%s "
                "project=%s — allowed, issuer ledger is NULL by construction",
                h,
                project,
            )

        # A1 (ArchCom 2026-08-27): the entry's own project — under the
        # composite PK the same hash may live in several projects, and
        # the bump must hit exactly the row that was issued. Computed
        # BEFORE _mark_issued is first called.
        entry_project = str(result.get("project") or "")

        def _mark_issued() -> None:
            """Bump the counter post-decision (F4) and reflect it in result."""
            self.sqlite.ccr_touch(h, project=entry_project)
            result["retrieval_count"] = int(result["retrieval_count"]) + 1

        # ── CWE-668 ergonomics: unscoped retrieval of a scoped row ────
        if not project and entry_project:
            if self.settings.ccr.require_project_match:
                logger.warning(
                    "CCR issuance denied (project scope required): hash=%s entry_project=%s",
                    h,
                    entry_project,
                )
                return {
                    "hash": h,
                    "found": True,
                    "refused": True,
                    "reason": (
                        "project-scoped entry requires a matching project "
                        "(ccr.require_project_match)"
                    ),
                    "redactions": 0,
                    "redacted_patterns": {},
                }
            logger.warning(
                "Unscoped CCR retrieval of project-scoped entry (CWE-668): "
                "hash=%s entry_project=%s — pass project=<slug> to scope "
                "the lookup",
                h,
                entry_project,
            )

        # ── Snippet path: scan + redact each snippet in place ───────────
        if "snippets" in result:
            redactions = 0
            pattern_counts: dict[str, int] = {}
            try:
                for snippet in result["snippets"]:
                    text = str(snippet.get("snippet", ""))
                    # m2: detect on a marker-stripped copy — the raw
                    # marked snippet can split a multi-token secret.
                    findings = detect_secrets(_snippet_scan_text(text))
                    if not findings:
                        continue
                    # Offsets in the stripped copy do not map back to the
                    # marked snippet, so the whole snippet is withheld.
                    snippet["snippet"] = "<REDACTED:snippet>"
                    redactions += len(findings)
                    for name, count in findings_by_pattern(findings).items():
                        pattern_counts[name] = pattern_counts.get(name, 0) + count
            except Exception as exc:
                # m5: fail-closed — never issue unscanned snippets.
                logger.error(
                    "CCR issuance scanner error (snippets): hash=%s error=%s",
                    h,
                    exc,
                )
                return {
                    "hash": h,
                    "found": True,
                    "refused": True,
                    "reason": "scanner error",
                    "redactions": 0,
                    "redacted_patterns": {},
                }
            if redactions and self.settings.ccr.retrieve_refuse_on_secret:
                logger.warning(
                    "CCR issuance refused (secret in snippets): hash=%s redactions=%d",
                    h,
                    redactions,
                )
                return {
                    "hash": h,
                    "found": True,
                    "refused": True,
                    "reason": "secret detected in retrieved snippets",
                    "redactions": redactions,
                    "redacted_patterns": pattern_counts,
                }
            _mark_issued()
            result["redactions"] = redactions
            if redactions:
                result["redacted_patterns"] = pattern_counts
                logger.info(
                    "CCR issuance redacted (snippets): hash=%s redactions=%d patterns=%s",
                    h,
                    redactions,
                    pattern_counts,
                )
            return result

        # ── Full-original path: scan + redact the returned copy ─────────
        original = result.get("original")
        if not isinstance(original, str):
            result["redactions"] = 0
            return result
        scan = self.scan_issuance(original, context="ccr:retrieve-original")
        if scan.refused:
            reason = (
                "secret detected in cached original"
                if scan.reason == "secret detected"
                else scan.reason
            )
            logger.warning(
                "CCR issuance refused (%s): hash=%s redactions=%d",
                "secret in original" if scan.redactions else scan.reason,
                h,
                scan.redactions,
            )
            return {
                "hash": h,
                "found": True,
                "refused": True,
                "reason": reason,
                "redactions": scan.redactions,
                "redacted_patterns": scan.redacted_patterns,
            }
        result["original"] = scan.text
        result["redactions"] = scan.redactions
        _mark_issued()
        if scan.redactions:
            result["redacted_patterns"] = scan.redacted_patterns
            logger.info(
                "CCR issuance redacted: hash=%s redactions=%d patterns=%s",
                h,
                scan.redactions,
                scan.redacted_patterns,
            )
        return result

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

    # ── Memory edges (ADR-0018 Phase 1 groundwork) ────────────────────────

    def add_memory_edge(
        self,
        from_memory_id: str,
        to_memory_id: str,
        *,
        kind: str = "supersedes",
    ) -> bool:
        """Record that ``from_memory_id`` supersedes ``to_memory_id``.

        Thin wrapper over ``SQLiteStore.add_memory_edge`` (validation and
        constraints live there). Returns ``True`` when inserted, ``False``
        when the edge already existed (idempotent). No MCP surface and no
        graph expansion in Phase 1 — on_context_rewrite arrives with #125.
        """
        return self.sqlite.add_memory_edge(from_memory_id, to_memory_id, kind=kind)

    def get_memory_edges(
        self,
        from_memory_id: str,
        *,
        kind: str = "supersedes",
    ) -> list[dict[str, Any]]:
        """Return direct outgoing edges (one hop, no expansion)."""
        return self.sqlite.get_direct_edges(from_memory_id, kind=kind)

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
