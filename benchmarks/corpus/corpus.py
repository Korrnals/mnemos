"""Golden evaluation corpus (ADR-0017 D5, #125 Phase 1 Wave 4).

A curated, deterministic fixture corpus of 84 memories across four
projects, exercising every axis the D5 metrics must see:

- content types: code, prose, raw log excerpts;
- statuses: ``published`` (vector+FTS eligible), ``processed`` (FTS only
  — never embedded), ``raw`` (excluded from default search by the
  ADR-0018 entry-invariant status gate);
- tag contract: exactly one ``project:<slug>`` + one ``agent:<slug>`` +
  at least one ``mnemos:<subtype>`` per entry;
- planted FAKE secrets (``PLANTED_SECRETS``) — obviously-fake literals
  matching the detector catalogue, embedded in topically-relevant
  entries so they RANK for their queries; injection-acceptance measures
  that their issued forms never leak the literal;
- CCR markers embedded in some entries (syntactically valid markers that
  do not resolve to a cache row — they exercise the marker-parse path,
  not expansion).

Every entry carries a stable ``slug`` — the golden relevance judgments
(``queries.py``) reference slugs, and the harness maps slug → real
memory id after ingest (``Memory.id`` is a fresh uuid4 per run).

All content is synthetic. Secret-looking strings are FAKE values
generated for this fixture (sensitive-data policy); they exist to be
caught, never to be echoed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Planted FAKE secrets (detector-catalogue-shaped, obviously fake) ─────────
#
# Keyed by a short name; the value is the literal the harness asserts is
# ABSENT from every issued form. Do not reuse these strings anywhere else.
PLANTED_SECRETS: dict[str, str] = {
    # Length-exact fakes: every literal MUST match its detector pattern —
    # the injection-acceptance metric is vacuous against non-matching
    # plants (a first-run finding: ghp_ needs exactly 36 alnum after it).
    "aws-key": "AKIAFAKEFAKEFAKE77AA",  # AKIA + 16 [0-9A-Z]
    "github-token": "ghp_FAKEfakeFAKEfakeFAKEfakeFAKEfake1234",  # ghp_ + 36
    "slack-token": "xoxb-fake-slack-token-not-real",
    "openai-key": "sk-FAKEfakeFAKEfakeFAKEfake12345",  # sk- + >=20
    "jwt": "eyJFAKEeyJfake.eyJFAKEfake2.FAKEsigFAKEsig",
    "pem-private-key": "-----BEGIN FAKE PRIVATE KEY-----",
    "connection-string": "postgres://fakeuser:fakepassword@db-internal",
}

_PEM_BODY = (
    "-----BEGIN FAKE PRIVATE KEY-----\n"
    "ZmFrZWtleWZha2VrZXlmYWtla2V5Cg==\n"
    "-----END FAKE PRIVATE KEY-----"
)


@dataclass(frozen=True)
class GoldenEntry:
    """One golden corpus memory (pre-ingest fixture shape)."""

    slug: str
    project: str
    agent: str
    title: str
    content: str
    mnemos_tags: tuple[str, ...]  # subtypes only ("decision", "rule", ...)
    free_tags: tuple[str, ...] = field(default=())
    status: str = "published"  # published | processed | raw
    source: str = "mcp"  # manual | mcp | file | cli | synthesized | rule | web
    planted: tuple[str, ...] = field(default=())  # PLANTED_SECRETS keys


# ── Project 1: aurora-api (backend service) ──────────────────────────────────

AURORA: list[GoldenEntry] = [
    GoldenEntry(
        slug="aurora-deploy-runbook",
        project="aurora-api",
        agent="aurora-backend",
        title="Aurora API deployment runbook",
        content=(
            "aurora-api deployment runbook: build the container image, run the\n"
            "sqlite-to-postgres migration check, rotate the service token, then\n"
            "deploy the gateway pods. Verify /healthz returns 200 and the\n"
            "migration ledger row count matches before promoting green."
        ),
        mnemos_tags=("rule",),
        free_tags=("runbook", "deploy"),
    ),
    GoldenEntry(
        slug="aurora-auth-middleware",
        project="aurora-api",
        agent="aurora-backend",
        title="Auth middleware (Go)",
        content=(
            "func authMiddleware(next http.Handler) http.Handler {\n"
            "    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {\n"
            "        token := bearerToken(r) // aurora auth gateway\n"
            "        if !tokenValidator.Verify(token) {\n"
            '            http.Error(w, "invalid bearer token", 401)\n'
            "            return\n"
            "        }\n"
            "        next.ServeHTTP(w, r)\n"
            "    })\n"
            "}"
        ),
        mnemos_tags=("learning",),
        free_tags=("code", "auth"),
    ),
    GoldenEntry(
        slug="aurora-rate-limiter",
        project="aurora-api",
        agent="aurora-backend",
        title="Token-bucket rate limiter",
        content=(
            "rate limiter: token bucket per API key, refill 100/min, burst 50.\n"
            "Slower than expected under the load test — the bucket refill used a\n"
            "wall-clock read per request; switching to a monotonic clock cut\n"
            "p99 latency from 40ms to 9ms."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("code", "performance"),
    ),
    GoldenEntry(
        slug="aurora-oom-log",
        project="aurora-api",
        agent="aurora-oncall",
        title="OOM incident log excerpt",
        content=(
            "2026-08-14T03:12:41Z ERROR aurora-api pod=gateway-7f9 container oom killed\n"
            "2026-08-14T03:12:44Z WARN restart backoff 2s rss=512Mi limit=512Mi\n"
            "2026-08-14T03:13:02Z INFO pod=gateway-7f9 restarted serving traffic"
        ),
        mnemos_tags=("session",),
        free_tags=("log", "incident"),
    ),
    GoldenEntry(
        slug="aurora-500-spike-log",
        project="aurora-api",
        agent="aurora-oncall",
        title="5xx spike log excerpt",
        content=(
            "2026-08-20T11:04:02Z WARN aurora-api 500 spike on /v1/checkout\n"
            "2026-08-20T11:04:03Z WARN upstream db pool exhausted conns=32/32\n"
            "2026-08-20T11:06:10Z INFO pool recovered after connection storm"
        ),
        mnemos_tags=("session",),
        free_tags=("log", "incident"),
    ),
    GoldenEntry(
        slug="aurora-rls-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="ADR: adopt Postgres RLS for tenant isolation",
        content=(
            "Decision: adopt row-level security (RLS) policies for tenant\n"
            "isolation instead of application-level WHERE clauses. Rationale:\n"
            "one missed clause is a cross-tenant leak; the policy lives with\n"
            "the schema and is exercised by the same migration tests."
        ),
        mnemos_tags=("decision",),
    ),
    GoldenEntry(
        slug="aurora-no-fstring-sql-rule",
        project="aurora-api",
        agent="aurora-backend",
        title="Rule: never build SQL by f-string",
        content=(
            "Rule: SQL statements in aurora-api are built with bound parameters\n"
            "only. f-string interpolation of user input into SQL is a blocking\n"
            "review violation (SQL injection, see security training module 4)."
        ),
        mnemos_tags=("rule",),
        source="rule",
    ),
    GoldenEntry(
        slug="aurora-sharding-question",
        project="aurora-api",
        agent="aurora-backend",
        title="Open question: shard the events table?",
        content=(
            "Open question: at what row count does the events table need\n"
            "sharding? Current growth ~2M rows/month; the partition-wise join\n"
            "benchmark suggests we stay unsharded until ~200M rows."
        ),
        mnemos_tags=("open-question",),
    ),
    GoldenEntry(
        slug="aurora-release-checkpoint",
        project="aurora-api",
        agent="aurora-backend",
        title="Checkpoint: release 2.7.0",
        content=(
            "Checkpoint: aurora-api 2.7.0 shipped — RLS policies on 6 tables,\n"
            "token-bucket limiter v2, migration ledger backfill. Rollout 100%\n"
            "after 2h soak with zero 5xx regression."
        ),
        mnemos_tags=("checkpoint",),
    ),
    GoldenEntry(
        slug="aurora-db-conn-leak",
        project="aurora-api",
        agent="aurora-oncall",
        title="DB bootstrap script with leaked credential (PLANTED)",
        content=(
            "db bootstrap snippet for the staging environment — connect with\n"
            "postgres://fakeuser:fakepassword@db-internal:5432/aurora then run\n"
            "the schema migration. Rotate the credential if the script is\n"
            "committed to a public branch."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("code", "security"),
        planted=("connection-string",),
    ),
    GoldenEntry(
        slug="aurora-ci-token-note",
        project="aurora-api",
        agent="aurora-backend",
        title="CI publisher token handling note (PLANTED)",
        content=(
            "The release workflow authenticates to the package registry with\n"
            "a machine token ghp_FAKEfakeFAKEfakeFAKEfakeFAKEfake1234 stored in the\n"
            "CI secret store. Never paste the token into issue comments; the\n"
            "scanner flags github-token shapes on write."
        ),
        mnemos_tags=("rule",),
        planted=("github-token",),
    ),
    GoldenEntry(
        slug="aurora-jwt-session-log",
        project="aurora-api",
        agent="aurora-oncall",
        title="Debug session log with sample bearer JWT (PLANTED)",
        content=(
            "debug log captured while tracing the auth gateway loop:\n"
            "authorization: Bearer eyJFAKEeyJfake.eyJFAKEfake2.FAKEsigFAKEsig\n"
            "The token validator rejected the sample; audience mismatch.\n"
            "Redact before sharing the log excerpt externally."
        ),
        mnemos_tags=("session",),
        free_tags=("log", "auth"),
        planted=("jwt",),
    ),
    GoldenEntry(
        slug="aurora-cache-ttl-pattern",
        project="aurora-api",
        agent="aurora-backend",
        title="Bug pattern: TTL cache stampede",
        content=(
            "Bug pattern: cache stampede on TTL expiry. When the deployment\n"
            "manifest cache expired, 200 concurrent pods re-fetched the manifest\n"
            "and overloaded the config service. Fix pattern: single-flight lock\n"
            "per cache key plus jittered TTL."
        ),
        mnemos_tags=("bug-pattern",),
    ),
    GoldenEntry(
        slug="aurora-migration-notes",
        project="aurora-api",
        agent="aurora-backend",
        title="Migration 0043 notes",
        content=(
            "migration 0043 renames audit_events.actor_id to principal_id and\n"
            "backfills from the legacy mapping table. Lock timeout 5s; run\n"
            "outside peak. Downstream dashboards updated in the same release."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="aurora-healthcheck-code",
        project="aurora-api",
        agent="aurora-backend",
        title="Healthcheck handler (Go)",
        content=(
            "func healthz(w http.ResponseWriter, r *http.Request) {\n"
            "    if !db.Ping(r.Context()) {\n"
            '        http.Error(w, "db unreachable", 503)\n'
            "        return\n"
            "    }\n"
            "    w.WriteHeader(200)\n"
            "}"
        ),
        mnemos_tags=("learning",),
        free_tags=("code",),
    ),
    GoldenEntry(
        slug="aurora-observability-lean",
        project="aurora-api",
        agent="aurora-backend",
        title="Observability learning: RED metrics",
        content=(
            "Learning: the aurora gateway dashboards follow RED — rate, errors,\n"
            "duration — per endpoint. Adding saturation (connection pool use)\n"
            "caught the checkout 5xx spike before customer reports."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="aurora-incident-retro",
        project="aurora-api",
        agent="aurora-oncall",
        title="Incident retro 2026-08-20",
        content=(
            "Retro: the checkout 5xx spike rooted in the db pool exhaustion\n"
            "during the migration backfill. Action items: cap backfill batch\n"
            "concurrency, alert on pool saturation > 80%, add a load-shedding\n"
            "circuit breaker on /v1/checkout."
        ),
        mnemos_tags=("decision",),
        free_tags=("incident",),
    ),
    GoldenEntry(
        slug="aurora-config-guide",
        project="aurora-api",
        agent="aurora-backend",
        title="Configuration guide",
        content=(
            "aurora-api configuration: AURORA_DB_DSN, AURORA_TOKEN_TTL (default\n"
            "15m), AURORA_RATE_LIMIT (default 100/min). Config arrives via env\n"
            "plus a manifest layer; the manifest wins on conflict."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="aurora-processed-cluster",
        project="aurora-api",
        agent="aurora-backend",
        title="Synthesis cluster: gateway latency themes",
        content=(
            "Cluster synthesis (processed): gateway latency themes across the\n"
            "week — monotonic-clock fix, pool sizing, and the rate limiter\n"
            "refill all recur; recommend a shared perf regression budget."
        ),
        mnemos_tags=("synthesized",),
        source="synthesized",
        status="processed",
    ),
    GoldenEntry(
        slug="aurora-processed-review",
        project="aurora-api",
        agent="aurora-oncall",
        title="Weekly review digest (processed)",
        content=(
            "Weekly digest (processed): three deploys green, one rollback of\n"
            "the connection-storm alert threshold — too sensitive at 60%\n"
            "pool saturation; reset to 80% after the retro."
        ),
        mnemos_tags=("synthesized",),
        source="synthesized",
        status="processed",
    ),
    GoldenEntry(
        slug="aurora-raw-ingest",
        project="aurora-api",
        agent="aurora-oncall",
        title="Raw ingest: vendor webhook dump",
        content=(
            "RAW pipeline ingest — unreviewed vendor webhook payload dump for\n"
            "the billing connector. Not yet quality-gated; do not surface in\n"
            "search until the pipeline publishes it."
        ),
        mnemos_tags=("legacy",),
        status="raw",
    ),
    GoldenEntry(
        slug="aurora-raw-scratch",
        project="aurora-api",
        agent="aurora-backend",
        title="Raw scratch: rewrite draft",
        content=(
            "RAW scratch draft of the deploy runbook rewrite — half-finished\n"
            "sentences, unverified claims about the migration ledger. Draft\n"
            "only; the published runbook is the source of truth."
        ),
        mnemos_tags=("legacy",),
        status="raw",
    ),
    GoldenEntry(
        slug="aurora-ccr-handoff",
        project="aurora-api",
        agent="aurora-backend",
        title="Compressed handoff note",
        content=(
            "Handoff to the next oncall: the full incident timeline lives\n"
            "behind the marker below; redeem via mnemos_retrieve if needed.\n"
            "[compressed: "
            "a3f5c7901b2d4e6f8a0c1d3e5f70921b4d6e8f0a2c4e6d8b0a2c4e6f8d1a3c5e | "
            "5000→500 chars | retrieve via mnemos_retrieve]"
        ),
        mnemos_tags=("session",),
        free_tags=("ccr",),
    ),
]

# ── Project 2: vault-ui (frontend) ───────────────────────────────────────────

VAULT_UI: list[GoldenEntry] = [
    GoldenEntry(
        slug="vaultui-design-tokens",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Design tokens spec",
        content=(
            "vault-ui design tokens: spacing scale 4/8/12/16/24, radius 6px,\n"
            "surface colours from the slate ramp, accent indigo-500. Dark mode\n"
            "remaps surfaces only; accents and spacing stay invariant."
        ),
        mnemos_tags=("decision",),
        free_tags=("design",),
    ),
    GoldenEntry(
        slug="vaultui-keyboard-nav",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Keyboard navigation rules",
        content=(
            "Rule: every vault-ui interactive surface is reachable by keyboard\n"
            "in DOM order; Escape closes modals; focus returns to the invoking\n"
            "control. Tab traps inside dialogs until dismissed."
        ),
        mnemos_tags=("rule",),
        free_tags=("a11y",),
    ),
    GoldenEntry(
        slug="vaultui-list-virtualization",
        project="vault-ui",
        agent="vaultui-frontend",
        title="List virtualization learning",
        content=(
            "Learning: the memory list jank at 10k rows vanished with windowed\n"
            "rendering (visible rows + 5 overscan). Re-measure on low-end\n"
            "hardware before claiming victory; 60fps on the dev box is not a\n"
            "baseline."
        ),
        mnemos_tags=("learning",),
        free_tags=("performance",),
    ),
    GoldenEntry(
        slug="vaultui-modal-component",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Modal component (tsx)",
        content=(
            "export function Modal({ open, onClose, children }: ModalProps) {\n"
            "  const ref = useFocusTrap(open);          // vault-ui focus trap\n"
            "  useEscapeKey(open, onClose);\n"
            "  if (!open) return null;\n"
            '  return createPortal(<div role="dialog" ref={ref}>{children}</div>,\n'
            "    document.body);\n"
            "}"
        ),
        mnemos_tags=("learning",),
        free_tags=("code",),
    ),
    GoldenEntry(
        slug="vaultui-state-machine",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Search box state machine",
        content=(
            "The search box is an explicit state machine: idle → typing →\n"
            "debounced → loading → results | empty | error. Direct transitions\n"
            "from loading to typing are illegal — a stale response must never\n"
            "overwrite fresh keystrokes (last-writer-wins guard on request id)."
        ),
        mnemos_tags=("bug-pattern",),
    ),
    GoldenEntry(
        slug="vaultui-contrast-audit",
        project="vault-ui",
        agent="vaultui-design",
        title="Contrast audit findings",
        content=(
            "Audit: three placeholder labels failed WCAG AA contrast on the\n"
            "slate-200 surface (3.1:1). Fix: slate-500 for placeholder text,\n"
            "keeping the input border at slate-300 for non-text contrast."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("a11y",),
    ),
    GoldenEntry(
        slug="vaultui-release-notes",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Release notes 1.9",
        content=(
            "vault-ui 1.9: windowed memory list, focus-trapped modals, the\n"
            "search state machine, and dark-mode token remap. Bundle down 12%\n"
            "after dropping the icon barrel import."
        ),
        mnemos_tags=("checkpoint",),
    ),
    GoldenEntry(
        slug="vaultui-open-question-icons",
        project="vault-ui",
        agent="vaultui-design",
        title="Open question: icon set migration",
        content=(
            "Open question: migrate to the lucide icon set before or after\n"
            "the dashboard rewrite? Migration touches 40 components; the\n"
            "rewrite will touch them again — sequencing saves a duplicate pass."
        ),
        mnemos_tags=("open-question",),
    ),
    GoldenEntry(
        slug="vaultui-console-error-log",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Console error excerpt",
        content=(
            "vault-ui console: Warning: Cannot update a component while\n"
            "rendering a different component (SearchBox). Located at the\n"
            "debounce effect dispatching inside render phase — moved the\n"
            "dispatch to the effect body."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("log",),
    ),
    GoldenEntry(
        slug="vaultui-hydration-log",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Hydration mismatch log",
        content=(
            "console error: Hydration failed because the server rendered HTML\n"
            "didn't match the client at div.dashboard-chart. Cause: locale\n"
            "date formatting; fix: format dates after mount, render a stable\n"
            "skeleton during hydration."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("log",),
    ),
    GoldenEntry(
        slug="vaultui-slack-webhook-note",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Alert channel note with webhook token (PLANTED)",
        content=(
            "The design critique bot posts to the team channel with token\n"
            "xoxb-fake-slack-token-not-real configured in the CI env. If the\n"
            "bot double-posts, check the retry queue before rotating the\n"
            "token; the token is also flagged no-federate by the scanner."
        ),
        mnemos_tags=("rule",),
        planted=("slack-token",),
    ),
    GoldenEntry(
        slug="vaultui-embed-key-note",
        project="vault-ui",
        agent="vaultui-design",
        title="Playwright screenshot note with API key (PLANTED)",
        content=(
            "Visual regression runs against the preview deployment using key\n"
            "sk-FAKEfakeFAKEfakeFAKEfake12345 for the caption model. Keep the\n"
            "key out of screenshots; the issuance scan redacts openai-key\n"
            "shapes on every echo path."
        ),
        mnemos_tags=("learning",),
        planted=("openai-key",),
    ),
    GoldenEntry(
        slug="vaultui-chart-theming",
        project="vault-ui",
        agent="vaultui-design",
        title="Chart theming decision",
        content=(
            "Decision: charts consume the same design tokens as the shell —\n"
            "no bespoke palette per chart. Series colours derive from the\n"
            "accent with fixed lightness steps to survive dark mode."
        ),
        mnemos_tags=("decision",),
    ),
    GoldenEntry(
        slug="vaultui-form-validation",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Form validation patterns",
        content=(
            "Form validation pattern: validate on blur, re-validate on change\n"
            "after first error, never block the submit button — surface errors\n"
            "on attempt. Async validators debounce at 300ms and cancel stale\n"
            "checks by request id."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="vaultui-session-note",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Pairing session: dashboard grid",
        content=(
            "Pairing session: rebuilt the dashboard grid on CSS grid instead\n"
            "of flex rows — the chart panel now keeps a stable aspect across\n"
            "breakpoints and the sidebar stops wrapping under 960px."
        ),
        mnemos_tags=("session",),
    ),
    GoldenEntry(
        slug="vaultui-processed-tokens",
        project="vault-ui",
        agent="vaultui-design",
        title="Processed: token usage synthesis",
        content=(
            "Processed synthesis: token usage across 12 screens — spacing\n"
            "scale adopted everywhere except two legacy panels; the off-scale\n"
            "24px gaps are queued for the next sweep."
        ),
        mnemos_tags=("synthesized",),
        source="synthesized",
        status="processed",
    ),
    GoldenEntry(
        slug="vaultui-raw-feedback",
        project="vault-ui",
        agent="vaultui-design",
        title="Raw: untriaged user feedback",
        content=(
            "RAW untriaged feedback dump from the beta survey — contradictory\n"
            "requests about the sidebar density. Pipeline review pending; not\n"
            "safe to cite until synthesized."
        ),
        mnemos_tags=("legacy",),
        status="raw",
    ),
    GoldenEntry(
        slug="vaultui-ccr-review-pack",
        project="vault-ui",
        agent="vaultui-design",
        title="Compressed design review pack",
        content=(
            "Design review pack for 1.10 — the full annotated frames sit\n"
            "behind the marker; redeem on demand.\n"
            "[compressed: "
            "b7e2d4f6a8c0e2d4f6a8b0c2e4d6f8a0c2e4d6f8a0b2c4e6d8f0a2c4e6b8d0f2 | "
            "4200→420 chars | retrieve via mnemos_retrieve]"
        ),
        mnemos_tags=("session",),
        free_tags=("ccr",),
    ),
]

# ── Project 3: mnemos-core (the memory system, dogfooded) ────────────────────

MNEMOS: list[GoldenEntry] = [
    GoldenEntry(
        slug="mnemos-tag-contract-summary",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Tag contract summary",
        content=(
            "mnemos tag contract: exactly one project:<slug> and one\n"
            "agent:<slug> tag per memory, plus at least one mnemos:<subtype>\n"
            "category tag. gcw: prefixes auto-migrate to mnemos:. Strict mode\n"
            "rejects violations at the write boundary."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="mnemos-rrf-fusion",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="RRF fusion notes",
        content=(
            "Hybrid search fuses FTS5 and vector legs with Reciprocal Rank\n"
            "Fusion, rrf_k=60, alpha weighting the vector leg (default 0.7).\n"
            "A leg that errors degrades gracefully — the other leg still\n"
            "ranks, and search_type reports fts_only."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="mnemos-status-pipeline",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Knowledge pipeline statuses",
        content=(
            "Knowledge pipeline: raw → processing → processed → published,\n"
            "with a dead-letter queue for gate failures. Default search admits\n"
            "only published and processed entries — the ADR-0018 entry\n"
            "invariant keeps raw and DLQ content out of context."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="mnemos-project-predicate",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="A9 project predicate note",
        content=(
            "A9 fix: the vector leg applies the project predicate before\n"
            "fusion — native store filter plus an authoritative resolve-time\n"
            "guard on the SQLite project column. The 4x over-fetch keeps the\n"
            "leg's contribution depth fillable after the predicate drops\n"
            "foreign candidates."
        ),
        mnemos_tags=("decision",),
    ),
    GoldenEntry(
        slug="mnemos-issuance-scan",
        project="mnemos-core",
        agent="mnemos-security",
        title="Issuance scan semantics",
        content=(
            "Every content-echoing path scans at issuance: matched secret\n"
            "spans become <REDACTED:<pattern>> in the returned copy, or the\n"
            "whole string is refused when retrieve_refuse_on_secret is on.\n"
            "Stored originals are never mutated (zero-loss storage)."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="mnemos-ccr-design",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="CCR design note",
        content=(
            "CCR (compress-cache-retrieve): content-addressed originals in a\n"
            "TTL/LRU cache, thin marker in context, FTS5 snippet retrieval by\n"
            "query, full redemption by hash. 86-96% reduction on prose logs."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="mnemos-rewrite-event",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="on_context_rewrite semantics",
        content=(
            "on_context_rewrite is an idempotent lifecycle event: the original\n"
            "of the replaced block enters the knowledge pipeline at raw and\n"
            "gates to published; a marker with an advisory diff stays in the\n"
            "window; supersedes edges link successive blocks (Phase 2 uses\n"
            "them for traversal)."
        ),
        mnemos_tags=("decision",),
    ),
    GoldenEntry(
        slug="mnemos-federation-gossip",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Federation sync notes",
        content=(
            "Federation: batch sync plus mediated pull between trusted nodes;\n"
            "no-federate tagged records never leave the node. Access logs\n"
            "record every exchange; the auth bypass regression is covered by\n"
            "a dedicated suite."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="mnemos-vector-store",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Vector store layout",
        content=(
            "Vector store: SQLite table of packed float32 blobs plus metadata;\n"
            "similarity is a matrix cosine over the filtered candidate set —\n"
            "no external vector DB, no Rust deps. Only published memories are\n"
            "embedded at write time."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="mnemos-sqlite-wal-log",
        project="mnemos-core",
        agent="mnemos-oncall",
        title="WAL checkpoint log excerpt",
        content=(
            "mnemos sqlite: WAL auto-checkpoint at 1000 pages; long federation\n"
            "pulls held a read txn and blocked checkpoint — wal grew to 800MB.\n"
            "Fix: chunked pull with periodic commit every 500 rows."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("log",),
    ),
    GoldenEntry(
        slug="mnemos-mcp-timeout-log",
        project="mnemos-core",
        agent="mnemos-oncall",
        title="MCP tool timeout log",
        content=(
            "2026-08-22T09:15:33Z WARN mnemos mcp tool mnemos_search exceeded\n"
            "soft deadline 800ms (took 1430ms) — cold vector cache after\n"
            "restart; second call 40ms. Action: pre-warm embeddings on boot."
        ),
        mnemos_tags=("session",),
        free_tags=("log",),
    ),
    GoldenEntry(
        slug="mnemos-embedder-bench",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Embedder benchmark comparison",
        content=(
            "Embedder benchmark: MiniLM 384-dim ~8ms/text on CPU, multilingual\n"
            "e5-small 12ms with better RU recall; hash-based lexical embedders\n"
            "run in microseconds but cap semantic recall at bag-of-words\n"
            "level. Choose per deployment latency budget."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="mnemos-cache-aligner",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="CacheAligner purpose",
        content=(
            "CacheAligner relocates dynamic content (timestamps, counters) to\n"
            "the tail of each assembled block so provider KV prefix caches\n"
            "keep hitting across turns — measured 31% fewer cache misses on\n"
            "the Hermes adapter trace."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="mnemos-golden-set-design",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Golden set design decisions",
        content=(
            "Golden set design: honest small judgments over large sloppy\n"
            "ones; deterministic lexical embedder so the baseline measures\n"
            "the pipeline, not the model download; planted fake secrets to\n"
            "measure injection-acceptance as one minus leak rate."
        ),
        mnemos_tags=("decision",),
    ),
    GoldenEntry(
        slug="mnemos-otel-traces",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Trace sampling decision",
        content=(
            "Decision: trace sampling at 10% for retrieval spans, 100% for\n"
            "errors; the analytics plane stays deferred until event volume\n"
            "passes 10M rows (ADR-0017 D4 triggers)."
        ),
        mnemos_tags=("decision",),
    ),
    GoldenEntry(
        slug="mnemos-aws-log",
        project="mnemos-core",
        agent="mnemos-oncall",
        title="Backup env excerpt with AWS key (PLANTED)",
        content=(
            "nightly backup env (excerpt): S3_ACCESS_KEY_ID=AKIAFAKEFAKEFAKE77AA\n"
            "plus the secret in the vault. The key shape trips the aws-key\n"
            "detector on write — expected; the row is stored zero-loss and\n"
            "redacted at issuance."
        ),
        mnemos_tags=("session",),
        free_tags=("log", "security"),
        planted=("aws-key",),
    ),
    GoldenEntry(
        slug="mnemos-pem-note",
        project="mnemos-core",
        agent="mnemos-security",
        title="Node TLS note with key header (PLANTED)",
        content=(
            "federation TLS note: the node cert rotates quarterly; the fake\n"
            "sample below shows the header shape the scanner flags.\n"
            + _PEM_BODY
            + "\nThe pem-private-key pattern redacts only the header line."
        ),
        mnemos_tags=("rule",),
        free_tags=("security",),
        planted=("pem-private-key",),
    ),
    GoldenEntry(
        slug="mnemos-open-question-budget",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Open question: budget partitioning",
        content=(
            "Open question: how many tokens does assemble_context reserve for\n"
            "active-state lines before recall allocation? The ~500 figure has\n"
            "no evidence basis; the corridor opens after the D5 baseline."
        ),
        mnemos_tags=("open-question",),
    ),
    GoldenEntry(
        slug="mnemos-checkpoint-phase1",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Checkpoint: Phase 1 waves",
        content=(
            "Checkpoint: ADR-0017 Phase 1 — provider contract stages, hooks,\n"
            "SDK facade landed; the golden set and D5 baseline close the wave\n"
            "sequence. Hermes adapter migration follows the baseline."
        ),
        mnemos_tags=("checkpoint",),
    ),
    GoldenEntry(
        slug="mnemos-processed-metrics",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Processed: metric synthesis",
        content=(
            "Processed synthesis: precision/recall floors, injection-acceptance\n"
            "and the rewrite pair all recorded on the golden set; corridors\n"
            "stay non-normative until the owner ratifies them."
        ),
        mnemos_tags=("synthesized",),
        source="synthesized",
        status="processed",
    ),
    GoldenEntry(
        slug="mnemos-processed-federation",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Processed: federation digest",
        content=(
            "Processed digest: two peers synced, zero no-federate leaks in the\n"
            "access log audit, one clock-skew warning resolved by NTP step."
        ),
        mnemos_tags=("synthesized",),
        source="synthesized",
        status="processed",
    ),
    GoldenEntry(
        slug="mnemos-raw-prototype",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Raw: graph expansion prototype",
        content=(
            "RAW prototype notes for bounded graph expansion — depth 2 with a\n"
            "node-visit cap; unreviewed, contains rejected traversal ideas.\n"
            "Not citable until the pipeline publishes a distilled version."
        ),
        mnemos_tags=("legacy",),
        status="raw",
    ),
    GoldenEntry(
        slug="mnemos-ccr-bridge",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Compressed bridge spec",
        content=(
            "Rewrite-bridge spec summary; the full committee contract text is\n"
            "behind the marker and redeems on demand.\n"
            "[compressed: "
            "d1c3e5f7a9b1c3e5f7a9b1c3e5f7a9b1c3e5f7a9b1c3e5f7a9b1c3e5f7a9b1c3 | "
            "6000→600 chars | retrieve via mnemos_retrieve]"
        ),
        mnemos_tags=("decision",),
        free_tags=("ccr",),
    ),
]

# ── Project 4: atlas-pipeline (data ETL) ─────────────────────────────────────

ATLAS: list[GoldenEntry] = [
    GoldenEntry(
        slug="atlas-dag-design",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="DAG design conventions",
        content=(
            "atlas DAG conventions: idempotent tasks keyed by (run_date,\n"
            "partition), late arrivals land in a repair lane, and every task\n"
            "declares its upstream checkpoint explicitly — no implicit\n"
            "schedule-time ordering."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="atlas-backfill-runbook",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Backfill runbook",
        content=(
            "Backfill runbook: freeze the partition manifest, replay the\n"
            "repair lane from the watermark table, verify row counts against\n"
            "the source ledger, then unfreeze. A backfill without a frozen\n"
            "manifest is a rollback plan, not a plan."
        ),
        mnemos_tags=("rule",),
        free_tags=("runbook",),
    ),
    GoldenEntry(
        slug="atlas-spark-skew",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Spark skew bug pattern",
        content=(
            "Bug pattern: spark stage skew when partitioning by tenant_id —\n"
            "one whale tenant owned 40% of rows. Fix: salted keys for the\n"
            "shuffle, then a second pass to re-aggregate. Skew warning:\n"
            "max partition > 3x median."
        ),
        mnemos_tags=("bug-pattern",),
    ),
    GoldenEntry(
        slug="atlas-late-arrival-log",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Late arrival log excerpt",
        content=(
            "2026-08-19T02:00:11Z WARN atlas late-arrival events for\n"
            "run_date=2026-08-17 partition=eu count=1204 — routed to repair\n"
            "lane. Watermark lag 26h, SLA 24h; paging suppressed for lag\n"
            "< 30h after the vendor incident."
        ),
        mnemos_tags=("session",),
        free_tags=("log",),
    ),
    GoldenEntry(
        slug="atlas-watermark-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: watermark table over file markers",
        content=(
            "Decision: track watermarks in a transactional table instead of\n"
            "success-marker files — markers drifted during the storage\n"
            "migration and double-counted 3 partitions. The table is the\n"
            "single source of replay truth."
        ),
        mnemos_tags=("decision",),
    ),
    GoldenEntry(
        slug="atlas-parquet-compaction",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Parquet compaction learning",
        content=(
            "Learning: compact daily partitions weekly — small-file counts\n"
            "past 10k per partition crushed scan latency (4min → 40s after\n"
            "compaction). Trigger: file count > 5k or median file < 32MB."
        ),
        mnemos_tags=("learning",),
    ),
    GoldenEntry(
        slug="atlas-schema-evolution",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Schema evolution pattern",
        content=(
            "Schema evolution: additive columns only within a minor version;\n"
            "readers tolerate nulls; writers pin the writer-schema id in the\n"
            "manifest. Breaking changes ship as a new dataset generation."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="atlas-dq-gates",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Data quality gates",
        content=(
            "DQ gates: row-count delta within 2% of source ledger, null rate\n"
            "on join keys < 0.1%, exactly-once event ids per partition. A\n"
            "gate failure routes the partition to the DLQ lane with the\n"
            "failing rule id."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="atlas-oom-shuffle-log",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Executor OOM log",
        content=(
            "2026-08-21T13:47:02Z ERROR atlas executor 14 oom killed during\n"
            "shuffle write stage 7 (tenant rollup) — spark.memory.fraction\n"
            "0.6 left too little off-heap for the salted-key buffers."
        ),
        mnemos_tags=("session",),
        free_tags=("log",),
    ),
    GoldenEntry(
        slug="atlas-open-question-streaming",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Open question: streaming ingest",
        content=(
            "Open question: move the events tail to streaming ingest, or keep\n"
            "15-min micro-batches? Streaming cuts lag to minutes but breaks\n"
            "the idempotent replay model the backfill runbook relies on."
        ),
        mnemos_tags=("open-question",),
    ),
    GoldenEntry(
        slug="atlas-checkpoint-migration",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Checkpoint: storage migration done",
        content=(
            "Checkpoint: storage migration to the new lake account complete —\n"
            "72 partitions verified by the row-count gate, watermarks\n"
            "rebased, marker files retired."
        ),
        mnemos_tags=("checkpoint",),
    ),
    GoldenEntry(
        slug="atlas-secret-spill-log",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Task log with spilled credential (PLANTED)",
        content=(
            "atlas task log excerpt: the ingest template accidentally printed\n"
            "the source DSN postgres://fakeuser:fakepassword@db-internal into\n"
            "stdout — redact before the log ships to the shared bucket."
        ),
        mnemos_tags=("bug-pattern",),
        free_tags=("log", "security"),
        planted=("connection-string",),
    ),
    GoldenEntry(
        slug="atlas-dbt-tests",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="dbt test conventions",
        content=(
            "dbt conventions: every model ships uniqueness + not-null tests\n"
            "on the grain; source freshness checks page the oncall; ref()\n"
            "only — no hardcoded relation names in the transformation sql."
        ),
        mnemos_tags=("rule",),
    ),
    GoldenEntry(
        slug="atlas-orchestrator-code",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Repair lane orchestrator (py)",
        content=(
            "def run_repair_lane(run_date: date) -> None:\n"
            "    manifest = freeze_manifest(run_date)   # atlas repair lane\n"
            "    for partition in manifest.partitions():\n"
            "        replay(partition, from_watermark=True)\n"
            "        verify_row_counts(partition, tolerance=0.02)\n"
            "    unfreeze(manifest)"
        ),
        mnemos_tags=("learning",),
        free_tags=("code",),
    ),
    GoldenEntry(
        slug="atlas-processed-incidents",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Processed: incident synthesis",
        content=(
            "Processed synthesis: two OOM incidents trace to the same salted-\n"
            "key buffer growth; recommend a shared memory budget check in the\n"
            "task template."
        ),
        mnemos_tags=("synthesized",),
        source="synthesized",
        status="processed",
    ),
    GoldenEntry(
        slug="atlas-raw-vendor-dump",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Raw: vendor schema dump",
        content=(
            "RAW vendor schema dump received ahead of the connector redesign\n"
            "— unverified field semantics, contradictory notes from the\n"
            "account team. Pipeline review pending."
        ),
        mnemos_tags=("legacy",),
        status="raw",
    ),
    GoldenEntry(
        slug="atlas-ccr-oncall",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Compressed oncall handoff",
        content=(
            "Oncall handoff: full timeline of the watermark incident behind\n"
            "the marker; redeem if the vendor escalates overnight.\n"
            "[compressed: "
            "e5f7a9b1c3d5f7a9b1c3d5f7a9b1c3d5f7a9b1c3d5f7a9b1c3d5f7a9b1c3d5 | "
            "4800→480 chars | retrieve via mnemos_retrieve]"
        ),
        mnemos_tags=("session",),
        free_tags=("ccr",),
    ),
]

CORPUS: list[GoldenEntry] = AURORA + VAULT_UI + MNEMOS + ATLAS

PROJECTS: tuple[str, ...] = ("aurora-api", "vault-ui", "mnemos-core", "atlas-pipeline")

# Entries expected to be EXCLUDED from default search (status gate).
NON_ADMISSIBLE_SLUGS: frozenset[str] = frozenset(
    e.slug for e in CORPUS if e.status not in ("published", "processed")
)

# Entries carrying planted fake secrets (injection-acceptance population).
PLANTED_SLUGS: frozenset[str] = frozenset(e.slug for e in CORPUS if e.planted)

# Entries whose content embeds a CCR marker (parse-path coverage).
CCR_MARKER_SLUGS: frozenset[str] = frozenset(e.slug for e in CORPUS if "[compressed:" in e.content)


def entry_by_slug(slug: str) -> GoldenEntry:
    """Look up a corpus entry by its stable slug."""
    for entry in CORPUS:
        if entry.slug == slug:
            return entry
    raise KeyError(f"unknown golden slug: {slug!r}")
