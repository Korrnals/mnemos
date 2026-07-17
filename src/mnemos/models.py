"""Data models for the Mnemos memory system.

Core models: TagContract (Mnemos tag validation, M2), Memory (pipeline + Context
Filter fields, M4/M10), Trace (explainability layer, M6), AgentRecallQuery
(per-agent recall, M3).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ── Enums ──────────────────────────────────────────────────────────────────────


class MemoryType(StrEnum):
    NOTE = "note"
    FACT = "fact"
    SNIPPET = "snippet"
    BOOKMARK = "bookmark"
    CONVERSATION = "conversation"
    SESSION_CONTEXT = "session_context"


class MemorySource(StrEnum):
    MANUAL = "manual"
    WEB = "web"
    FILE = "file"
    MCP = "mcp"
    OBSIDIAN = "obsidian"
    CLI = "cli"
    RULE = "rule"  # M8: path-scoped rules ingest
    SYNTHESIZED = "synthesized"  # M4: output of synthesis worker


class MemoryStatus(StrEnum):
    RAW = "raw"
    PROCESSING = "processing"
    PROCESSED = "processed"
    PUBLISHED = "published"
    ARCHIVED = "archived"


# ── Mnemos Tag Contract (M2) ──────────────────────────────────────────────────────


# Valid mnemos:* subtypes (enforced when strict_tag_contract=True)
MNEMOS_TAG_SUBTYPES: frozenset[str] = frozenset(
    {
        "session",
        "bug-pattern",
        "learning",
        "decision",
        "rule",
        "open-question",
        "checkpoint",
        "legacy",
        # Pipeline-synthesised entries (output of the synthesis worker, not
        # agent-authored). Mirrors MemorySource.SYNTHESIZED — the concept
        # already exists, the tag subtype now catches up so synthesised
        # memories can carry a valid mnemos: category instead of falling
        # back to mnemos:legacy.
        "synthesized",
        # Exclusion marker (ArchCom 2026-07-17 federation contract §4 КП-6):
        # ``mnemos:no-federate`` excludes a record from ALL external exchange
        # (batch sync + mediated pull). It is NOT a cognitive category — it
        # is an opt-out marker living in the ``mnemos:`` namespace so it
        # passes tag-contract validation without a new prefix. The auto-tagger
        # in ``secrets_detector`` (Layer 1) adds it on write when a secret is
        # detected. Owners can remove it with explicit confirmation.
        # Decision: option (a) — add to whitelist with a comment, rather than
        # a special-case bypass in ``validate_tag_contract``. Simpler, and
        # the contract explicitly says "Совместим с tag contract (не новый
        # prefix, mnemos: subtype namespace)".
        "no-federate",
    }
)


#: Tag that marks a record as excluded from all federation (batch export +
#: mediated pull). Auto-added by the write-path secrets scanner (Layer 1).
#: See ArchCom 2026-07-17 federation contract §4 КП-6 and §2.2.1.
NO_FEDERATE_TAG: str = "mnemos:no-federate"

# Allowed optional tag prefixes beyond the required ones
ALLOWED_OPTIONAL_PREFIXES: frozenset[str] = frozenset(
    {"severity:", "stack:", "applyTo:", "source:"}
)

_PROJECT_RE = re.compile(r"^project:[a-z0-9_\-]{1,64}$")
_AGENT_RE = re.compile(r"^agent:[a-z0-9_\-]{1,64}$")
_MNEMOS_RE = re.compile(r"^mnemos:[a-z][a-z0-9\-]*$")


class TagContractError(ValueError):
    """Raised when a tag set violates the Mnemos tag contract in strict mode."""


def validate_tag_contract(tags: list[str], *, strict: bool = True) -> list[str]:
    """Validate tags against the Mnemos tag contract.

    Args:
        tags: The list of tag strings to validate.
        strict: When True, raises TagContractError on any violation.
                When False (lax mode), patches the tag list with legacy
                defaults and returns it with a warning-level log entry.

    Returns:
        The (possibly augmented) tag list.

    Raises:
        TagContractError: If strict=True and any contract requirement is not met.
    """
    # Backward compat: gcw: is accepted as an alias for mnemos:
    # Old memories with gcw: tags are auto-migrated to mnemos: on validation.
    _migrated: list[str] = []
    for t in tags:
        if t.startswith("gcw:"):
            subtype = t[4:]
            if subtype in MNEMOS_TAG_SUBTYPES:
                _migrated.append(f"mnemos:{subtype}")
            else:
                _migrated.append(t)  # invalid gcw: subtype, keep as-is for error msg
        else:
            _migrated.append(t)
    tags = _migrated

    project_tags = [t for t in tags if t.startswith("project:")]
    agent_tags = [t for t in tags if t.startswith("agent:")]
    mnemos_tags = [t for t in tags if t.startswith("mnemos:")]

    # Errors that are fatal even in lax mode (ambiguous context, can't auto-patch)
    fatal_errors: list[str] = []
    # Errors that can be patched in lax mode
    patchable_errors: list[str] = []

    # --- Require exactly one project:* tag ---
    if not project_tags:
        patchable_errors.append("missing required tag: project:<slug> (exactly one required)")
    elif len(project_tags) > 1:
        fatal_errors.append(
            f"exactly one project: tag required, got {len(project_tags)}: {project_tags}"
        )
    elif not _PROJECT_RE.match(project_tags[0]):
        patchable_errors.append(
            f"invalid project: tag format '{project_tags[0]}' "
            "(must match project:[a-z0-9_-]{1,64})"
        )

    # --- Require exactly one agent:* tag ---
    if not agent_tags:
        patchable_errors.append("missing required tag: agent:<slug> (exactly one required)")
    elif len(agent_tags) > 1:
        fatal_errors.append(f"exactly one agent: tag required, got {len(agent_tags)}: {agent_tags}")
    elif not _AGENT_RE.match(agent_tags[0]):
        patchable_errors.append(
            f"invalid agent: tag format '{agent_tags[0]}' (must match agent:[a-z0-9_-]{{1,64}})"
        )

    # --- Require at least one mnemos:* tag ---
    if not mnemos_tags:
        patchable_errors.append(
            "missing required tag: mnemos:<subtype> "
            f"(valid subtypes: {', '.join(sorted(MNEMOS_TAG_SUBTYPES))})"
        )
    else:
        for mnemos_tag in mnemos_tags:
            if not _MNEMOS_RE.match(mnemos_tag):
                patchable_errors.append(f"invalid mnemos: tag format: '{mnemos_tag}'")
            else:
                subtype = mnemos_tag[len("mnemos:") :]
                if subtype not in MNEMOS_TAG_SUBTYPES:
                    patchable_errors.append(
                        f"invalid mnemos: subtype '{subtype}' — "
                        f"allowed: {', '.join(sorted(MNEMOS_TAG_SUBTYPES))}"
                    )

    # Always fatal errors raise regardless of strict flag
    if fatal_errors:
        raise TagContractError(
            "Mnemos tag contract violation(s) (always fatal):\n"
            + "\n".join(f"  - {e}" for e in fatal_errors)
        )

    if not patchable_errors:
        return list(tags)

    if strict:
        raise TagContractError(
            "Mnemos tag contract violation(s):\n" + "\n".join(f"  - {e}" for e in patchable_errors)
        )

    # Lax mode: patch the tag list rather than reject
    import logging

    logger = logging.getLogger(__name__)
    logger.warning("Tag contract violations (lax mode — auto-patching): %s", patchable_errors)
    patched = list(tags)

    # Normalize case for project/agent tags instead of dropping to "unknown".
    # This prevents duplicate namespaces (project:Project-Umbra vs
    # project:project-umbra) when callers pass mixed-case slugs.
    def _normalize_slug(tag: str, regex: re.Pattern[str], prefix: str) -> str | None:
        """Return a normalized form of ``tag`` if it can be salvaged, else None.

        Strips leading/trailing whitespace, lowercases the slug portion, and
        replaces spaces with hyphens. If the normalized form still does not
        match ``regex``, the tag is not recoverable and the caller falls back
        to the ``<prefix>unknown`` default.
        """
        slug = tag[len(prefix) :].strip()
        normalized = prefix + slug.lower().replace(" ", "-")
        return normalized if regex.match(normalized) else None

    if project_tags and not _PROJECT_RE.match(project_tags[0]):
        normalized = _normalize_slug(project_tags[0], _PROJECT_RE, "project:")
        if normalized is not None:
            patched = [normalized if t == project_tags[0] else t for t in patched]
            logger.warning("Normalized project tag: %s → %s", project_tags[0], normalized)
        else:
            patched = [t for t in patched if t != project_tags[0]] + ["project:unknown"]
    elif not project_tags:
        patched.append("project:unknown")

    if agent_tags and not _AGENT_RE.match(agent_tags[0]):
        normalized = _normalize_slug(agent_tags[0], _AGENT_RE, "agent:")
        if normalized is not None:
            patched = [normalized if t == agent_tags[0] else t for t in patched]
            logger.warning("Normalized agent tag: %s → %s", agent_tags[0], normalized)
        else:
            patched = [t for t in patched if t != agent_tags[0]] + ["agent:unknown"]
    elif not agent_tags:
        patched.append("agent:unknown")

    if not mnemos_tags:
        patched.append("mnemos:legacy")
    return patched


class TagContract(BaseModel):
    """Validated tag set with denormalised project + agent slugs."""

    tags: list[str]
    strict: bool = Field(default=True, exclude=True)
    project: str = ""
    agent: str = ""
    mnemos_subtypes: frozenset[str] = Field(default_factory=frozenset, exclude=True)

    @model_validator(mode="after")
    def _validate_and_extract(self) -> TagContract:
        # May raise TagContractError (→ caught by Pydantic as ValidationError)
        validated = validate_tag_contract(self.tags, strict=self.strict)
        self.tags = validated
        subtypes: set[str] = set()
        for tag in validated:
            if tag.startswith("project:") and not self.project:
                self.project = tag[len("project:") :]
            elif tag.startswith("agent:") and not self.agent:
                self.agent = tag[len("agent:") :]
            elif tag.startswith("mnemos:"):
                subtypes.add(tag[len("mnemos:") :])
        self.mnemos_subtypes = frozenset(subtypes)
        return self


# ── Core memory model ──────────────────────────────────────────────────────────


class Memory(BaseModel):
    """Single unified memory entry — status-driven pipeline model.

    Field groups:
      - Mnemos tag contract denormalisations (project, agent)
      - Knowledge pipeline fields (quality_score, confidence, cluster_id, derived_from …)
      - Context Filter fields (raw_content, clean_content, filter_profile …) — M10
      - Embedding tracking (embedding_id)
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str

    # ── Basic metadata ──────────────────────────────────────────────────────
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = MemorySource.MANUAL
    source_url: str | None = None
    memory_type: MemoryType = MemoryType.NOTE
    file_path: str | None = None
    category: str | None = None

    # ── Mnemos tag contract (denormalised from tags, set by MCP/TagContract layer) ──
    project: str = ""
    agent: str = ""

    # ── Knowledge pipeline status (M4) ─────────────────────────────────────
    status: MemoryStatus = MemoryStatus.RAW
    quality_score: float | None = None
    confidence: float | None = None
    source_coverage: int | None = None  # number of distinct source URLs/paths in cluster
    cluster_id: str | None = None
    derived_from: list[str] = Field(default_factory=list)  # source Memory ids
    embedding_id: str | None = None  # ChromaDB id; set when status = published

    # ── Context Filter (M10) ───────────────────────────────────────────────
    # Fields present from day 1; filter logic wired in M10.
    # Invariant: raw_content is never mutated after first write.
    raw_content: str | None = None  # immutable source payload (logs, HTML, etc.)
    clean_content: str | None = None  # filtered projection for model-facing flows
    filter_profile: str | None = None  # log | terminal | code | docs | web | default
    filter_stats: dict[str, Any] | None = None  # token + dedup reduction stats
    filter_version: str | None = None  # filter pipeline version used

    # ── Timestamps ──────────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ── Compat (retained for migration tooling) ─────────────────────────────
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Validation control (not stored) ────────────────────────────────────
    # Set strict_tags=True to enforce Mnemos tag contract on construction.
    strict_tags: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _maybe_validate_tags(self) -> Memory:
        if self.strict_tags and self.tags:
            validate_tag_contract(self.tags, strict=True)
        return self

    def auto_title(self) -> str:
        """Generate a title from the first line of content if not set."""
        if self.title:
            return self.title
        first_line = self.content.strip().split("\n")[0][:100]
        return first_line.lstrip("# ").strip() or "Untitled"

    def effective_content(self) -> str:
        """Return clean_content if available, otherwise fall back to content.

        This is the default payload for retrieval and model-facing flows.
        Use raw_content for audit / drill-down.
        """
        return self.clean_content or self.content


