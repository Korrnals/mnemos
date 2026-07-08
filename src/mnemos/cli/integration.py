"""Mnemos integration layer — deploy instructions/skills/prompts to agent harnesses.

This module is the engine behind the `mnemos util-*` CLI subcommands. It:

* Detects installed agent harnesses (GCW, generic Copilot, Cursor) via
  ``integrations/targets.yaml``.
* Deploys the shipped pack (``integrations/{instructions,skills,prompts}/``)
  into each detected harness, stamping every file with a version header so
  later runs can detect stale files and safely uninstall only our own.
* Verifies deployed files against the current package version.
* Updates stale files in place.
* Uninstalls only stamped files — never user-created content.

The version stamp is a Markdown HTML comment on the first non-shebang line::

    <!-- mnemos-integration: v2.0.0 -->

This is invisible in rendered Markdown but trivially greppable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "ArtefactKind",
    "DeployResult",
    "DeployStatus",
    "IntegrationManager",
    "Target",
    "TargetsConfig",
    "VerifyResult",
    "load_targets",
]

# ── Constants ─────────────────────────────────────────────────────────────────

#: The stamp injected into every deployed file (first useful line).
STAMP_PATTERN = re.compile(r"<!--\s*mnemos-integration:\s*v(\S+?)\s*-->")

#: Artefact sub-directories inside the shipped ``integrations/`` pack.
ARTEFACT_DIRS: tuple[str, ...] = ("instructions", "skills", "prompts")

#: File extensions considered deployable (skip ``.gitkeep`` and READMEs).
DEPLOYABLE_SUFFIXES: tuple[str, ...] = (".md", ".yaml", ".yml", ".json", ".txt")


class ArtefactKind(StrEnum):
    """Logical kind of an integration artefact — maps to a deploy key."""

    INSTRUCTIONS = "instructions"
    SKILLS = "skills"
    PROMPTS = "prompts"


class DeployStatus(StrEnum):
    """Per-file outcome of a deploy/verify/update operation."""

    DEPLOYED = "deployed"
    UPDATED = "updated"
    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"
    SKIPPED = "skipped"


# ── Pack-root resolution ─────────────────────────────────────────────────────


def _resolve_pack_targets() -> Path:
    """Find the shipped ``targets.yaml`` across install layouts.

    Tries the source-tree path first (editable installs / repo checkout),
    then falls back to the installed-package location via
    ``importlib.resources`` (wheels that ship ``mnemos/integrations/``).
    """
    # 1. Source-tree layout: src/mnemos/cli/integration.py → up 4 levels.
    source_candidate = (
        Path(__file__).resolve().parent.parent.parent.parent / "integrations" / "targets.yaml"
    )
    if source_candidate.is_file():
        return source_candidate

    # 2. Installed-package layout via importlib.resources.
    try:
        from importlib.resources import files

        pack_targets = files("mnemos") / "integrations" / "targets.yaml"
        if pack_targets.is_file():
            return Path(str(pack_targets))
    except (ImportError, ModuleNotFoundError, FileNotFoundError):
        pass

    # 3. Upward search for an integrations/ sibling of any parent.
    here = Path(__file__).resolve()
    for parent in here.parents:
        maybe = parent / "integrations" / "targets.yaml"
        if maybe.is_file():
            return maybe

    # 4. Last resort: CWD (used in tests).
    return Path.cwd() / "integrations" / "targets.yaml"


# ── Config model ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    """A single harness target (e.g. ``copilot``, ``cursor``)."""

    name: str
    detect_paths: tuple[Path, ...]
    deploy_map: dict[str, Path]
    format: str = "copy"

    def is_detected(self) -> bool:
        """A target is detected if ANY of its detect paths exists."""
        return any(p.exists() for p in self.detect_paths)


@dataclass(frozen=True)
class TargetsConfig:
    """Parsed ``targets.yaml`` — immutable collection of targets."""

    targets: tuple[Target, ...]

    def get(self, name: str) -> Target | None:
        return next((t for t in self.targets if t.name == name), None)

    def detected(self) -> tuple[Target, ...]:
        return tuple(t for t in self.targets if t.is_detected())


def _expand(path: str) -> Path:
    return Path(path).expanduser()


def load_targets(config_path: Path | None = None) -> TargetsConfig:
    """Load and parse ``integrations/targets.yaml``.

    Args:
        config_path: Explicit path to a ``targets.yaml``. When ``None`` the
            file shipped inside the package tree is used. Resolution order:

            1. Source-tree layout (``src/mnemos/.../integrations/targets.yaml``)
               — works for editable / repo checkouts.
            2. Installed-package layout via ``importlib.resources`` — works
               for wheels that ship ``mnemos/integrations/targets.yaml``
               (added in v2.0.1 via ``[tool.hatch.build.targets.wheel.force-include]``).
            3. Upward search for an ``integrations/`` sibling of any parent.
            4. CWD fallback (used in tests).

    Raises:
        FileNotFoundError: if the config file does not exist.
        ValueError: if the YAML is structurally invalid.
    """
    if config_path is None:
        config_path = _resolve_pack_targets()

    if not config_path.exists():
        raise FileNotFoundError(f"targets.yaml not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "targets" not in raw:
        raise ValueError(
            f"targets.yaml: expected top-level 'targets' key, got {type(raw).__name__}"
        )

    targets_raw = raw["targets"]
    if not isinstance(targets_raw, dict):
        raise ValueError(
            f"targets.yaml: 'targets' must be a mapping, got {type(targets_raw).__name__}"
        )

    targets: list[Target] = []
    for name, spec in targets_raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"targets.yaml: target '{name}' must be a mapping")

        detect_raw = spec.get("detect", [])
        if not isinstance(detect_raw, list):
            raise ValueError(f"targets.yaml: target '{name}'.detect must be a list")
        detect_paths = tuple(
            _expand(d["path"]) for d in detect_raw if isinstance(d, dict) and "path" in d
        )

        deploy_raw = spec.get("deploy", {})
        if not isinstance(deploy_raw, dict):
            raise ValueError(f"targets.yaml: target '{name}'.deploy must be a mapping")
        deploy_map = {
            kind: _expand(path) for kind, path in deploy_raw.items() if isinstance(path, str)
        }

        fmt = str(spec.get("format", "copy"))
        targets.append(
            Target(
                name=name,
                detect_paths=detect_paths,
                deploy_map=deploy_map,
                format=fmt,
            )
        )

    return TargetsConfig(targets=tuple(targets))


# ── Version stamping ──────────────────────────────────────────────────────────


def make_stamp(version: str) -> str:
    """Build the stamp comment for a given version."""
    return f"<!-- mnemos-integration: v{version} -->"


def stamp_content(content: str, version: str) -> str:
    """Inject or replace the version stamp in file content.

    The stamp is placed on the first line that is not a shebang (``#!``) or
    front-matter delimiter (``---``). If a stamp already exists it is
    replaced in-place so the file does not accumulate duplicates.
    """
    stamp = make_stamp(version)
    lines = content.splitlines(keepends=True)

    # If a stamp already exists, replace it (idempotent update).
    if _find_stamp_line(content) is not None:
        new_lines: list[str] = []
        replaced = False
        for line in lines:
            if not replaced and STAMP_PATTERN.search(line):
                new_lines.append(stamp + "\n")
                replaced = True
            else:
                new_lines.append(line)
        return "".join(new_lines)

    # No existing stamp — insert after any leading shebang/front-matter.
    insert_at = 0
    in_frontmatter = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#!"):
            insert_at = i + 1
            continue
        if stripped == "---":
            if not in_frontmatter:
                # Opening front-matter delimiter — skip the whole block.
                in_frontmatter = True
                continue
            # Closing delimiter — insert after this line.
            in_frontmatter = False
            insert_at = i + 1
            continue
        if in_frontmatter:
            continue
        break

    lines.insert(insert_at, stamp + "\n")
    return "".join(lines)


def _find_stamp_line(content: str) -> int | None:
    """Return the 0-based line index of the stamp, or ``None``."""
    for i, line in enumerate(content.splitlines()):
        if STAMP_PATTERN.search(line):
            return i
    return None


def read_stamp(content: str) -> str | None:
    """Extract the version from a stamped file, or ``None`` if unstamped."""
    match = STAMP_PATTERN.search(content)
    return match.group(1) if match else None


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class FileResult:
    """Outcome for a single file in a deploy/verify/update/uninstall run."""

    source: Path
    destination: Path
    status: DeployStatus
    deployed_version: str | None = None
    note: str = ""


@dataclass
class DeployResult:
    """Aggregate result of a deploy operation across one or more targets."""

    target_name: str
    files: list[FileResult] = field(default_factory=list)
    mcp_registered: bool = False
    mcp_note: str = ""

    @property
    def deployed_count(self) -> int:
        return sum(
            1 for f in self.files if f.status in (DeployStatus.DEPLOYED, DeployStatus.UPDATED)
        )

    @property
    def skipped_count(self) -> int:
        return sum(1 for f in self.files if f.status == DeployStatus.SKIPPED)


@dataclass
class VerifyResult:
    """Aggregate result of a verify operation."""

    target_name: str
    files: list[FileResult] = field(default_factory=list)

    @property
    def all_current(self) -> bool:
        return all(f.status == DeployStatus.CURRENT for f in self.files) and len(self.files) > 0

    @property
    def stale_count(self) -> int:
        return sum(1 for f in self.files if f.status == DeployStatus.STALE)

    @property
    def missing_count(self) -> int:
        return sum(1 for f in self.files if f.status == DeployStatus.MISSING)


@dataclass
class UninstallResult:
    """Aggregate result of an uninstall operation."""

    target_name: str
    removed: list[Path] = field(default_factory=list)
    skipped_user_files: list[Path] = field(default_factory=list)


# ── Manager ───────────────────────────────────────────────────────────────────


class IntegrationManager:
    """Orchestrates detection, deploy, verify, update, uninstall.

    The manager is stateless aside from the resolved pack root and version.
    All operations are idempotent.
    """

    def __init__(
        self,
        version: str,
        pack_root: Path | None = None,
        targets_config: TargetsConfig | None = None,
    ) -> None:
        self.version = version
        self.pack_root = pack_root or self._default_pack_root()
        self.targets = targets_config or load_targets()

    @staticmethod
    def _default_pack_root() -> Path:
        """Resolve the shipped ``integrations/`` directory.

        Works both in editable installs (``src/mnemos/...``) and wheel
        installs where the package lives under ``site-packages``. Resolution
        order mirrors :func:`_resolve_pack_targets`:

        1. Source-tree layout (``src/mnemos/.../integrations``).
        2. Installed-package layout via ``importlib.resources``.
        3. Upward search for an ``integrations/`` sibling.
        4. CWD fallback (used in tests).
        """
        here = Path(__file__).resolve()
        # 1. Editable / repo layout: src/mnemos/cli/integration.py → up 4 levels
        candidate = here.parent.parent.parent.parent / "integrations"
        if candidate.is_dir():
            return candidate
        # 2. Installed-package layout via importlib.resources.
        try:
            from importlib.resources import files

            pack_integrations = files("mnemos") / "integrations"
            if pack_integrations.is_dir():
                return Path(str(pack_integrations))
        except (ImportError, ModuleNotFoundError, FileNotFoundError):
            pass
        # 3. Fallback: search upward for an integrations/ sibling.
        for parent in here.parents:
            maybe = parent / "integrations"
            if maybe.is_dir():
                return maybe
        # 4. Last resort: assume CWD (used in tests).
        return Path.cwd() / "integrations"

    # ── Pack discovery ────────────────────────────────────────────────────────

    def _pack_files(self, kind: ArtefactKind) -> list[Path]:
        """Return sorted deployable files for a given artefact kind."""
        directory = self.pack_root / kind.value
        if not directory.is_dir():
            return []
        files: list[Path] = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if path.name == ".gitkeep":
                continue
            if path.suffix not in DEPLOYABLE_SUFFIXES:
                continue
            files.append(path)
        return files

    def _all_pack_files(self) -> dict[ArtefactKind, list[Path]]:
        return {kind: self._pack_files(kind) for kind in ArtefactKind}

    # ── Deploy ─────────────────────────────────────────────────────────────────

    def deploy(
        self,
        target_name: str,
        *,
        dry_run: bool = False,
    ) -> DeployResult:
        """Deploy all pack files to a single target.

        Files are stamped with the current version and copied into the
        target's deploy directories. Existing stamped files are updated;
        user files are never touched.
        """
        target = self.targets.get(target_name)
        if target is None:
            raise ValueError(f"Unknown target: {target_name!r}")

        result = DeployResult(target_name=target_name)

        for kind, files in self._all_pack_files().items():
            dest_dir = target.deploy_map.get(kind.value)
            if dest_dir is None:
                # Target doesn't accept this artefact kind — skip silently.
                # Not every target supports every kind (e.g. generic-copilot
                # only has prompts, copilot has instructions+skills). Logging a
                # noisy "no deploy map" row for every unsupported kind makes
                # the output look like something is broken when it isn't.
                logger.debug(
                    "target %r has no deploy map for %s — skipping silently",
                    target_name,
                    kind.value,
                )
                continue

            for src in files:
                rel = src.relative_to(self.pack_root / kind.value)
                dest = dest_dir / rel
                file_result = self._deploy_file(src, dest, dry_run=dry_run)
                result.files.append(file_result)

        return result

    def _deploy_file(self, src: Path, dest: Path, *, dry_run: bool) -> FileResult:
        """Deploy a single file, returning the outcome."""
        content = src.read_text(encoding="utf-8")
        stamped = stamp_content(content, self.version)

        if dest.exists():
            existing = dest.read_text(encoding="utf-8")
            existing_version = read_stamp(existing)
            if existing_version == self.version and existing == stamped:
                return FileResult(
                    source=src,
                    destination=dest,
                    status=DeployStatus.CURRENT,
                    deployed_version=self.version,
                    note="already up to date",
                )
            # Update in place (stale or content changed).
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(stamped, encoding="utf-8")
            return FileResult(
                source=src,
                destination=dest,
                status=DeployStatus.UPDATED,
                deployed_version=self.version,
                note=(
                    f"updated from v{existing_version}" if existing_version else "content refreshed"
                ),
            )

        # New deployment.
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(stamped, encoding="utf-8")
        return FileResult(
            source=src,
            destination=dest,
            status=DeployStatus.DEPLOYED,
            deployed_version=self.version,
        )

    # ── Verify ────────────────────────────────────────────────────────────────

    def verify(self, target_name: str) -> VerifyResult:
        """Compare deployed files against the shipped pack.

        For each pack file, checks if the deployed copy exists and is current.
        Also scans deploy directories for extra files (user-created or stale
        mnemos files no longer in the pack) and reports them as SKIPPED.
        """
        target = self.targets.get(target_name)
        if target is None:
            raise ValueError(f"Unknown target: {target_name!r}")

        result = VerifyResult(target_name=target_name)

        for kind, files in self._all_pack_files().items():
            dest_dir = target.deploy_map.get(kind.value)
            if dest_dir is None:
                continue

            # Track which dest paths correspond to pack files.
            seen_dests: set[Path] = set()
            for src in files:
                rel = src.relative_to(self.pack_root / kind.value)
                dest = dest_dir / rel
                seen_dests.add(dest)
                result.files.append(self._verify_file(src, dest))

            # Scan for extra files in the deploy dir (user files or stale mnemos files).
            if dest_dir.exists():
                for path in sorted(dest_dir.rglob("*")):
                    if not path.is_file() or path in seen_dests:
                        continue
                    if path.name == ".gitkeep":
                        continue
                    content = path.read_text(encoding="utf-8", errors="replace")
                    deployed_version = read_stamp(content)
                    if deployed_version is not None:
                        # Stamped but not in pack — stale mnemos file (removed from pack).
                        result.files.append(
                            FileResult(
                                source=Path("<not-in-pack>"),
                                destination=path,
                                status=DeployStatus.STALE,
                                deployed_version=deployed_version,
                                note="stamped file no longer in pack — safe to uninstall",
                            )
                        )
                    else:
                        result.files.append(
                            FileResult(
                                source=Path("<user-file>"),
                                destination=path,
                                status=DeployStatus.SKIPPED,
                                note="user file — not managed by mnemos",
                            )
                        )

        return result

    def _verify_file(self, src: Path, dest: Path) -> FileResult:
        if not dest.exists():
            return FileResult(
                source=src,
                destination=dest,
                status=DeployStatus.MISSING,
                note="not deployed",
            )

        existing = dest.read_text(encoding="utf-8")
        deployed_version = read_stamp(existing)
        if deployed_version is None:
            return FileResult(
                source=src,
                destination=dest,
                status=DeployStatus.SKIPPED,
                note="no mnemos stamp — user file, not ours",
            )
        if deployed_version != self.version:
            return FileResult(
                source=src,
                destination=dest,
                status=DeployStatus.STALE,
                deployed_version=deployed_version,
                note=f"deployed v{deployed_version} != current v{self.version}",
            )
        return FileResult(
            source=src,
            destination=dest,
            status=DeployStatus.CURRENT,
            deployed_version=self.version,
        )

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, target_name: str, *, dry_run: bool = False) -> DeployResult:
        """Bring stale deployed files to the current version.

        Equivalent to ``deploy`` but only touches files that carry an
        outdated stamp. Missing files are also deployed.
        """
        # deploy() already updates stale files in place, so we delegate.
        return self.deploy(target_name, dry_run=dry_run)

    # ── Uninstall ──────────────────────────────────────────────────────────────

    def uninstall(self, target_name: str, *, dry_run: bool = False) -> UninstallResult:
        """Remove ONLY files carrying the mnemos-integration stamp.

        User-created files (no stamp) are never deleted. The method scans
        each deploy directory recursively for stamped files.
        """
        target = self.targets.get(target_name)
        if target is None:
            raise ValueError(f"Unknown target: {target_name!r}")

        result = UninstallResult(target_name=target_name)

        for kind in ArtefactKind:
            dest_dir = target.deploy_map.get(kind.value)
            if dest_dir is None or not dest_dir.exists():
                continue

            for path in sorted(dest_dir.rglob("*")):
                if not path.is_file():
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                if read_stamp(content) is not None:
                    if not dry_run:
                        path.unlink()
                        # Clean up empty parent dirs (but not the deploy root).
                        self._cleanup_empty_parents(path, dest_dir)
                    result.removed.append(path)
                else:
                    result.skipped_user_files.append(path)

        return result

    @staticmethod
    def _cleanup_empty_parents(path: Path, root: Path) -> None:
        """Remove empty directories left after file deletion, up to root."""
        parent = path.parent
        while parent != root and parent.exists():
            try:
                next(parent.iterdir())
                return  # not empty — stop
            except StopIteration:
                parent.rmdir()
                parent = parent.parent

    # ── MCP registration ──────────────────────────────────────────────────────

    @staticmethod
    def _find_mcp_setup_script() -> Path | None:
        """Find ``mcp-setup.sh`` in source-tree, wheel, or upward search.

        Resolution order:

        1. **Source-tree layout** — ``src/mnemos/cli/`` → up 4 levels →
           ``scripts/mcp-setup.sh`` (editable / repo installs).
        2. **Wheel layout** — ``importlib.resources.files("mnemos") /
           "scripts" / "mcp-setup.sh"`` (pip-installed wheel).
        3. **Upward search** — walk parents of this file looking for a
           ``scripts/`` sibling (fallback for unusual layouts).

        Returns the first existing path, or ``None`` if not found anywhere.
        """
        here = Path(__file__).resolve()
        # 1. Source-tree layout: src/mnemos/cli/integration.py → up 4 levels
        candidate = here.parent.parent.parent.parent / "scripts" / "mcp-setup.sh"
        if candidate.is_file():
            return candidate
        # 2. Wheel layout via importlib.resources.
        try:
            from importlib.resources import files

            script = files("mnemos") / "scripts" / "mcp-setup.sh"
            if script.is_file():
                return Path(str(script))
        except (ImportError, ModuleNotFoundError, FileNotFoundError):
            pass
        # 3. Upward search for a scripts/ sibling.
        for parent in here.parents:
            candidate = parent / "scripts" / "mcp-setup.sh"
            if candidate.is_file():
                return candidate
        return None

    def register_mcp(self, mnemos_bin: str | None = None) -> tuple[bool, str]:
        """Invoke ``mcp-setup.sh`` to register the MCP server in VS Code.

        Returns ``(success, note)``. This is a thin wrapper — the heavy
        lifting lives in the shell script. We call it rather than reimplement
        the JSON merging to avoid drift.
        """
        import subprocess  # nosec B404 — used for trusted local mcp-setup.sh, not untrusted input

        script = self._find_mcp_setup_script()
        if script is None:
            return False, "mcp-setup.sh not found (not in wheel, not in source tree)"

        cmd: list[str] = ["bash", str(script)]
        if mnemos_bin:
            cmd += ["--command", mnemos_bin]

        try:
            proc = subprocess.run(  # nosec B603 — runs trusted local mcp-setup.sh with list args
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            return False, f"bash not available: {exc}"
        except subprocess.TimeoutExpired:
            return False, "mcp-setup.sh timed out after 60s"

        if proc.returncode == 0:
            return True, "MCP server registered"
        return False, f"mcp-setup.sh exited {proc.returncode}: {proc.stderr.strip()[:200]}"

    # ── Full setup ─────────────────────────────────────────────────────────────

    def setup(
        self,
        target_name: str,
        *,
        dry_run: bool = False,
        register_mcp: bool = True,
        mnemos_bin: str | None = None,
    ) -> DeployResult:
        """Unified setup: deploy files + register MCP + verify summary.

        This is the single entry point per owner request — ``mnemos util-setup``
        calls this for each detected target.
        """
        result = self.deploy(target_name, dry_run=dry_run)

        if register_mcp and not dry_run:
            ok, note = self.register_mcp(mnemos_bin=mnemos_bin)
            result.mcp_registered = ok
            result.mcp_note = note

        return result


def detect_all(config: TargetsConfig | None = None) -> list[Target]:
    """Return all detected targets (convenience for CLI)."""
    cfg = config or load_targets()
    return list(cfg.detected())


def deployable_targets(config: TargetsConfig | None = None) -> Sequence[str]:
    """Return names of all targets defined in the config."""
    cfg = config or load_targets()
    return [t.name for t in cfg.targets]
