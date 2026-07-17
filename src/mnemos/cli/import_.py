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

Import validation (ArchCom 2026-07-17 federation contract §3.1, issue #86):

* **Content** — max length (configurable, default 1 MiB), no control
  characters except ``\\n`` and ``\\t``, valid UTF-8.
* **Tags** — reuses ``validate_tag_contract``; max 32 tags, max 128 chars
  per tag.
* **Title** — max 256 chars, no control characters.
* **Prompt-injection patterns** — ``[INST]``, ``<|im_start|>``,
  ``"ignore previous instructions"``, ``"system:"``, ``"</s>"`` — logged
  at WARNING (NOT blocked; legitimate content may discuss injection).
* **Schema drift** — fields not in the current ``Memory`` schema are
  rejected with a field-level error.
"""

from __future__ import annotations

import functools
import io
import json
import logging
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

logger = logging.getLogger(__name__)

__all__ = [
    "ImportMode",
    "ImportResult",
    "run_import",
    "validate_import_payload",
    "validate_import_record",
]


# ── Validation constants ───────────────────────────────────────────────────────

#: Maximum content length accepted on import (chars). Configurable via
#: ``Settings.import_validation.max_content_chars`` (see config.py); the
#: CLI uses this default when the manager does not override it.
DEFAULT_MAX_CONTENT_CHARS: int = 1_048_576  # 1 MiB

#: Maximum tag count per record.
MAX_TAGS: int = 32

#: Maximum tag length (chars).
MAX_TAG_LEN: int = 128

#: Maximum title length (chars).
MAX_TITLE_LEN: int = 256

#: Allowed control characters in content (everything else in the C0/C1
#: ranges is rejected). Stored as a frozenset for O(1) lookup.
_ALLOWED_CONTROL_CHARS: frozenset[int] = frozenset({ord("\n"), ord("\t")})

#: Prompt-injection patterns. We LOG them at WARNING but do NOT block —
#: content may legitimately discuss prompt injection (security research,
#: runbooks, training material). Each entry: (pattern_name, substring).
#: Matching is case-insensitive substring (not regex) to keep it fast
#: and avoid ReDoS on untrusted content.
_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("chatml-im-start", "<|im_start|>"),
    ("chatml-im-end", "<|im_end|>"),
    ("llama-inst", "[inst]"),
    ("llama-inst-close", "[/inst]"),
    ("ignore-previous", "ignore previous instructions"),
    ("system-prefix", "system:"),
    ("eos-token", "</s>"),
)


# ── Validation report ─────────────────────────────────────────────────────────


@dataclass
class ImportValidationReport:
    """Aggregate result of validating a payload (used by dry-run + real run)."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    records_validated: int = 0
    records_with_warnings: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "records_validated": self.records_validated,
            "records_with_warnings": self.records_with_warnings,
        }


# ── Per-record validation ─────────────────────────────────────────────────────


def _has_disallowed_control_chars(text: str) -> bool:
    """Return True if ``text`` contains a C0/C1 control char not in the allow-list."""
    for ch in text:
        o = ord(ch)
        # C0: 0x00-0x1F, C1: 0x7F-0x9F. Allow \n (0x0A) and \t (0x09).
        if (o < 0x20 or 0x7F <= o <= 0x9F) and o not in _ALLOWED_CONTROL_CHARS:
            return True
    return False


