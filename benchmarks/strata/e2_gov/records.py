"""E2 G-gov stratum — seeded governance records (E0 §3.1, §4.4).

Pre-registered experiment corpus for the meta-level lanes legs (E0:
``docs/experiments/e0-meta-level.md``). This module is the seeding act
E0 §4.4 locks BEFORE any run: **100 synthetic-but-realistic governance
records** (12 rules + 88 decisions) in the ``tech_patterns.py``
authoring style — declarative mnemos-universe governance prose across
the four golden projects, no lorem ipsum, no real secrets.

E0 §3.1 numeric lock (the analyzed denominator):

* analyzed stratum  = **48 records x 2 queries = 96** (the McNemar n);
* seeding budget    = 100 records (E0 permits 60-100);
* replacement pool  = the 52 surplus records — adjudication rejects are
  replaced from it (§4.4), replacements are logged, the denominator
  stays 96.

Internal composition mirrors the live-store governance ratio
(E0 §3.3: 30 rules : 217 decisions ≈ 1 : 7.2): 12 : 88 ≈ 1 : 7.3.
The analyzed 48 carry the same shape (6 rules : 42 decisions).

Seed hygiene (E0 §4.4, enforced by test): declarative prose only — no
second-person imperatives, no ``applyTo``, no severity tags; every
record passes the mnemos injection screen (``danger_detectors.detect``
finds nothing); provenance stays in fixture columns (project/agent),
never free tags. All records are ``published`` (admissible by the
ADR-0018 status gate) so the drowning experiment measures ranking, not
gating.

This module defines RECORDS ONLY — queries, ground truth and the
adjudication artifacts live in separate modules (``queries``,
``ground_truth``), per the blind-adjudication separation E0 §4.3
assumes. Nothing here imports into the S1 stand's measured corpus: the
S1 ``corpus_fingerprint`` hashes ``benchmarks/corpus`` modules only, so
this stratum is invisible to ``make bench-s1`` by construction (that
isolation is asserted by test — G-gov queries enter no measured set
before the E3 runner exists).
"""

from __future__ import annotations

from benchmarks.corpus.corpus import GoldenEntry

# ── aurora-api governance ─────────────────────────────────────────────────────

AURORA_GOV_RULES: list[GoldenEntry] = [
    GoldenEntry(
        slug="aurora-gov-deprecation-rule",
        project="aurora-api",
        agent="aurora-backend",
        title="Rule: public endpoint deprecation policy",
        content=(
            "Rule: a public aurora-api endpoint is removed only after two "
            "minor-release deprecation notices and a Sunset header with a "
            "concrete date. The header ships with the first notice. Breaking a "
            "contract without a sunset date is a release-blocking review "
            "violation."
        ),
        mnemos_tags=("rule",),
        free_tags=("api", "compat"),
        source="rule",
    ),
    GoldenEntry(
        slug="aurora-gov-migration-gating-rule",
        project="aurora-api",
        agent="aurora-backend",
        title="Rule: schema migration gating",
        content=(
            "Rule: aurora-api schema migrations run with lock_timeout 5s "
            "inside a maintenance window announced a day ahead. A migration "
            "that needs a lock beyond the timeout is redesigned, not retried. "
            "Peak-hour schema changes are rejected at review."
        ),
        mnemos_tags=("rule",),
        free_tags=("db", "releases"),
        source="rule",
    ),
    GoldenEntry(
        slug="aurora-gov-error-budget-rule",
        project="aurora-api",
        agent="aurora-oncall",
        title="Rule: error-budget freeze",
        content=(
            "Rule: aurora-api feature rollouts pause when the seven-day "
            "error-budget burn rate exceeds twice the allocated pace. The "
            "freeze lifts only after the burn returns inside budget for a "
            "full day. Budget state is reviewed at the weekly ops meeting."
        ),
        mnemos_tags=("rule",),
        free_tags=("reliability",),
        source="rule",
    ),
]

