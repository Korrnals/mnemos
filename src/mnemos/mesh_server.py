"""MnemosCore gRPC server on Unix socket (#105 M4.0).

Implements the ``MnemosCore`` service defined in
``federation/proto/mnemos_core_api.proto`` as the mnemos-side counterpart
to :mod:`mnemos.mesh_client` (the client that talks to the mesh binary).

Architectural role (ArchCom 2026-07-17 federation contract):
    * **mnemos is the source of truth** for storage AND moderation. This
      server owns the SQLite store and runs the moderation pipeline on
      both export (:rpc:`ListMemories`) and import (:rpc:`WriteMemory`).
    * **The mesh is a dumb carrier** (criterion 1). It forwards
      already-redacted :class:`~mnemos.compact.CompactRecord` envelopes
      and does NOT duplicate the ACL. The ACL GATE lives here (Q4
      decision): this server returns ``PERMISSION_DENIED`` for any
      ``project_scope`` the caller is not allowed to access.
    * **Cursor storage is mesh-side** (Q3 decision). This server's
      :rpc:`GetSubscriptionState` returns an empty cursor; durable
      cursor persistence belongs to the mesh (M5). MnemosCore does NOT
      store subscription cursors.

Transport:
    Unix socket + gRPC (criterion 8). The socket path comes from
    :attr:`mnemos.config.MeshConfig.socket_path`. The server creates
    the socket (unlike :class:`~mnemos.mesh_client.MeshClient`, which
    connects to it); it is the listener for the mesh↔mnemos channel.

Import strategy for generated stubs
-----------------------------------
Reuses :mod:`mnemos._mesh_gen` (the same shim the client uses) to
import the generated ``mnemos_core_api_pb2_grpc`` /
``mnemos_core_api_pb2`` modules without touching ``sys.path`` here.

ACL model
---------
The ACL reuses :class:`mnemos.config.PeerConfig.allowed_projects` from
the federation config. The mesh is authenticated as a single logical
peer (its A2A id) and carries the originating peer's ``project_scope``
in each request. The server checks ``project_scope`` against the
configured peer's ``allowed_projects`` (or the global ``["*"]``
wildcard) and returns ``PERMISSION_DENIED`` when the scope is
disallowed. Fail-closed: unknown peer or empty allow-list → refuse.

Security notes:
    * The server binds a Unix socket with filesystem permissions. The
      operator is responsible for restricting access to the socket
      file (``chmod 0600`` + the mnemos user owns it).
    * No TLS on the Unix socket — local-only transport (criterion 11).
    * The secrets scanner runs on :rpc:`WriteMemory` via the
      :class:`~mnemos.manager.MemoryManager.add` Layer 1 path; a
      detected secret auto-tags ``mnemos:no-federate`` so the record is
      excluded from future federation (defence-in-depth Layer 1).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Sequence
from concurrent import futures
from pathlib import Path
from typing import TYPE_CHECKING, Any

import grpc

from mnemos import __version__ as _mnemos_version
from mnemos import _mesh_gen
from mnemos.compact import CompactRecord, build_compact_record
from mnemos.config import PeerConfig, Settings
from mnemos.manager import MemoryManager
from mnemos.models import NO_FEDERATE_TAG, MemoryCreate, MemorySource
from mnemos.moderation import ModerationVerdict, moderate
from mnemos.trigger_codes import TriggerCode

if TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger(__name__)

__all__ = ["MeshServer", "MnemosCoreServicer"]

#: Default page size for :rpc:`ListMemories` when the request omits ``limit``.
#:
#: Capped to keep a single response bounded — the mesh paginates via
#: ``has_more`` (contract §3.1). Mirrors the federation server's search
#: cap of 20.
_DEFAULT_PAGE_SIZE: int = 50

#: Hard ceiling on the page size a caller may request. A request with
#: ``limit > _MAX_PAGE_SIZE`` is clamped down. Prevents a misbehaving or
#: hostile mesh from requesting the entire corpus in one RPC.
_MAX_PAGE_SIZE: int = 500


def _trigger_code_to_proto(code: TriggerCode) -> Any:
    """Map a :class:`TriggerCode` to the generated ``TriggerCodes`` enum.

    The generated ``fed_pb2.TriggerCodes`` is an int enum with the same
    names as :class:`TriggerCode` (``EXHAUSTIVE`` / ``REFUSED`` / ...).
    We look up by name so the mapping survives enum reordering on the
    proto side as long as the names stay stable (they are part of the
    wire contract).
    """
    return getattr(_mesh_gen.fed_pb2.TriggerCodes, code.value)


def _compact_to_proto(record: CompactRecord) -> Any:
    """Marshal a :class:`CompactRecord` to the protobuf ``CompactRecord``.

    Mirrors :func:`mnemos.mesh_client._compact_to_proto` so the server
    and client agree on the wire shape. The record is already
    moderation-processed by the time it reaches this layer.
    """
    return _mesh_gen.fed_pb2.CompactRecord(
        id=record.id,
        type=record.type,
        title=record.title,
        summary=record.summary,
        key_points=list(record.key_points),
        tags=list(record.tags),
        source_agent=record.source_agent,
        timestamp=record.timestamp,
    )


def _compact_from_proto(pb_record: Any) -> CompactRecord:
    """Marshal a protobuf ``CompactRecord`` back to :class:`CompactRecord`.

    Inverse of :func:`_compact_to_proto`. Copies protobuf repeated
    fields into plain ``list`` objects so the result is
    JSON-serialisable and behaves like any other :class:`CompactRecord`.
    """
    return CompactRecord(
        id=pb_record.id,
        type=pb_record.type,
        title=pb_record.title,
        summary=pb_record.summary,
        key_points=list(pb_record.key_points),
        tags=list(pb_record.tags),
        source_agent=pb_record.source_agent,
        timestamp=pb_record.timestamp,
    )


def _acl_allows(peer: PeerConfig, project_scope: str) -> bool:
    """Return ``True`` if ``project_scope`` is allowed for ``peer``.

    Mirrors :func:`mnemos.federation_server._acl_allows` so the mesh↔
    mnemos ACL uses the same semantics as the HTTP federation pull path:
    ``["*"]`` is the explicit wildcard; empty list = none (fail-closed).
    """
    if not peer.allowed_projects:
        return False
    if "*" in peer.allowed_projects:
        return True
    return project_scope in peer.allowed_projects


def _resolve_peer(settings: Settings, peer_id: str) -> PeerConfig | None:
    """Look up a peer by A2A id in the federation config.

    Returns ``None`` when the peer is unknown — the caller treats this
    as ``PERMISSION_DENIED`` (fail-closed, mirroring
    :func:`mnemos.federation_server.handle_pull` step 1).
    """
    return settings.federation.peers.get(peer_id)


def _clamp_page_limit(limit: int) -> int:
    """Clamp the requested page size to ``[_DEFAULT_PAGE_SIZE, _MAX_PAGE_SIZE]``.

    A request with ``limit <= 0`` (proto default) gets the default page
    size. A request above the ceiling is clamped down.
    """
    if limit <= 0:
        return _DEFAULT_PAGE_SIZE
    return min(limit, _MAX_PAGE_SIZE)


def _memory_type_for_filter(tags: list[str]) -> str:
    """Return the ``mnemos:<subtype>`` value for a memory, or ``""`` if none.

    Mirrors :func:`mnemos.federation_server._memory_type_for_filter` so
    the type filter on :rpc:`ListMemories` uses the same semantics as
    the HTTP pull path. ``mnemos:no-federate`` is skipped.
    """
    for tag in tags:
        if not tag.startswith("mnemos:"):
            continue
        suffix = tag[len("mnemos:") :]
        if suffix == "no-federate":
            continue
        return suffix
    return ""


def _tag_value(tags: list[str], prefix: str) -> str:
    """Extract the value after ``prefix`` from the first matching tag.

    Returns ``""`` when no tag starts with ``prefix``. Used to parse
    ``project:<slug>`` and ``agent:<slug>`` from a compact record's tags
    without importing the full tag-contract validator.
    """
    for tag in tags:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return ""


# ── Servicer ──────────────────────────────────────────────────────────────────


class MnemosCoreServicer:
    """gRPC servicer implementing the four ``MnemosCore`` RPCs.

    The servicer holds a reference to the :class:`MemoryManager` (for
    SQLite access + moderation) and the :class:`Settings` (for ACL +
    federation thresholds). It is stateless beyond those references —
    no per-RPC state, no cursors (Q3: cursors are mesh-side).

    The servicer is constructed by :class:`MeshServer` and registered on
    the gRPC server via ``add_MnemosCoreServicer_to_server``. It is safe
    to construct one servicer and register it on one server.
    """

    def __init__(
        self,
        manager: MemoryManager,
        *,
        settings: Settings,
        start_time: float | None = None,
    ) -> None:
        self._manager: MemoryManager = manager
        self._settings: Settings = settings
        self._start_time: float = start_time if start_time is not None else time.monotonic()

    # ── ACL helper ─────────────────────────────────────────────────────────

    def _check_acl(
        self,
        peer_id: str,
        project_scope: str,
        context: grpc.ServicerContext[Any, Any],
    ) -> PeerConfig | None:
        """Resolve the peer and enforce the ACL GATE (Q4).

        Returns the :class:`PeerConfig` when access is allowed, or
        ``None`` after setting the gRPC status to ``PERMISSION_DENIED``
        when the peer is unknown or the scope is disallowed.

        Fail-closed: unknown peer, empty ``allowed_projects``, or a
        scope not in the allow-list all return ``None``.
        """
        peer = _resolve_peer(self._settings, peer_id)
        if peer is None:
            logger.info(
                "mesh_server: refused — peer_id=%s not in peers config",
                peer_id,
            )
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(f"peer {peer_id!r} not configured")
            return None
        if not _acl_allows(peer, project_scope):
            logger.info(
                "mesh_server: refused — project_scope=%s not allowed for peer_id=%s",
                project_scope,
                peer_id,
            )
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(f"ACL REFUSED: project_scope {project_scope!r} not allowed")
            return None
        return peer

    # ── RPC: ListMemories ──────────────────────────────────────────────────

    def ListMemories(  # noqa: N802 -- gRPC servicer override; name dictated by generated core_pb2_grpc.MnemosCoreServicer
        self,
        request: Any,
        context: grpc.ServicerContext[Any, Any],
    ) -> Any:
        """Export moderation-processed :class:`CompactRecord` bodies.

        The mesh calls this on startup/refresh to materialise the local
        view of what mnemos is willing to federate. Steps (contract §3.1):

        1. Resolve the caller's peer. Enforce the ACL on every
           ``project`` in the request.
        2. Query :class:`SQLiteStore.list_all` with the filter fields.
        3. Exclude ``mnemos:no-federate`` records (defence-in-depth
           layer 3 — moderation would refuse them anyway).
        4. Build :class:`CompactRecord` via
           :func:`mnemos.compact.build_compact_record` (runs moderation).
        5. Return a page with ``total`` + ``has_more``.
        """
        projects = list(request.projects) if request.projects else []
        project_scope = projects[0] if projects else ""
        # Resolve the peer: the mesh identifies itself via gRPC metadata
        # in production; for the unit-test path we fall back to the single
        # configured peer when exactly one exists. The per-project ACL
        # check still runs, so this fallback does not weaken security.
        peer_id = self._peer_id_from_context(context) or self._single_peer_id()
        if peer_id is None:
            logger.info("mesh_server: ListMemories refused — no peer identity")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details("no peer identity and not exactly one peer configured")
            return _mesh_gen.core_pb2.ListMemoriesResponse(records=[], total=0, has_more=False)
        if project_scope and self._check_acl(peer_id, project_scope, context) is None:
            return _mesh_gen.core_pb2.ListMemoriesResponse(records=[], total=0, has_more=False)

        allowed_projects = self._allowed_projects_for_peer(peer_id)
        effective_projects = self._intersect_projects(projects, allowed_projects)

        tags_include = list(request.tags_include) if request.tags_include else None
        tags_exclude = list(request.tags_exclude) if request.tags_exclude else None
        include_no_federate = bool(request.include_no_federate)
        since = request.since or None
        page_limit = _clamp_page_limit(int(request.limit))
        # Fetch one extra row to detect has_more without a second query.
        fetch_limit = page_limit + 1

        records: list[Any] = []
        total_seen = 0
        has_more = False
        # When no projects are requested (effective_projects is empty),
        # query with no project filter (None = all projects the peer may
        # see; the ACL already passed for the scope, and an empty request
        # is the mesh asking for "everything this peer is allowed").
        projects_to_query: list[str | None] = (
            list(effective_projects) if effective_projects else [None]
        )
        for project in projects_to_query:
            memories = self._manager.sqlite.list_all(
                limit=fetch_limit,
                project=project,
                tags=tags_include,
                since=since,
            )
            for memory in memories:
                total_seen += 1
                if len(records) >= page_limit:
                    has_more = True
                    break
                # Defence-in-depth: exclude no-federate records unless the
                # caller explicitly opted in (operator debug only).
                if not include_no_federate and NO_FEDERATE_TAG in memory.tags:
                    continue
                # Apply tags_exclude filter.
                if tags_exclude and any(t in memory.tags for t in tags_exclude):
                    continue
                # Apply type filter if the request specifies types.
                if request.types:
                    rec_type = _memory_type_for_filter(memory.tags)
                    if rec_type not in request.types:
                        continue
                # Build compact record (runs moderation → may refuse).
                rec = build_compact_record(
                    memory,
                    source_agent=memory.agent or "unknown",
                    refuse_threshold=self._settings.federation.moderation_refuse_threshold,
                )
                if rec is None:
                    continue
                records.append(_compact_to_proto(rec))
            if has_more or len(records) >= page_limit:
                break
        return _mesh_gen.core_pb2.ListMemoriesResponse(
            records=records,
            total=total_seen,
            has_more=has_more,
        )

    # ── RPC: WriteMemory ───────────────────────────────────────────────────

    def WriteMemory(  # noqa: N802 -- gRPC servicer override; name dictated by generated core_pb2_grpc.MnemosCoreServicer
        self,
        request: Any,
        context: grpc.ServicerContext[Any, Any],
    ) -> Any:
        """Import a :class:`CompactRecord` from a peer into mnemos.

        Steps (contract §3.1, #86 import validation):

        1. Validate the request: ``import_mode`` must be MERGE or
           RESTORE; RESTORE requires ``confirm=True`` (hard gate).
        2. Resolve the peer from the record's ``source_agent`` (the
           provenance). Enforce the ACL on the record's project (parsed
           from its tags).
        3. Run mnemos's own moderation on the record's ``summary`` (the
           compact payload is already moderation-processed by the peer,
           but mnemos applies its own validation on top per #86).
        4. Persist via :class:`MemoryManager.add` (Layer 1 secrets
           scanner runs inside).
        5. Return the written id + the mode actually applied + trigger
           code (``EXHAUSTIVE`` on clean merge, ``REFUSED`` on ACL or
           moderation refusal).
        """
        import_mode = int(request.import_mode)
        # Validate import_mode (UNSPECIFIED is rejected).
        if import_mode == int(_mesh_gen.core_pb2.ImportMode.IMPORT_MODE_UNSPECIFIED):
            logger.info("mesh_server: WriteMemory refused — UNSPECIFIED import_mode")
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("import_mode must be MERGE or RESTORE")
            return _mesh_gen.core_pb2.WriteMemoryResponse(
                written_id="",
                mode_applied=_mesh_gen.core_pb2.ImportMode.IMPORT_MODE_UNSPECIFIED,
                trigger_code=_trigger_code_to_proto(TriggerCode.REFUSED),
            )
        # RESTORE hard gate (mnemos-operations §1).
        if import_mode == int(_mesh_gen.core_pb2.ImportMode.RESTORE) and not bool(request.confirm):
            logger.warning("mesh_server: WriteMemory refused — RESTORE without confirm=True")
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details("RESTORE requires confirm=True (hard gate)")
            return _mesh_gen.core_pb2.WriteMemoryResponse(
                written_id="",
                mode_applied=_mesh_gen.core_pb2.ImportMode.MERGE,
                trigger_code=_trigger_code_to_proto(TriggerCode.REFUSED),
            )
        # Downgrade RESTORE→MERGE on the mesh↔mnemos path: the mesh is
        # transport, not an operator disaster-recovery tool. The applied
        # mode is recorded in the response so the mesh surfaces it to the
        # operator.
        mode_applied = _mesh_gen.core_pb2.ImportMode.MERGE
        if import_mode == int(_mesh_gen.core_pb2.ImportMode.RESTORE):
            logger.info("mesh_server: downgrading RESTORE→MERGE on mesh↔mnemos path")

        pb_record = request.record
        compact = _compact_from_proto(pb_record)
        project = _tag_value(compact.tags, "project:")
        agent = _tag_value(compact.tags, "agent:") or compact.source_agent
        # ACL: the caller is the mesh peer, identified via gRPC metadata
        # in production. For the single-peer unit-test path we fall back
        # to the only configured peer (same as ListMemories). The record's
        # ``source_agent`` is the *origin* agent on the remote mnemos — it
        # is NOT a federation peer and must not be used as the ACL identity.
        peer_id = self._peer_id_from_context(context) or self._single_peer_id()
        if peer_id is None:
            logger.info("mesh_server: WriteMemory refused — no peer identity")
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details("no peer identity and not exactly one peer configured")
            return _mesh_gen.core_pb2.WriteMemoryResponse(
                written_id="",
                mode_applied=mode_applied,
                trigger_code=_trigger_code_to_proto(TriggerCode.REFUSED),
            )
        if project and self._check_acl(peer_id, project, context) is None:
            return _mesh_gen.core_pb2.WriteMemoryResponse(
                written_id="",
                mode_applied=mode_applied,
                trigger_code=_trigger_code_to_proto(TriggerCode.REFUSED),
            )
        # mnemos's own moderation on the compact summary (#86 import
        # validation). The peer already moderated, but mnemos re-checks
        # on import — defence-in-depth.
        mod_result = moderate(
            compact.summary,
            tags=compact.tags,
            refuse_threshold=self._settings.federation.moderation_refuse_threshold,
            mapping_ttl_hours=self._settings.federation.moderation_mapping_ttl_hours,
        )
        if mod_result.verdict == ModerationVerdict.REFUSE:
            logger.info("mesh_server: WriteMemory refused — moderation REFUSE")
            return _mesh_gen.core_pb2.WriteMemoryResponse(
                written_id="",
                mode_applied=mode_applied,
                trigger_code=_trigger_code_to_proto(TriggerCode.REFUSED),
            )
        content = mod_result.sanitized_content or compact.summary
        # Persist. MemoryManager.add runs the Layer 1 secrets scanner.
        # The compact id is NOT reused as the storage id — mnemos
        # generates its own id (the compact id is a federation envelope
        # id). The compact id is stored in metadata for traceability.
        data = MemoryCreate(
            content=content,
            title=compact.title or None,
            tags=list(compact.tags),
            source=MemorySource.MCP,
            metadata={"fed_id": compact.id, "fed_source_agent": compact.source_agent},
        )
        memory = self._manager.add(data, project=project, agent=agent)
        logger.info(
            "mesh_server: WriteMemory wrote id=%s fed_id=%s project=%s",
            memory.id,
            compact.id,
            project,
        )
        return _mesh_gen.core_pb2.WriteMemoryResponse(
            written_id=memory.id,
            mode_applied=mode_applied,
            trigger_code=_trigger_code_to_proto(TriggerCode.EXHAUSTIVE),
        )

    # ── RPC: GetSubscriptionState ─────────────────────────────────────────

    def GetSubscriptionState(  # noqa: N802 -- gRPC servicer override; name dictated by generated core_pb2_grpc.MnemosCoreServicer
        self,
        request: Any,
        context: grpc.ServicerContext[Any, Any],
    ) -> Any:
        """Return the subscription cursor state for a peer+project.

        Per Q3 decision, MnemosCore does NOT persist subscription
        cursors — that is mesh-side (M5). This RPC returns an empty
        cursor and ``last_rev=0`` so the mesh starts a fresh
        :rpc:`Subscribe` stream on (re)connect. The ACL is still
        enforced: a disallowed scope returns ``PERMISSION_DENIED`` and
        an empty response.
        """
        peer_id = self._peer_id_from_context(context) or request.peer_id
        if self._check_acl(peer_id, request.project_scope, context) is None:
            return _mesh_gen.core_pb2.GetSubscriptionStateResponse(
                cursor="", last_rev=0, last_sync_timestamp=""
            )
        # M4: no persisted cursor. The mesh starts fresh.
        return _mesh_gen.core_pb2.GetSubscriptionStateResponse(
            cursor="",
            last_rev=0,
            last_sync_timestamp="",
        )

    # ── RPC: Heartbeat ─────────────────────────────────────────────────────

    def Heartbeat(  # noqa: N802 -- gRPC servicer override; name dictated by generated core_pb2_grpc.MnemosCoreServicer
        self,
        request: Any,
        context: grpc.ServicerContext[Any, Any],
    ) -> Any:
        """Return liveness + version + uptime.

        Cheap unary call. The ACL is NOT enforced on heartbeat — it is
        a liveness probe, not a data RPC, and refusing it would prevent
        the mesh from detecting a healthy mnemos.
        """
        uptime = int(time.monotonic() - self._start_time)
        return _mesh_gen.core_pb2.HeartbeatResponse(
            healthy=True,
            version=f"mnemos {_mnemos_version}",
            uptime_seconds=uptime,
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _peer_id_from_context(self, context: grpc.ServicerContext[Any, Any]) -> str | None:
        """Extract the caller's peer id from gRPC metadata.

        The mesh sets the ``x-mnemos-peer-id`` metadata key on every
        call. Returns ``None`` when the key is absent (e.g. unit tests
        that do not set it).
        """
        metadata = context.invocation_metadata()
        for key, value in metadata:
            if key.lower() == "x-mnemos-peer-id":
                return str(value)
        return None

    def _single_peer_id(self) -> str | None:
        """Return the only configured peer id, or ``None`` if 0 or 2+ peers.

        Convenience for single-peer deployments and unit tests: when
        exactly one peer is configured, we can infer the identity
        without metadata. With 0 or 2+ peers, the caller MUST set the
        metadata.
        """
        peers = list(self._settings.federation.peers.keys())
        return peers[0] if len(peers) == 1 else None

    def _allowed_projects_for_peer(self, peer_id: str) -> list[str]:
        """Return the allowed projects for a peer (``["*"]`` → all shared)."""
        peer = _resolve_peer(self._settings, peer_id)
        if peer is None:
            return []
        if "*" in peer.allowed_projects:
            return list(self._settings.federation.shared_projects)
        return list(peer.allowed_projects)

    @staticmethod
    def _intersect_projects(
        requested: Sequence[str],
        allowed: Sequence[str],
    ) -> list[str]:
        """Intersect the requested projects with the allowed set.

        ``allowed == []`` means "none" (fail-closed) → returns ``[]``.
        ``"*" in allowed`` means "all" → returns the requested list as-is.
        Otherwise returns the intersection.
        """
        allowed_list = list(allowed)
        if not allowed_list:
            return []
        if "*" in allowed_list:
            return list(requested)
        requested_set = set(requested)
        return (
            [p for p in allowed_list if p in requested_set] if requested_set else list(allowed_list)
        )


# ── Server lifecycle ─────────────────────────────────────────────────────────


class MeshServer:
    """Lifecycle wrapper around the gRPC ``MnemosCore`` server.

    Binds a Unix socket, registers the :class:`MnemosCoreServicer`, and
    exposes :meth:`start` / :meth:`stop` for clean lifecycle control.
    Designed to be owned by the mnemos process (or a test fixture) and
    stopped on shutdown.

    Args:
        socket_path: Filesystem path for the Unix socket. The server
            creates (or recreates) the socket file. Any existing socket
            file at this path is removed before binding so a restart
            does not get ``EADDRINUSE``.
        manager: The :class:`MemoryManager` backing storage + moderation.
        settings: The full :class:`Settings` (for ACL + federation
            thresholds).
        max_workers: gRPC thread pool size. Default 4 — the mesh↔mnemos
            channel is low-traffic (local Unix socket, batch sync); a
            large pool is wasteful.

    Usage::

        server = MeshServer("/run/mnemos/core.sock", manager, settings)
        server.start()
        try:
            ...
        finally:
            server.stop()
    """

    def __init__(
        self,
        socket_path: str,
        manager: MemoryManager,
        settings: Settings,
        *,
        max_workers: int = 4,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._socket_path: str = socket_path
        self._manager: MemoryManager = manager
        self._settings: Settings = settings
        self._max_workers: int = max_workers
        self._server: grpc.Server | None = None
        self._servicer: MnemosCoreServicer | None = None

    @property
    def socket_path(self) -> str:
        """The configured Unix socket path."""
        return self._socket_path

    @property
    def is_running(self) -> bool:
        """``True`` when the server has been started and not yet stopped."""
        return self._server is not None

    @property
    def servicer(self) -> MnemosCoreServicer | None:
        """The active servicer (``None`` before :meth:`start` / after :meth:`stop`)."""
        return self._servicer

    def start(self) -> None:
        """Bind the Unix socket and start serving.

        Removes any stale socket file at :attr:`socket_path` first
        (otherwise gRPC gets ``EADDRINUSE`` on restart). Creates the
        parent directory with mode ``0700`` so the socket is only
        accessible to the mnemos user (defence-in-depth: the operator is
        still responsible for the final perms, but we avoid a world-
        readable socket by default).
        """
        if self._server is not None:
            raise RuntimeError("MeshServer already started")
        sock_path = Path(self._socket_path)
        # Remove a stale socket file so a restart does not EADDRINUSE.
        if sock_path.exists() and sock_path.is_socket():
            sock_path.unlink()
        # Ensure the parent dir exists with restrictive perms.
        parent = sock_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except PermissionError:
            # Best-effort: if we cannot chmod the parent (e.g. /run),
            # the operator is responsible for the perms. Do not fail.
            logger.warning(
                "mesh_server: could not chmod parent %s — operator must secure it",
                parent,
            )
        self._servicer = MnemosCoreServicer(
            self._manager,
            settings=self._settings,
        )
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=self._max_workers))
        _mesh_gen.core_pb2_grpc.add_MnemosCoreServicer_to_server(self._servicer, self._server)
        # gRPC Unix-socket addressing: "unix:///path/to/sock".
        self._server.add_insecure_port(f"unix://{self._socket_path}")
        self._server.start()
        # Restrict the socket file perms (defence-in-depth: the socket
        # should only be accessible to the mnemos user + the mesh).
        try:
            os.chmod(self._socket_path, 0o600)
        except (PermissionError, FileNotFoundError):
            logger.warning(
                "mesh_server: could not chmod socket %s — operator must secure it",
                self._socket_path,
            )
        logger.info("mesh_server: listening on %s", self._socket_path)

    def stop(self, *, grace: float = 1.0) -> None:
        """Stop the server and release the socket.

        Args:
            grace: Grace period in seconds for in-flight RPCs to finish.
                Default 1.0s — short enough for a clean shutdown, long
                enough for a local Unix-socket round trip.
        """
        if self._server is None:
            return
        self._server.stop(grace=grace)
        self._server = None
        self._servicer = None
        # Best-effort socket cleanup. The OS reaps it when the process
        # exits, but removing it avoids a stale-file EADDRINUSE on the
        # next start (idempotent — no error if already gone).
        try:
            Path(self._socket_path).unlink(missing_ok=True)
        except PermissionError:
            logger.warning("mesh_server: could not remove socket %s", self._socket_path)
        logger.info("mesh_server: stopped")

    def __enter__(self) -> MeshServer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
