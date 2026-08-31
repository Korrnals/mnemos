"""Scripted rewrite scenario for the ADR-0018 metric pair (W4).

Operational definitions measured here (ratification items in
BASELINE.md):

- ``replace-hit-rate`` — of the M follow-up retrieves that target a
  marker minted by a rewrite event, the fraction that successfully
  redeem content through the real channel
  (``MemoryManager.retrieve_content``): snippet mode for ``detail``
  needs, full mode for ``whole`` needs. A hit requires ``found=True``,
  a non-refused response, and non-empty content. Misses model real
  friction: snippet queries that do not lexically match the original,
  TTL/LRU eviction, marker-validation refusal.

- ``replace-regret-rate`` — of the N rewrite events, the fraction whose
  original is later redeemed IN FULL (``whole`` need: the task the block
  served is reactivated and the entire block must re-enter the window).
  A full redemption right after a rewrite means the replacement was
  premature — the harness policy rewrote content that was still live.
  Denominator: rewrite events. (The ``whole`` need is scripted ground
  truth; the mechanism can still turn a would-be regret into a miss —
  that coupling is exactly why the pair is measured together.)

The committee explicitly does NOT aim replace-hit-rate at 100%: some
follow-ups legitimately miss (snippet lexical mismatch, eviction), and
forcing 100% would require never-evicting caches — a rejected
alternative in ADR-0018.

Controls: six never-rewritten CCR entries (compressed directly via
``compress_content``) redeemed alongside — they validate the channel
without entering either rate.

Determinism: all events run against a fresh golden manager in a fixed
order; one rewrite event per block text (unique hashes guaranteed by
distinct content); no wall-clock, network, or RNG dependence.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchmarks.corpus.corpus import PLANTED_SECRETS


@dataclass(frozen=True)
class RewriteEventSpec:
    """One scripted ``on_context_rewrite`` event + its follow-up ground truth."""

    slug: str
    project: str
    agent: str
    session: str
    subject: str
    steps: tuple[str, ...]
    fact: str
    detail_query: str
    supersedes: str | None = None
    later_need: str = "none"  # none | detail | whole
    planted: str | None = None  # PLANTED_SECRETS key embedded in the block


def _block(spec: RewriteEventSpec) -> str:
    """Render the original working-context block for one event (>= 500 chars)."""
    lines = [f"Working context block — {spec.subject}."]
    lines += [f"- {step}" for step in spec.steps]
    lines.append(f"KEY FACT: {spec.fact}")
    if spec.planted is not None:
        lines.append(f"credential used during this task: {PLANTED_SECRETS[spec.planted]}")
    lines.append("(block retired by on_context_rewrite; original preserved zero-loss)")
    return "\n".join(lines)


REWRITE_EVENTS: list[RewriteEventSpec] = [
    # ── aurora-api (6 events; 1→2→3 form a supersedes chain) ───────────
    RewriteEventSpec(
        slug="rw-au-01",
        project="aurora-api",
        agent="aurora-backend",
        session="au-s11",
        subject="checkout latency spike investigation",
        steps=(
            "p99 on /v1/checkout rose from 120ms to 900ms after deploy 2.7.0",
            "db pool saturation ruled out — pool steady at 40%",
            "token-bucket limiter refill loop pinned as the cause",
            "hotfix shipped: refill on monotonic clock",
            "soak for 2h confirmed p99 back under 150ms",
        ),
        fact="the refill loop held the limiter mutex for 38ms per request",
        detail_query="refill",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-au-02",
        project="aurora-api",
        agent="aurora-backend",
        session="au-s11",
        subject="checkout latency spike — postmortem draft",
        steps=(
            "timeline reconstructed from gateway logs",
            "root cause: limiter mutex contention, not db pool",
            "action item: alert on limiter wait queue depth",
            "postmortem doc opened for review",
        ),
        fact="wait-queue depth peaked at 2100 requests during the spike",
        detail_query="wait",
        supersedes="rw-au-01",
        later_need="whole",  # reactivated: postmortem review needed the full timeline
    ),
    RewriteEventSpec(
        slug="rw-au-03",
        project="aurora-api",
        agent="aurora-backend",
        session="au-s11",
        subject="checkout latency spike — postmortem published",
        steps=(
            "postmortem merged with the wait-queue alert shipped",
            "limiter gained a dedicated metrics gauge",
            "incident closed",
        ),
        fact="final p99 after fix: 141ms measured over 24h",
        detail_query="141",
        supersedes="rw-au-02",
        later_need="none",
    ),
    RewriteEventSpec(
        slug="rw-au-04",
        project="aurora-api",
        agent="aurora-oncall",
        session="au-s12",
        subject="gateway OOM triage",
        steps=(
            "gateway-7f9 oom killed at rss=512Mi twice overnight",
            "heap profile showed audit buffer retention",
            "buffer bounded to 64Mi in config",
        ),
        fact="audit buffer retained 3 days of events by default",
        detail_query="audit",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-au-05",
        project="aurora-api",
        agent="aurora-backend",
        session="au-s13",
        subject="RLS policy rollout checklist",
        steps=(
            "six tables enabled for row-level security",
            "migration tests extended with a tenant-crossing probe",
            "rollout staged 10/50/100 across fleets",
        ),
        fact="the tenant-crossing probe caught two missing policies pre-merge",
        detail_query="tenant",
        later_need="none",
    ),
    RewriteEventSpec(
        slug="rw-au-06",
        project="aurora-api",
        agent="aurora-backend",
        session="au-s14",
        subject="staging credential rotation",
        steps=(
            "staging db credential rotated after the bootstrap-script spill",
            "secret store entries updated",
            "scanner redaction verified on the echoed log",
        ),
        fact="rotation window kept staging down for 90 seconds",
        detail_query="rotation",
        planted="connection-string",
        later_need="whole",  # security review demanded the full block back
    ),
    # ── vault-ui (6 events) ────────────────────────────────────────────
    RewriteEventSpec(
        slug="rw-vu-01",
        project="vault-ui",
        agent="vaultui-frontend",
        session="vu-s21",
        subject="memory list jank fix",
        steps=(
            "10k rows dropped to 14fps on scroll in the memory list",
            "windowed rendering with overscan 5 applied",
            "re-measured on low-end hardware: 58fps sustained",
        ),
        fact="row height measurement was the last jank source, fixed by caching",
        detail_query="row",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-vu-02",
        project="vault-ui",
        agent="vaultui-frontend",
        session="vu-s21",
        subject="memory list jank fix — follow-up",
        steps=(
            "row-height cache invalidated on font-scale change",
            "acceptance notes captured for the perf budget",
        ),
        fact="font-scale invalidation covered by a new unit test",
        detail_query="font",
        supersedes="rw-vu-01",
        later_need="none",
    ),
    RewriteEventSpec(
        slug="rw-vu-03",
        project="vault-ui",
        agent="vaultui-design",
        session="vu-s22",
        subject="contrast remediation pass",
        steps=(
            "three placeholder labels failed AA at 3.1:1 on slate-200",
            "placeholder moved to slate-500, borders stay slate-300",
            "re-audit scheduled after the token sweep",
        ),
        fact="the re-audit measured 4.7:1 on every remediated label",
        detail_query="remediated",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-vu-04",
        project="vault-ui",
        agent="vaultui-frontend",
        session="vu-s23",
        subject="modal focus-trap hardening",
        steps=(
            "focus trap released early on rapid Escape + tab sequences",
            "trap now re-anchors on every keydown",
            "axe-core clean on all five dialog variants",
        ),
        fact="the re-anchor fix shipped in 1.9.1",
        detail_query="re-anchor",
        later_need="whole",  # a11y regression report needed the full sequence
    ),
    RewriteEventSpec(
        slug="rw-vu-05",
        project="vault-ui",
        agent="vaultui-frontend",
        session="vu-s24",
        subject="search state machine migration",
        steps=(
            "search box moved to the explicit idle/typing/loading state machine",
            "stale-response guard keyed by request id",
            "typing-to-loading direct transition removed",
        ),
        fact="request id monotonic counter lives in the search reducer",
        detail_query="reducer",
        later_need="none",
    ),
    RewriteEventSpec(
        slug="rw-vu-06",
        project="vault-ui",
        agent="vaultui-design",
        session="vu-s25",
        subject="icon set migration spike",
        steps=(
            "spike migrated 6 of 40 components to lucide",
            "bundle delta negligible; tree-shaking holds",
            "decision deferred until after the dashboard rewrite",
        ),
        fact="lucide tree-shaking verified with the bundle analyzer",
        detail_query="lucide",
        later_need="none",
    ),
    # ── mnemos-core (6 events) ─────────────────────────────────────────
    RewriteEventSpec(
        slug="rw-mn-01",
        project="mnemos-core",
        agent="mnemos-maintainer",
        session="mn-s31",
        subject="WAL growth incident",
        steps=(
            "federation pull held a read txn for 40 minutes",
            "WAL grew to 800MB and checkpoint stalled",
            "pull chunked to commit every 500 rows",
        ),
        fact="checkpoint resumed within one chunk boundary after the fix",
        detail_query="checkpoint",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-mn-02",
        project="mnemos-core",
        agent="mnemos-maintainer",
        session="mn-s31",
        subject="WAL growth incident — patch review",
        steps=(
            "chunked pull patch reviewed",
            "explicit PRAGMA wal_checkpoint(TRUNCATE) after large pulls",
            "chunk boundary sized against the observed write amp",
        ),
        fact="truncate pragma added behind a maintenance flag",
        detail_query="truncate",
        supersedes="rw-mn-01",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-mn-03",
        project="mnemos-core",
        agent="mnemos-security",
        session="mn-s32",
        subject="issuance scan coverage audit",
        steps=(
            "every echo path enumerated: search, agent recall, retrieve, assemble",
            "snippet path gained the offset-mapped tier-2 scan",
            "coverage proven by the planted-fixture suite",
        ),
        fact="offset mapping localizes fragments with a 64-char margin",
        detail_query="offset",
        later_need="none",
    ),
    RewriteEventSpec(
        slug="rw-mn-04",
        project="mnemos-core",
        agent="mnemos-researcher",
        session="mn-s33",
        subject="golden set determinism work",
        steps=(
            "feature-hashing embedder replaces the ONNX download",
            "judgments kept small and honest",
            "three consecutive runs byte-identical",
        ),
        fact="blake2b bucketing keeps embeddings stable across platforms",
        detail_query="blake2b",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-mn-05",
        project="mnemos-core",
        agent="mnemos-maintainer",
        session="mn-s34",
        subject="embedding pre-warm change",
        steps=(
            "cold cache made the first mnemos_search exceed the 800ms deadline",
            "boot sequence now pre-warms the ten most-used embeddings",
            "second-call latency back under 50ms",
        ),
        fact="pre-warm list is derived from the retrieval counter top-10",
        detail_query="pre",
        later_need="whole",  # capacity review re-read the whole change log
    ),
    RewriteEventSpec(
        slug="rw-mn-06",
        project="mnemos-core",
        agent="mnemos-security",
        session="mn-s35",
        subject="backup credential hygiene",
        steps=(
            "nightly backup env excerpt carried an aws-key shaped value",
            "value rotated; issuance redaction verified",
            "log shipping gained a scan gate",
        ),
        fact="scan gate runs before any log leaves the node",
        detail_query="gate",
        planted="aws-key",
        later_need="whole",  # audit required the full block with the redacted credential
    ),
    # ── atlas-pipeline (6 events) ──────────────────────────────────────
    RewriteEventSpec(
        slug="rw-at-01",
        project="atlas-pipeline",
        agent="atlas-etl",
        session="at-s41",
        subject="spark skew remediation",
        steps=(
            "whale tenant owned 40% of rows in stage 7",
            "salted keys applied for the shuffle, re-aggregated after",
            "stage time 22min → 6min",
        ),
        fact="median-to-max partition ratio now 1.9x after salting",
        detail_query="salting",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-at-02",
        project="atlas-pipeline",
        agent="atlas-oncall",
        session="at-s42",
        subject="late-arrival storm handling",
        steps=(
            "vendor sent 26h-late events for run_date 2026-08-17",
            "1204 events routed to the repair lane",
            "watermark lag alert suppressed under 30h per policy",
        ),
        fact="repair lane replay finished in 7 minutes",
        detail_query="repair",
        later_need="whole",  # vendor escalation call needed the full timeline
    ),
    RewriteEventSpec(
        slug="rw-at-03",
        project="atlas-pipeline",
        agent="atlas-etl",
        session="at-s43",
        subject="watermark table migration",
        steps=(
            "marker files drifted and double-counted 3 partitions",
            "transactional watermark table became the replay source",
            "markers retired after the rebasing verified",
        ),
        fact="rebase diff was exactly 3 partitions and zero rows lost",
        detail_query="rebase",
        later_need="detail",
    ),
    RewriteEventSpec(
        slug="rw-at-04",
        project="atlas-pipeline",
        agent="atlas-oncall",
        session="at-s44",
        subject="executor OOM postmortem",
        steps=(
            "executor 14 died during shuffle write in the tenant rollup",
            "salted-key buffers grew past the off-heap headroom",
            "memory.fraction lowered to 0.5 for rollup tasks",
        ),
        fact="rollup tasks now request 4g executors with overhead 2g",
        detail_query="rollup",
        later_need="none",
    ),
    RewriteEventSpec(
        slug="rw-at-05",
        project="atlas-pipeline",
        agent="atlas-etl",
        session="at-s45",
        subject="parquet compaction trigger tuning",
        steps=(
            "scan latency 4min traced to 11k small files in one partition",
            "weekly compaction landed: file count trigger at 5k",
            "median scan latency after: 40 seconds",
        ),
        fact="compaction window avoids the hourly DQ gate by 10 minutes",
        detail_query="compaction",
        later_need="none",
    ),
    RewriteEventSpec(
        slug="rw-at-06",
        project="atlas-pipeline",
        agent="atlas-oncall",
        session="at-s46",
        subject="DSN spill cleanup",
        steps=(
            "ingest template printed the source DSN into stdout",
            "spilled logs quarantined before the shared bucket sync",
            "template patched to read the DSN from the secret store",
        ),
        fact="quarantine caught 4 affected log files",
        detail_query="quarantine",
        planted="connection-string",
        later_need="detail",  # snippet redeem of the quarantine step (redacted)
    ),
]


@dataclass(frozen=True)
class ControlSpec:
    """A never-rewritten CCR entry redeemed as a channel control."""

    slug: str
    project: str
    text: str
    query: str


_CONTROLS: list[ControlSpec] = [
    ControlSpec(
        slug="ctl-01",
        project="aurora-api",
        text=(
            "Reference card — aurora oncall rotation:\n"
            "- primary rotates weekly on tuesday\n"
            "- escalation channel is the aurora-incidents room\n"
            "- runbook index lives in the deployment runbook memory\n"
            "KEY FACT: the escalation ack SLA is 5 minutes."
        ),
        query="escalation",
    ),
    ControlSpec(
        slug="ctl-02",
        project="vault-ui",
        text=(
            "Reference card — vault-ui perf budget:\n"
            "- interaction budget 100ms on the 25th-percentile device\n"
            "- bundle ceiling 180KB gzipped\n"
            "- list scroll budget 55fps sustained\n"
            "KEY FACT: the budget gate runs on every PR preview."
        ),
        query="budget",
    ),
    ControlSpec(
        slug="ctl-03",
        project="mnemos-core",
        text=(
            "Reference card — mnemos release cadence:\n"
            "- patch weekly, minor monthly\n"
            "- federation peers upgrade within two minors\n"
            "- breaking changes need a deprecation note one minor ahead\n"
            "KEY FACT: the cadence gate is the local-ci replica."
        ),
        query="cadence",
    ),
    ControlSpec(
        slug="ctl-04",
        project="atlas-pipeline",
        text=(
            "Reference card — atlas DQ gate thresholds:\n"
            "- row-count delta tolerance 2 percent of the source ledger\n"
            "- null rate ceiling 0.1 percent on join keys\n"
            "- duplicate event ids per partition: zero\n"
            "KEY FACT: gate failures page the etl oncall."
        ),
        query="tolerance",
    ),
    ControlSpec(
        slug="ctl-05",
        project="aurora-api",
        text=(
            "Reference card — aurora staging topology:\n"
            "- staging is a single node with a frozen tenant fixture\n"
            "- data resets nightly from the ledger snapshot\n"
            "- no production credentials, ever\n"
            "KEY FACT: the fixture carries 42 synthetic tenants."
        ),
        query="fixture",
    ),
    ControlSpec(
        slug="ctl-06",
        project="mnemos-core",
        text=(
            "Reference card — mnemos trace retention:\n"
            "- retrieval spans sampled at 10 percent\n"
            "- error spans kept at 100 percent\n"
            "- retention 30 days local, 7 days federated\n"
            "KEY FACT: retention compaction runs nightly."
        ),
        query="retention",
    ),
]


def control_blocks() -> list[ControlSpec]:
    """Never-rewritten CCR control entries (channel validation only).

    Control texts are padded with deterministic filler so each block
    clears the CCR ``min_size_chars`` threshold (default 500) and is
    actually cached by ``compress_content`` — unpadded short texts would
    be returned as-is with no marker, defeating the control.
    """
    pad_line = "\n(padding line for ccr min-size compliance — no signal)"
    padded: list[ControlSpec] = []
    for ctl in _CONTROLS:
        need = 560 - len(ctl.text)
        reps = max(1, -(-need // len(pad_line)))  # ceil division
        padded.append(ControlSpec(ctl.slug, ctl.project, ctl.text + pad_line * reps, ctl.query))
    return padded