AURORA_GOV_DECISIONS: list[GoldenEntry] = [
    GoldenEntry(
        slug="aurora-gov-otel-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: OpenTelemetry for trace propagation",
        content=(
            "Decision: aurora-api adopts OpenTelemetry for trace propagation, "
            "replacing the hand-rolled span logger. Rationale: the gateway and "
            "the migration jobs then share one trace vocabulary, and vendor "
            "dashboards consume spans without a custom adapter."
        ),
        mnemos_tags=("decision",),
        free_tags=("observability",),
    ),
    GoldenEntry(
        slug="aurora-gov-blue-green-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: blue-green gateway releases",
        content=(
            "Decision: gateway releases ship blue-green with an automated "
            "health gate on /healthz before promotion. Canary releases were "
            "rejected because a 1% slice cannot exercise the connection-pool "
            "behavior that actually breaks."
        ),
        mnemos_tags=("decision",),
        free_tags=("releases",),
    ),
    GoldenEntry(
        slug="aurora-gov-postgres16-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: Postgres 16 upgrade",
        content=(
            "Decision: the platform upgrades to Postgres 16 at the next "
            "maintenance window, mainly for parallel vacuum on the audit "
            "tables. The rollout rehearses on the staging clone first; "
            "rollback is a point-in-time restore rehearsed the same week."
        ),
        mnemos_tags=("decision",),
        free_tags=("db",),
    ),
    GoldenEntry(
        slug="aurora-gov-v1-sunset-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: retire JSON API v1",
        content=(
            "Decision: the JSON API v1 surface is retired after two "
            "deprecation notices; v2 has been the only documented contract "
            "since release 2.6. Clients still on v1 are enumerated from "
            "access logs and contacted before the sunset date."
        ),
        mnemos_tags=("decision",),
        free_tags=("api",),
    ),
    GoldenEntry(
        slug="aurora-gov-idempotency-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: idempotency keys on payment endpoints",
        content=(
            "Decision: every state-changing checkout and payment endpoint "
            "requires an Idempotency-Key header; requests replaying a seen "
            "key return the original response. The key store is the "
            "transactional outbox, so retries survive a restart."
        ),
        mnemos_tags=("decision",),
        free_tags=("payments",),
    ),
    GoldenEntry(
        slug="aurora-gov-webhook-signing-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: signed outbound webhooks",
        content=(
            "Decision: outbound webhooks sign payloads with HMAC SHA-256 and "
            "a per-endpoint secret; unsigned callbacks are rejected by "
            "contract tests. Timestamp tolerance is five minutes against "
            "replay."
        ),
        mnemos_tags=("decision",),
        free_tags=("integrations", "security"),
    ),
    GoldenEntry(
        slug="aurora-gov-etag-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: optimistic concurrency via ETag",
        content=(
            "Decision: aurora-api resource updates use optimistic "
            "concurrency: writes carry If-Match with the ETag of the read "
            "version, and a stale tag yields 412. Last-writer-wins updates "
            "are rejected at review."
        ),
        mnemos_tags=("decision",),
        free_tags=("api",),
    ),
    GoldenEntry(
        slug="aurora-gov-upstream-timeout-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: upstream call timeout budget",
        content=(
            "Decision: upstream calls from the gateway use a thirty-second "
            "hard timeout with at most two retries and jittered backoff. "
            "Unlimited retries were rejected because they convert partner "
            "slowness into our outage."
        ),
        mnemos_tags=("decision",),
        free_tags=("reliability",),
    ),
    GoldenEntry(
        slug="aurora-gov-tenant-rate-limit-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: rate limits scoped per tenant",
        content=(
            "Decision: token-bucket rate limits are scoped per tenant rather "
            "than per API key, so a tenant scripting many keys cannot "
            "multiply its quota. The bucket refill rate stays a server-side "
            "configuration value."
        ),
        mnemos_tags=("decision",),
        free_tags=("limits",),
    ),
    GoldenEntry(
        slug="aurora-gov-flags-manifest-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: feature flags in the manifest layer",
        content=(
            "Decision: feature flags resolve from the configuration manifest "
            "layer, with environment overrides only for local development. "
            "Flags living solely in env vars were rejected because production "
            "state became undebuggable."
        ),
        mnemos_tags=("decision",),
        free_tags=("config",),
    ),
    GoldenEntry(
        slug="aurora-gov-read-replica-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: analytics reads from replicas",
        content=(
            "Decision: the analytics read path moves to Postgres read "
            "replicas with a replication-lag guard that falls back to primary "
            "reads beyond five seconds of lag. The request path never reads "
            "replicas."
        ),
        mnemos_tags=("decision",),
        free_tags=("db",),
    ),
    GoldenEntry(
        slug="aurora-gov-pgbouncer-decision",
        project="aurora-api",
        agent="aurora-oncall",
        title="Decision: PgBouncer for connection pooling",
        content=(
            "Decision: connection pooling goes through PgBouncer in "
            "transaction mode; services drop their own pools. Direct "
            "connections were rejected after the pool-exhaustion incident "
            "showed three independent pools racing one database."
        ),
        mnemos_tags=("decision",),
        free_tags=("db", "incident"),
    ),
    GoldenEntry(
        slug="aurora-gov-json-logs-decision",
        project="aurora-api",
        agent="aurora-oncall",
        title="Decision: structured JSON gateway logs",
        content=(
            "Decision: gateway logs are structured JSON with trace and "
            "tenant correlation fields on every line. Free-form log prose is "
            "rejected at review; humans read the rendered dashboard, "
            "machines read JSON."
        ),
        mnemos_tags=("decision",),
        free_tags=("observability",),
    ),
    GoldenEntry(
        slug="aurora-gov-rest-not-grpc-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: public contract stays REST",
        content=(
            "Decision: the public aurora-api contract stays REST with JSON; "
            "gRPC is adopted only for internal service-to-service calls. A "
            "dual public surface was rejected as double documentation for "
            "zero customer value."
        ),
        mnemos_tags=("decision",),
        free_tags=("api",),
    ),
    GoldenEntry(
        slug="aurora-gov-cursor-pagination-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: cursor pagination",
        content=(
            "Decision: list endpoints paginate by opaque cursor; offset "
            "pagination is banned beyond ten thousand rows. Cursor tokens "
            "encode the sort key and expire after a day."
        ),
        mnemos_tags=("decision",),
        free_tags=("api",),
    ),
    GoldenEntry(
        slug="aurora-gov-circuit-breaker-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: platform circuit-breaker library",
        content=(
            "Decision: circuit breaking on upstream dependencies uses the "
            "platform library with the default half-open probe; hand-rolled "
            "breaker flags are retired. The library's breaker state is "
            "exported to the dashboard."
        ),
        mnemos_tags=("decision",),
        free_tags=("reliability",),
    ),
    GoldenEntry(
        slug="aurora-gov-checkout-slo-decision",
        project="aurora-api",
        agent="aurora-oncall",
        title="Decision: checkout availability SLO",
        content=(
            "Decision: checkout carries a 99.9% availability SLO measured at "
            "the gateway edge. Alerting pages on budget burn rate, not on "
            "raw error counts."
        ),
        mnemos_tags=("decision",),
        free_tags=("reliability",),
    ),
    GoldenEntry(
        slug="aurora-gov-backpressure-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: reject-on-overload, never queue",
        content=(
            "Decision: overload responses are 429 with a Retry-After header; "
            "the gateway never queues requests to shed load later. Queueing "
            "was rejected because hidden queues convert overload into "
            "latency."
        ),
        mnemos_tags=("decision",),
        free_tags=("reliability",),
    ),
    GoldenEntry(
        slug="aurora-gov-token-review-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: quarterly service-token review",
        content=(
            "Decision: service tokens undergo a quarterly access review; "
            "unused tokens are rotated out of the manifest automatically. "
            "The review inventory comes from the auth ledger, not from team "
            "memory."
        ),
        mnemos_tags=("decision",),
        free_tags=("security",),
    ),
    GoldenEntry(
        slug="aurora-gov-refresh-ttl-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: token lifetimes",
        content=(
            "Decision: access tokens carry a fifteen-minute TTL matching the "
            "documented AURORA_TOKEN_TTL default; refresh tokens are "
            "single-use and rotate on redemption."
        ),
        mnemos_tags=("decision",),
        free_tags=("auth",),
    ),
    GoldenEntry(
        slug="aurora-gov-event-registry-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: registered event schemas",
        content=(
            "Decision: event contracts between aurora-api services are "
            "registered schemas with a pinned writer version; unregistered "
            "event shapes fail CI. The registry check runs in the contract "
            "tests, not at runtime."
        ),
        mnemos_tags=("decision",),
        free_tags=("events",),
    ),
    GoldenEntry(
        slug="aurora-gov-monolith-decision",
        project="aurora-api",
        agent="aurora-backend",
        title="Decision: stay a modular monolith",
        content=(
            "Decision: aurora-api stays a modular monolith; the checkout and "
            "tenant modules communicate through in-process interfaces, not "
            "network calls. A microservice split was rejected until deploy "
            "telemetry shows a real scaling wall."
        ),
        mnemos_tags=("decision",),
        free_tags=("architecture",),
    ),
]

