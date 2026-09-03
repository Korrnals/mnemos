"""NM-1c — NanoProvider: the bundled distilled embedder (ADR-0021).

Guards the new production default:

* the bundled artifact under ``mnemos/models/nano-embed-v1/`` is complete
  and its manifest pins the REAL weights hash (the manifest drifting
  from the shipped bytes would poison every fingerprint consumer);
* the provider loads from the shipped default config (no config edits
  needed — zero-config offline install), embeds 384-dim L2-normalized
  vectors in single and batch mode;
* legacy ``provider=chromadb`` configs migrate to nano with a loud
  deprecation warning instead of crashing the legacy install (the
  chromadb runtime dependency was removed in NM-1c);
* an unknown provider still fails loud at the boundary.
"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files as resource_files
from pathlib import Path

import pytest

from mnemos.config import EmbeddingConfig
from mnemos.embeddings import (
    NanoProvider,
    create_embedding_provider,
    nano_artifact_onnx_path,
)


@pytest.fixture(scope="module")
def provider() -> NanoProvider:
    """One ORT session for the whole module (construction is ~1s)."""
    return NanoProvider()


def _artifact_dir() -> Path:
    return Path(str(resource_files("mnemos") / "models" / "nano-embed-v1"))


# ── bundled artifact ──────────────────────────────────────────────────────────


def test_bundled_artifact_manifest_pins_real_weights() -> None:
    """manifest.json matches the shipped bytes (name, dims, sha256)."""
    artifact = _artifact_dir()
    assert (artifact / "tokenizer.json").is_file(), "tokenizer missing from the bundle"

    manifest = json.loads((artifact / "manifest.json").read_text())
    assert manifest["name"] == "nano-embed-v1"
    assert manifest["dimensions"] == 384
    assert manifest["max_seq"] == 256
    assert "Apache-2.0" in manifest["license"]

    digest = hashlib.sha256()
    with (artifact / "model.onnx").open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    assert manifest["weights_sha256"] == digest.hexdigest(), (
        "manifest weights_sha256 drifted from the shipped model.onnx"
    )


def test_default_config_builds_nano() -> None:
    """The shipped default is provider=nano / model=nano-embed-v1."""
    cfg = EmbeddingConfig()
    assert cfg.provider == "nano"
    assert cfg.model == "nano-embed-v1"
    built = create_embedding_provider(cfg)
    assert isinstance(built, NanoProvider)


# ── embeddings ────────────────────────────────────────────────────────────────


def test_embed_returns_384d_l2_normalized(provider: NanoProvider) -> None:
    vec = provider.embed("привет мир — nano embedder smoke")
    assert len(vec) == 384
    assert all(isinstance(x, float) for x in vec)
    norm = sum(x * x for x in vec) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-3), "graph-side L2 normalization missing"
    assert any(abs(x) > 1e-6 for x in vec), "degenerate all-zero embedding"


def test_embed_batch_shapes_and_stability(provider: NanoProvider) -> None:
    texts = ["первый текст", "second text", ""]
    rows = provider.embed_batch(texts)
    assert len(rows) == 3
    assert all(len(r) == 384 for r in rows)
    for row in rows:
        assert sum(x * x for x in row) ** 0.5 == pytest.approx(1.0, abs=1e-3)

    # same session + same input → deterministic (per-arch corridors rely on it)
    again = provider.embed(texts[0])
    assert again == rows[0]


def test_embed_truncates_overlong_input(provider: NanoProvider) -> None:
    """Boundary: input beyond max_seq 256 tokens still yields a valid vector."""
    long_text = "деталь сборки механизм " * 400  # well over 256 tokens
    vec = provider.embed(long_text)
    assert len(vec) == 384
    assert sum(x * x for x in vec) ** 0.5 == pytest.approx(1.0, abs=1e-3)


def test_custom_onnx_path_resolution(provider: NanoProvider) -> None:
    """An explicit .onnx path resolves its sibling tokenizer.json."""
    onnx_path = nano_artifact_onnx_path("nano-embed-v1")
    custom = NanoProvider(model=str(onnx_path))
    assert custom.dimension == 384
    assert custom.weights_sha256 == provider.weights_sha256
    assert custom.embed("same text") == provider.embed("same text")


# ── migration: legacy provider values (chromadb removed in NM-1c) ─────────────


@pytest.mark.parametrize("legacy", ["chromadb", "chroma", "default"])
def test_legacy_provider_migrates_to_nano(legacy: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="mnemos.embeddings"):
        built = create_embedding_provider(EmbeddingConfig(provider=legacy))
    assert isinstance(built, NanoProvider)
    assert any(
        "deprecated" in rec.message and "provider=nano" in rec.message for rec in caplog.records
    ), f"expected a deprecation warning for provider={legacy!r}"


def test_unknown_provider_fails_loud() -> None:
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        create_embedding_provider(EmbeddingConfig(provider="no-such-provider"))
