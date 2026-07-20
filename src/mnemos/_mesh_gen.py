"""Import shim for gRPC-generated stubs (mnemos-mesh Phase 3, issue #105 M3).

The gRPC Python plugin emits flat top-level imports
(``import mnemos_core_api_pb2 as ...``) inside the generated
``*_pb2_grpc.py`` files. The generated directory
(``federation/gen/python/``) is gitignored and lives outside the
``mnemos`` package tree, so the generated modules are not importable as
ordinary package members.

This shim resolves that by inserting the generated directory on
``sys.path`` *once* and re-exporting the four generated modules under
stable, package-qualified names. Importers use::

    from mnemos._mesh_gen import core_pb2, core_pb2_grpc, fed_pb2

instead of touching ``sys.path`` themselves. The generated directory
location is resolved relative to the repo root (``federation/gen/python``)
so the shim works both from a source checkout and after ``pip install -e``.

This is the import strategy documented in :mod:`mnemos.mesh_client`.
Generated code is dynamically imported via :func:`importlib.import_module`,
so the attributes below are typed as ``Any`` by mypy (the project's
``ignore_missing_imports = true`` config treats the generated modules as
``Any``); callers apply targeted ``# type: ignore[name-defined]`` at the
proto-message construction sites in :mod:`mnemos.mesh_client`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

#: Absolute path to the gRPC-generated Python stubs directory.
#:
#: Resolved relative to this file: ``src/mnemos/_mesh_gen.py`` ->
#: ``../../federation/gen/python``. Kept as a resolved ``Path`` so the
#: shim works regardless of the current working directory.
_GEN_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent / "federation" / "gen" / "python"
)


def _ensure_gen_dir_on_path() -> None:
    """Insert the generated stubs directory on ``sys.path`` once.

    Idempotent: a no-op if the directory is already present. Called at
    import time so callers do not need to invoke it manually.
    """
    gen_dir_str = str(_GEN_DIR)
    if gen_dir_str not in sys.path:
        sys.path.insert(0, gen_dir_str)


_ensure_gen_dir_on_path()

#: ``mnemos_core_api_pb2`` — request/response messages for the MnemosCore
#: service (ListMemories, WriteMemory, GetSubscriptionState, Heartbeat).
core_pb2: Any = importlib.import_module("mnemos_core_api_pb2")

#: ``mnemos_core_api_pb2_grpc`` — ``MnemosCoreStub`` / ``MnemosCoreServicer``
#: for the core service over the Unix socket.
core_pb2_grpc: Any = importlib.import_module("mnemos_core_api_pb2_grpc")

#: ``federation_pb2`` — ``CompactRecord``, ``TriggerCodes`` and the other
#: federation.v1 messages shared between the peer and core APIs.
fed_pb2: Any = importlib.import_module("federation_pb2")


__all__ = ["_GEN_DIR", "core_pb2", "core_pb2_grpc", "fed_pb2"]