# ── vault-ui governance ───────────────────────────────────────────────────────

VAULT_GOV_RULES: list[GoldenEntry] = [
    GoldenEntry(
        slug="vaultui-gov-a11y-gate-rule",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Rule: accessibility is a merge gate",
        content=(
            "Rule: vault-ui merges block when the axe scan reports a new "
            "WCAG AA violation on changed routes. The scan runs in CI on "
            "every pull request. Fixes land in the same change that "
            "introduced the regression."
        ),
        mnemos_tags=("rule",),
        free_tags=("a11y",),
        source="rule",
    ),
    GoldenEntry(
        slug="vaultui-gov-bundle-budget-rule",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Rule: per-route bundle budget",
        content=(
            "Rule: each vault-ui route stays under a 180 KB gzipped "
            "JavaScript budget; a regression blocks the merge until trimmed. "
            "Budget numbers are revisited at the quarterly performance "
            "review, not per feature."
        ),
        mnemos_tags=("rule",),
        free_tags=("performance",),
        source="rule",
    ),
    GoldenEntry(
        slug="vaultui-gov-changelog-rule",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Rule: changelog entry with every visible change",
        content=(
            "Rule: every user-visible vault-ui change ships a changelog "
            "entry in the same pull request. A change without an entry is "
            "returned at review; entries are written for users, not for "
            "developers."
        ),
        mnemos_tags=("rule",),
        free_tags=("process",),
        source="rule",
    ),
]