class MemoryCreate(BaseModel):
    content: str
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = MemorySource.MANUAL
    source_url: str | None = None
    memory_type: MemoryType = MemoryType.NOTE
    metadata: dict[str, Any] = Field(default_factory=dict)
    category: str | None = None
    # M10: explicit filter profile; if omitted, heuristics select one
    filter_profile: str | None = None
    # Allow override for path-scoped rules ingest (M8) and migrations (M13)
    status: MemoryStatus = MemoryStatus.RAW


class RuleIngestRequest(BaseModel):
    """Request body for POST /rules/ingest."""

    rules_dir: str
    project: str = ""
    agent: str = ""
    pattern: str = "*.instructions.md"


class RuleRemoveRequest(BaseModel):
    """Request body for DELETE /rules/ingest."""

    file_path: str


class FilterRequest(BaseModel):
    """Request body for POST /filter/{memory_id}."""

    profile: str | None = None
    budget: int | None = None


class MemoryUpdate(BaseModel):
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    memory_type: MemoryType | None = None
    metadata: dict[str, Any] | None = None
    status: MemoryStatus | None = None
    category: str | None = None
    quality_score: float | None = None
    confidence: float | None = None
    cluster_id: str | None = None


class SearchQuery(BaseModel):
    query: str
    tags: list[str] | None = None
    source: MemorySource | None = None
    memory_type: MemoryType | None = None
    status: MemoryStatus | None = None
    project: str | None = None
    agent: str | None = None  # M3: per-agent filter
    current_file_path: str | None = None  # M8: file-context boost
    limit: int = 20
    hybrid_alpha: float | None = None  # override config default
    include_raw: bool = False  # M10: drill-down to raw_content


