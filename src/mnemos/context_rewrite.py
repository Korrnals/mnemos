"""ADR-0018 — ``on_context_rewrite`` lifecycle event (mnemos #125, Wave 2).

The harness (zcode or any MCP-capable peer) owns the *replacement policy*:
when it rewrites a block inside its own context window it emits this event
so the original becomes losslessly recoverable. The provider owns the
*guarantees*: zero-loss storage, secret scan, provenance, status gate.

Event semantics (ADR-0018 §"on_context_rewrite", verbatim requirements):

* **Idempotent** — re-delivery of the same rewrite event performs no
  duplicate writes. The idempotency key is content-addressed (the CCR
  house style): SHA-256 over the length-prefixed canonical tuple
  ``(project, agent, session, supersedes, content)``. The advisory
  ``diff`` is DELIBERATELY excluded from the key — it is not load-bearing,
  so a re-delivery carrying a different advisory diff is still the same
  event and deduplicates into the first memory. The key is persisted as
  ``metadata["rewrite_event_key"]`` and looked up before any write.
* **Version-less** — the event promises NO ordering and NO version
  chains. What would be a "version pair" elsewhere is a ``supersedes``
  edge (``memory_edges``, kind ``supersedes`` — the Phase 1 minimal
  surface); traversal/expansion is Phase 2 (ADR-0017 D2) and runs under
  the entry invariant when it arrives.
* **Pipeline entry** — the original goes through the NORMAL knowledge
  path (``MemoryManager.add``): it enters at ``raw`` and is context-
  reachable only after the pipeline advances it to ``processed`` /
  ``published`` (``CONTEXT_ADMISSIBLE_STATUSES`` gate). The Layer-1
  write-path secret scan runs inside ``add`` (a hit auto-tags
  ``mnemos:no-federate``). Rehydrate is the EXISTING scanned/gated path —
  ``mnemos_retrieve`` / ``assemble_context`` — never a new one.
* **Advisory diff** — caller-supplied, stored as metadata
  (``rewrite_diff``), never load-bearing and never echoed in responses.
  Because the diff becomes part of the persisted record, it gets its own
  Layer-1 verdict: a secret in the diff also auto-tags the record
  ``mnemos:no-federate`` (otherwise the advisory payload would federate
  unflagged through a channel that only scans ``content``), and the
  verdict is recorded as ``rewrite_diff_scan_verdict`` (clean | hit |
  unknown — the P1-a ``ccr_cache`` vocabulary). Zero-loss: the diff is
  stored verbatim either way.
* **Marker** — the CCR marker stays in the harness window (caller-side).
  When the caller asks (``include_marker=True``) the event returns the
  compress marker for the original via the existing ``compress_content``
  (content-addressed, project-scoped, Layer-1 scanned at write).

Design decisions (flagged for ArchCom ratification in the #125 report):

* **Idempotency key** — content-hash over the canonical tuple, not a
  caller-supplied event id: the harness cannot fabricate collisions, and
  identical re-deliveries dedupe even when the caller lost its event id.
  Two identical blocks replaced in two different sessions are two events
  (``session`` participates in the key) and store twice — correct, they
  are distinct windows.
* **Provenance shape** — ``metadata["source"] = "context-rewrite"`` plus
  ``metadata["rewrite_session"]``; the project/agent identity lives in
  the tag contract (``project:<slug>`` / ``agent:<slug>`` tags, enforced
  via ``validate_tag_contract`` with the caller's strictness knob).
  ``Memory.source`` stays ``MemorySource.MCP`` — the event arrives from
  the harness over the MCP/REST surface like every other tool call.
* **No new retrieval surface** — deliberately. The rehydrate roundtrip is
  verified in tests through ``assemble_context`` / ``mnemos_retrieve``.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import TYPE_CHECKING, Any, Final

from mnemos.models import MemoryCreate, MemorySource, validate_tag_contract

if TYPE_CHECKING:
    from mnemos.manager import MemoryManager

logger = logging.getLogger(__name__)

#: Provenance discriminator for rewrite-stored originals (metadata key
#: ``source``). Names the ingestion channel; the identity lives in tags.
SOURCE_CONTEXT_REWRITE: Final[str] = "context-rewrite"

#: Diff-scan verdicts (mirrors the P1-a ``ccr_cache`` vocabulary).
_DIFF_VERDICT_CLEAN: Final[str] = "clean"
_DIFF_VERDICT_HIT: Final[str] = "hit"
_DIFF_VERDICT_UNKNOWN: Final[str] = "unknown"


# ── Idempotency key ───────────────────────────────────────────────────────────


def compute_event_key(
    *,
    project: str,
    agent: str,
    session: str | None,
    supersedes: str | None,
    content: str,
) -> str:
    """Content-addressed idempotency key for one rewrite event.

    SHA-256 over the length-prefixed canonical tuple ``(project, agent,
    session, supersedes, content)``. Length prefixing makes the encoding
    injective for arbitrary content (a delimiter scheme could be spoofed
    by content containing the delimiter). The advisory ``diff`` is
    excluded by design — see the module docstring.
    """
    parts = (project, agent, session or "", supersedes or "", content)
    canonical = "".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Validation ─────────────────────────────────────────────────────────────────


def _validate(
    content: str,
    project: str,
    agent: str,
    session: str | None,
    supersedes: str | None,
    diff: str | None,
) -> None:
    """Boundary validation — raises ``ValueError`` with actionable messages."""
    if not content or not content.strip():
        raise ValueError("content is required (non-empty string)")
    if not project or not project.strip():
        raise ValueError("project is required (non-empty string)")
    if not agent or not agent.strip():
        raise ValueError("agent is required (non-empty string)")
    for name, value in (("session", session), ("supersedes", supersedes), ("diff", diff)):
        if value is not None and not value.strip():
            raise ValueError(f"{name} must be a non-empty string when provided")


# ── Layer-1 verdict for the advisory diff ─────────────────────────────────────


def _scan_diff(diff: str | None) -> tuple[str, list[str]]:
    """Scan the advisory diff at write time; return ``(verdict, extra_tags)``.

    The diff is part of the persisted record but is NOT ``content`` — the
    ``add`` write-path scanner would never see it, and a secret there
    would federate unflagged. Same Layer-1 policy as ``add``: a hit
    auto-tags ``mnemos:no-federate`` (idempotent — ``add`` skips the tag
    when already present); the diff itself is stored verbatim
    (zero-loss). A scanner error degrades to verdict ``unknown`` with NO
    tag (the background Layer-2 scanner backstops, mirroring ``add``).
    Only pattern names and counts are ever logged.
    """
    if diff is None:
        return "", []
    from mnemos.secrets_detector import detect_secrets, findings_by_pattern

    try:
        findings = detect_secrets(diff)
    except Exception as exc:  # pragma: no cover — defensive, mirrors add()
        logger.warning("rewrite diff scan failed (non-fatal, verdict=unknown): %s", exc)
        return _DIFF_VERDICT_UNKNOWN, []
    if not findings:
        return _DIFF_VERDICT_CLEAN, []
    counts = findings_by_pattern(findings)
    logger.warning(
        "rewrite diff secret hit: redactions=%d patterns=%s — raw values not logged",
        len(findings),
        counts,
    )
    return _DIFF_VERDICT_HIT, ["mnemos:no-federate"]


# ── The lifecycle event ───────────────────────────────────────────────────────


def context_rewrite(
    mgr: MemoryManager,
    *,
    content: str,
    project: str,
    agent: str,
    session: str | None = None,
    supersedes: str | None = None,
    diff: str | None = None,
    include_marker: bool = False,
) -> dict[str, Any]:
    """Handle one ``on_context_rewrite`` event (ADR-0018, idempotent).

    Args:
        mgr: The owning ``MemoryManager`` (storage via the normal
            knowledge-pipeline ``add`` path; edges via the P1-a store
            methods; marker via the existing ``compress_content``).
        content: The ORIGINAL text of the replaced block — the source of
            truth, stored to LTM unchanged (zero-loss).
        project: Project slug (tag ``project:<slug>``).
        agent: Agent slug (tag ``agent:<slug>``).
        session: Optional session identifier — provenance metadata and
            part of the idempotency key (two sessions replacing
            identical content are two events).
        supersedes: Optional memory id of the block being replaced —
            creates the ``supersedes`` edge ``new → old``. Must reference
            an existing memory (validated up front; the store FK is the
            backstop).
        diff: Optional advisory was→becomes diff — stored as metadata,
            never load-bearing, never echoed.
        include_marker: When True, also return the CCR compress marker
            for the original (the caller keeps the marker in its window;
            rehydrate goes through ``mnemos_retrieve``).

    Returns:
        Event receipt: ``status`` (``stored`` | ``deduplicated``),
        ``memory_id``, ``memory_status`` (pipeline status — ``raw`` on
        first delivery), ``event_key``, echoed ``project``/``agent``/
        ``session``, ``supersedes`` (``{"to_memory_id", "edge_created"}``
        or ``None``) and ``ccr_marker`` (the full ``compress_content``
        result, only when ``include_marker``).

    Raises:
        ValueError: Invalid boundary input, tag-contract violation, or a
            ``supersedes`` target that does not exist.
    """
    _validate(content, project, agent, session, supersedes, diff)

    event_key = compute_event_key(
        project=project, agent=agent, session=session, supersedes=supersedes, content=content
    )

    # ── Idempotency: the same event re-delivered writes nothing new ──────
    existing_id = mgr.sqlite.get_memory_id_by_rewrite_event_key(event_key)
    if existing_id is not None:
        existing = mgr.get(existing_id)
        receipt: dict[str, Any] = {
            "status": "deduplicated",
            "memory_id": existing_id,
            "memory_status": existing.status.value if existing else "unknown",
            "event_key": event_key,
            "project": project,
            "agent": agent,
            "session": session,
        }
        if supersedes is not None:
            receipt["supersedes"] = _link_supersedes(mgr, existing_id, supersedes)
        else:
            receipt["supersedes"] = None
        if include_marker:
            receipt["ccr_marker"] = mgr.compress_content(content, project=project)
        logger.info(
            "context_rewrite: DEDUPLICATED event_key=%s… memory=%s project=%s agent=%s",
            event_key[:12],
            existing_id[:8],
            project,
            agent,
        )
        return receipt

    # ── Pre-flight the supersedes target (clean error before any write) ──
    if supersedes is not None and mgr.get(supersedes) is None:
        raise ValueError(f"supersedes target {supersedes!r} does not exist")

    # ── Store the original via the NORMAL knowledge-pipeline path ────────
    diff_verdict, extra_tags = _scan_diff(diff)
    # mnemos:session — the original is live session material preserved
    # verbatim; closest existing subtype. A dedicated mnemos:context-rewrite
    # subtype would extend the shared tag-contract vocabulary (flagged for
    # ArchCom ratification in the #125 report instead of landing silently).
    tags = [f"project:{project}", f"agent:{agent}", "mnemos:session", *extra_tags]
    tags = validate_tag_contract(tags, strict=mgr.settings.mnemos.strict_tag_contract)

    metadata: dict[str, Any] = {
        "source": SOURCE_CONTEXT_REWRITE,
        "rewrite_event_key": event_key,
    }
    if session is not None:
        metadata["rewrite_session"] = session
    if diff is not None:
        metadata["rewrite_diff"] = diff
        metadata["rewrite_diff_scan_verdict"] = diff_verdict

    memory = mgr.add(
        MemoryCreate(content=content, tags=tags, source=MemorySource.MCP, metadata=metadata),
        project=project,
        agent=agent,
    )

    receipt = {
        "status": "stored",
        "memory_id": memory.id,
        "memory_status": memory.status.value,
        "event_key": event_key,
        "project": project,
        "agent": agent,
        "session": session,
        "supersedes": None,
    }
    if supersedes is not None:
        receipt["supersedes"] = _link_supersedes(mgr, memory.id, supersedes)
    if include_marker:
        receipt["ccr_marker"] = mgr.compress_content(content, project=project)

    logger.info(
        "context_rewrite: stored memory=%s project=%s agent=%s edge=%s",
        memory.id[:8],
        project,
        agent,
        receipt["supersedes"] is not None,
    )
    return receipt


def _link_supersedes(mgr: MemoryManager, new_id: str, old_id: str) -> dict[str, Any]:
    """Create the ``new → old`` supersedes edge (idempotent by PK).

    The P1-a store method is ``INSERT OR IGNORE`` on the composite PK, so
    a re-delivery reports ``edge_created=False`` instead of failing. The
    FK backstop converts a vanished target into a clean ``ValueError``
    (the pre-flight in :func:`context_rewrite` normally catches this
    first; the race window between pre-flight and insert is the only gap).
    """
    try:
        created = mgr.add_memory_edge(new_id, old_id, kind="supersedes")
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"supersedes target {old_id!r} does not exist") from exc
    return {"to_memory_id": old_id, "edge_created": created}
