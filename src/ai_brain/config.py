"""Configuration management."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class BrainConfig(BaseModel):
    vault_path: Path = Path("~/brain-vault")
    data_dir: Path = Path("~/.ai-brain")


class EmbeddingConfig(BaseModel):
    provider: str = "chromadb"  # chromadb | onnx | ollama | sentence-transformers
    model: str = "all-MiniLM-L6-v2"  # HF model ID for onnx/ollama/sentence-transformers
    onnx_file: str = "onnx/model.onnx"  # ONNX filename within HF repo
    ollama_url: str = "http://localhost:11434"
    openai_api_key: str = ""


class SearchConfig(BaseModel):
    default_limit: int = 20
    hybrid_alpha: float = Field(default=0.7, ge=0.0, le=1.0)


class ApiConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787


class TelegramConfig(BaseModel):
    bot_token: str = ""
    allowed_users: list[int] = []


class McpConfig(BaseModel):
    transport: str = "stdio"


class WatcherConfig(BaseModel):
    paths: list[str] = []  # directories to watch
    ignore_dirs: list[str] = [
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ]
    extensions: list[str] = [
        ".md", ".py", ".js", ".ts", ".yaml", ".yml", ".toml",
        ".json", ".txt", ".rst", ".sh", ".css", ".html", ".sql",
    ]
    max_file_size_kb: int = 512
    auto_scan: bool = True  # scan existing files on startup


class Settings(BaseSettings):
    brain: BrainConfig = BrainConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    search: SearchConfig = SearchConfig()
    api: ApiConfig = ApiConfig()
    telegram: TelegramConfig = TelegramConfig()
    mcp: McpConfig = McpConfig()
    watcher: WatcherConfig = WatcherConfig()

    def resolve_paths(self) -> None:
        self.brain.vault_path = self.brain.vault_path.expanduser().resolve()
        self.brain.data_dir = self.brain.data_dir.expanduser().resolve()


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from YAML config file, with env var overrides."""
    if config_path is None:
        candidates = [
            Path("config.yaml"),
            Path("~/.ai-brain/config.yaml").expanduser(),
            Path(os.environ.get("AI_BRAIN_CONFIG", "")),
        ]
        for candidate in candidates:
            if candidate.is_file():
                config_path = candidate
                break

    data: dict = {}
    if config_path and Path(config_path).is_file():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    settings = Settings(**data)
    settings.resolve_paths()
    return settings


_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create global settings singleton."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
