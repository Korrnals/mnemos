"""E2 checkpoint padding — the drowning mass (E0 §3.3).

The live store that produced the measured governance drowning carries
1457 entries of which 839 (58%) are checkpoints (30 rules, 217
decisions). E0 §3.3 pins the COMBINED experimental corpus profile at
**checkpoints 58% ± 2 pp** so the drowning condition is reproduced,
not sanitized: governance must drown under a realistic checkpoint
flood, exactly as it does live.

This module generates the padding: **240 synthetic checkpoints** (+
the golden corpus's own 4 = 244 of 421 combined entries = 57.96%),
deterministically — index-driven combinatorics over fixed fragment
pools, no RNG, no wall-clock, no corpus-module imports. Content is
project-scoped shipped-work prose in the golden checkpoint style
(``Checkpoint: <project> <version> — <items>. <verification>.``):
plausible, repetitive-in-the-way-real-checkpoints-are-repetitive, and
semantically inert with respect to every G-gov/G-neg query (checkpoints
report WHAT shipped; the governance stratum states norms — no
checkpoint correctly answers any stratum query).

Hygiene (E0 §4.4, test-enforced): declarative prose, no control-token
shapes (the word ``system`` and friends never precede a colon), no
secrets, all entries ``published``/admissible.
"""

from __future__ import annotations

from benchmarks.corpus.corpus import PROJECTS, GoldenEntry

#: Number of padding checkpoints. Derived from the E0 §3.3 pin:
#: (4 golden checkpoints + 240) / (81 golden + 100 gov + 240) entries
#: = 244/421 = 0.5796 ∈ [0.56, 0.60]. The profile test re-derives the
#: share from the actual module contents, so this constant and the
#: corpus cannot drift apart silently.
CHECKPOINT_COUNT = 240

_PROJECT_SHORT = {
    "aurora-api": "aurora",
    "vault-ui": "vaultui",
    "mnemos-core": "mnemos",
    "atlas-pipeline": "atlas",
}

# Per-project shipped-work fragments (16 each). Index-driven selection
# with co-prime strides covers the pool without RNG.
_ITEMS: dict[str, tuple[str, ...]] = {
    "aurora-api": (
        "gateway latency budget held",
        "token-bucket refill tuning merged",
        "RLS policy coverage extended to two more tables",
        "migration ledger backfill green",
        "checkout soak window clean",
        "auth validator cache warmed on boot",
        "healthz probe timing tightened",
        "connection pool sizing retuned",
        "deprecation notices rendered for v1 endpoints",
        "trace propagation enabled on the migration jobs",
        "retry backoff jitter shipped",
        "idempotency key store on the outbox",
        "webhook signing keys rotated",
        "rate limit manifest values refreshed",
        "circuit breaker dashboard panels live",
        "read replica lag guard exercised",
    ),
    "vault-ui": (
        "focus trap harness extended to drawers",
        "token sweep fixed the off-scale gaps",
        "windowed list overscan retuned",
        "dark mode surface remap verified",
        "keyboard model on the data grid",
        "hydration skeletons on the slow routes",
        "bundle trim from the icon tree-shake",
        "prefetch caps wired to the router",
        "catalog key checks in CI",
        "screenshot diffs clean on previews",
        "error boundary retry affordance",
        "form validation timing adjusted",
        "chart series derivation from the accent",
        "breakpoint lint across layouts",
        "date formatting moved after mount",
        "contrast fixes on the placeholder labels",
    ),
    "mnemos-core": (
        "hybrid fusion regression suite green",
        "vector blob packing compacted",
        "FTS snippet coverage on redemption",
        "marker parse path exercised",
        "cache-aligner relocation on the tail blocks",
        "quarantine render format pinned",
        "federation batch sync verified",
        "embed cache key widened to the provider id",
        "status gate assertions on the search path",
        "supersedes traversal smoke green",
        "issuance scan patterns refreshed",
        "wal checkpoint cadence observed",
        "predicate guard on the vector leg",
        "corpus fingerprint recorded",
        "baseline corridors re-derived",
        "determinism sweep on the stands",
    ),
    "atlas-pipeline": (
        "partition manifest frozen and verified",
        "watermark rebased on the migrated set",
        "repair lane replay from the ledger",
        "row-count gates within tolerance",
        "salted shuffle re-aggregation merged",
        "compaction run on the weekly window",
        "schema registry entries pinned",
        "copy cluster rehearsal completed",
        "dead-letter lane drained",
        "late-arrival repair for the eu region",
        "freshness checks wired to paging",
        "contract tests on the producer side",
        "lineage events on the new task set",
        "spot runner preemption drill",
        "cost per run on the dashboard",
        "erasure key rotation rehearsed",
    ),
}