VAULT_GOV_DECISIONS: list[GoldenEntry] = [
    GoldenEntry(
        slug="vaultui-gov-grid-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: dashboard on CSS grid",
        content=(
            "Decision: the vault-ui dashboard layout is CSS grid; flex stays "
            "for one-dimensional strips inside grid areas. Per-page bespoke "
            "layout systems were rejected after the sidebar wrapping bug on "
            "narrow viewports."
        ),
        mnemos_tags=("decision",),
        free_tags=("layout",),
    ),
    GoldenEntry(
        slug="vaultui-gov-dark-mode-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: dark mode via token remap",
        content=(
            "Decision: dark mode remaps surface tokens only; accents, spacing "
            "and radii stay invariant across themes. Per-component dark "
            "palettes were rejected because charts drifted off the shared "
            "ramp."
        ),
        mnemos_tags=("decision",),
        free_tags=("design",),
    ),
    GoldenEntry(
        slug="vaultui-gov-ssr-shell-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: server-rendered shells for public routes",
        content=(
            "Decision: public vault-ui routes render server-side shells; the "
            "authenticated app stays a client application. Full universal "
            "rendering was rejected as a hydration-mismatch farm."
        ),
        mnemos_tags=("decision",),
        free_tags=("rendering",),
    ),
    GoldenEntry(
        slug="vaultui-gov-lucide-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: lucide icons after the rewrite",
        content=(
            "Decision: vault-ui migrates to the lucide icon set after the "
            "dashboard rewrite, one migration pass instead of two. The "
            "bundled icon barrel was the bundle-budget offender; lucide "
            "ships per-icon imports."
        ),
        mnemos_tags=("decision",),
        free_tags=("icons",),
    ),
    GoldenEntry(
        slug="vaultui-gov-windowing-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: windowed rendering for long lists",
        content=(
            "Decision: vault-ui lists beyond a thousand rows use windowed "
            "rendering with a five-row overscan. Rendering full lists was "
            "rejected after the memory list jank at ten thousand entries."
        ),
        mnemos_tags=("decision",),
        free_tags=("performance",),
    ),
    GoldenEntry(
        slug="vaultui-gov-headless-tables-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: headless table primitives",
        content=(
            "Decision: vault-ui data grids are built on headless table "
            "primitives; the component-library grid is retired. Rationale: "
            "sorting, virtualization and keyboard models compose instead of "
            "fighting a black box."
        ),
        mnemos_tags=("decision",),
        free_tags=("components",),
    ),
    GoldenEntry(
        slug="vaultui-gov-token-pipeline-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: token build pipeline",
        content=(
            "Decision: design tokens flow through a build pipeline that "
            "emits CSS variables and a TypeScript module from one source "
            "file. Hand-copied token values were rejected after two themes "
            "drifted by eye."
        ),
        mnemos_tags=("decision",),
        free_tags=("design",),
    ),
    GoldenEntry(
        slug="vaultui-gov-playwright-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: Playwright for end-to-end tests",
        content=(
            "Decision: vault-ui end-to-end tests run on Playwright; the "
            "Cypress suite is retired at the next major. Rationale: one "
            "runner covers Chromium and WebKit and runs headless in CI "
            "without a display server."
        ),
        mnemos_tags=("decision",),
        free_tags=("testing",),
    ),
    GoldenEntry(
        slug="vaultui-gov-visual-regression-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: visual regression on previews",
        content=(
            "Decision: visual regression screenshots run against the preview "
            "deployment on every pull request, with a pixel diff threshold "
            "of zero for interactive components. Diffs above threshold block "
            "the merge."
        ),
        mnemos_tags=("decision",),
        free_tags=("testing",),
    ),
    GoldenEntry(
        slug="vaultui-gov-error-boundary-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: error boundary per route",
        content=(
            "Decision: every vault-ui route mounts an error boundary with a "
            "retry affordance; a render crash degrades one route, never the "
            "application shell. Blank-screen failures were the top support "
            "ticket source."
        ),
        mnemos_tags=("decision",),
        free_tags=("reliability",),
    ),
    GoldenEntry(
        slug="vaultui-gov-optimistic-ui-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: optimistic updates with rollback",
        content=(
            "Decision: vault-ui mutations apply optimistic updates with "
            "rollback on rejection; pending state renders skeletons, not "
            "locked forms. Silently dropping optimistic state on navigation "
            "was fixed in the same change."
        ),
        mnemos_tags=("decision",),
        free_tags=("ux",),
    ),
    GoldenEntry(
        slug="vaultui-gov-no-microfrontends-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: one application, no microfrontends",
        content=(
            "Decision: vault-ui stays a single application bundle per route; "
            "a microfrontend split was rejected because the team is one unit "
            "and the shell cost buys nothing at current size."
        ),
        mnemos_tags=("decision",),
        free_tags=("architecture",),
    ),
    GoldenEntry(
        slug="vaultui-gov-prefetch-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: viewport prefetch for route chunks",
        content=(
            "Decision: route chunks prefetch when the trigger link enters "
            "the viewport, capped at two concurrent prefetches. "
            "Prefetch-on-hover alone left cold navigations on mid-tier "
            "hardware."
        ),
        mnemos_tags=("decision",),
        free_tags=("performance",),
    ),
    GoldenEntry(
        slug="vaultui-gov-i18n-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: message catalogs for i18n",
        content=(
            "Decision: vault-ui ships English and Russian through message "
            "catalogs with compile-time key checks; inline user-facing "
            "strings fail lint. Machine translation is a draft step, never a "
            "release step."
        ),
        mnemos_tags=("decision",),
        free_tags=("i18n",),
    ),
    GoldenEntry(
        slug="vaultui-gov-focus-ring-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: native focus-visible styling",
        content=(
            "Decision: focus indicators use the browser's native "
            "focus-visible styling recolored through tokens; custom ring "
            "libraries were rejected as an a11y-gate liability."
        ),
        mnemos_tags=("decision",),
        free_tags=("a11y",),
    ),
    GoldenEntry(
        slug="vaultui-gov-skeleton-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: skeletons over spinners",
        content=(
            "Decision: loading states render layout-stable skeletons; "
            "spinner-only loading was rejected because layout shift on data "
            "arrival broke scan reading."
        ),
        mnemos_tags=("decision",),
        free_tags=("ux",),
    ),
    GoldenEntry(
        slug="vaultui-gov-swr-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: stale-while-revalidate data cache",
        content=(
            "Decision: vault-ui client data fetching uses a "
            "stale-while-revalidate cache keyed by request identity; ad-hoc "
            "fetch effects are retired. Cache revalidation is per-key, never "
            "global."
        ),
        mnemos_tags=("decision",),
        free_tags=("data",),
    ),
    GoldenEntry(
        slug="vaultui-gov-strict-ts-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: strict TypeScript",
        content=(
            "Decision: vault-ui compiles under strict TypeScript with no "
            "implicit any and no non-null assertions in components; the "
            "flags are errors, not warnings. Escape hatches require a paired "
            "refactor ticket."
        ),
        mnemos_tags=("decision",),
        free_tags=("tooling",),
    ),
    GoldenEntry(
        slug="vaultui-gov-lint-zero-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: zero-new-warnings lint policy",
        content=(
            "Decision: vault-ui holds a zero-new-warnings lint policy: the "
            "count may only decrease. Warning budgets were rejected because "
            "they fill by inertia."
        ),
        mnemos_tags=("decision",),
        free_tags=("tooling",),
    ),
    GoldenEntry(
        slug="vaultui-gov-workbench-parity-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: component docs in the workbench",
        content=(
            "Decision: component documentation lives in the workbench next "
            "to the source; a component without a workbench page fails the "
            "docs build."
        ),
        mnemos_tags=("decision",),
        free_tags=("docs",),
    ),
    GoldenEntry(
        slug="vaultui-gov-breakpoints-decision",
        project="vault-ui",
        agent="vaultui-design",
        title="Decision: standardized breakpoints",
        content=(
            "Decision: vault-ui layout breakpoints are standardized at 640, "
            "960 and 1280 pixels; ad-hoc media queries fail lint. The "
            "three-step scale covers the observed device distribution."
        ),
        mnemos_tags=("decision",),
        free_tags=("layout",),
    ),
    GoldenEntry(
        slug="vaultui-gov-hydration-dates-decision",
        project="vault-ui",
        agent="vaultui-frontend",
        title="Decision: format dates after mount",
        content=(
            "Decision: vault-ui formats dates and times after mount, "
            "rendering a stable skeleton during hydration; locale formatting "
            "never runs on the server render path. The decision follows the "
            "hydration mismatch incident on the dashboard chart."
        ),
        mnemos_tags=("decision",),
        free_tags=("rendering",),
    ),
]

# ── mnemos-core governance (the memory system, dogfooded) ─────────────────────

