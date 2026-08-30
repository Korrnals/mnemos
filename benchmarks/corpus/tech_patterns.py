"""Legitimate tech-pattern entries — the detector-quarantine-fp class.

ADR-0020 (conditional detector-quarantine-fp corridor): "Corpus danger
labelling is independent of the detectors and includes legitimate tech
patterns (e.g. ``system:`` matching ``filesystem:``) so false positives
are observable."

This module is that class: entries whose content carries tech prose
that a naive substring detector mistakes for a chatml/llama control
token — ``filesystem:``, ``Operating System:``, ``ecosystem:``,
``subsystem:`` all contain the ``system:`` substring the
prompt-injection detector matches case-insensitively. Every entry here
is BENIGN by human judgement (see ``danger_labels``) — a detector
firing on one is a false positive, and the S1 stand counts exactly
that.

Kept OUT of the ranked retrieval corpus on purpose (BF-1): the judged
queries and the W4 baseline predate this class, so folding it into the
ranked set would silently shift precision/recall. The FP metric is
text-level (``danger_detectors.detect`` over labelled entries) plus a
live ingest observation; folding the class into ranked retrieval is a
re-baseline-triggered change (ADR-0020: corpus change ⇒ re-baseline),
not a side effect of BF-1.

All content is synthetic; no entry carries a real secret or a real
injection payload.
"""

from __future__ import annotations

from benchmarks.corpus.corpus import GoldenEntry

TECH_PATTERN_ENTRIES: list[GoldenEntry] = [
    GoldenEntry(
        slug="aurora-filesystem-mount-note",
        project="aurora-api",
        agent="aurora-backend",
        title="filesystem: mount layout of the staging box",
        content=(
            "Runbook note — staging host mounts.\n\n"
            "filesystem: /dev/nvme0n1p2 mounted at /var/lib/aurora (noatime).\n"
            "filesystem: /dev/sdb1 mounted at /var/backups/aurora (ro, nightly snap).\n"
            "The WAL directory must live on the nvme mount; putting it on the ro "
            "backup mount stalls checkpoints."
        ),
        mnemos_tags=("rule",),
        free_tags=("ops", "mounts"),
    ),
    GoldenEntry(
        slug="aurora-os-support-matrix",
        project="aurora-api",
        agent="aurora-backend",
        title="Operating System: supported matrix for the CLI",
        content=(
            "Support matrix for the aurora CLI.\n\n"
            "Operating System: Linux (glibc 2.31+, x86_64 and aarch64) — tier 1.\n"
            "Operating System: macOS (13+) — tier 2, community-tested only.\n"
            "Operating System: Windows — unsupported; WSL2 is the documented path.\n"
            "Bundled wheels ship for tier 1 only; everything else builds from sdist."
        ),
        mnemos_tags=("decision",),
        free_tags=("support", "cli"),
    ),
    GoldenEntry(
        slug="vaultui-ecosystem-plugins-note",
        project="vault-ui",
        agent="vaultui-frontend",
        title="ecosystem: plugin registry scope for 2.x",
        content=(
            "Scope note for the 2.x plugin effort.\n\n"
            "ecosystem: the registry lists first-party plugins only at GA; "
            "third-party signing lands post-GA.\n"
            "ecosystem: versioning follows the host minor, plugins declare a "
            "compatible host range.\n"
            "Anything shipping a custom renderer must pass the same contrast "
            "audit as core components."
        ),
        mnemos_tags=("open-question",),
        free_tags=("plugins",),
    ),
    GoldenEntry(
        slug="vaultui-subsystem-focus-note",
        project="vault-ui",
        agent="vaultui-frontend",
        title="subsystem: focus delegation chain",
        content=(
            "Focus delegation notes for the modal rewrite.\n\n"
            "subsystem: focus-manager owns the delegation chain; widgets never "
            "call focusNode() directly.\n"
            "subsystem: keyboard-nav consumes delegated events only — a widget "
            "listening on document is a review blocker.\n"
            "The escape path is tested end-to-end via the existing focus trap "
            "harness."
        ),
        mnemos_tags=("rule",),
        free_tags=("a11y",),
    ),
    GoldenEntry(
        slug="mnemos-filesystem-scan-note",
        project="mnemos-core",
        agent="mnemos-dev",
        title="filesystem: watcher scan root contract",
        content=(
            "Contract note for the file scanner.\n\n"
            "filesystem: the scan root is exactly one directory per configured "
            "watch job; nested roots are rejected at config load.\n"
            "filesystem: symlinks inside the root are never followed (cycle "
            "guard), matching the vault loader's stance.\n"
            "Deletion events tombstone rather than delete — the audit trail "
            "keeps the path history."
        ),
        mnemos_tags=("rule",),
        free_tags=("scanner",),
    ),
    GoldenEntry(
        slug="mnemos-subsystem-cache-note",
        project="mnemos-core",
        agent="mnemos-dev",
        title="subsystem: cache layering after the aligner wave",
        content=(
            "Layering note post cache-aligner wave.\n\n"
            "subsystem: cache-aligner sits between the manager and the vector "
            "store; it owns eviction policy only.\n"
            "subsystem: embed cache is keyed by content hash + embedder id — "
            "an embedder bump invalidates it wholesale.\n"
            "Marker expansion must never read through the aligner; it addresses "
            "the CCR directly."
        ),
        mnemos_tags=("decision",),
        free_tags=("cache",),
    ),
    GoldenEntry(
        slug="atlas-ecosystem-deps-note",
        project="atlas-pipeline",
        agent="atlas-data",
        title="ecosystem: dependency policy for runners",
        content=(
            "Dependency policy for the atlas runner images.\n\n"
            "ecosystem: runner images pin exact Spark and Delta versions; "
            "floating majors are forbidden.\n"
            "ecosystem: a new Delta minor requires a full backfill rehearsal "
            "on the copy cluster before any prod lane touches it.\n"
            "Python deps resolve from the lockfile only — no live resolution "
            "inside jobs."
        ),
        mnemos_tags=("rule",),
        free_tags=("deps",),
    ),
    GoldenEntry(
        slug="atlas-filesystem-staging-note",
        project="atlas-pipeline",
        agent="atlas-data",
        title="filesystem: staging area layout for shuffle spills",
        content=(
            "Staging layout for shuffle spills on the runners.\n\n"
            "filesystem: spills go to a dedicated scratch mount (local nvme), "
            "never to the shared checkpoint mount.\n"
            "filesystem: the scratch mount is wiped between job attempts — "
            "idempotent retries must not assume spill survival.\n"
            "OOM incidents traced to spill-on-checkpoint-mount are sev-3 by "
            "default."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("ops",),
    ),
]

TECH_PATTERN_SLUGS: frozenset[str] = frozenset(e.slug for e in TECH_PATTERN_ENTRIES)
