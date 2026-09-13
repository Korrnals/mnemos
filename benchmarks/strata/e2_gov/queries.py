"""E2 stratum queries — G-gov pairs + G-neg negative control (E0 §3.1, §3.2).

Two phrasings per seeded record, in the BF-4 family convention
(``benchmarks/corpus/queries.py``):

* ``-ph`` exact phrase — a distinctive span lifted VERBATIM from the
  record content (FTS5 phrase-matchable by construction);
* ``-pr`` paraphrase — the same norm asked in different words with no
  four-word verbatim overlap (rides the vector leg — the discordant
  pairs the McNemar pairs need, E0 §6.1).

Every one of the 100 seeded records carries both phrasings, so an
adjudication replacement (``ground_truth.record_rejection``) activates
a pool record's queries atomically. The ACTIVE analyzed set is the 96
queries of the 48 ``ANALYZED_GOV_SLUGS`` records — the locked E0 §3.1
denominator.

G-neg (E0 §3.2): 24 knowledge questions about the same four projects —
near-miss wording with lexical pull toward governance vocabulary, but
the correct answer is knowledge content (learning/bug-pattern/session
entries of the golden corpus) BY CONSTRUCTION: no rule- or
decision-class record (golden or seeded) correctly answers any of them.
The gold sets here are knowledge slugs only; the noise-rate metric an
E3 runner derives from this stratum (governance blocks in top-5 =
false insertions) is E0 §2.4 metric (b), measured at run time —
nothing here runs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from benchmarks.strata.e2_gov.records import ANALYZED_GOV_SLUGS, GOV_RECORDS

# ── G-gov: two phrasings per seeded record ────────────────────────────────────

#: slug → (exact-phrase span, paraphrase). The phrase span MUST appear
#: verbatim in the record content (asserted by test); the paraphrase
#: must share no 4-word window with it (asserted by test).
_GOV_QUERY_TEXTS: dict[str, tuple[str, str]] = {
    # aurora-api rules
    "aurora-gov-deprecation-rule": (
        "two minor-release deprecation notices",
        "when can an old public endpoint be dropped",
    ),
    "aurora-gov-migration-gating-rule": (
        "lock_timeout 5s",
        "when are database schema changes allowed to ship",
    ),
    "aurora-gov-error-budget-rule": (
        "error-budget burn rate",
        "what pauses feature rollouts when reliability slips",
    ),
    # aurora-api decisions
    "aurora-gov-otel-decision": (
        "hand-rolled span logger",
        "what did we adopt for trace propagation",
    ),
    "aurora-gov-blue-green-decision": (
        "cannot exercise the connection-pool behavior",
        "why not canary releases for the gateway",
    ),
    "aurora-gov-postgres16-decision": (
        "parallel vacuum on the audit tables",
        "what does the database upgrade bring",
    ),
    "aurora-gov-v1-sunset-decision": (
        "the only documented contract",
        "what happened to the first json api surface",
    ),
    "aurora-gov-idempotency-decision": (
        "Idempotency-Key header",
        "how are retried payment requests kept safe",
    ),
    "aurora-gov-webhook-signing-decision": (
        "HMAC SHA-256",
        "how do we prove outbound callbacks are genuine",
    ),
    "aurora-gov-etag-decision": (
        "optimistic concurrency",
        "how are conflicting writes to a resource prevented",
    ),
    "aurora-gov-upstream-timeout-decision": (
        "thirty-second hard timeout",
        "what is the calling budget toward partner services",
    ),
    "aurora-gov-tenant-rate-limit-decision": (
        "per tenant rather than per API key",
        "how is the request quota scoped for customers",
    ),
    "aurora-gov-flags-manifest-decision": (
        "configuration manifest layer",
        "where do runtime feature toggles live",
    ),
    "aurora-gov-read-replica-decision": (
        "five seconds of lag",
        "where do analytics queries read from",
    ),
    "aurora-gov-pgbouncer-decision": (
        "three independent pools racing one database",
        "why one shared connection pooler",
    ),
    "aurora-gov-json-logs-decision": (
        "trace and tenant correlation fields",
        "what shape do gateway logs take",
    ),
    "aurora-gov-rest-not-grpc-decision": (
        "dual public surface",
        "is grpc part of the public api",
    ),
    "aurora-gov-cursor-pagination-decision": (
        "opaque cursor",
        "why not page numbers on big lists",
    ),
    "aurora-gov-circuit-breaker-decision": (
        "default half-open probe",
        "what happens to calls when a dependency keeps failing",
    ),
    "aurora-gov-checkout-slo-decision": (
        "99.9% availability SLO",
        "what uptime target does checkout carry",
    ),
    "aurora-gov-backpressure-decision": (
        "429 with a Retry-After header",
        "how does the gateway react to overload",
    ),
    "aurora-gov-token-review-decision": (
        "quarterly access review",
        "what happens to unused service credentials",
    ),
    "aurora-gov-refresh-ttl-decision": (
        "single-use and rotate on redemption",
        "how long do access tokens live",
    ),
    "aurora-gov-event-registry-decision": (
        "registered schemas with a pinned writer version",
        "how do services agree on event shapes",
    ),
    "aurora-gov-monolith-decision": (
        "modular monolith",
        "why is the service still one deployable",
    ),
    # vault-ui rules
    "vaultui-gov-a11y-gate-rule": (
        "new WCAG AA violation",
        "what stops accessibility regressions from merging",
    ),
    "vaultui-gov-bundle-budget-rule": (
        "180 KB gzipped JavaScript budget",
        "how much script may a route ship",
    ),
    "vaultui-gov-changelog-rule": (
        "changelog entry in the same pull request",
        "what must accompany user facing changes",
    ),
    # vault-ui decisions
    "vaultui-gov-grid-decision": (
        "one-dimensional strips",
        "how is the dashboard laid out now",
    ),
    "vaultui-gov-dark-mode-decision": (
        "remaps surface tokens only",
        "what changes when the theme flips to dark",
    ),
    "vaultui-gov-ssr-shell-decision": (
        "server-side shells",
        "how are public pages delivered",
    ),
    "vaultui-gov-lucide-decision": (
        "one migration pass instead of two",
        "when does the icon set change",
    ),
    "vaultui-gov-windowing-decision": (
        "five-row overscan",
        "how are very long lists kept usable",
    ),
    "vaultui-gov-headless-tables-decision": (
        "headless table primitives",
        "what are the data grids built on",
    ),
    "vaultui-gov-token-pipeline-decision": (
        "CSS variables and a TypeScript module",
        "how do design tokens reach the code",
    ),
    "vaultui-gov-playwright-decision": (
        "without a display server",
        "which runner owns the browser tests",
    ),
    "vaultui-gov-visual-regression-decision": (
        "pixel diff threshold of zero",
        "how strict are the screenshot comparisons",
    ),
    "vaultui-gov-error-boundary-decision": (
        "degrades one route, never the application shell",
        "what happens when a screen fails to render",
    ),
    "vaultui-gov-optimistic-ui-decision": (
        "skeletons, not locked forms",
        "how does the interface behave while saving",
    ),
    "vaultui-gov-no-microfrontends-decision": (
        "microfrontend split",
        "why keep one frontend application",
    ),
    "vaultui-gov-prefetch-decision": (
        "capped at two concurrent prefetches",
        "when do route bundles get fetched",
    ),
    "vaultui-gov-i18n-decision": (
        "compile-time key checks",
        "how are translations kept complete",
    ),
    "vaultui-gov-focus-ring-decision": (
        "native focus-visible styling",
        "where do focus outlines come from",
    ),
    "vaultui-gov-skeleton-decision": (
        "layout-stable skeletons",
        "what renders while data loads",
    ),
    "vaultui-gov-swr-decision": (
        "stale-while-revalidate cache",
        "how does the app reuse fetched data",
    ),
    "vaultui-gov-strict-ts-decision": (
        "no implicit any",
        "how loose can the types get",
    ),
    "vaultui-gov-lint-zero-decision": (
        "the count may only decrease",
        "how are lint warnings handled",
    ),
    "vaultui-gov-workbench-parity-decision": (
        "fails the docs build",
        "where is component usage documented",
    ),
    "vaultui-gov-breakpoints-decision": (
        "640, 960 and 1280 pixels",
        "which viewport widths define the layout",
    ),
    "vaultui-gov-hydration-dates-decision": (
        "stable skeleton during hydration",
        "why not format dates on first paint",
    ),
    # mnemos-core rules
    "mnemos-gov-fingerprint-rule": (
        "stale fingerprint fails the gate",
        "what forces a benchmark refresh when fixtures change",
    ),
    "mnemos-gov-quarantine-rule": (
        "format-constrained retraction renders",
        "what do blocked records show to agents",
    ),
    "mnemos-gov-determinism-rule": (
        "no wall-clock values",
        "may benchmark metrics use the clock",
    ),
    # mnemos-core decisions
    "mnemos-gov-rrf-params-decision": (
        "rrf_k=60",
        "how are the ranking constants governed",
    ),
    "mnemos-gov-sqlite-store-decision": (
        "rows, vectors and full-text search",
        "why no separate vector database",
    ),
    "mnemos-gov-blake2-reference-decision": (
        "the pipeline, not the model download",
        "what does the reference embedder actually pin",
    ),
    "mnemos-gov-fts5-stemming-decision": (
        "porter tokenizer",
        "how does text search split its words",
    ),
    "mnemos-gov-supersedes-decision": (
        "supersedes edges instead of in-place mutation",
        "what happens to replaced context blocks",
    ),
    "mnemos-gov-ccr-snippets-decision": (
        "snippet selections for detail needs",
        "how is compressed content served back",
    ),
    "mnemos-gov-scan-at-issuance-decision": (
        "never mutates stored originals",
        "when are secrets removed from results",
    ),
    "mnemos-gov-status-gate-decision": (
        "entry invariant, not a filter preference",
        "which rows can search actually see",
    ),
    "mnemos-gov-project-predicate-decision": (
        "project predicate before fusion",
        "how are other projects kept out of results",
    ),
    "mnemos-gov-overfetch-decision": (
        "over-fetch factor is pinned at four",
        "how deep does the vector leg reach",
    ),
    "mnemos-gov-lanes-v0-decision": (
        "without a manifest lane",
        "what is absent from lanes in the first version",
    ),
    "mnemos-gov-b0-control-decision": (
        "trivial control leg",
        "why compare against a plain ranking boost",
    ),
    "mnemos-gov-mcnemar-decision": (
        "discordant pairs at alpha 0.05",
        "how are paired experiment legs compared statistically",
    ),
    "mnemos-gov-corridor-decision": (
        "the larger of a two-point floor",
        "how are performance floors chosen",
    ),
    "mnemos-gov-e2-profile-decision": (
        "checkpoint share of 58 percent",
        "what share of the experiment store is checkpoints",
    ),
    "mnemos-gov-blind-adjudication-decision": (
        "Cohen kappa calibration floor",
        "how is judge agreement quality checked",
    ),
    "mnemos-gov-equal-budget-decision": (
        "identical assembled-context token budgets",
        "are legs allowed different context sizes",
    ),
    "mnemos-gov-pin-cost-decision": (
        "5, 15 and 30 percent",
        "how much of the budget may pinned rules take",
    ),
    "mnemos-gov-two-key-decision": (
        "two-key rule",
        "who approves pinning a governance record",
    ),
    "mnemos-gov-origin-provenance-decision": (
        "server-observed columns only",
        "where does record origin come from",
    ),
    "mnemos-gov-retention-invariant-decision": (
        "retention-or-report invariant",
        "what must hold for facts at collapse boundaries",
    ),
    "mnemos-gov-synthetic-corpus-decision": (
        "zero tenant data",
        "why not use real production memories in experiments",
    ),
    # atlas-pipeline rules
    "atlas-gov-late-arrival-rule": (
        "route to the repair lane",
        "what happens to events that land late",
    ),
    "atlas-gov-retention-rule": (
        "queryable for ninety days",
        "how long do hot partitions remain readable",
    ),
    "atlas-gov-pii-masking-rule": (
        "curated access tier",
        "can analytics marts expose who tenants are",
    ),
    # atlas-pipeline decisions
    "atlas-gov-delta-decision": (
        "lakehouse table format",
        "which table format stores the lake",
    ),
    "atlas-gov-defer-streaming-decision": (
        "fifteen-minute micro-batches",
        "did we move event ingest to streaming",
    ),
    "atlas-gov-salted-keys-decision": (
        "second re-aggregation pass",
        "how is the whale tenant shuffle handled",
    ),
    "atlas-gov-dbt-decision": (
        "uniqueness and not-null tests on every grain",
        "what must every transformation model declare",
    ),
    "atlas-gov-window-decision": (
        "hard close at 06:00 UTC",
        "when does the daily batch window run",
    ),
    "atlas-gov-hive-layout-decision": (
        "run_date and region",
        "how are partitions laid out for pruning",
    ),
    "atlas-gov-compaction-trigger-decision": (
        "median file size under 32 MB",
        "when do small files get merged",
    ),
    "atlas-gov-exactly-once-decision": (
        "idempotent event identifiers per partition",
        "how are duplicate deliveries prevented",
    ),
    "atlas-gov-writer-schema-decision": (
        "a new dataset generation",
        "how do breaking schema changes ship",
    ),
    "atlas-gov-copy-cluster-decision": (
        "a tenth of production",
        "where are dangerous backfills rehearsed",
    ),
    "atlas-gov-dlq-sla-decision": (
        "seven-day replay service level",
        "how long may a dead-lettered partition sit",
    ),
    "atlas-gov-rowcount-gate-decision": (
        "two percent tolerance",
        "how are published partitions verified against sources",
    ),
    "atlas-gov-shared-pool-decision": (
        "idle capacity, not isolation",
        "why not give every tenant its own cluster",
    ),
    "atlas-gov-spot-runners-decision": (
        "prefer spot capacity",
        "which workloads may run on preemptible machines",
    ),
    "atlas-gov-data-contracts-decision": (
        "run in the producer pipeline",
        "who owns schema test failures",
    ),
    "atlas-gov-incremental-decision": (
        "written reason in the model header",
        "when is a full refresh acceptable",
    ),
    "atlas-gov-cost-dashboards-decision": (
        "cost regression above twenty percent",
        "how are pipeline spend regressions caught",
    ),
    "atlas-gov-connector-isolation-decision": (
        "egress limited to the vendor endpoint",
        "how are vendor connectors contained",
    ),
    "atlas-gov-openlineage-decision": (
        "column-level lineage",
        "what must new pipelines emit about their steps",
    ),
    "atlas-gov-lag-slo-decision": (
        "twenty-four-hour service objective",
        "how far behind may the watermarks lag",
    ),
    "atlas-gov-erasure-decision": (
        "crypto-shredding of per-tenant data keys",
        "how are deletion requests executed",
    ),
    "atlas-gov-dq-review-decision": (
        "tolerance registry",
        "who revisits recurring data quality failures",
    ),
}


@dataclass(frozen=True)
class GovQuery:
    """One G-gov query, bound to the record it was generated from."""

    qid: str
    text: str
    record_slug: str
    project: str
    family: str  # "ph" (exact phrase) | "pr" (paraphrase)


def _build_gov_queries() -> list[GovQuery]:
    queries: list[GovQuery] = []
    for ordinal, record in enumerate(GOV_RECORDS, start=1):
        phrase, paraphrase = _GOV_QUERY_TEXTS[record.slug]
        for family, text in (("ph", phrase), ("pr", paraphrase)):
            queries.append(
                GovQuery(
                    qid=f"gg-{ordinal:03d}-{family}",
                    text=text,
                    record_slug=record.slug,
                    project=record.project,
                    family=family,
                )
            )
    return queries


#: All 200 G-gov query definitions (2 per seeded record, record order).
GOV_QUERIES: list[GovQuery] = _build_gov_queries()

#: E0 §3.1: the analyzed set — exactly the 96 queries of the 48
#: analyzed records (48 x 2 phrasings). Pool records' queries stay
#: defined but dormant until an adjudication replacement activates them.
ANALYZED_GOV_QUERIES: tuple[GovQuery, ...] = tuple(
    q for q in GOV_QUERIES if q.record_slug in frozenset(ANALYZED_GOV_SLUGS)
)

ANALYZED_GOV_QIDS: frozenset[str] = frozenset(q.qid for q in ANALYZED_GOV_QUERIES)

GOV_QIDS: frozenset[str] = frozenset(q.qid for q in GOV_QUERIES)


def gov_queries_for_record(slug: str) -> list[GovQuery]:
    """The two phrasings of one seeded record (ph first, then pr)."""
    return [q for q in GOV_QUERIES if q.record_slug == slug]


# ── G-neg: negative control (E0 §3.2) ────────────────────────────────────────


@dataclass(frozen=True)
class NegQuery:
    """One G-neg query: knowledge-seeking, governance-adjacent wording.

    ``expected`` holds only knowledge-class golden slugs — never a
    rule/decision record: by construction no governance entry correctly
    answers a G-neg query, so any governance block surfacing in a
    result set is a false insertion (E0 §2.4 metric b, measured at run
    time).
    """

    qid: str
    text: str
    project: str
    expected: frozenset[str] = field(default_factory=frozenset)


def _neg(qid: str, text: str, project: str, *expected: str) -> NegQuery:
    return NegQuery(qid=qid, text=text, project=project, expected=frozenset(expected))


NEG_QUERIES: tuple[NegQuery, ...] = (
    # aurora-api — knowledge gold, governance lexicon nearby
    _neg(
        "gn-01", "how does the token bucket refill under load", "aurora-api", "aurora-rate-limiter"
    ),
    _neg(
        "gn-02",
        "raw log lines from the checkout five hundred spike",
        "aurora-api",
        "aurora-500-spike-log",
    ),
    _neg(
        "gn-03",
        "the go implementation of the auth middleware",
        "aurora-api",
        "aurora-auth-middleware",
    ),
    _neg(
        "gn-04",
        "how did migration 0043 rename the actor column",
        "aurora-api",
        "aurora-migration-notes",
    ),
    _neg(
        "gn-05",
        "why did the manifest cache overload the config service",
        "aurora-api",
        "aurora-cache-ttl-pattern",
    ),
    _neg(
        "gn-06",
        "what do the gateway dashboards track per endpoint",
        "aurora-api",
        "aurora-observability-lean",
    ),
    # vault-ui
    _neg(
        "gn-07",
        "how does the search box state machine handle stale responses",
        "vault-ui",
        "vaultui-state-machine",
    ),
    _neg(
        "gn-08",
        "why did hydration fail on the dashboard chart",
        "vault-ui",
        "vaultui-hydration-log",
    ),
    _neg(
        "gn-09",
        "what caused the render phase dispatch warning",
        "vault-ui",
        "vaultui-console-error-log",
    ),
    _neg(
        "gn-10",
        "the tsx implementation of the dialog portal",
        "vault-ui",
        "vaultui-modal-component",
    ),
    _neg(
        "gn-11",
        "what did the placeholder contrast audit find",
        "vault-ui",
        "vaultui-contrast-audit",
    ),
    _neg(
        "gn-12",
        "why is sixty fps on the dev box not a baseline",
        "vault-ui",
        "vaultui-list-virtualization",
    ),
    # mnemos-core
    _neg(
        "gn-13",
        "what happens to results when one retrieval leg errors",
        "mnemos-core",
        "mnemos-rrf-fusion",
    ),
    _neg(
        "gn-14",
        "how does marker compression shrink long prose logs",
        "mnemos-core",
        "mnemos-ccr-design",
    ),
    _neg("gn-15", "what sits inside the vector store tables", "mnemos-core", "mnemos-vector-store"),
    _neg(
        "gn-16",
        "which embedding models were compared on cpu",
        "mnemos-core",
        "mnemos-embedder-bench",
    ),
    _neg("gn-17", "how were provider cache misses reduced", "mnemos-core", "mnemos-cache-aligner"),
    _neg(
        "gn-18",
        "why did the wal balloon during federation pulls",
        "mnemos-core",
        "mnemos-sqlite-wal-log",
    ),
    # atlas-pipeline
    _neg(
        "gn-19",
        "the python orchestrator code for the repair lane",
        "atlas-pipeline",
        "atlas-orchestrator-code",
    ),
    _neg(
        "gn-20",
        "what did compaction do to scan latency",
        "atlas-pipeline",
        "atlas-parquet-compaction",
    ),
    _neg(
        "gn-21",
        "the late arrival log for the eu partition",
        "atlas-pipeline",
        "atlas-late-arrival-log",
    ),
    _neg(
        "gn-22",
        "which task log printed a connection string",
        "atlas-pipeline",
        "atlas-secret-spill-log",
    ),
    _neg(
        "gn-23",
        "why did the executor die during shuffle write",
        "atlas-pipeline",
        "atlas-oom-shuffle-log",
    ),
    _neg(
        "gn-24",
        "what pattern links the two executor oom incidents",
        "atlas-pipeline",
        "atlas-processed-incidents",
    ),
)

NEG_QIDS: frozenset[str] = frozenset(q.qid for q in NEG_QUERIES)
