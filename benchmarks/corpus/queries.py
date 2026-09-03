"""Golden queries + relevance judgments (ADR-0017 D5, #125 W4; ADR-0020 BF-4).

192 queries, each with a project scope (or ``None`` for the explicit
global mode) and the honest-small set of expected-relevant corpus slugs.
The JUDGMENTS are the golden part: an entry is expected only when it is
genuinely an answer to the query, not merely topically adjacent.

BF-4 corpus growth 48 → 192 (ADR-0020: McNemar is underpowered at 48
rated pairs; ~192 is the deferred activation threshold). The original 48
W4 queries are unchanged (prefixes ``au``/``vu``/``mn``/``at``/``gl``
with plain numbers); the 144 BF-4 additions carry a FAMILY marker in the
qid so analyses can slice by retrieval regime:

* ``-ph`` exact phrases — verbatim multi-word spans lifted from corpus
  content (FTS5 phrase-matchable by construction);
* ``-pr`` paraphrases — the same knowledge asked in different words (no
  verbatim phrase overlap; they ride the vector leg — the discordant
  pairs McNemar needs);
* ``-tp`` topics — broad topical labels with honest-small judgments;
* ``-xr`` cross-record queries — themes answered by several entries at
  once (multi-member judgment sets, the global mode exercises these
  across projects).

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
    # ── aurora-api BF-4 additions: exact phrases ───────────────────────
    _q("au-ph01", "wall-clock read per request", "aurora-api", "aurora-rate-limiter"),
    _q("au-ph02", "cross-tenant leak", "aurora-api", "aurora-rls-decision"),
    _q(
        "au-ph03",
        "connection storm",
        "aurora-api",
        "aurora-500-spike-log",
        "aurora-processed-review",
    ),
    _q("au-ph04", "load-shedding circuit breaker", "aurora-api", "aurora-incident-retro"),
    _q("au-ph05", "single-flight lock", "aurora-api", "aurora-cache-ttl-pattern"),
    _q("au-ph06", "promoting green", "aurora-api", "aurora-deploy-runbook"),
    _q("au-ph07", "lock timeout", "aurora-api", "aurora-migration-notes"),
    _q("au-ph08", "invalid bearer token", "aurora-api", "aurora-auth-middleware"),
    _q("au-ph09", "2h soak", "aurora-api", "aurora-release-checkpoint"),
    _q("au-ph10", "partition-wise join", "aurora-api", "aurora-sharding-question"),
    _q("au-ph11", "principal_id", "aurora-api", "aurora-migration-notes"),
    _q("au-ph12", "weekly digest", "aurora-api", "aurora-processed-review"),
    _q("au-ph13", "handoff to the next oncall", "aurora-api", "aurora-ccr-handoff"),
    _q("au-ph14", "manifest cache", "aurora-api", "aurora-cache-ttl-pattern"),
    _q(
        "au-ph15",
        "refill",
        "aurora-api",
        "aurora-rate-limiter",
        "aurora-processed-cluster",
    ),
    _q("au-ph16", "rotate the service token", "aurora-api", "aurora-deploy-runbook"),
    _q(
        "au-ph17",
        "monotonic clock",
        "aurora-api",
        "aurora-rate-limiter",
        "aurora-processed-cluster",
    ),
    _q("au-ph18", "audience mismatch", "aurora-api", "aurora-jwt-session-log"),
    # ── aurora-api BF-4 additions: paraphrases (vector-leg riders) ─────
    _q("au-pr01", "why was the gateway restarting", "aurora-api", "aurora-oom-log"),
    _q("au-pr02", "how to make the rate limiter faster", "aurora-api", "aurora-rate-limiter"),
    _q("au-pr03", "approach to tenant isolation", "aurora-api", "aurora-rls-decision"),
    _q(
        "au-pr04",
        "protecting queries from sql injection",
        "aurora-api",
        "aurora-no-fstring-sql-rule",
    ),
    _q(
        "au-pr05",
        "when does the events table need sharding",
        "aurora-api",
        "aurora-sharding-question",
    ),
    _q(
        "au-pr06",
        "what happened during the checkout outage",
        "aurora-api",
        "aurora-500-spike-log",
        "aurora-incident-retro",
    ),
    _q("au-pr07", "database alerts too sensitive", "aurora-api", "aurora-processed-review"),
    _q(
        "au-pr08",
        "pool saturation alerting",
        "aurora-api",
        "aurora-incident-retro",
        "aurora-observability-lean",
    ),
    _q("au-pr09", "service health endpoint", "aurora-api", "aurora-healthcheck-code"),
    _q("au-pr10", "what do the dashboards show", "aurora-api", "aurora-observability-lean"),
    # ── aurora-api BF-4 additions: topics ──────────────────────────────
    _q(
        "au-tp01",
        "incident",
        "aurora-api",
        "aurora-oom-log",
        "aurora-500-spike-log",
        "aurora-incident-retro",
    ),
    _q("au-tp02", "go code", "aurora-api", "aurora-auth-middleware", "aurora-healthcheck-code"),
    _q("au-tp03", "configuration", "aurora-api", "aurora-config-guide"),
    _q(
        "au-tp04",
        "credential handling",
        "aurora-api",
        "aurora-db-conn-leak",
        "aurora-ci-token-note",
        "aurora-jwt-session-log",
    ),
    # ── aurora-api BF-4 additions: cross-record themes ─────────────────
    _q(
        "au-xr01",
        "latency regressions",
        "aurora-api",
        "aurora-rate-limiter",
        "aurora-processed-cluster",
    ),
    _q(
        "au-xr02",
        "deploy safety checklist",
        "aurora-api",
        "aurora-deploy-runbook",
        "aurora-release-checkpoint",
    ),
    _q(
        "au-xr03",
        "auth troubleshooting",
        "aurora-api",
        "aurora-auth-middleware",
        "aurora-jwt-session-log",
    ),
    _q("au-xr04", "cache", "aurora-api", "aurora-cache-ttl-pattern"),
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
    # ── vault-ui BF-4 additions: exact phrases ─────────────────────────
    _q("vu-ph01", "windowed rendering", "vault-ui", "vaultui-list-virtualization"),
    _q("vu-ph02", "slate ramp", "vault-ui", "vaultui-design-tokens"),
    _q("vu-ph03", "last-writer-wins", "vault-ui", "vaultui-state-machine"),
    _q(
        "vu-ph04",
        "request id",
        "vault-ui",
        "vaultui-state-machine",
        "vaultui-form-validation",
    ),
    _q("vu-ph05", "stable aspect", "vault-ui", "vaultui-session-note"),
    _q("vu-ph06", "skeleton during hydration", "vault-ui", "vaultui-hydration-log"),
    _q("vu-ph07", "icon barrel", "vault-ui", "vaultui-release-notes"),
    _q(
        "vu-ph08",
        "spacing scale",
        "vault-ui",
        "vaultui-design-tokens",
        "vaultui-processed-tokens",
    ),
    _q("vu-ph09", "dialogs", "vault-ui", "vaultui-modal-component", "vaultui-keyboard-nav"),
    _q("vu-ph10", "lucide", "vault-ui", "vaultui-open-question-icons"),
    _q("vu-ph11", "wcag", "vault-ui", "vaultui-contrast-audit"),
    _q("vu-ph12", "createPortal", "vault-ui", "vaultui-modal-component"),
    _q("vu-ph13", "60fps", "vault-ui", "vaultui-list-virtualization"),
    _q("vu-ph14", "caption model", "vault-ui", "vaultui-embed-key-note"),
    _q("vu-ph15", "retry queue", "vault-ui", "vaultui-slack-webhook-note"),
    _q("vu-ph16", "under 960px", "vault-ui", "vaultui-session-note"),
    _q("vu-ph17", "placeholder text", "vault-ui", "vaultui-contrast-audit"),
    _q("vu-ph18", "design review pack", "vault-ui", "vaultui-ccr-review-pack"),
    _q("vu-ph19", "overscan", "vault-ui", "vaultui-list-virtualization"),
    _q("vu-ph20", "escape closes modals", "vault-ui", "vaultui-keyboard-nav"),
    # ── vault-ui BF-4 additions: paraphrases (vector-leg riders) ───────
    _q("vu-pr01", "smooth scrolling for huge lists", "vault-ui", "vaultui-list-virtualization"),
    _q("vu-pr02", "keyboard reachability rules", "vault-ui", "vaultui-keyboard-nav"),
    _q("vu-pr03", "deriving chart colors", "vault-ui", "vaultui-chart-theming"),
    _q("vu-pr04", "showing form errors", "vault-ui", "vaultui-form-validation"),
    _q("vu-pr05", "bot posted twice", "vault-ui", "vaultui-slack-webhook-note"),
    _q("vu-pr06", "server html differs from client", "vault-ui", "vaultui-hydration-log"),
    _q(
        "vu-pr07",
        "setstate inside render warning",
        "vault-ui",
        "vaultui-console-error-log",
    ),
    # ── vault-ui BF-4 additions: topics ────────────────────────────────
    _q(
        "vu-tp01",
        "accessibility",
        "vault-ui",
        "vaultui-keyboard-nav",
        "vaultui-contrast-audit",
        "vaultui-modal-component",
    ),
    _q("vu-tp02", "dashboard layout", "vault-ui", "vaultui-session-note"),
    _q(
        "vu-tp03",
        "performance",
        "vault-ui",
        "vaultui-list-virtualization",
        "vaultui-release-notes",
    ),
    _q("vu-tp04", "design decisions", "vault-ui", "vaultui-design-tokens", "vaultui-chart-theming"),
    # ── vault-ui BF-4 additions: cross-record themes ───────────────────
    _q("vu-xr01", "debounce", "vault-ui", "vaultui-state-machine", "vaultui-form-validation"),
    _q(
        "vu-xr02",
        "ui audit findings",
        "vault-ui",
        "vaultui-contrast-audit",
        "vaultui-embed-key-note",
    ),
    _q("vu-xr03", "grid layout", "vault-ui", "vaultui-session-note"),
    _q(
        "vu-xr04",
        "component rendering bugs",
        "vault-ui",
        "vaultui-console-error-log",
        "vaultui-hydration-log",
    ),
    _q(
        "vu-xr05",
        "token adoption across screens",
        "vault-ui",
        "vaultui-processed-tokens",
        "vaultui-design-tokens",
    ),
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
    # ── mnemos-core BF-4 additions: exact phrases ──────────────────────
    _q("mn-ph01", "rrf_k", "mnemos-core", "mnemos-rrf-fusion"),
    _q("mn-ph02", "bag-of-words", "mnemos-core", "mnemos-embedder-bench"),
    _q("mn-ph03", "zero-loss storage", "mnemos-core", "mnemos-issuance-scan"),
    _q("mn-ph04", "content-addressed originals", "mnemos-core", "mnemos-ccr-design"),
    _q("mn-ph05", "supersedes edges", "mnemos-core", "mnemos-rewrite-event"),
    _q("mn-ph06", "dead-letter queue", "mnemos-core", "mnemos-status-pipeline"),
    _q("mn-ph07", "external vector db", "mnemos-core", "mnemos-vector-store"),
    _q("mn-ph08", "wal grew", "mnemos-core", "mnemos-sqlite-wal-log"),
    _q("mn-ph09", "cold vector cache", "mnemos-core", "mnemos-mcp-timeout-log"),
    _q("mn-ph10", "cache misses", "mnemos-core", "mnemos-cache-aligner"),
    _q("mn-ph11", "e5-small", "mnemos-core", "mnemos-embedder-bench"),
    _q("mn-ph12", "384-dim", "mnemos-core", "mnemos-embedder-bench"),
    _q("mn-ph13", "10M rows", "mnemos-core", "mnemos-otel-traces"),
    _q("mn-ph14", "kv prefix", "mnemos-core", "mnemos-cache-aligner"),
    _q("mn-ph15", "active-state", "mnemos-core", "mnemos-open-question-budget"),
    _q(
        "mn-ph16",
        "hermes adapter",
        "mnemos-core",
        "mnemos-cache-aligner",
        "mnemos-checkpoint-phase1",
    ),
    _q("mn-ph17", "adr-0018", "mnemos-core", "mnemos-status-pipeline"),
    _q("mn-ph18", "phase 2", "mnemos-core", "mnemos-rewrite-event"),
    _q("mn-ph19", "honest small judgments", "mnemos-core", "mnemos-golden-set-design"),
    _q("mn-ph20", "non-normative", "mnemos-core", "mnemos-processed-metrics"),
    # ── mnemos-core BF-4 additions: paraphrases (vector-leg riders) ────
    _q(
        "mn-pr01",
        "keeping other projects out of results",
        "mnemos-core",
        "mnemos-project-predicate",
    ),
    _q("mn-pr02", "what gets redacted at issuance", "mnemos-core", "mnemos-issuance-scan"),
    _q("mn-pr03", "shrinking long logs for context", "mnemos-core", "mnemos-ccr-design"),
    _q("mn-pr04", "lifecycle of replaced context", "mnemos-core", "mnemos-rewrite-event"),
    _q("mn-pr05", "sync between trusted nodes", "mnemos-core", "mnemos-federation-gossip"),
    _q("mn-pr06", "picking an embedding model", "mnemos-core", "mnemos-embedder-bench"),
    _q("mn-pr07", "slow first search after boot", "mnemos-core", "mnemos-mcp-timeout-log"),
    _q(
        "mn-pr08",
        "database ballooned during a long pull",
        "mnemos-core",
        "mnemos-sqlite-wal-log",
    ),
    _q("mn-pr09", "keeping provider caches warm", "mnemos-core", "mnemos-cache-aligner"),
    _q("mn-pr10", "nightly backup key flagged", "mnemos-core", "mnemos-aws-log"),
    # ── mnemos-core BF-4 additions: topics ─────────────────────────────
    _q("mn-tp01", "telemetry", "mnemos-core", "mnemos-otel-traces"),
    _q(
        "mn-tp02",
        "security scanning",
        "mnemos-core",
        "mnemos-issuance-scan",
        "mnemos-aws-log",
        "mnemos-pem-note",
    ),
    _q("mn-tp03", "knowledge statuses", "mnemos-core", "mnemos-status-pipeline"),
    # ── mnemos-core BF-4 additions: cross-record themes ────────────────
    _q("mn-xr01", "marker redemption", "mnemos-core", "mnemos-ccr-design", "mnemos-ccr-bridge"),
    _q("mn-xr02", "clock skew", "mnemos-core", "mnemos-processed-federation"),
    _q(
        "mn-xr03",
        "d5 metrics",
        "mnemos-core",
        "mnemos-processed-metrics",
        "mnemos-golden-set-design",
    ),
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
    # ── atlas-pipeline BF-4 additions: exact phrases ───────────────────
    _q("at-ph01", "whale tenant", "atlas-pipeline", "atlas-spark-skew"),
    _q("at-ph02", "small-file counts", "atlas-pipeline", "atlas-parquet-compaction"),
    _q(
        "at-ph03",
        "marker files",
        "atlas-pipeline",
        "atlas-watermark-decision",
        "atlas-checkpoint-migration",
    ),
    _q("at-ph04", "exactly-once event ids", "atlas-pipeline", "atlas-dq-gates"),
    _q("at-ph05", "off-heap", "atlas-pipeline", "atlas-oom-shuffle-log"),
    _q(
        "at-ph06",
        "row-count gate",
        "atlas-pipeline",
        "atlas-checkpoint-migration",
        "atlas-dq-gates",
    ),
    _q("at-ph07", "source freshness", "atlas-pipeline", "atlas-dbt-tests"),
    _q(
        "at-ph08",
        "partition manifest",
        "atlas-pipeline",
        "atlas-backfill-runbook",
        "atlas-orchestrator-code",
    ),
    _q("at-ph09", "implicit schedule-time ordering", "atlas-pipeline", "atlas-dag-design"),
    _q("at-ph10", "micro-batches", "atlas-pipeline", "atlas-open-question-streaming"),
    _q("at-ph11", "tenant rollup", "atlas-pipeline", "atlas-oom-shuffle-log"),
    _q("at-ph12", "memory.fraction", "atlas-pipeline", "atlas-oom-shuffle-log"),
    _q("at-ph13", "vendor escalates", "atlas-pipeline", "atlas-ccr-oncall"),
    _q("at-ph14", "double-counted", "atlas-pipeline", "atlas-watermark-decision"),
    _q("at-ph15", "tolerance", "atlas-pipeline", "atlas-orchestrator-code"),
    _q("at-ph16", "null rate", "atlas-pipeline", "atlas-dq-gates"),
    _q("at-ph17", "uniqueness", "atlas-pipeline", "atlas-dbt-tests"),
    # ── atlas-pipeline BF-4 additions: paraphrases (vector-leg riders) ─
    _q(
        "at-pr01",
        "events landing after the watermark",
        "atlas-pipeline",
        "atlas-late-arrival-log",
        "atlas-dag-design",
    ),
    _q("at-pr02", "too many tiny files", "atlas-pipeline", "atlas-parquet-compaction"),
    _q("at-pr03", "replaying from a known good point", "atlas-pipeline", "atlas-backfill-runbook"),
    _q(
        "at-pr04",
        "evolving schema without breaking readers",
        "atlas-pipeline",
        "atlas-schema-evolution",
    ),
    _q("at-pr05", "executor died from memory pressure", "atlas-pipeline", "atlas-oom-shuffle-log"),
    _q(
        "at-pr06",
        "credentials printed into task logs",
        "atlas-pipeline",
        "atlas-secret-spill-log",
    ),
    _q("at-pr07", "lag past the sla", "atlas-pipeline", "atlas-late-arrival-log"),
    _q("at-pr08", "designing idempotent tasks", "atlas-pipeline", "atlas-dag-design"),
    # ── atlas-pipeline BF-4 additions: topics ──────────────────────────
    _q("at-tp01", "oncall handoff", "atlas-pipeline", "atlas-ccr-oncall"),
    _q(
        "at-tp02",
        "memory budget",
        "atlas-pipeline",
        "atlas-processed-incidents",
        "atlas-oom-shuffle-log",
    ),
    _q(
        "at-tp03",
        "storage migration",
        "atlas-pipeline",
        "atlas-checkpoint-migration",
        "atlas-watermark-decision",
    ),
    # ── atlas-pipeline BF-4 additions: cross-record themes ─────────────
    _q("at-xr01", "streaming or batch", "atlas-pipeline", "atlas-open-question-streaming"),
    _q("at-xr02", "data quality", "atlas-pipeline", "atlas-dq-gates", "atlas-dbt-tests"),
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
    # ── global BF-4 additions: cross-record themes (explicit global mode)
    _q(
        "gl-xr01",
        "checkpoint",
        None,
        "aurora-release-checkpoint",
        "mnemos-checkpoint-phase1",
        "atlas-checkpoint-migration",
    ),
    _q(
        "gl-xr02",
        "latency",
        None,
        "aurora-rate-limiter",
        "mnemos-mcp-timeout-log",
        "aurora-processed-cluster",
    ),
    _q(
        "gl-xr03",
        "processed synthesis",
        None,
        "aurora-processed-cluster",
        "aurora-processed-review",
        "vaultui-processed-tokens",
        "mnemos-processed-metrics",
        "mnemos-processed-federation",
        "atlas-processed-incidents",
    ),
    _q(
        "gl-xr04",
        "redaction",
        None,
        "mnemos-issuance-scan",
        "atlas-secret-spill-log",
        "vaultui-embed-key-note",
    ),
    _q(
        "gl-xr05",
        "compressed marker",
        None,
        "aurora-ccr-handoff",
        "vaultui-ccr-review-pack",
        "mnemos-ccr-bridge",
        "atlas-ccr-oncall",
        "mnemos-ccr-design",
    ),
    _q("gl-xr06", "saturation", None, "aurora-observability-lean", "aurora-incident-retro"),
]