MNEMOS_GOV_RULES: list[GoldenEntry] = [
    GoldenEntry(
        slug="mnemos-gov-fingerprint-rule",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Rule: corpus edits re-baseline in the same PR",
        content=(
            "Rule: a change to any mnemos corpus-defining module re-records "
            "the affected benchmark baseline in the same pull request. The "
            "corpus fingerprint is the enforcement signal; a stale "
            "fingerprint fails the gate."
        ),
        mnemos_tags=("rule",),
        free_tags=("benchmarks",),
        source="rule",
    ),
    GoldenEntry(
        slug="mnemos-gov-quarantine-rule",
        project="mnemos-core",
        agent="mnemos-security",
        title="Rule: quarantined rows are never issued",
        content=(
            "Rule: quarantined mnemos rows are never issued into assembled "
            "context; they surface only as format-constrained retraction "
            "renders with content and title withheld. Retrieval by "
            "identifier remains available for audit."
        ),
        mnemos_tags=("rule",),
        free_tags=("security",),
        source="rule",
    ),
    GoldenEntry(
        slug="mnemos-gov-determinism-rule",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Rule: benchmark stands are deterministic",
        content=(
            "Rule: mnemos benchmark stands admit no wall-clock values and no "
            "unseeded randomness; timestamps appear only in run metadata. A "
            "metric that needs a clock belongs on the timing stand with a "
            "noise band."
        ),
        mnemos_tags=("rule",),
        free_tags=("benchmarks",),
        source="rule",
    ),
]

MNEMOS_GOV_DECISIONS: list[GoldenEntry] = [
    GoldenEntry(
        slug="mnemos-gov-rrf-params-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: fusion constants stay pinned",
        content=(
            "Decision: hybrid fusion keeps Reciprocal Rank Fusion with "
            "rrf_k=60 and a 0.7 alpha weighting toward the vector leg. The "
            "constants stay pinned until a corpus change forces a "
            "re-baseline; tuning rides the registered experiments."
        ),
        mnemos_tags=("decision",),
        free_tags=("retrieval",),
    ),
    GoldenEntry(
        slug="mnemos-gov-sqlite-store-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: SQLite stays the single store",
        content=(
            "Decision: mnemos storage stays SQLite for rows, vectors and "
            "full-text search in one file set; an external vector database "
            "was rejected as an operational dependency with no measured "
            "retrieval win at this scale."
        ),
        mnemos_tags=("decision",),
        free_tags=("storage",),
    ),
    GoldenEntry(
        slug="mnemos-gov-blake2-reference-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: BLAKE2b as the benchmark reference embedder",
        content=(
            "Decision: the deterministic benchmark reference embedder is the "
            "BLAKE2b lexical hasher; model embedders are measured as their "
            "own contour. Rationale: the reference pins the pipeline, not "
            "the model download."
        ),
        mnemos_tags=("decision",),
        free_tags=("benchmarks",),
    ),
    GoldenEntry(
        slug="mnemos-gov-fts5-stemming-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: FTS5 porter tokenizer",
        content=(
            "Decision: the lexical leg keeps FTS5 with the porter tokenizer "
            "and unicode folding; custom tokenizers were rejected after the "
            "RU recall check showed no win for the maintenance cost."
        ),
        mnemos_tags=("decision",),
        free_tags=("retrieval",),
    ),
    GoldenEntry(
        slug="mnemos-gov-supersedes-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: supersedes edges over mutation",
        content=(
            "Decision: replaced context blocks keep identity through "
            "supersedes edges instead of in-place mutation; traversal of the "
            "edge chain powers later phases. Deletion of superseded "
            "originals was rejected for audit reasons."
        ),
        mnemos_tags=("decision",),
        free_tags=("lifecycle",),
    ),
    GoldenEntry(
        slug="mnemos-gov-ccr-snippets-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: marker redemption serves snippets or wholes",
        content=(
            "Decision: compressed-cache-retrieve redemption serves FTS5 "
            "snippet selections for detail needs and full originals for "
            "whole needs; markers never inline the cached content."
        ),
        mnemos_tags=("decision",),
        free_tags=("ccr",),
    ),
    GoldenEntry(
        slug="mnemos-gov-scan-at-issuance-decision",
        project="mnemos-core",
        agent="mnemos-security",
        title="Decision: scan at issuance, zero-loss storage",
        content=(
            "Decision: mnemos scans content at issuance and never mutates "
            "stored originals; matched secret spans render redacted or the "
            "echo is refused. The store keeps zero-loss originals by design."
        ),
        mnemos_tags=("decision",),
        free_tags=("security",),
    ),
    GoldenEntry(
        slug="mnemos-gov-status-gate-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: admissibility is an entry invariant",
        content=(
            "Decision: search and assembly admit only published and "
            "processed rows; raw and dead-letter content stays invisible "
            "until the pipeline gates it. The status gate is an entry "
            "invariant, not a filter preference."
        ),
        mnemos_tags=("decision",),
        free_tags=("pipeline",),
    ),
    GoldenEntry(
        slug="mnemos-gov-project-predicate-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: vector leg filters by project before fusion",
        content=(
            "Decision: the vector leg applies the project predicate before "
            "fusion, backed by the native store filter and a resolve-time "
            "guard on the authoritative column. The over-fetch keeps the "
            "contribution depth fillable."
        ),
        mnemos_tags=("decision",),
        free_tags=("retrieval",),
    ),
    GoldenEntry(
        slug="mnemos-gov-overfetch-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: over-fetch factor pinned at four",
        content=(
            "Decision: the vector-leg over-fetch factor is pinned at four "
            "until a measured corpus change justifies re-registration; the "
            "constant is documentation of intent, not configuration."
        ),
        mnemos_tags=("decision",),
        free_tags=("retrieval",),
    ),
    GoldenEntry(
        slug="mnemos-gov-lanes-v0-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: lanes v0 ships without a manifest lane",
        content=(
            "Decision: lanes v0 ships without a manifest lane; governance "
            "pinning goes through the operator CLI only, and metrics live on "
            "rules, decisions and knowledge over existing records."
        ),
        mnemos_tags=("decision",),
        free_tags=("lanes",),
    ),
    GoldenEntry(
        slug="mnemos-gov-b0-control-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: the lanes experiment carries a trivial control",
        content=(
            "Decision: the lanes experiment carries a trivial control leg "
            "that type-boosts governance at recall, because any pinning "
            "mechanics lifts drowned records and only the comparison "
            "separates structure from boost."
        ),
        mnemos_tags=("decision",),
        free_tags=("experiments",),
    ),
    GoldenEntry(
        slug="mnemos-gov-mcnemar-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: exact two-sided McNemar for paired legs",
        content=(
            "Decision: paired benchmark legs compare with the exact "
            "two-sided McNemar test on discordant pairs at alpha 0.05, per "
            "comparison, with no familywise correction registered after the "
            "fact."
        ),
        mnemos_tags=("decision",),
        free_tags=("experiments",),
    ),
    GoldenEntry(
        slug="mnemos-gov-corridor-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: corridors derive from baselines",
        content=(
            "Decision: benchmark corridors derive as baseline minus the "
            "larger of a two-point floor and the recorded 95% confidence "
            "interval; hand-picked thresholds are prohibited."
        ),
        mnemos_tags=("decision",),
        free_tags=("benchmarks",),
    ),
    GoldenEntry(
        slug="mnemos-gov-e2-profile-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: E2 corpus reproduces the drowning profile",
        content=(
            "Decision: the experimental corpus extension reproduces the "
            "live-store drowning profile with a checkpoint share of 58 "
            "percent and rules-to-decisions seeded per the live ratio, so "
            "the drowning condition is reproduced, not sanitized."
        ),
        mnemos_tags=("decision",),
        free_tags=("experiments", "corpus"),
    ),
    GoldenEntry(
        slug="mnemos-gov-blind-adjudication-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: blind ground-truth adjudication",
        content=(
            "Decision: ground-truth adjudication for governance recall is "
            "blind: judges see query and candidate pairs only, leg-stripped, "
            "with a Cohen kappa calibration floor of 0.6 before scoring."
        ),
        mnemos_tags=("decision",),
        free_tags=("experiments",),
    ),
    GoldenEntry(
        slug="mnemos-gov-equal-budget-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: equal budget for cross-leg comparisons",
        content=(
            "Decision: cross-leg comparisons run at identical "
            "assembled-context token budgets; budget-inflated runs are "
            "report-only and never a basis for conclusions."
        ),
        mnemos_tags=("decision",),
        free_tags=("experiments",),
    ),
    GoldenEntry(
        slug="mnemos-gov-pin-cost-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: pin-cost curve at 5, 15, 30 percent",
        content=(
            "Decision: the pinned-governance prefix share sweeps a 5, 15 and "
            "30 percent cost curve with the guardrail floor binding at every "
            "point; the curve is descriptive and feeds the configuration "
            "choice."
        ),
        mnemos_tags=("decision",),
        free_tags=("lanes",),
    ),
    GoldenEntry(
        slug="mnemos-gov-two-key-decision",
        project="mnemos-core",
        agent="mnemos-security",
        title="Decision: mint-to-pin needs two keys",
        content=(
            "Decision: mint-to-pin promotion requires operator approval "
            "under a two-key rule; no automated path mints or pins "
            "governance records."
        ),
        mnemos_tags=("decision",),
        free_tags=("security", "lanes"),
    ),
    GoldenEntry(
        slug="mnemos-gov-origin-provenance-decision",
        project="mnemos-core",
        agent="mnemos-security",
        title="Decision: provenance from server columns only",
        content=(
            "Decision: record provenance derives from server-observed "
            "columns only; client-supplied tags and metadata never establish "
            "origin."
        ),
        mnemos_tags=("decision",),
        free_tags=("security",),
    ),
    GoldenEntry(
        slug="mnemos-gov-retention-invariant-decision",
        project="mnemos-core",
        agent="mnemos-maintainer",
        title="Decision: collapse keeps retention-or-report at 1.000",
        content=(
            "Decision: collapse keeps a retention-or-report invariant at "
            "1.000: every seeded critical fact at a collapse boundary is "
            "retained or its loss is visible in the collapse report; any "
            "silent loss fails the level."
        ),
        mnemos_tags=("decision",),
        free_tags=("collapse",),
    ),
    GoldenEntry(
        slug="mnemos-gov-synthetic-corpus-decision",
        project="mnemos-core",
        agent="mnemos-researcher",
        title="Decision: synthetic seeded experiment corpora",
        content=(
            "Decision: experiment corpora are seeded synthetic fixtures in "
            "the repository, not scraped production stores; realistic shape, "
            "zero tenant data."
        ),
        mnemos_tags=("decision",),
        free_tags=("experiments", "corpus"),
    ),
]

