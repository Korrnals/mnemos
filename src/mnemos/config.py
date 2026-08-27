"""Configuration management for Mnemos."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

logger = logging.getLogger(__name__)


class MnemosConfig(BaseModel):
    # Consolidated layout (v2.1): everything lives under ~/.mnemos/.
    # Old scattered paths (~/mnemos-vault, ~/.mnemos as data_dir) are
    # auto-migrated by ``Settings.migrate_layout()`` on first load.
    vault_path: Path = Path("~/.mnemos/vault")
    data_dir: Path = Path("~/.mnemos/data")
    db_name: str = "mnemos.db"
    # M2: tag contract enforcement
    strict_tag_contract: bool = True
    # M10: auto-run the context filter on ingest (mnemos_add / manager.add).
    # When True, raw_content is preserved and clean_content is populated;
    # filter failures are non-fatal (memory is still saved with raw content).
    auto_filter: bool = True
    # mnemos #96: workflow lifecycle guardrails. Stale-lock threshold governs
    # how long a lock survives before a different actor can take it over
    # without ``force`` (guardrail 2). Rate limit caps transitions per memory
    # per minute to prevent churn (guardrail 5) — it is per-memory, NOT
    # per-actor: churn on a single memory is throttled regardless of which
    # actor drives the transitions.
    workflow_stale_lock_threshold_hours: int = Field(default=24, ge=1, le=720)
    workflow_rate_limit_per_minute: int = Field(default=30, ge=1, le=1000)
    # mnemos #125 W2 review F1: on_context_rewrite write-surface guardrails.
    # The rate limit counts STORED rewrite events per (project, session) per
    # minute — a deduplicated re-delivery performs no write and consumes no
    # quota, so at-least-once retry storms stay harmless. 0 disables the
    # limiter. Size caps reject oversized payloads at the boundary before
    # any write: content = the original block (default 1 MiB in chars),
    # diff = the advisory was→becomes payload (default 256 KiB in chars).
    context_rewrite_rate_limit_per_minute: int = Field(default=30, ge=0, le=10_000)
    # C10 (ArchCom 2026-08-27) — SECONDARY per-project aggregate ceiling:
    # total stored rewrite events per project per minute across ALL of
    # that project's sessions (the distinct-session count rides along in
    # the 429 message as the noisy-neighbor signal). Same 429/rate_limited
    # shape as the primary limiter; NULL-session events land in their own
    # bucket under the same knobs. 0 disables the aggregate ceiling (the
    # per-(project, session) limiter still applies). Default 300 = ten
    # sessions at full per-session burn; the residual noisy-neighbor risk
    # (one project starving its siblings on a shared node) is
    # ADR-0018-accepted (single-operator).
    context_rewrite_project_rate_limit_per_minute: int = Field(default=300, ge=0, le=100_000)
    context_rewrite_max_content_chars: int = Field(default=1_048_576, ge=1)
    context_rewrite_max_diff_chars: int = Field(default=262_144, ge=1)


class LoggingConfig(BaseModel):
    """Logging configuration — file + console handlers with rotation.

    Set ``log_file`` to an empty path (``Path("")``) to disable file logging
    and emit to stderr only.
    """

    level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR
    log_file: Path = Path("~/.mnemos/logs/mnemos.log")
    max_file_size_mb: int = Field(default=10, ge=1, le=1024)
    backup_count: int = Field(default=3, ge=0, le=100)
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"


class EmbeddingConfig(BaseModel):
    provider: str = "chromadb"  # chromadb | onnx | ollama | sentence-transformers
    model: str = "all-MiniLM-L6-v2"  # HF model ID
    onnx_file: str = "onnx/model.onnx"  # ONNX filename within HF repo
    ollama_url: str = "http://localhost:11434"
    # M15.2: pin HF Hub downloads to a specific revision to mitigate supply-chain
    # risk (CWE-494 — download of code without integrity check). Override via
    # MNEMOS_EMBEDDING__HF_REVISION env var or config.yaml. The default is
    # empty so the ``if not revision: raise`` guard in ONNXHubProvider fires
    # and forces operators to pin an explicit revision when using the ONNX
    # provider. When changing the ``model`` field, set ``hf_revision`` to a
    # matching pinned SHA/tag.
    hf_revision: str = ""


class SearchConfig(BaseModel):
    default_limit: int = 20
    hybrid_alpha: float = Field(default=0.7, ge=0.0, le=1.0)


class ApiConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    # T-CORS: browser cross-origin allow-list for mnemos-eyes
    # Default is strict - CORS disabled, no origin permitted.
    cors_enabled: bool = False
    cors_allow_origins: list[str] = []
    cors_allow_credentials: bool = False
    cors_allow_methods: list[str] = ["GET", "POST", "DELETE"]
    cors_allow_headers: list[str] = ["Authorization", "Content-Type"]
    # T-AUTH additions (ADR-0014) ─────────────────────────────────────────────
    auth_enabled: bool = False  # default off — safe for loopback-only bind
    totp_enabled: bool = False  # default off — safe for loopback-only bind
    # env-only; never written to disk — MNEMOS_API__TOTP_MASTER_KEY
    totp_master_key: SecretStr = SecretStr("")
    session_ttl_sec: int = Field(default=8 * 3600, ge=300, le=24 * 3600)
    session_pin_ip: bool = False  # bind session to creation IP
    behind_tls_proxy: bool = False  # operator-asserted TLS termination ahead
    trusted_proxies: list[str] = Field(default_factory=list)  # CIDRs for X-Forwarded-*


class McpConfig(BaseModel):
    transport: str = "stdio"


class WatcherConfig(BaseModel):
    paths: list[str] = []
    # M8: enable path-scoped rules ingest
    include_rules: bool = False
    ignore_dirs: list[str] = [
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    ]
    extensions: list[str] = [
        ".md",
        ".py",
        ".js",
        ".ts",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".txt",
        ".rst",
        ".sh",
        ".css",
        ".html",
        ".sql",
    ]
    max_file_size_kb: int = 512
    auto_scan: bool = True
    auto_translate: bool = False


class LLMConfig(BaseModel):
    """Multi-provider LLM configuration (M4 synthesis workers, M10 context filter)."""

    provider: str = "ollama"  # anthropic | openai | azure_openai | ollama | gemini
    model: str = "qwen2.5:3b"
    # Ollama
    ollama_url: str = "http://localhost:11434"
    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = ""
    # Azure OpenAI
    azure_endpoint: str = ""
    azure_api_version: str = "2024-02-01"
    azure_deployment: str = ""
    # Anthropic
    anthropic_api_key: SecretStr = SecretStr("")
    # Google Gemini
    gemini_api_key: SecretStr = SecretStr("")
    temperature: float = 0.3
    max_tokens: int = 4096


class AutomationConfig(BaseModel):
    """M5 — policy engine / scheduler configuration."""

    enabled: bool = True
    # APScheduler interval for periodic tasks
    scheduler_interval_sec: int = Field(default=300, ge=30, le=86400)
    # Debounce after vault write events
    event_debounce_sec: int = Field(default=45, ge=5, le=3600)
    # Minimum raw entries required before auto-clustering triggers
    min_raw_to_trigger: int = Field(default=3, ge=1, le=10000)
    # Cooldown between automated pipeline runs
    cooldown_sec: int = Field(default=180, ge=10, le=86400)


class RuntimeConfig(BaseModel):
    # Hard cap for CPU-bound thread pools (BLAS/OMP/ONNX/tokenizers)
    cpu_threads: int = Field(default=4, ge=1, le=64)
    # Uvicorn worker processes for `mnemos serve`
    uvicorn_workers: int = Field(default=1, ge=1, le=8)


class CCRConfig(BaseModel):
    """P1-4 — CCR (Compress-Cache-Retrieve) reversible compression.

    Inspired by headroom's CCR (https://github.com/headroomlabs-ai/headroom),
    Apache 2.0. We implement our own version integrated into the existing
    mnemos SQLite store (one DB, one backup) with FTS5 snippet retrieval
    and per-project scoping.
    """

    enabled: bool = True
    # Cache entries older than this are eligible for cleanup (days).
    ttl_days: int = Field(default=7, ge=1, le=365)
    # LRU eviction kicks in when the entry count exceeds this.
    max_entries: int = Field(default=10000, ge=100, le=1_000_000)
    # Content shorter than this (chars) is returned as-is — not cached,
    # not compressed (tiny content has no token savings).
    min_size_chars: int = Field(default=500, ge=50, le=100_000)
    # Number of snippet fragments returned by retrieve(query=...).
    snippet_count: int = Field(default=5, ge=1, le=50)
    # Token budget passed to apply_filter for the compress stage.
    filter_budget: int = Field(default=4096, ge=256, le=1_000_000)
    # ADR-0018 P0 — issuance guard. When True, retrieve refuses to issue
    # content whose scan detected a secret (refused=True, no content in
    # the response). Default False → redact-and-issue: matched spans are
    # replaced with <REDACTED:<pattern>> in the returned payload only;
    # the stored original is never mutated (zero-loss storage).
    retrieve_refuse_on_secret: bool = False
    # ADR-0018 P1-b (Security findings 1+4, CWE-668 ergonomics) — when
    # True, retrieving a project-scoped cache entry WITHOUT a matching
    # ``project`` argument is DENIED (refused=True, no content) instead
    # of merely logged as a WARNING. Default False preserves the legacy
    # unscoped behavior for callers without project context (the
    # WARNING still fires); flip to True on single-project deployments
    # to make the scope check enforceable.
    require_project_match: bool = False
    # A2 (ArchCom 2026-08-27) — strict marker validation on issuance.
    # When True, a MARKER-SHAPED retrieve (one carrying original_chars
    # and/or agent/session identity from a [compressed: ...] marker)
    # must pass existence (project-scoped, after A1), original_chars
    # integrity, and provenance (the cache row's issuer ledger must
    # match the caller's trusted issuer context) BEFORE any content is
    # issued; a failed check returns refused=True with no content
    # (fail-closed). Plain hash-only retrieves are unaffected. Default
    # False until W3 automation ships; flip to True in the automation
    # config so hooks redeem only markers minted in their own
    # agent/session context. Per-call ``validate_marker`` overrides.
    validate_markers: bool = False
    # P1-5/T3: background CCR cleanup interval (seconds). Default 1200s = 20 min.
    # The processor loop runs every `interval_sec` (default 120s); CCR cleanup runs
    # every `ccr_cleanup_interval_sec` to avoid scanning the cache table every cycle.
    ccr_cleanup_interval_sec: int = Field(default=1200, ge=60, le=86400)


class HooksConfig(BaseModel):
    """ADR-0017 D1 lifecycle hooks (mnemos #125, Wave 3).

    The hooks themselves (``pre_llm_call`` / ``on_session_start`` /
    ``post_tool_call``) are stateless thin wrappers over the manager's
    existing recall / assemble / compress paths — exposing them adds no
    capability the MCP/REST surfaces do not already have, so there is no
    master ``enabled`` switch (tool-surface exposure is a server concern,
    not per-hook config). The ONLY behavior knob is autocompression,
    which turns ``post_tool_call`` from a no-op envelope into a
    side-effecting CCR write.
    """

    # post_tool_call autocompression (ADR-0018 Phase 1): compress the
    # tool output via CCR and return the marker to substitute. Default
    # False — a side-effecting write is opt-in; automation deployments
    # (the W3+ harness configs) enable it. The per-call ``auto_compress``
    # argument overrides per invocation.
    auto_compress: bool = False


class CacheAlignerConfig(BaseModel):
    """P1-5 — CacheAligner prefix stabilization.

    Inspired by headroom's CacheAligner
    (https://github.com/headroomlabs-ai/headroom, Apache 2.0). Original
    implementation — no headroom code is imported.

    When enabled, dynamic content (timestamps, UUIDs, session ids, tokens)
    is relocated to the end of system-prompt-like text so the prefix stays
    byte-identical across requests and provider KV caches
    (Anthropic ``cache_control``, OpenAI prefix caching) hit.
    """

    enabled: bool = True
    # Toggle individual extractor kinds. Disabling a kind means those
    # spans stay in-place (not relocated). Useful for workloads where a
    # kind is known to be stable (e.g. a fixed session id per session).
    extract_timestamps: bool = True
    extract_uuids: bool = True
    extract_session_ids: bool = True
    extract_dates: bool = True
    extract_tokens: bool = True


class OutputStyleConfig(BaseModel):
    """P1-7 — Output token reduction via verbosity steering + effort routing.

    Inspired by headroom's output token reduction work. Original
    implementation. These are *hints* injected into tool result framing
    and passed through to the caller — they are not model config changes.
    """

    enabled: bool = True
    # Default verbosity when the caller does not specify one.
    #   "default" — preserve current behaviour (no steering injected).
    #   "terse"   — inject "be terse, no preambles, no restated context".
    #   "minimal" — inject "absolute minimum, facts only".
    default_verbosity: str = "default"
    # Default reasoning effort hint when the caller does not specify one.
    #   "low" / "medium" / "high" — dial thinking depth for routine steps.
    default_effort: str = "medium"


class PeerConfig(BaseModel):
    """Per-peer federation ACL — Phase 1 prerequisite (contract §3.2, §6).

    ADR-0016 mandates per-peer bearer ``mnk_fed_<peer_id>_`` plus mTLS
    client cert pinned per peer, plus a per-peer ACL GATE. Each entry
    in :attr:`FederationConfig.peers` is one peer — keyed by the peer's
    A2A id (e.g. ``mnemos-A``).

    Fail-closed defaults: every list field defaults to empty, which
    means "none" — never implicit "allow all". ``["*"]`` is the explicit
    wildcard. This is the contract §3.2 / §6 rule: an operator who wants
    to open a peer must say so explicitly.

    Fields:
        bearer_token_env: NAME of the environment variable holding the
            per-peer bearer token (e.g. ``MNEMOS_FED_PEER_A_TOKEN``).
            Per ``sensitive-data.instructions.md`` we store the NAME,
            never the value — the server reads the token from this env
            var at request time. The token format is
            ``mnk_fed_<peer_id>_<random>`` per ADR-0016.
        allowed_projects: Which projects this peer may pull. Subset
            filter applied on top of the global
            :attr:`FederationConfig.shared_projects` whitelist. Empty
            list = none (fail-closed). ``["*"]`` = all projects in
            ``shared_projects`` (explicit wildcard, not implicit).
        allowed_types: Which record types this peer may pull
            (``decision`` / ``learning`` / ``bug-pattern`` / ``rule`` /
            ``open-question`` / ``checkpoint`` / ``session``). Empty
            list = none. ``["*"]`` = all types.
        rate_limit_per_minute: Per-peer rate limit on pull requests
            (contract §8 — DDoS mitigation; slowapi is already used on
            the ``/auth/*`` surface). Default 30/min. Clamped to
            ``[1, 600]`` — below 1 is unusable, above 600 defeats the
            purpose.
        mtls_cert_fingerprint: Optional SHA-256 fingerprint of the
            peer's mTLS client cert, for pinning. If set, the server
            rejects connections whose client cert fingerprint does not
            match. If ``None``, mTLS pinning is not enforced for this
            peer (operator opts in). ADR-0016 recommends pinning for
            networked deployments.
    """

    bearer_token_env: str = Field(..., min_length=1, max_length=256)
    allowed_projects: list[str] = Field(default_factory=list)
    allowed_types: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=30, ge=1, le=600)
    mtls_cert_fingerprint: str | None = Field(default=None, max_length=128)


class FederationConfig(BaseModel):
    """Federation (Phase 0 batch sync) configuration.

    ArchCom 2026-07-17 federation contract §3.1. This section governs
    operator-curated, offline, cron-triggered batch sync between two
    mnemos instances. It is NOT networked — transfer is out-of-band
    (rsync / scp / shared volume by the operator).

    Fields:
        shared_projects: Whitelist of project slugs eligible for sync.
            Only records whose ``project:`` tag matches a slug in this
            list are included in ``mnemos sync export``. Empty list =
            no projects are eligible (sync exports nothing). The
            receiving side re-applies the same filter on import.
        moderation_mapping_ttl_hours: TTL for the per-run moderation
            mapping table (contract §2.2). The mapping is in-memory only
            and NEVER replicated (it is a leak surface). Default 24h.
            After expiry, a fresh mapping is issued on the next
            moderation run.
        moderation_refuse_threshold: Fraction of content that must be
            redacted/anonymized to trigger a ``refuse`` verdict (contract
            §2.2). Default 0.8 = 80%. If >80% of content is redacted or
            anonymized, the record is refused (no useful remainder).
        peers: Per-peer ACL map — Phase 1 prerequisite (contract §3.2,
            §6, ADR-0016). Keyed by peer A2A id (e.g. ``mnemos-A``).
            Empty dict = no peers configured = the federation server
            refuses all pull requests (fail-closed). Each value is a
            :class:`PeerConfig` with the per-peer bearer token env name,
            allowed projects/types, rate limit, and optional mTLS cert
            fingerprint. ``shared_projects`` stays as the global filter;
            per-peer ``allowed_projects`` is a subset filter on top.
        access_log_path: Optional override for the B-side federation
            access log location (contract §10). When ``None`` (default)
            the log falls back to ``~/.mnemos/logs/federation-access.jsonl``
            (see :data:`mnemos.federation_access_log.DEFAULT_LOG_PATH`).
            Set to an absolute path (e.g. ``/data/federation-access.jsonl``)
            to place the log on a persistent volume — useful for
            containerised deployments where ``~/.mnemos`` is ephemeral.
            The log is never replicated, never exported, never synced
            (leak surface, contract §10 "Где хранится").
    """

    shared_projects: list[str] = Field(default_factory=list)
    moderation_mapping_ttl_hours: int = Field(default=24, ge=1, le=168)
    moderation_refuse_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    peers: dict[str, PeerConfig] = Field(default_factory=dict)
    access_log_path: str | None = Field(default=None, max_length=4096)


class ScannerConfig(BaseModel):
    """Background secrets scanner configuration (Layer 2 defence-in-depth).

    ArchCom 2026-07-17 federation contract §2.2.1 — the background
    scanner periodically re-scans the whole corpus for secrets missed
    by the write-path scanner (Layer 1) and auto-tags
    ``mnemos:no-federate`` so the record is excluded from all external
    exchange. It re-uses :func:`mnemos.secrets_detector.detect_secrets`
    unchanged (DRY — one source of truth for patterns).

    Fields:
        enabled: When ``False``, ``BackgroundScanner.start()`` is a
            no-op and the scanner does not run on the configured
            interval. Defaults to ``True`` (defence-in-depth is on by
            default — operators who want to disable it must do so
            explicitly).
        interval_hours: Wall-clock interval between automatic scan
            passes. Default 6h per contract §2.2.1. Clamped to
            ``[1, 168]`` — anything below 1h is wasteful, anything
            above a week defeats the purpose of catching false
            negatives in a timely manner.
        incremental: When ``True`` (default), ``run_scan`` only scans
            records whose ``created_at`` OR ``updated_at`` is newer than
            the last successful scan timestamp. When ``False``, every
            scan is a full corpus scan. The CLI ``--full`` flag forces
            ``incremental=False`` for one run.
    """

    enabled: bool = True
    interval_hours: int = Field(default=6, ge=1, le=168)
    incremental: bool = True


class MeshConfig(BaseModel):
    """mnemos-mesh gRPC client configuration (Phase 3, issue #105 M3).

    ArchCom 2026-07-17 federation contract §3.1. This section governs the
    Python gRPC client (:class:`mnemos.mesh_client.MeshClient`) that talks
    to the ``mnemos-mesh`` Go binary over a Unix socket. The mesh is a
    dumb transport (criterion 1); moderation and storage stay in Python
    (criterion 2/11). This section is OFF by default — an operator opts in
    by setting ``enabled: true`` after deploying the mesh binary.

    Fields:
        socket_path: Filesystem path to the ``mnemos-mesh`` Unix socket.
            The mesh binary creates the socket; mnemos connects to it.
            Default ``/run/mnemos/core.sock`` (systemd-tmpfiles convention
            for runtime sockets owned by the mnemos user).
        enabled: Master switch. When ``False`` (default), :class:`MeshClient`
            is not constructed and the MCP/HTTP paths do not attempt to
            talk to the mesh. Operators enable it after deploying the mesh.
        timeout_s: Per-RPC deadline in seconds. Short enough that a dead
            mesh is noticed quickly, long enough for a local Unix-socket
            round trip. Default 2.0s.
    """

    socket_path: str = "/run/mnemos/core.sock"
    enabled: bool = False
    timeout_s: float = Field(default=2.0, gt=0.0, le=60.0)


# ── Issue #139: legacy short env-name compatibility ──────────────────────────
#
# ``Settings`` maps env vars with the ``MNEMOS_`` prefix + ``__`` nesting, so
# the canonical names for the nested ``mnemos`` section fields are
# ``MNEMOS_MNEMOS__DATA_DIR`` / ``MNEMOS_MNEMOS__VAULT_PATH``. Historically the
# repo docs and ``scripts/mcp-setup.sh`` advertised the shorter
# ``MNEMOS_DATA_DIR`` / ``MNEMOS_VAULT__VAULT_PATH`` forms, which
# pydantic-settings silently ignores (no matching field). The mapping below
# restores those two short names as compatibility aliases. Scope is
# deliberately fixed to these two — this is NOT a general renaming engine.

_ENV_COMPAT_ALIASES: Final[dict[str, tuple[str, str]]] = {
    # legacy env name            -> (settings section, field)
    "MNEMOS_DATA_DIR": ("mnemos", "data_dir"),
    "MNEMOS_VAULT__VAULT_PATH": ("mnemos", "vault_path"),
}


class _EnvCompatAliasSettingsSource(PydanticBaseSettingsSource):
    """Settings source mapping legacy short env names (#139) to nested fields.

    Reads the process environment (NOT ``.env`` files — that is a separate,
    lower-priority source) on every call, so each ``Settings()`` construction
    observes the current ``os.environ``. Empty-string values are treated as
    unset.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Not consulted by the source-merge machinery (only __call__ is);
        # required by the PydanticBaseSettingsSource ABC.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        data: dict[str, dict[str, Any]] = {}
        for alias, (section, field_name) in _ENV_COMPAT_ALIASES.items():
            value = os.environ.get(alias, "")
            if value:
                data.setdefault(section, {})[field_name] = value
        return data


class Settings(BaseSettings):
    mnemos: MnemosConfig = MnemosConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    search: SearchConfig = SearchConfig()
    api: ApiConfig = ApiConfig()
    mcp: McpConfig = McpConfig()
    watcher: WatcherConfig = WatcherConfig()
    llm: LLMConfig = LLMConfig()
    automation: AutomationConfig = AutomationConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    ccr: CCRConfig = CCRConfig()
    hooks: HooksConfig = HooksConfig()
    cache_aligner: CacheAlignerConfig = CacheAlignerConfig()
    output_style: OutputStyleConfig = OutputStyleConfig()
    federation: FederationConfig = FederationConfig()
    scanner: ScannerConfig = ScannerConfig()
    mesh: MeshConfig = MeshConfig()
    logging: LoggingConfig = LoggingConfig()
    # M5: declarative policy rules (loaded from YAML or set programmatically)
    policies: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "env_prefix": "MNEMOS_",
        "env_nested_delimiter": "__",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the #139 legacy alias source between env and dotenv sources.

        Resulting precedence for ``mnemos.data_dir`` / ``mnemos.vault_path``
        (high → low; sources deep-merge, so higher priority wins per field):

        1. Init kwargs — ``load_settings()`` passes the YAML config file here,
           so an explicit file value beats env vars. This mirrors the
           pre-existing pydantic-settings behaviour of canonical names
           (verified against pydantic-settings 2.14.2: init > env).
        2. Canonical env: ``MNEMOS_MNEMOS__DATA_DIR`` /
           ``MNEMOS_MNEMOS__VAULT_PATH``.
        3. Compat alias (this source): ``MNEMOS_DATA_DIR`` /
           ``MNEMOS_VAULT__VAULT_PATH`` — honoured only when neither the file
           nor the canonical name provides the field. A short alias therefore
           never overrides an explicit config-file value and never wins
           against the canonical name; it only fills the gap that previously
           fell through to the defaults.
        4. ``.env`` dotenv file.  5. Field defaults.
        """
        return (
            init_settings,
            env_settings,
            _EnvCompatAliasSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    def resolve_paths(self) -> None:
        self.mnemos.vault_path = self.mnemos.vault_path.expanduser().resolve()
        self.mnemos.data_dir = self.mnemos.data_dir.expanduser().resolve()
        # Resolve log_file only if non-empty; an empty Path("") becomes "."
        # which means "stderr only" — leave it as an empty Path().
        log_str = str(self.logging.log_file).strip()
        if log_str and log_str != ".":
            self.logging.log_file = self.logging.log_file.expanduser().resolve()
        else:
            self.logging.log_file = Path()

    def apply_runtime_env(self) -> None:
        """Apply conservative thread caps unless explicitly overridden by user env."""
        threads = str(self.runtime.cpu_threads)
        defaults = {
            "OMP_NUM_THREADS": threads,
            "OPENBLAS_NUM_THREADS": threads,
            "MKL_NUM_THREADS": threads,
            "NUMEXPR_NUM_THREADS": threads,
            "VECLIB_MAXIMUM_THREADS": threads,
            "BLIS_NUM_THREADS": threads,
            "TOKENIZERS_PARALLELISM": "false",
        }
        for key, value in defaults.items():
            os.environ.setdefault(key, value)

    @property
    def db_path(self) -> Path:
        return self.mnemos.data_dir / self.mnemos.db_name

    def migrate_layout(self) -> list[str]:
        """Migrate scattered old paths to the consolidated ``~/.mnemos/`` layout.

        Detection rules (all idempotent — only moves if old exists AND new doesn't):

        * Old data dir ``~/.mnemos`` containing ``mnemos.db`` (and optionally
          ``vectors.db``) → moved to ``~/.mnemos/data/``.
        * Old vault ``~/mnemos-vault/`` → moved to ``~/.mnemos/vault/``.

        The config file ``~/.mnemos/config.yaml`` stays in place — it was
        already at the root. If the old ``~/.mnemos`` dir contained a
        ``config.yaml``, it is left in place (the new layout keeps config at
        the root, not under ``data/``).

        Returns a list of human-readable descriptions of what was moved
        (empty if nothing was migrated).
        """
        actions: list[str] = []
        home = Path.home()
        new_data = self.mnemos.data_dir
        new_vault = self.mnemos.vault_path

        # ── Data dir migration ────────────────────────────────────────────
        # Old layout: ~/.mnemos/mnemos.db (and vectors.db) directly under root.
        # New layout: ~/.mnemos/data/mnemos.db
        # Only migrate if the *default* data_dir is in use (i.e. the user
        # hasn't overridden it to a custom path). If data_dir was overridden
        # via config/env, we respect that and skip migration.
        old_data_root = home / ".mnemos"
        default_new_data = (home / ".mnemos" / "data").resolve()
        if (
            new_data == default_new_data
            and old_data_root.is_dir()
            and (old_data_root / "mnemos.db").exists()
            and not (new_data / "mnemos.db").exists()
        ):
            new_data.mkdir(parents=True, exist_ok=True)
            for item in old_data_root.iterdir():
                # Don't move config.yaml, data/, vault/, logs/, cache/ — those
                # are either already in the right place or belong at root.
                if item.name in ("config.yaml", "data", "vault", "logs", "cache"):
                    continue
                dest = new_data / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))
                    actions.append(f"data: {item.name} → {dest}")
                    logger.info("migrate_layout: moved %s → %s", item, dest)

        # ── Vault migration ───────────────────────────────────────────────
        # Old layout: ~/mnemos-vault/
        # New layout: ~/.mnemos/vault/
        old_vault = home / "mnemos-vault"
        default_new_vault = (home / ".mnemos" / "vault").resolve()
        if new_vault == default_new_vault and old_vault.is_dir() and not new_vault.exists():
            new_vault.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_vault), str(new_vault))
            actions.append(f"vault: {old_vault} → {new_vault}")
            logger.info("migrate_layout: moved vault %s → %s", old_vault, new_vault)

        return actions


def find_config_file(config_path: str | Path | None = None) -> Path | None:
    """Resolve the config file :func:`load_settings` would load, or ``None``.

    Zero-config support (ADR-0017 Phase 0, D6): a ``None`` return means no
    user config exists anywhere on the search path, so ``load_settings``
    falls back to the built-in safe defaults (loopback bind, storage under
    ``~/.mnemos/``). Surfaces such as ``mnemos serve`` use this to tell the
    zero-config profile apart from an explicit config.

    Search order (identical to :func:`load_settings`):
      1. Explicit config_path argument
      2. MNEMOS_CONFIG env var
      3. ./config.yaml in cwd
      4. ~/.mnemos/config.yaml

    Env handling for ``mnemos.data_dir`` / ``mnemos.vault_path`` (per field,
    high → low; full contract in ``Settings.settings_customise_sources``):
      config-file value > canonical env (``MNEMOS_MNEMOS__DATA_DIR`` /
      ``MNEMOS_MNEMOS__VAULT_PATH``) > legacy short alias (``MNEMOS_DATA_DIR``
      / ``MNEMOS_VAULT__VAULT_PATH``, issue #139 compatibility) > ``.env``
      file > defaults.
    """
    if config_path is None:
        env_config = os.environ.get("MNEMOS_CONFIG", "")
        candidates: list[Path | None] = [
            Path(env_config) if env_config else None,
            Path.cwd() / "config.yaml",
            Path.home() / ".mnemos" / "config.yaml",
        ]
    else:
        candidates = [Path(config_path)]

    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return None


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from YAML config file with env var overrides.

    Search order:
      1. Explicit config_path argument
      2. MNEMOS_CONFIG env var
      3. ./config.yaml in cwd
      4. ~/.mnemos/config.yaml

    When no config file is found (zero-config profile), the built-in
    defaults apply: loopback-only API bind (127.0.0.1:8787), storage
    auto-created under ``~/.mnemos/``, FTS5 lexical recall active with or
    without an embedding provider (vector leg degrades non-fatally).
    See :func:`find_config_file` for programmatic detection.
    """
    found = find_config_file(config_path)
    config_data: dict[str, Any] = {}
    if found is not None:
        with found.open() as fh:
            config_data = yaml.safe_load(fh) or {}

    settings = Settings(**config_data)
    settings.resolve_paths()
    settings.migrate_layout()
    settings.apply_runtime_env()
    _warn_federation_mtls_pinning_off(settings)
    return settings


def _warn_federation_mtls_pinning_off(settings: Settings) -> None:
    """Warn when federation is active but a peer has mTLS pinning off.

    Federation is considered active when at least one peer is configured
    (``settings.federation.peers`` is non-empty — there is no separate
    ``enabled`` flag; a non-empty peers map is the activation signal).
    For each such peer whose ``mtls_cert_fingerprint`` is ``None``, emit
    a warning so the operator knows pinning is OFF (operator opt-out).
    This is non-breaking — a warning only, not a refusal. ADR-0016
    recommends pinning for networked deployments; the warning makes the
    opt-out visible at config-load time rather than silently accepted.
    """
    fed = settings.federation
    if not fed.peers:
        return
    for peer_id, peer in fed.peers.items():
        if peer.mtls_cert_fingerprint is None:
            logger.warning(
                "Peer '%s' has no mTLS cert fingerprint — pinning OFF "
                "(operator opt-out). Set mtls_cert_fingerprint to "
                "SHA-256 hex to enable pinning.",
                peer_id,
            )
