"""Golden queries + relevance judgments (ADR-0017 D5, #125 W4).

48 queries, each with a project scope (or ``None`` for the explicit
global mode) and the honest-small set of expected-relevant corpus slugs.
The JUDGMENTS are the golden part: an entry is expected only when it is
genuinely an answer to the query, not merely topically adjacent.

FTS5 notes shaping the wording: ``fts_search`` wraps the whole query in
one FTS5 phrase, so a multi-word query matches only verbatim adjacent
tokens; single distinctive tokens phrase-match trivially. The vector leg
(unigram+bigram feature hashing) carries the semantic spread. Both
behaviours are visible in the judgments (e.g. ``pool exhaustion`` also
expects the entry that says ``pool exhausted`` — a unigram/vector hit,
not a phrase hit).

Metric convention (measure.py implements):
  precision@k = |top-k ∩ expected| / k      (strict denominator)
  recall@k    = |top-k ∩ expected| / |expected|
macro-averaged over queries with a non-empty judgment set.
Queries flagged ``expect_no_results`` (status-gate probes whose only
matching entry is ``raw``) are excluded from the averages and instead
asserted directly: no non-admissible slug may ever surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoldenQuery:
    """One golden query with its relevance judgment (expected slugs)."""

    qid: str
    text: str
    project: str | None  # None = explicit global (cross-project) mode
    expected: frozenset[str] = field(default_factory=frozenset)
    expect_no_results: bool = False  # status-gate probe: raw-only match


def _q(qid: str, text: str, project: str | None, *expected: str) -> GoldenQuery:
    return GoldenQuery(qid=qid, text=text, project=project, expected=frozenset(expected))


def _probe(qid: str, text: str, project: str) -> GoldenQuery:
    return GoldenQuery(qid=qid, text=text, project=project, expect_no_results=True)


GOLDEN_QUERIES: list[GoldenQuery] = [
    # ── aurora-api (scoped) ────────────────────────────────────────────
    _q("au-01", "deployment runbook", "aurora-api", "aurora-deploy-runbook"),
    _q("au-02", "token bucket", "aurora-api", "aurora-rate-limiter"),
    _q("au-03", "pool exhaustion", "aurora-api", "aurora-500-spike-log", "aurora-incident-retro"),
    _q("au-04", "rls", "aurora-api", "aurora-rls-decision", "aurora-release-checkpoint"),
    _q("au-05", "healthz", "aurora-api", "aurora-healthcheck-code"),
    _q(
        "au-06",
        "migration",
        "aurora-api",
        "aurora-migration-notes",
        "aurora-deploy-runbook",
        "aurora-release-checkpoint",
    ),
    _q("au-07", "auth gateway", "aurora-api", "aurora-auth-middleware", "aurora-jwt-session-log"),
    _q("au-08", "release workflow", "aurora-api", "aurora-ci-token-note"),
    _q("au-09", "circuit breaker", "aurora-api", "aurora-incident-retro"),
    _q("au-10", "checkpoint", "aurora-api", "aurora-release-checkpoint"),
    _q("au-11", "staging", "aurora-api", "aurora-db-conn-leak"),
    # ── vault-ui (scoped) ──────────────────────────────────────────────
    _q("vu-01", "design tokens", "vault-ui", "vaultui-design-tokens", "vaultui-chart-theming"),
    _q("vu-02", "focus trap", "vault-ui", "vaultui-modal-component"),
    _q("vu-03", "hydration", "vault-ui", "vaultui-hydration-log"),
    _q("vu-04", "virtualization", "vault-ui", "vaultui-list-virtualization"),
    _q("vu-05", "contrast", "vault-ui", "vaultui-contrast-audit"),
    _q(
        "vu-06",
        "dark mode",
        "vault-ui",
        "vaultui-design-tokens",
        "vaultui-release-notes",
        "vaultui-chart-theming",
    ),
    _q("vu-07", "escape", "vault-ui", "vaultui-keyboard-nav"),
    _q("vu-08", "bundle", "vault-ui", "vaultui-release-notes"),
    _q("vu-09", "icon", "vault-ui", "vaultui-open-question-icons", "vaultui-release-notes"),
    _q("vu-10", "critique bot", "vault-ui", "vaultui-slack-webhook-note"),
    _q("vu-11", "visual regression", "vault-ui", "vaultui-embed-key-note"),
    _q("vu-12", "stale response", "vault-ui", "vaultui-state-machine"),
    # ── mnemos-core (scoped) ───────────────────────────────────────────
    _q("mn-01", "tag contract", "mnemos-core", "mnemos-tag-contract-summary"),
    _q("mn-02", "reciprocal rank fusion", "mnemos-core", "mnemos-rrf-fusion"),
    _q(
        "mn-03",
        "knowledge pipeline",
        "mnemos-core",
        "mnemos-status-pipeline",
        "mnemos-rewrite-event",
    ),
    _q("mn-04", "over fetch", "mnemos-core", "mnemos-project-predicate"),
    _q("mn-05", "redacted", "mnemos-core", "mnemos-issuance-scan", "mnemos-aws-log"),
    _q("mn-06", "cache misses", "mnemos-core", "mnemos-cache-aligner"),
    _q("mn-07", "no federate", "mnemos-core", "mnemos-federation-gossip"),
    _q("mn-08", "embedder", "mnemos-core", "mnemos-embedder-bench", "mnemos-golden-set-design"),
    _q("mn-09", "checkpoint", "mnemos-core", "mnemos-checkpoint-phase1"),
    _probe("mn-10", "graph expansion", "mnemos-core"),
    # ── atlas-pipeline (scoped) ────────────────────────────────────────
    _q(
        "at-01",
        "backfill",
        "atlas-pipeline",
        "atlas-backfill-runbook",
        "atlas-open-question-streaming",
    ),
    _q("at-02", "skew", "atlas-pipeline", "atlas-spark-skew"),
    _q(
        "at-03",
        "watermark",
        "atlas-pipeline",
        "atlas-backfill-runbook",
        "atlas-late-arrival-log",
        "atlas-watermark-decision",
        "atlas-ccr-oncall",
    ),
    _q(
        "at-04",
        "repair lane",
        "atlas-pipeline",
        "atlas-dag-design",
        "atlas-late-arrival-log",
        "atlas-orchestrator-code",
    ),
    _q("at-05", "parquet", "atlas-pipeline", "atlas-parquet-compaction"),
    _q("at-06", "dlq", "atlas-pipeline", "atlas-dq-gates"),
    _q(
        "at-07",
        "salted",
        "atlas-pipeline",
        "atlas-spark-skew",
        "atlas-oom-shuffle-log",
        "atlas-processed-incidents",
    ),
    _q("at-08", "exactly once", "atlas-pipeline", "atlas-dq-gates"),
    _q("at-09", "freshness", "atlas-pipeline", "atlas-dbt-tests"),
    _q(
        "at-10",
        "ingest",
        "atlas-pipeline",
        "atlas-open-question-streaming",
        "atlas-secret-spill-log",
    ),
    _q("at-11", "checkpoint", "atlas-pipeline", "atlas-checkpoint-migration"),
    # ── global (project=None — explicit cross-project mode) ────────────
    _q("gl-01", "runbook", None, "aurora-deploy-runbook", "atlas-backfill-runbook"),
    _q("gl-02", "oom", None, "aurora-oom-log", "atlas-oom-shuffle-log"),
    _q(
        "gl-03",
        "federation",
        None,
        "mnemos-federation-gossip",
        "mnemos-pem-note",
        "mnemos-processed-federation",
    ),
    _q("gl-04", "sql injection", None, "aurora-no-fstring-sql-rule"),
]
