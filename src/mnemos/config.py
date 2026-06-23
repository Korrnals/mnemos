"""Configuration management for Mnemos."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings

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
    logging: LoggingConfig = LoggingConfig()
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
        if (
            new_vault == default_new_vault
            and old_vault.is_dir()
            and not new_vault.exists()
        ):
            new_vault.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_vault), str(new_vault))
            actions.append(f"vault: {old_vault} → {new_vault}")
            logger.info("migrate_layout: moved vault %s → %s", old_vault, new_vault)

        return actions


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
    settings.migrate_layout()
    settings.apply_runtime_env()
    return settings
