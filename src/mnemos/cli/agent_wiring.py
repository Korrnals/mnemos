"""Agent MCP wiring — add ``mnemos/*`` tools to GCW agent frontmatter.

This module extends the integration layer with **agent MCP wiring**: it
detects ``*.agent.md`` files in ``~/.copilot/agents/``, parses their YAML
frontmatter, and adds ``mnemos/*`` (wildcard) or individual
``mnemos/mnemos_*`` tool references to the ``tools:`` array.

Design constraints:

* **Only ``tools:`` is touched** — never ``model:``, ``model_tier:``,
  ``agents:``, or any other frontmatter key.
* **Agents with ``tool_profile:`` are skipped** — those are resolved by
  the GCW installer, not by us. Mutating them would be overwritten on the
  next ``make install-all``.
* **Idempotent** — re-running does not duplicate ``mnemos/*`` entries.
* **Formatting preserved** — we use ``python-frontmatter`` which round-trips
  the YAML structure without mangling block/flow styles.
* **Safe** — the original file is only rewritten when the frontmatter
  actually changes. A backup is not written (the change is trivially
  reversible by removing the token), but ``--dry-run`` is supported.

Public API:

* :func:`detect_agents` — scan a directory, return parsed agent metadata.
* :func:`wire_agent` — wire a single agent file (wildcard or precise mode).
* :func:`wire_agents` — batch wire a list of agents.
* :func:`verify_agents` — aggregate wiring status for ``integration verify``.
* :class:`AgentInfo`, :class:`WireResult`, :class:`WireStatus` — data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "DEFAULT_AGENTS_DIR",
    "MNEMOS_TOOLS",
    "MNEMOS_WILDCARD",
    "AgentInfo",
    "AgentVerifySummary",
    "WireResult",
    "WireStatus",
    "detect_agents",
    "verify_agents",
    "wire_agent",
    "wire_agents",
]

# ── Constants ─────────────────────────────────────────────────────────────────

#: Default GCW agents directory (``~/.copilot/agents``).
DEFAULT_AGENTS_DIR = Path.home() / ".copilot" / "agents"

#: Individual mnemos MCP tool names used in **precise** mode.
#:
#: ``watch_*`` tools are admin-only (start/stop file watchers) and are
#: intentionally excluded from precise mode — they are not appropriate for
#: general agent wiring.
MNEMOS_TOOLS: tuple[str, ...] = (
    "mnemos/mnemos_add",
    "mnemos/mnemos_search",
    "mnemos/mnemos_recall_context",
    "mnemos/mnemos_agent_recall",
    "mnemos/mnemos_save_context",
    "mnemos/mnemos_list_recent",
    "mnemos/mnemos_list_tags",
    "mnemos/mnemos_ingest_url",
    "mnemos/mnemos_stats",
    "mnemos/mnemos_auto_collect_status",
)

#: Wildcard token granting all mnemos tools in one entry.
MNEMOS_WILDCARD = "mnemos/*"


# ── Data models ───────────────────────────────────────────────────────────────


class WireStatus(StrEnum):
    """Outcome of a single ``wire_agent`` call."""

    WIRED = "wired"
    ALREADY_WIRED = "already_wired"
    SKIPPED_TOOL_PROFILE = "skipped_tool_profile"
    SKIPPED_NO_FRONTMATTER = "skipped_no_frontmatter"
    ERROR = "error"
    DRY_RUN = "dry_run"


@dataclass(frozen=True)
class AgentInfo:
    """Parsed metadata for a single agent file.

    Returned by :func:`detect_agents`. Immutable so callers can safely
    aggregate and sort without mutation risk.
    """

    path: Path
    name: str
    filename: str
    has_mnemos: bool
    uses_tool_profile: bool
    tools_count: int
    has_tools: bool


@dataclass
class WireResult:
    """Result of wiring a single agent file."""

    path: Path
    name: str
    status: WireStatus
    note: str = ""
    tools_added: list[str] = field(default_factory=list)


# ── Detection ─────────────────────────────────────────────────────────────────


def _has_mnemos_in_tools(tools: object) -> bool:
    """Return ``True`` if any element in ``tools`` starts with ``mnemos/``."""
    if not isinstance(tools, list):
        return False
    return any(isinstance(t, str) and t.startswith("mnemos/") for t in tools)


def detect_agents(agents_dir: Path | None = None) -> list[AgentInfo]:
    """Scan ``agents_dir`` for ``*.agent.md`` files and parse frontmatter.

    Args:
        agents_dir: Directory to scan. Defaults to
            :data:`DEFAULT_AGENTS_DIR` (``~/.copilot/agents``).

    Returns:
        Sorted list of :class:`AgentInfo` (by agent ``name``, fallback
        filename). Agents with unparseable frontmatter are included with
        ``name="<filename>"`` and ``has_tools=False`` so the caller can
        report them — they are never silently dropped.
    """
    directory = agents_dir or DEFAULT_AGENTS_DIR
    if not directory.is_dir():
        return []

    infos: list[AgentInfo] = []
    for path in sorted(directory.glob("*.agent.md")):
        info = _parse_agent(path)
        infos.append(info)
    return infos


def _parse_agent(path: Path) -> AgentInfo:
    """Parse a single agent file into :class:`AgentInfo`.

    Robust against malformed frontmatter: if parsing fails, the agent is
    returned with safe defaults so it appears in reports but is skipped
    during wiring.
    """
    filename = path.name
    try:
        post = frontmatter.load(path)
    except Exception:
        # Malformed frontmatter — report it, but don't crash detection.
        return AgentInfo(
            path=path,
            name=filename,
            filename=filename,
            has_mnemos=False,
            uses_tool_profile=False,
            tools_count=0,
            has_tools=False,
        )

    metadata = post.metadata
    name = str(metadata.get("name", filename))
    has_tool_profile = "tool_profile" in metadata
    tools = metadata.get("tools")
    has_tools = isinstance(tools, list)
    tools_count = len(tools) if isinstance(tools, list) else 0
    has_mnemos = _has_mnemos_in_tools(tools)

    return AgentInfo(
        path=path,
        name=name,
        filename=filename,
        has_mnemos=has_mnemos,
        uses_tool_profile=has_tool_profile,
        tools_count=tools_count,
        has_tools=has_tools,
    )


# ── Wiring ────────────────────────────────────────────────────────────────────


def _build_tools_to_add(mode: str) -> list[str]:
    """Return the list of tool tokens to add for a given wiring mode.

    Args:
        mode: ``"wildcard"`` (default) or ``"precise"``.

    Raises:
        ValueError: if ``mode`` is not recognised.
    """
    if mode == "wildcard":
        return [MNEMOS_WILDCARD]
    if mode == "precise":
        return list(MNEMOS_TOOLS)
    raise ValueError(
        f"Unknown wiring mode: {mode!r} (expected 'wildcard' or 'precise')"
    )


def _filter_missing(tools: list[str], existing: list[str]) -> list[str]:
    """Return tokens from ``tools`` not already present in ``existing``.

    Compares by exact string match. This keeps the operation idempotent:
    re-running with the same mode adds nothing.
    """
    existing_set = set(existing)
    return [t for t in tools if t not in existing_set]


def wire_agent(
    agent_path: Path,
    mode: str = "wildcard",
    *,
    dry_run: bool = False,
) -> WireResult:
    """Wire a single agent file with mnemos MCP tools.

    Args:
        agent_path: Path to the ``*.agent.md`` file.
        mode: ``"wildcard"`` (add ``mnemos/*``) or ``"precise"`` (add
            individual ``mnemos/mnemos_*`` tokens).
        dry_run: If ``True``, report what would change without writing.

    Returns:
        :class:`WireResult` describing the outcome.

    The function is idempotent: an agent that already has the requested
    tokens is reported as ``ALREADY_WIRED`` and left untouched.
    """
    to_add = _build_tools_to_add(mode)

    try:
        post = frontmatter.load(agent_path)
    except Exception as exc:
        return WireResult(
            path=agent_path,
            name=agent_path.name,
            status=WireStatus.ERROR,
            note=f"frontmatter parse failed: {exc}",
        )

    metadata = post.metadata
    name = str(metadata.get("name", agent_path.name))

    # Skip agents that use tool_profile — the installer owns their tools.
    if "tool_profile" in metadata:
        return WireResult(
            path=agent_path,
            name=name,
            status=WireStatus.SKIPPED_TOOL_PROFILE,
            note="uses tool_profile (resolved by GCW installer)",
        )

    tools = metadata.get("tools")
    existing_tools: list[str] = list(tools) if isinstance(tools, list) else []

    # Determine which tokens are actually missing.
    missing = _filter_missing(to_add, existing_tools)

    if not missing:
        return WireResult(
            path=agent_path,
            name=name,
            status=WireStatus.ALREADY_WIRED,
            note=f"already has {mode} mnemos tools",
        )

    # Build the new tools list (preserve order, append missing at end).
    new_tools = [*existing_tools, *missing]

    if dry_run:
        return WireResult(
            path=agent_path,
            name=name,
            status=WireStatus.DRY_RUN,
            note=f"would add {len(missing)} tool(s): {', '.join(missing)}",
            tools_added=missing,
        )

    # Mutate metadata and write back.
    metadata["tools"] = new_tools
    try:
        frontmatter.dump(post, agent_path)
    except Exception as exc:
        return WireResult(
            path=agent_path,
            name=name,
            status=WireStatus.ERROR,
            note=f"write failed: {exc}",
        )

    return WireResult(
        path=agent_path,
        name=name,
        status=WireStatus.WIRED,
        note=f"added {len(missing)} tool(s)",
        tools_added=missing,
    )


def wire_agents(
    agents: Sequence[AgentInfo],
    mode: str = "wildcard",
    *,
    dry_run: bool = False,
) -> list[WireResult]:
    """Wire a batch of agents, returning one result per agent.

    Agents that use ``tool_profile`` or are already wired are skipped
    (reported, not mutated). Agents that produced an error are included
    in the result list so the caller can report them.
    """
    results: list[WireResult] = []
    for agent in agents:
        result = wire_agent(agent.path, mode=mode, dry_run=dry_run)
        results.append(result)
    return results


# ── Verification ──────────────────────────────────────────────────────────────


@dataclass
class AgentVerifySummary:
    """Aggregate wiring status for ``integration verify`` and ``doctor``."""

    total: int = 0
    wired: int = 0
    already_wired: int = 0
    skipped_tool_profile: int = 0
    unwired: int = 0
    errors: int = 0
    unwired_names: list[str] = field(default_factory=list)

    @property
    def all_wired(self) -> bool:
        """``True`` if every agent is wired or legitimately skipped."""
        return self.total > 0 and self.unwired == 0 and self.errors == 0


def verify_agents(agents_dir: Path | None = None) -> AgentVerifySummary:
    """Aggregate wiring status across all agents in ``agents_dir``.

    Used by ``mnemos integration verify`` (agents section) and
    ``mnemos doctor`` (agent wiring check).
    """
    infos = detect_agents(agents_dir)
    summary = AgentVerifySummary(total=len(infos))

    for info in infos:
        if info.uses_tool_profile:
            summary.skipped_tool_profile += 1
        elif info.has_mnemos:
            summary.wired += 1
        else:
            summary.unwired += 1
            summary.unwired_names.append(info.name)

    return summary