def validate_import_record(
    entry: dict[str, Any],
    *,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    memory_id: str | None = None,
) -> tuple[list[str], list[str]]:
    """Validate a single memory entry from an export payload.

    Args:
        entry: The JSON dict for one memory record.
        max_content_chars: Maximum allowed content length.
        memory_id: Optional id hint for error messages (falls back to
            ``entry.get("id")``).

    Returns:
        ``(errors, warnings)`` — ``errors`` are fatal (record is rejected),
        ``warnings`` are non-fatal (prompt-injection mentions, etc.).
        Field-level error messages of the form
        ``"<id>:<field>: <reason>"`` so the caller can surface them
        directly.
    """
    errors: list[str] = []
    warnings: list[str] = []
    rid = memory_id or entry.get("id", "?")

    # ── Schema drift: reject unknown fields ──────────────────────────────
    # Build the allowed field set from the Memory model. We use a cached
    # frozenset computed once (lazy) to avoid re-reading the model on every
    # record. The model fields are stable across a process.
    allowed_fields = _memory_field_names()
    unknown = [k for k in entry if k not in allowed_fields]
    if unknown:
        errors.append(f"{rid}:schema: unknown fields: {sorted(unknown)}")

    # ── Content ───────────────────────────────────────────────────────────
    content = entry.get("content")
    if content is None:
        errors.append(f"{rid}:content: missing required field 'content'")
    elif not isinstance(content, str):
        errors.append(f"{rid}:content: must be a string, got {type(content).__name__}")
    else:
        if len(content) > max_content_chars:
            errors.append(
                f"{rid}:content: exceeds max length ({len(content)} > {max_content_chars} chars)"
            )
        # UTF-8 validity: a Python str is always valid Unicode, but the
        # caller may have decoded with errors='replace' — we re-encode to
        # UTF-8 and back to catch surrogate / lone surrogate issues.
        try:
            content.encode("utf-8").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError) as exc:
            errors.append(f"{rid}:content: invalid UTF-8: {exc}")
        if _has_disallowed_control_chars(content):
            errors.append(
                f"{rid}:content: contains disallowed control characters "
                "(only \\n and \\t permitted)"
            )
        # Prompt-injection scan — warn only.
        lower = content.lower()
        for pname, pat in _PROMPT_INJECTION_PATTERNS:
            if pat.lower() in lower:
                warnings.append(
                    f"{rid}:content: prompt-injection pattern '{pname}' detected "
                    "(logged, not blocked — content may legitimately discuss injection)"
                )

    # ── Title ─────────────────────────────────────────────────────────────
    title = entry.get("title")
    if title is not None:
        if not isinstance(title, str):
            errors.append(f"{rid}:title: must be a string, got {type(title).__name__}")
        else:
            if len(title) > MAX_TITLE_LEN:
                errors.append(
                    f"{rid}:title: exceeds max length ({len(title)} > {MAX_TITLE_LEN} chars)"
                )
            if _has_disallowed_control_chars(title):
                errors.append(f"{rid}:title: contains disallowed control characters")

    # ── Tags ──────────────────────────────────────────────────────────────
    tags = entry.get("tags", [])
    if not isinstance(tags, list):
        errors.append(f"{rid}:tags: must be a list, got {type(tags).__name__}")
    else:
        if len(tags) > MAX_TAGS:
            errors.append(f"{rid}:tags: exceeds max count ({len(tags)} > {MAX_TAGS})")
        for t in tags:
            if not isinstance(t, str):
                errors.append(f"{rid}:tags: every tag must be a string, got {type(t).__name__}")
                break
            if len(t) > MAX_TAG_LEN:
                errors.append(
                    f"{rid}:tags: tag exceeds max length ({len(t)} > {MAX_TAG_LEN}): {t[:32]!r}…"
                )
                break
        # Reuse the tag contract validator (lax mode → patchable errors
        # become warnings; strict mode raises which we surface as error).
        if tags and all(isinstance(t, str) for t in tags):
            from mnemos.models import TagContractError, validate_tag_contract

            try:
                validate_tag_contract(tags, strict=True)
            except TagContractError as exc:
                errors.append(f"{rid}:tags: tag contract violation: {exc}")

    return errors, warnings


@functools.lru_cache(maxsize=1)
def _memory_field_names() -> frozenset[str]:
    """Return the set of allowed Memory model field names (cached)."""
    return frozenset(Memory.model_fields.keys())


# ── Payload-level validation ──────────────────────────────────────────────────


def validate_import_payload(
    payload: dict[str, Any],
    *,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> ImportValidationReport:
    """Validate every record in a parsed JSON export payload.

    Does NOT write to the store. Used by ``--dry-run`` and as the first
    pass of a real import (so we reject the whole batch on schema drift
    rather than writing some records and aborting).
    """
    report = ImportValidationReport(valid=True)
    memories: list[dict[str, Any]] = payload.get("memories", [])
    if not isinstance(memories, list):
        report.errors.append("payload: 'memories' is not a list")
        report.valid = False
        return report

    for entry in memories:
        if not isinstance(entry, dict):
            report.errors.append(
                f"payload: memory entry must be an object, got {type(entry).__name__}"
            )
            report.valid = False
            continue
        report.records_validated += 1
        errs, warns = validate_import_record(entry, max_content_chars=max_content_chars)
        if errs:
            report.errors.extend(errs)
            report.valid = False
        if warns:
            report.warnings.extend(warns)
            report.records_with_warnings += 1

    return report


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

    # ── Validation pass (runs for both dry-run and real import) ──────────
    # Reject the whole batch on schema drift / contract violations rather
    # than writing some records and aborting. The validation report is
    # attached to the ImportResult so callers can surface field-level
    # errors. Prompt-injection warnings are added to result.warnings.
    validation = validate_import_payload(payload)
    if not validation.valid:
        result.errors.extend(validation.errors)
        # In dry-run we still return a report dict; in real mode we abort.
        if not dry_run:
            return result
    result.warnings.extend(validation.warnings)

    # ── Dry-run short-circuit ────────────────────────────────────────────
    # Dry-run validates and reports; it does NOT write. The summary carries
    # the validation report so the caller can decide whether to proceed.
    if dry_run:
        result.imported = validation.records_validated
        result.warnings.append(
            f"Dry-run: validated {validation.records_validated} records, "
            f"{len(validation.errors)} errors, "
            f"{validation.records_with_warnings} records with warnings."
        )
        return result

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
                    id=p.get("id", ""),
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
    except Exception as exc:
        # Re-embedding is best-effort; the memory is still persisted.
        logger.warning("re-embedding failed for memory %s: %s", memory.id, exc)


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
        result.errors.append("Restore mode requires --confirm (it wipes all existing data).")
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
        return _import_sqlite(mgr, raw, mode=mode, dry_run=dry_run, backup_dir=backup_dir)

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
