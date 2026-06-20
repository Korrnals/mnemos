"""Configuration management for Mnemos."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


class MnemosConfig(BaseModel):
    vault_path: Path = Path("~/mnemos-vault")
    data_dir: Path = Path("~/.mnemos")
    db_name: str = "mnemos.db"
    # M2: tag contract enforcement
    strict_tag_contract: bool = True


class EmbeddingConfig(BaseModel):
    provider: str = "chromadb"  # chromadb | onnx | ollama | sentence-transformers
    model: str = "all-MiniLM-L6-v2"  # HF model ID
    onnx_file: str = "onnx/model.onnx"  # ONNX filename within HF repo
    ollama_url: str = "http://localhost:11434"
    # M15.2: pin HF Hub downloads to a specific revision to mitigate supply-chain
    # risk (CWE-494 — download of code without integrity check). Override via
    # MNEMOS_EMBEDDING__HF_REVISION env var or config.yaml. The default below
    # pins the recommended revision for the default ONNX model; when changing
    # the `model` field, also update `hf_revision` to a matching pinned SHA/tag.
    hf_revision: str = "c9745ed1d7e3b0194c2e1c2b5d7e3e0b3c1c1c1c"  # all-MiniLM-L6-v2 ONNX


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


class RLMSettings(BaseModel):
    """Recursive Language Model (RLM) integration settings.

    RLM is an optional, opt-in capability that lets the synthesis pipeline
    delegate sub-problems to recursive sub-LLM calls inside a sandboxed
    Python REPL (see ADR 0008 for the GCW↔RLM pattern mapping). The toolkit
    (`rlm_toolkit`) is an optional dependency; when absent, `enabled=true`
    is rejected at runtime in `create_provider()`, not at config load time —
    pydantic cannot import the toolkit without making it a hard dependency.

    All defaults are offline-safe: `enabled=false`, `use_infiniretri=false`.
    Operators opt in explicitly via config or `MNEMOS_LLM__RLM__ENABLED=true`.
    """

    enabled: bool = False
    # RLM backend — which rlm_toolkit factory to use. ``ollama`` is the
    # offline default; ``openai`` / ``anthropic`` require cloud keys.
    backend: str = "ollama"
    # Root model for the RLM engine (per-backend naming: ollama tag, OpenAI
    # model id, Anthropic model id).
    model: str = "qwen2.5:3b"
    # Optional sub-model for recursive sub-LLM calls (cheaper/faster than
    # the root model). ``None`` lets rlm_toolkit pick its default.
    sub_model: str | None = None
    # Resilient mode — rlm_toolkit retries transient failures internally.
    resilient: bool = True
    # InfiniRetri — dynamic context retrieval mid-generation. Offline by
    # default (Tech Lead decision #3); operator opt-in only.
    use_infiniretri: bool = False
    # InfiniRetri token threshold — only activates above this context size.
    infiniretri_threshold: int = Field(default=100_000, ge=1, le=10_000_000)
    # Token threshold above which RLM decomposition kicks in. Below this,
    # the provider falls back to a plain completion call.
    threshold_tokens: int = Field(default=10_000, ge=1, le=1_000_000)
    # Sandbox guard — MUST stay true. Disabling the sandbox would let RLM
    # execute arbitrary code on the host; rejected at validation time.
    sandbox: bool = True
    # Whitelist of modules the sandboxed REPL may import. numpy is excluded
    # by default (Tech Lead decision #5) to keep the attack surface small;
    # operators can extend via config.
    allowed_imports: list[str] = Field(
        default_factory=lambda: [
            "re",
            "json",
            "math",
            "datetime",
            "collections",
            "itertools",
        ]
    )
    # Resource bounds for a single RLM synthesis call.
    max_iterations: int = Field(default=50, ge=1, le=1_000)
    max_subcalls: int = Field(default=100, ge=1, le=10_000)
    max_cost: float = Field(default=0.50, ge=0.0, le=100.0)
    max_depth: int = Field(default=3, ge=1, le=10)
    max_execution_time: int = Field(default=120, ge=1, le=3600)
    max_memory_mb: int = Field(default=512, ge=16, le=8192)
    # Truncate sub-LLM outputs to this many chars (prevents context blowup).
    truncate_output: int = Field(default=10_000, ge=100, le=1_000_000)
    # When True, an RLM failure falls back to the standard provider with
    # ``fallback_used=True``. When False, the LLMExecutionError propagates.
    fallback_on_failure: bool = True

    @field_validator("sandbox")
    @classmethod
    def _sandbox_must_be_enabled(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "rlm.sandbox cannot be False — disabling the sandbox would "
                "allow arbitrary host code execution. Keep sandbox=true."
            )
        return v


class LLMConfig(BaseModel):
    """Multi-provider LLM configuration (M4 synthesis workers, M10 context filter)."""

    provider: str = "ollama"  # anthropic | openai | azure_openai | ollama | gemini
    model: str = "qwen2.5:3b"
    # Ollama
    ollama_url: str = "http://localhost:11434"
    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = ""
    # Azure OpenAI
    azure_endpoint: str = ""
    azure_api_version: str = "2024-02-01"
    azure_deployment: str = ""
    # Anthropic
    anthropic_api_key: str = ""
    # Google Gemini
    gemini_api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096
    # RLM (Recursive Language Model) integration — optional, opt-in.
    # See ADR 0008 for the pattern mapping. The rlm_toolkit import guard is
    # enforced at runtime in create_provider(), not here, so pydantic does
    # not need the optional dependency at config load time.
    rlm: RLMSettings = RLMSettings()


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
    # M5: declarative policy rules (loaded from YAML or set programmatically)
    policies: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "env_prefix": "MNEMOS_",
        "env_nested_delimiter": "__",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    def resolve_paths(self) -> None:
        self.mnemos.vault_path = self.mnemos.vault_path.expanduser().resolve()
        self.mnemos.data_dir = self.mnemos.data_dir.expanduser().resolve()

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


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from YAML config file with env var overrides.

    Search order:
      1. Explicit config_path argument
      2. MNEMOS_CONFIG env var
      3. ./config.yaml in cwd
      4. ~/.mnemos/config.yaml
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

    config_data: dict[str, Any] = {}
    for candidate in candidates:
        if candidate and candidate.is_file():
            with candidate.open() as fh:
                config_data = yaml.safe_load(fh) or {}
            break

    settings = Settings(**config_data)
    settings.resolve_paths()
    settings.apply_runtime_env()
    return settings
