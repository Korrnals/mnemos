"""``mnemos import`` — restore memories from a JSON or SQLite export.

Two modes:

* **merge** (default, idempotent) — insert memories whose ID is absent;
  skip existing IDs (or update with ``--overwrite``). Projects are merged
  (create if absent, update paths if changed). Vectors are regenerated
  for published memories.
* **restore** (destructive) — wipe all memories, vectors, and projects,
  then import. Requires ``--confirm``. For SQLite format, the raw DB
  files are replaced (after an optional backup).

``--dry-run`` validates the export without writing.
"""

from __future__ import annotations

import io
import json
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mnemos.cli.export import (
    CompressMode,
    ExportFormat,
    decompress,
    decrypt,
    detect_sqlite_snapshot,
    is_encrypted,
    parse_json_export,
    restore_sqlite_snapshot,
)
from mnemos.models import Memory, MemoryStatus

if TYPE_CHECKING:
    from mnemos.manager import MemoryManager

__all__ = [
    "ImportMode",
    "ImportResult",
    "run_import",
]


# ── Enums / result ────────────────────────────────────────────────────────────


class ImportMode:
    MERGE = "merge"
    RESTORE = "restore"


@dataclass
class ImportResult:
    mode: str
    dry_run: bool
    imported: int = 0
    skipped: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    format_version: str | None = None
    mnemos_version: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "dry_run": self.dry_run,
            "imported": self.imported,
            "skipped": self.skipped,
            "updated": self.updated,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "format_version": self.format_version,
            "mnemos_version": self.mnemos_version,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _detect_format(data: bytes) -> ExportFormat:
    """Detect whether the payload is a SQLite snapshot or JSON."""
    if detect_sqlite_snapshot(data):
        return ExportFormat.SQLITE
    # Could be JSON (possibly compressed+encrypted); caller handles those layers.
    return ExportFormat.JSON


def _unwrap_payload(
    data: bytes,
    *,
    compress: CompressMode | None,
    encrypted: bool,
    passphrase: str | None,
) -> bytes:
    """Reverse encryption + compression to get the raw payload bytes."""
    if encrypted:
        if passphrase is None:
            raise ValueError("Payload is encrypted but no passphrase was provided.")
        data = decrypt(data, passphrase)
    if compress is not None and compress != CompressMode.NONE:
        data = decompress(data, compress)
    return data


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _memory_from_export(entry: dict[str, Any]) -> Memory:
    """Reconstruct a Memory from an export dict entry."""
    # created_at / updated_at come back as ISO strings from JSON.
    payload = dict(entry)
    if "created_at" in payload and isinstance(payload["created_at"], str):
        payload["created_at"] = _coerce_datetime(payload["created_at"]) or datetime.now()
    if "updated_at" in payload and isinstance(payload["updated_at"], str):
        payload["updated_at"] = _coerce_datetime(payload["updated_at"]) or datetime.now()
    # strict_tags is a transient field; never set it on import.
    payload.pop("strict_tags", None)
    return Memory.model_validate(payload)


# ── JSON import ───────────────────────────────────────────────────────────────