# Shared verification fragments (8). Index-selected per entry.
_VERIFICATIONS: tuple[str, ...] = (
    "Soak for two hours with zero error regression.",
    "All gates green on the replay rehearsal.",
    "Rollout complete after the health gate held.",
    "Spot checks matched the ledger counts.",
    "The cold window finished inside the budget.",
    "Dashboards refreshed with the new series.",
    "The follow-up ticket closed as verified.",
    "Duplicate delivery probes came back clean.",
)

# Per-project phase labels (4 each) — what kind of milestone this is.
_PHASES: dict[str, tuple[str, ...]] = {
    "aurora-api": ("release", "hardening wave", "soak follow-up", "rollout step"),
    "vault-ui": ("release", "polish pass", "regression sweep", "refactor step"),
    "mnemos-core": ("wave close", "gate pass", "extension sweep", "verification pass"),
    "atlas-pipeline": ("window close", "backfill step", "replay pass", "rehearsal"),
}

#: Version bases: (major, minor_start). Patch advances fastest, minor
#: every 6 checkpoints per project, so versions read like a cadence.
_VERSION_BASE = {
    "aurora-api": (2, 7),
    "vault-ui": (1, 9),
    "mnemos-core": (4, 1),
    "atlas-pipeline": (3, 2),
}


def _pick(pool: tuple[str, ...] | list[str], index: int, stride: int, offset: int) -> str:
    """Deterministic pool pick: co-prime strides cover the whole pool."""
    return pool[(index * stride + offset) % len(pool)]


def _checkpoint_entry(global_index: int) -> GoldenEntry:
    project = PROJECTS[global_index % len(PROJECTS)]
    j = global_index // len(PROJECTS)  # 0..59 within the project
    items = _ITEMS[project]
    i1 = _pick(items, j, 3, 0)
    i2 = _pick(items, j, 5, 7)
    i3 = _pick(items, j, 7, 3)
    # de-duplicate by nudging the third item forward until distinct
    while len({i1, i2, i3}) < 3:
        i3 = items[(items.index(i3) + 1) % len(items)]
    phase = _pick(_PHASES[project], j, 1, j // 6)
    major, minor_start = _VERSION_BASE[project]
    version = f"{major}.{minor_start + j // 6}.{j % 6}"
    verification = _pick(_VERIFICATIONS, j, 1, (j * 3) % len(_VERIFICATIONS))
    return GoldenEntry(
        slug=f"{_PROJECT_SHORT[project]}-gov-ckpt-{global_index:03d}",
        project=project,
        agent=_agent_for(project),
        title=f"Checkpoint: {project} {version} ({phase})",
        content=(f"Checkpoint: {project} {version} {phase} — {i1}, {i2}, {i3}. {verification}"),
        mnemos_tags=("checkpoint",),
        free_tags=("cadence",),
    )


def _agent_for(project: str) -> str:
    return {
        "aurora-api": "aurora-backend",
        "vault-ui": "vaultui-frontend",
        "mnemos-core": "mnemos-maintainer",
        "atlas-pipeline": "atlas-etl",
    }[project]


#: The full padding list, fixed order (round-robin across projects).
CHECKPOINT_ENTRIES: list[GoldenEntry] = [_checkpoint_entry(i) for i in range(CHECKPOINT_COUNT)]

CHECKPOINT_SLUGS: frozenset[str] = frozenset(e.slug for e in CHECKPOINT_ENTRIES)