# ── atlas-pipeline governance ─────────────────────────────────────────────────

ATLAS_GOV_RULES: list[GoldenEntry] = [
    GoldenEntry(
        slug="atlas-gov-late-arrival-rule",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Rule: late events replay, never drop",
        content=(
            "Rule: late-arriving atlas events route to the repair lane and "
            "replay from the watermark; dropping late events is prohibited. "
            "Paging thresholds live with the oncall rota, not with the "
            "pipeline."
        ),
        mnemos_tags=("rule",),
        free_tags=("ops",),
        source="rule",
    ),
    GoldenEntry(
        slug="atlas-gov-retention-rule",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Rule: partition retention ledger",
        content=(
            "Rule: atlas hot partitions stay queryable for ninety days, then "
            "move to cold storage; deletion happens only through the "
            "retention ledger. Manual partition deletion is prohibited."
        ),
        mnemos_tags=("rule",),
        free_tags=("storage",),
        source="rule",
    ),
    GoldenEntry(
        slug="atlas-gov-pii-masking-rule",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Rule: masked identifiers in analytics marts",
        content=(
            "Rule: tenant identifiers are masked in every atlas analytics "
            "mart; raw identifiers exist only in the curated access tier. A "
            "mart exposing raw identifiers fails the data quality gate."
        ),
        mnemos_tags=("rule",),
        free_tags=("privacy",),
        source="rule",
    ),
]