def _import_json(
    mgr: MemoryManager,
    payload: dict[str, Any],
    *,
    mode: str,
    overwrite: bool,
    dry_run: bool,
) -> ImportResult:
    result = ImportResult(mode=mode, dry_run=dry_run)
    result.format_version = payload.get("format_version")
    result.mnemos_version = payload.get("mnemos_version")

    memories: list[dict[str, Any]] = payload.get("memories", [])
    projects: list[dict[str, Any]] = payload.get("projects", [])

    # ── Restore mode: wipe first (only when not dry-run) ────────────────
    if mode == ImportMode.RESTORE and not dry_run:
        mgr.sqlite.wipe_all()
        mgr.sqlite.wipe_projects()
        mgr.vectors.wipe()

    # ── Projects ────────────────────────────────────────────────────────
    for p in projects:
        existing = mgr.sqlite.get_project_by_name(p.get("name", ""))
        if existing is None:
            if dry_run:
                result.imported += 0  # projects counted separately
            else:
                from mnemos.models import Project

                proj = Project(
                    id=p.get("id") or existing.id if existing else p.get("id", ""),
                    name=p["name"],
                    description=p.get("description", ""),
                    paths=p.get("paths", []),
                    created_at=_coerce_datetime(p.get("created_at")) or datetime.now(),
                    updated_at=_coerce_datetime(p.get("updated_at")) or datetime.now(),
                )
                if not proj.id:
                    import uuid

                    proj.id = str(uuid.uuid4())
                mgr.sqlite.save_project(proj)
        else:
            # Merge: update paths if changed.
            new_paths = p.get("paths", [])
            if new_paths and new_paths != existing.paths and not dry_run:
                existing.paths = new_paths
                mgr.sqlite.save_project(existing)

    # ── Memories ─────────────────────────────────────────────────────────
    for entry in memories:
        try:
            memory = _memory_from_export(entry)
        except Exception as exc:
            result.errors.append(f"memory {entry.get('id', '?')}: {exc}")
            continue

        existing_mem = mgr.sqlite.get(memory.id)
        if existing_mem is not None:
            if overwrite and not dry_run:
                mgr.sqlite.save(memory)
                result.updated += 1
                _reembed(mgr, memory)
            else:
                result.skipped += 1
            continue

        # New memory
        if not dry_run:
            mgr.sqlite.save(memory)
            _reembed(mgr, memory)
        result.imported += 1

    return result


def _reembed(mgr: MemoryManager, memory: Memory) -> None:
    """Re-embed a published memory into the vector store (best-effort)."""
    if memory.status != MemoryStatus.PUBLISHED:
        return
    try:
        embedding = mgr.embedder.embed(mgr._embedding_text(memory))
        mgr.vectors.upsert(
            memory.id,
            embedding,
            {"project": memory.project, "agent": memory.agent},
        )
    except Exception:
        # Re-embedding is best-effort; the memory is still persisted.
        pass


# ── SQLite import ─────────────────────────────────────────────────────────────