# ── Per-agent recall (M3) ──────────────────────────────────────────────────────


class AgentRecallQuery(BaseModel):
    """M3 — first-class per-agent recall query."""

    agent: str
    project: str | None = None
    query: str | None = None  # if None: return most recent N entries for agent
    limit: int = 20
    include_raw: bool = False


class SearchResult(BaseModel):
    memory: Memory
    score: float
    search_type: str  # "semantic" | "fts" | "hybrid"


# ── Trace model (M6 — explainability layer) ────────────────────────────────────


class Trace(BaseModel):
    """Per-pipeline-step audit record.

    Security note: rationale_summary is a ≤200-char human-readable summary.
    Raw LLM chain-of-thought is NEVER stored here.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_label: str  # cluster | synthesize | publish | recall
    project: str
    step: str
    item_id: str | None = None  # Memory id being processed
    llm_called: bool = False
    llm_done: bool = False
    cache_hit: bool = False
    fallback_used: bool = False
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_per_sec: float = 0.0
    rationale_summary: str = ""  # ≤200 chars — NO chain-of-thought
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("rationale_summary")
    @classmethod
    def _truncate_rationale(cls, v: str) -> str:
        return v[:200]


# ── Project model ─────────────────────────────────────────────────────────────


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    paths: list[str] = Field(default_factory=list)


class BulkDeleteRequest(BaseModel):
    ids: list[str]


class BulkTagRequest(BaseModel):
    ids: list[str]
    add_tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)


# ── Pipeline models (M4) ───────────────────────────────────────────────────────


class ClusterResult(BaseModel):
    """Output of the cluster worker."""

    cluster_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    memory_ids: list[str] = Field(default_factory=list)
    centroid: list[float] | None = None  # mean embedding of cluster members
    representative_id: str | None = None  # id of the most central memory


class SynthesisResult(BaseModel):
    """Output of the LLM synthesis worker."""

    draft_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    cluster_id: str
    content: str
    title: str | None = None
    quality_score: float = 0.0
    confidence: float = 0.0
    source_coverage: int = 0
    model_used: str = ""
    prompt_version: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class QualityResult(BaseModel):
    """Output of the quality gate stage."""

    passed: bool
    memory_id: str
    quality_score: float = 0.0
    confidence: float = 0.0
    source_coverage: int = 0
    failures: list[str] = Field(default_factory=list)
    rationale: str = ""  # ≤200 chars


class PublishResult(BaseModel):
    """Output of the publish stage."""

    memory_id: str
    published: bool
    vector_indexed: bool = False
    previous_status: str = ""
