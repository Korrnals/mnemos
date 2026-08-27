"""MnemosSDK — the thin typed facade over :class:`mnemos.manager.MemoryManager`.

The programmatic contract surface for adapters (mnemos #125, Wave 3):
the Hermes migration (next wave) and any in-process harness consume the
memory server through THIS class instead of weaving manager calls into
adapter code. Local-first by construction — the SDK talks to the
manager directly (no HTTP/MCP hop); remote deployments use the MCP /
REST surfaces, which call the same manager methods.

The facade owns NO logic. Every verb is a one-line delegation (see the
per-method map below); validation, scanning, gating, idempotency, and
observability all live in the manager paths exactly as the MCP/REST
surfaces see them. If a verb needs logic, that is a signal the manager
method — not the facade — is missing something.

Verb → manager method map:

============================  ===========================================
SDK verb                      Manager method
============================  ===========================================
``remember``                  ``MemoryManager.add`` (knowledge pipeline:
                              enters ``raw``, write-path secret scan,
                              tag contract; context-reachable after the
                              pipeline advances it)
``recall``                    ``MemoryManager.search`` (hybrid RRF, A9
                              project predicate pre-RRF)
``forget``                    ``MemoryManager.get`` (project guard) +
                              ``MemoryManager.delete``
``stats``                     ``MemoryManager.stats`` (project slice =
                              presentation, see the method docstring)
``assemble_context``          ``MemoryManager.assemble_context``
                              (ADR-0017 D1 pipeline; the ADR-0018 entry
                              invariant — scan, provenance, status gate
                              — runs inside)
``rewrite``                   ``MemoryManager.context_rewrite``
                              (ADR-0018 ``on_context_rewrite`` event:
                              idempotent, version-less)
============================  ===========================================

Construction mirrors the manager (local-first): pass ``settings`` to
get a manager-owned SDK, or pass an existing ``manager`` (tests,
embedding into a server that already holds one). Exactly one of the two.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mnemos.models import Memory, MemoryCreate, SearchResult

if TYPE_CHECKING:
    from mnemos.config import Settings
    from mnemos.manager import MemoryManager

logger = logging.getLogger(__name__)


class MnemosSDK:
    """Typed facade over ``MemoryManager`` — see the module docstring."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        manager: MemoryManager | None = None,
    ) -> None:
        """Create the SDK over exactly one manager.

        Args:
            settings: Construct a NEW ``MemoryManager`` from these
                settings (local-first single-process use).
            manager: Reuse an EXISTING manager (tests spy on it; servers
                embed the SDK over their own instance).

        Raises:
            ValueError: Neither or both arguments were given.
        """
        from mnemos.manager import MemoryManager as _ConcreteManager

        if manager is not None:
            if settings is not None:
                raise ValueError("pass exactly one of settings or manager")
            self._manager: MemoryManager = manager
        else:
            if settings is None:
                raise ValueError("pass exactly one of settings or manager")
            self._manager = _ConcreteManager(settings)

    @property
    def manager(self) -> MemoryManager:
        """The underlying manager (escape hatch for surfaced operations)."""
        return self._manager

    def close(self) -> None:
        """Release the underlying manager (only when the SDK owns it)."""
        self._manager.close()

    # ── Five verbs + rewrite ─────────────────────────────────────────────

    def remember(
        self,
        content: str,
        project: str,
        agent: str,
        **kw: Any,
    ) -> Memory:
        """Store one memory (delegates to ``MemoryManager.add``).

        ``kw`` passes through to the ``MemoryCreate`` fields (``title``,
        ``tags``, ``memory_type``, ``source``, ``metadata``, …). The
        entry enters the knowledge pipeline at ``raw`` with the
        write-path secret scan and tag contract exactly as the MCP/REST
        write surfaces apply them.
        """
        data = MemoryCreate(content=content, **kw)
        memory = self._manager.add(data, project=project, agent=agent)
        logger.info("sdk.remember: project=%s agent=%s id=%s", project, agent, memory.id)
        return memory

    def recall(
        self,
        query: str,
        project: str,
        **kw: Any,
    ) -> list[SearchResult]:
        """Hybrid RRF recall (delegates to ``MemoryManager.search``).

        ``kw`` passes through (``tags``, ``agent``, ``limit``,
        ``hybrid_alpha``, …). Issuance scanning is the CALLING channel's
        boundary duty (the manager returns stored rows) — same contract
        as the MCP/REST search surfaces.
        """
        return self._manager.search(query=query, project=project, **kw)

    def forget(self, memory_id: str, project: str) -> bool:
        """Delete one memory, project-guarded (``get`` + ``delete``).

        The guard is the facade's ONE boundary check: ``delete`` itself
        is global by id, so a facade caller scoped to a project must not
        be able to remove another project's memory. Unknown id →
        ``False``; known id under a DIFFERENT project → ``ValueError``
        (no cross-project deletion oracle beyond the error itself).
        """
        memory = self._manager.get(memory_id)
        if memory is None:
            return False
        if memory.project != project:
            raise ValueError("memory belongs to a different project (cross-project delete denied)")
        deleted = self._manager.delete(memory_id)
        if deleted:
            logger.info("sdk.forget: project=%s id=%s", project, memory_id)
        return deleted

    def stats(self, project: str | None = None) -> dict[str, Any]:
        """Server stats (delegates to ``MemoryManager.stats``).

        ``project=None`` returns the full manager envelope. With a
        ``project``, the same envelope gains two presentation keys —
        ``project`` and ``project_total`` (from the manager's own
        per-project counts) — so adapter dashboards get their slice
        without a second data path.
        """
        result = self._manager.stats()
        if project is not None:
            projects = result.get("projects", {})
            if not isinstance(projects, dict):
                projects = {}
            result["project"] = project
            result["project_total"] = int(projects.get(project, 0))
        return result

    def assemble_context(
        self,
        session: str,
        project: str,
        **kw: Any,
    ) -> dict[str, Any]:
        """Assemble the model-facing context block.

        Delegates to ``MemoryManager.assemble_context`` — the ADR-0017
        D1 fixed pipeline (recall → optional CCR → filter → MANDATORY
        secret scan → CacheAligner → budget) with provenance on every
        injected block. ``kw`` passes through (``file``, ``budget``,
        ``mode``, ``expand_ccr``, ``agent``, ``query``, …).
        """
        return self._manager.assemble_context(session=session, project=project, **kw)

    def rewrite(
        self,
        original_content: str,
        project: str,
        agent: str,
        session: str,
        **kw: Any,
    ) -> dict[str, Any]:
        """Report one ``on_context_rewrite`` event (ADR-0018).

        Delegates to ``MemoryManager.context_rewrite`` — idempotent
        (content-addressed event key), version-less; the original is
        stored losslessly through the knowledge pipeline. ``kw`` passes
        through (``supersedes``, ``diff``, ``include_marker``).
        """
        return self._manager.context_rewrite(
            content=original_content,
            project=project,
            agent=agent,
            session=session,
            **kw,
        )