def _import_sqlite(
    mgr: MemoryManager,
    snapshot: bytes,
    *,
    mode: str,
    dry_run: bool,
    backup_dir: Path | None,
) -> ImportResult:
    result = ImportResult(mode=mode, dry_run=dry_run)

    # Validate the snapshot by reading the memory count from it.
    with tarfile.open(fileobj=io.BytesIO(snapshot), mode="r:gz") as tar:
        mnemos_member = None
        for m in tar.getmembers():
            if m.name == "mnemos.db":
                mnemos_member = m
                break
        if mnemos_member is None:
            result.errors.append("SQLite snapshot missing mnemos.db.")
            return result
        extracted = tar.extractfile(mnemos_member)
        if extracted is None:
            result.errors.append("SQLite snapshot mnemos.db payload is empty.")
            return result
        db_bytes = extracted.read()

    # Count memories in the snapshot (works for both dry-run and real run).
    import sqlite3
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tf.write(db_bytes)
        tmp_path = Path(tf.name)
    try:
        conn = sqlite3.connect(str(tmp_path))
        row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        snapshot_count = int(row[0]) if row else 0
        conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    if dry_run:
        result.imported = snapshot_count
        result.warnings.append("SQLite dry-run: snapshot validated, no files replaced.")
        return result

    if mode == ImportMode.RESTORE:
        restore_sqlite_snapshot(mgr, snapshot, backup_current=backup_dir)
        result.imported = snapshot_count
    else:
        # Merge for SQLite = read memories from snapshot, insert missing.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tf.write(db_bytes)
            snap_path = Path(tf.name)
        try:
            conn = sqlite3.connect(str(snap_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM memories").fetchall()
            for row in rows:
                mem_id = row["id"]
                if mgr.sqlite.get(mem_id) is None:
                    # Insert via the store's save path: reconstruct Memory.
                    # We use a lightweight import: raw SQL insert into the live DB.
                    live = mgr.sqlite._get_conn()
                    live.execute(
                        """INSERT OR REPLACE INTO memories
                           (id, content, title, tags, source, source_url,
                            memory_type, created_at, updated_at, metadata,
                            file_path, category, project, agent, status,
                            quality_score, confidence, source_coverage,
                            cluster_id, derived_from, embedding_id,
                            raw_content, clean_content, filter_profile,
                            filter_stats, filter_version)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        tuple(row[k] for k in row),
                    )
                    live.commit()
                    result.imported += 1
                else:
                    result.skipped += 1
            conn.close()
            mgr.sqlite._invalidate_caches()
        finally:
            snap_path.unlink(missing_ok=True)

    return result


# ── Orchestrator ──────────────────────────────────────────────────────────────


def run_import(
    mgr: MemoryManager,
    source: Path,
    *,
    mode: str = ImportMode.MERGE,
    overwrite: bool = False,
    confirm: bool = False,
    dry_run: bool = False,
    passphrase: str | None = None,
    passphrase_file: Path | None = None,
    compress: CompressMode | None = None,
    backup_dir: Path | None = None,
) -> ImportResult:
    """Import an export file. See module docstring for mode semantics."""
    if mode not in (ImportMode.MERGE, ImportMode.RESTORE):
        raise ValueError(f"Unknown import mode: {mode}")

    if mode == ImportMode.RESTORE and not dry_run and not confirm:
        result = ImportResult(mode=mode, dry_run=dry_run)
        result.errors.append(
            "Restore mode requires --confirm (it wipes all existing data)."
        )
        return result

    if not source.exists():
        result = ImportResult(mode=mode, dry_run=dry_run)
        result.errors.append(f"Source file not found: {source}")
        return result

    raw = source.read_bytes()

    # ── Detect encryption ────────────────────────────────────────────────
    encrypted = is_encrypted(raw)
    if encrypted:
        if passphrase is None and passphrase_file is not None:
            passphrase = passphrase_file.read_text(encoding="utf-8").strip()
        if passphrase is None:
            result = ImportResult(mode=mode, dry_run=dry_run)
            result.errors.append("File is encrypted but no passphrase provided.")
            return result

    # ── Detect format BEFORE compression unwrapping ──────────────────────
    # A SQLite snapshot is a tar.gz — it starts with the gzip magic (1f 8b).
    # If we auto-decompressed it first, we'd corrupt the format detection.
    # So: detect SQLite on the raw (possibly encrypted) bytes; only fall
    # through to JSON + compression handling if it's not a SQLite snapshot.
    if not encrypted and detect_sqlite_snapshot(raw):
        return _import_sqlite(
            mgr, raw, mode=mode, dry_run=dry_run, backup_dir=backup_dir
        )

    # ── Detect compression (JSON path) ──────────────────────────────────
    # If the caller did not specify, try to auto-detect by magic bytes.
    if compress is None and not encrypted:
        compress = CompressMode.GZIP if raw[:2] == b"\x1f\x8b" else CompressMode.NONE

    try:
        payload_bytes = _unwrap_payload(
            raw, compress=compress, encrypted=encrypted, passphrase=passphrase
        )
    except ValueError as exc:
        result = ImportResult(mode=mode, dry_run=dry_run)
        result.errors.append(str(exc))
        return result

    # ── JSON path ────────────────────────────────────────────────────────
    try:
        payload = parse_json_export(payload_bytes)
    except (json.JSONDecodeError, ValueError) as exc:
        result = ImportResult(mode=mode, dry_run=dry_run)
        result.errors.append(f"Invalid JSON export: {exc}")
        return result

    return _import_json(mgr, payload, mode=mode, overwrite=overwrite, dry_run=dry_run)