ATLAS_GOV_DECISIONS: list[GoldenEntry] = [
    GoldenEntry(
        slug="atlas-gov-delta-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: Delta as the table format",
        content=(
            "Decision: the lakehouse table format is Delta, pinned per "
            "runner image; format bumps rehearse a full backfill on the copy "
            "cluster before any production lane."
        ),
        mnemos_tags=("decision",),
        free_tags=("storage",),
    ),
    GoldenEntry(
        slug="atlas-gov-defer-streaming-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: stay on micro-batches",
        content=(
            "Decision: the events tail stays on fifteen-minute micro-batches; "
            "streaming ingest was deferred because it breaks the idempotent "
            "replay model the backfill runbook depends on."
        ),
        mnemos_tags=("decision",),
        free_tags=("ingest",),
    ),
    GoldenEntry(
        slug="atlas-gov-salted-keys-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: salted keys for whale-tenant shuffles",
        content=(
            "Decision: whale-tenant shuffles use salted keys with a second "
            "re-aggregation pass; the salt bucket count is eight pending a "
            "re-measure. The skew warning threshold stays at three times the "
            "median partition."
        ),
        mnemos_tags=("decision",),
        free_tags=("spark",),
    ),
    GoldenEntry(
        slug="atlas-gov-dbt-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: dbt for transformations",
        content=(
            "Decision: atlas transformations are dbt models with uniqueness "
            "and not-null tests on every grain; raw SQL notebooks were "
            "retired from the production path."
        ),
        mnemos_tags=("decision",),
        free_tags=("transform",),
    ),
    GoldenEntry(
        slug="atlas-gov-window-decision",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Decision: daily orchestration window",
        content=(
            "Decision: daily orchestration windows open at 02:00 UTC with a "
            "hard close at 06:00 UTC; late windows page the oncall, they "
            "never roll into the next day silently."
        ),
        mnemos_tags=("decision",),
        free_tags=("ops",),
    ),
    GoldenEntry(
        slug="atlas-gov-hive-layout-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: hive partition layout",
        content=(
            "Decision: partitions follow a hive layout keyed by run_date and "
            "region so the engine prunes without manifest lookups; nested "
            "partition keys beyond two levels were rejected."
        ),
        mnemos_tags=("decision",),
        free_tags=("storage",),
    ),
    GoldenEntry(
        slug="atlas-gov-compaction-trigger-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: compaction triggers",
        content=(
            "Decision: small-file compaction triggers on file count above "
            "five thousand per partition or median file size under 32 MB; "
            "compaction runs weekly on the cold window."
        ),
        mnemos_tags=("decision",),
        free_tags=("storage",),
    ),
    GoldenEntry(
        slug="atlas-gov-exactly-once-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: exactly-once via idempotent event ids",
        content=(
            "Decision: atlas pipelines guarantee exactly-once effects "
            "through idempotent event identifiers per partition; downstream "
            "consumers deduplicate by identifier, not by arrival count."
        ),
        mnemos_tags=("decision",),
        free_tags=("correctness",),
    ),
    GoldenEntry(
        slug="atlas-gov-writer-schema-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: writer-schema pinning",
        content=(
            "Decision: atlas writers pin their writer-schema version in "
            "every manifest; readers tolerate additive columns and nulls "
            "within a minor version. Breaking changes ship as a new dataset "
            "generation."
        ),
        mnemos_tags=("decision",),
        free_tags=("schema",),
    ),
    GoldenEntry(
        slug="atlas-gov-copy-cluster-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: rehearsals on the copy cluster",
        content=(
            "Decision: version bumps and destructive backfills rehearse on "
            "an isolated copy cluster sized at a tenth of production; the "
            "rehearsal is a release gate, not a courtesy."
        ),
        mnemos_tags=("decision",),
        free_tags=("releases",),
    ),
    GoldenEntry(
        slug="atlas-gov-dlq-sla-decision",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Decision: DLQ replay service level",
        content=(
            "Decision: dead-lettered partitions carry a seven-day replay "
            "service level; anything older escalates to the data quality "
            "review. The DLQ lane is a parking place, not an archive."
        ),
        mnemos_tags=("decision",),
        free_tags=("ops",),
    ),
    GoldenEntry(
        slug="atlas-gov-rowcount-gate-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: row-count reconciliation gate",
        content=(
            "Decision: every published partition passes a row-count "
            "reconciliation against the source ledger within a two percent "
            "tolerance; a breach routes the partition to the dead-letter "
            "lane with the failing rule."
        ),
        mnemos_tags=("decision",),
        free_tags=("correctness",),
    ),
    GoldenEntry(
        slug="atlas-gov-shared-pool-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: shared runner pool",
        content=(
            "Decision: atlas runners stay a shared cluster pool; per-tenant "
            "clusters were rejected because idle capacity, not isolation, is "
            "the current cost driver."
        ),
        mnemos_tags=("decision",),
        free_tags=("ops",),
    ),
    GoldenEntry(
        slug="atlas-gov-spot-runners-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: spot capacity for batch only",
        content=(
            "Decision: batch runners prefer spot capacity with checkpointed "
            "tasks that survive preemption; the repair lane makes "
            "re-execution cheap. The serving path never uses spot."
        ),
        mnemos_tags=("decision",),
        free_tags=("ops",),
    ),
    GoldenEntry(
        slug="atlas-gov-data-contracts-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: producer-owned data contracts",
        content=(
            "Decision: producers own data contracts with executable tests "
            "that run in the producer pipeline; consumer-discovered schema "
            "drift is a contract breach, not a ticket."
        ),
        mnemos_tags=("decision",),
        free_tags=("schema",),
    ),
    GoldenEntry(
        slug="atlas-gov-incremental-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: incremental models by default",
        content=(
            "Decision: atlas models default to incremental materialization; "
            "a full refresh requires a written reason in the model header "
            "and an oncall sign-off."
        ),
        mnemos_tags=("decision",),
        free_tags=("transform",),
    ),
    GoldenEntry(
        slug="atlas-gov-cost-dashboards-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: per-pipeline cost reporting",
        content=(
            "Decision: every atlas pipeline reports compute cost per run on "
            "the shared dashboard; a cost regression above twenty percent "
            "opens a ticket automatically. Cost visibility is a pipeline "
            "deliverable."
        ),
        mnemos_tags=("decision",),
        free_tags=("cost",),
    ),
    GoldenEntry(
        slug="atlas-gov-connector-isolation-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: sandboxed vendor connectors",
        content=(
            "Decision: vendor connectors run in sandboxed runner images with "
            "egress limited to the vendor endpoint; connector code never "
            "runs on the shared driver."
        ),
        mnemos_tags=("decision",),
        free_tags=("security",),
    ),
    GoldenEntry(
        slug="atlas-gov-openlineage-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: OpenLineage events for every task",
        content=(
            "Decision: atlas emits OpenLineage events for every task "
            "completion; column-level lineage is a launch requirement for "
            "new pipelines, not a follow-up."
        ),
        mnemos_tags=("decision",),
        free_tags=("observability",),
    ),
    GoldenEntry(
        slug="atlas-gov-lag-slo-decision",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Decision: watermark lag service objective",
        content=(
            "Decision: watermark lag carries a twenty-four-hour service "
            "objective measured at the daily close; lag beyond thirty hours "
            "pages regardless of vendor status."
        ),
        mnemos_tags=("decision",),
        free_tags=("ops",),
    ),
    GoldenEntry(
        slug="atlas-gov-erasure-decision",
        project="atlas-pipeline",
        agent="atlas-etl",
        title="Decision: erasure via crypto-shredding",
        content=(
            "Decision: erasure requests execute through crypto-shredding of "
            "per-tenant data keys; deletion by row scan was rejected as "
            "unprovable at lakehouse scale."
        ),
        mnemos_tags=("decision",),
        free_tags=("privacy",),
    ),
    GoldenEntry(
        slug="atlas-gov-dq-review-decision",
        project="atlas-pipeline",
        agent="atlas-oncall",
        title="Decision: weekly data quality review",
        content=(
            "Decision: a weekly data quality review walks the gate failures "
            "and dead-letter lanes; recurring failures become model tests. "
            "The review owns the tolerance registry."
        ),
        mnemos_tags=("decision",),
        free_tags=("correctness",),
    ),
]

#: All 100 seeded governance records, fixed order (project-grouped):
#: aurora rules → aurora decisions → vault rules → vault decisions →
#: mnemos rules → mnemos decisions → atlas rules → atlas decisions.
GOV_RECORDS: list[GoldenEntry] = (
    AURORA_GOV_RULES
    + AURORA_GOV_DECISIONS
    + VAULT_GOV_RULES
    + VAULT_GOV_DECISIONS
    + MNEMOS_GOV_RULES
    + MNEMOS_GOV_DECISIONS
    + ATLAS_GOV_RULES
    + ATLAS_GOV_DECISIONS
)

#: E0 §3.1: the analyzed stratum is locked at 48 records (6 rules +
#: 42 decisions — the live governance shape, E0 §3.3). Each analyzed
#: record contributes exactly 2 queries → the McNemar denominator of 96.
ANALYZED_GOV_SLUGS: tuple[str, ...] = (
    # aurora-api: 2 rules + 10 decisions
    "aurora-gov-deprecation-rule",
    "aurora-gov-migration-gating-rule",
    "aurora-gov-otel-decision",
    "aurora-gov-blue-green-decision",
    "aurora-gov-postgres16-decision",
    "aurora-gov-v1-sunset-decision",
    "aurora-gov-idempotency-decision",
    "aurora-gov-webhook-signing-decision",
    "aurora-gov-etag-decision",
    "aurora-gov-upstream-timeout-decision",
    "aurora-gov-tenant-rate-limit-decision",
    "aurora-gov-flags-manifest-decision",
    # vault-ui: 1 rule + 11 decisions
    "vaultui-gov-a11y-gate-rule",
    "vaultui-gov-grid-decision",
    "vaultui-gov-dark-mode-decision",
    "vaultui-gov-ssr-shell-decision",
    "vaultui-gov-lucide-decision",
    "vaultui-gov-windowing-decision",
    "vaultui-gov-headless-tables-decision",
    "vaultui-gov-token-pipeline-decision",
    "vaultui-gov-playwright-decision",
    "vaultui-gov-visual-regression-decision",
    "vaultui-gov-error-boundary-decision",
    "vaultui-gov-optimistic-ui-decision",
    # mnemos-core: 2 rules + 10 decisions
    "mnemos-gov-fingerprint-rule",
    "mnemos-gov-quarantine-rule",
    "mnemos-gov-rrf-params-decision",
    "mnemos-gov-sqlite-store-decision",
    "mnemos-gov-blake2-reference-decision",
    "mnemos-gov-fts5-stemming-decision",
    "mnemos-gov-supersedes-decision",
    "mnemos-gov-ccr-snippets-decision",
    "mnemos-gov-scan-at-issuance-decision",
    "mnemos-gov-status-gate-decision",
    "mnemos-gov-project-predicate-decision",
    "mnemos-gov-overfetch-decision",
    # atlas-pipeline: 1 rule + 11 decisions
    "atlas-gov-late-arrival-rule",
    "atlas-gov-delta-decision",
    "atlas-gov-defer-streaming-decision",
    "atlas-gov-salted-keys-decision",
    "atlas-gov-dbt-decision",
    "atlas-gov-window-decision",
    "atlas-gov-hive-layout-decision",
    "atlas-gov-compaction-trigger-decision",
    "atlas-gov-exactly-once-decision",
    "atlas-gov-writer-schema-decision",
    "atlas-gov-copy-cluster-decision",
    "atlas-gov-dlq-sla-decision",
)

#: E0 §4.4: the seeding surplus forms the adjudication replacement pool,
#: consumed in this fixed order by ``ground_truth.record_rejection``.
REPLACEMENT_POOL_SLUGS: tuple[str, ...] = tuple(
    e.slug for e in GOV_RECORDS if e.slug not in set(ANALYZED_GOV_SLUGS)
)

GOV_RECORD_SLUGS: frozenset[str] = frozenset(e.slug for e in GOV_RECORDS)


def gov_record_by_slug(slug: str) -> GoldenEntry:
    """Look up a seeded governance record by its stable slug."""
    for entry in GOV_RECORDS:
        if entry.slug == slug:
            return entry
    raise KeyError(f"unknown e2-gov slug: {slug!r}")
