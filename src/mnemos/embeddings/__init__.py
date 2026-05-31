"""Embeddings layer for Mnemos.

Uses local ONNX MiniLM-class models by default (privacy + offline).
Forked from ai-brain's embedding.py.

Providers:
  - ChromaDefaultProvider   — zero-dep ONNX via chromadb (default)
  - ONNXHubProvider         — any HuggingFace ONNX model
  - OllamaProvider          — local Ollama embeddings
  - SentenceTransformerProvider — via sentence-transformers (optional dep)
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

import numpy as np

from mnemos.config import EmbeddingConfig

logger = logging.getLogger(__name__)


# ── Abstract base ─────────────────────────────────────────────────────────────


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...


# ── ChromaDB default (zero extra deps) ────────────────────────────────────────


class ChromaDefaultProvider(EmbeddingProvider):
    """Uses ChromaDB's built-in ONNX embedding function (all-MiniLM-L6-v2).

    Zero extra dependencies — onnxruntime is pulled by chromadb.
    ~80 MB model, 384-dim output. Fast on CPU.
    """

    def __init__(self) -> None:
        _n = os.environ.get("ONNX_NUM_THREADS") or os.environ.get("OMP_NUM_THREADS") or "4"
        os.environ.setdefault("OMP_NUM_THREADS", _n)
        os.environ.setdefault("MKL_NUM_THREADS", _n)
        os.environ.setdefault("OPENBLAS_NUM_THREADS", _n)
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        logger.info("Using ChromaDB default ONNX embeddings (all-MiniLM-L6-v2)")
        self._fn = DefaultEmbeddingFunction()
        self._dim = 384

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in self._fn([text])[0]]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in row] for row in self._fn(texts)]

    @property
    def dimension(self) -> int:
        return self._dim


# ── Ollama ────────────────────────────────────────────────────────────────────


class OllamaProvider(EmbeddingProvider):
    """Embeddings via a local Ollama instance."""

    def __init__(self, model_name: str, base_url: str) -> None:
        import ollama as _ollama

        logger.info("Using Ollama embeddings: %s @ %s", model_name, base_url)
        self._client = _ollama.Client(host=base_url)
        self._model = model_name
        self._dim: int | None = None

    def embed(self, text: str) -> list[float]:
        response = self._client.embed(model=self._model, input=text)
        vec = response["embeddings"][0]
        if self._dim is None:
            self._dim = len(vec)
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embed(model=self._model, input=texts)
        vecs = response["embeddings"]
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return vecs

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self.embed("test")
        return self._dim or 768


# ── ONNX Hub ──────────────────────────────────────────────────────────────────


class ONNXHubProvider(EmbeddingProvider):
    """Load any ONNX embedding model from HuggingFace Hub.

    Recommended models:
    - sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  (384d, RU+EN)
    - BAAI/bge-small-en-v1.5                                       (384d, EN fast)
    - intfloat/multilingual-e5-small                                (384d, multilingual)
    """

    def __init__(
        self,
        model_id: str,
        onnx_file: str = "onnx/model.onnx",
        max_length: int = 512,
    ) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        logger.info("Loading ONNX model: %s (%s)", model_id, onnx_file)

        try:
            model_path = hf_hub_download(model_id, onnx_file)
        except Exception:
            model_path = hf_hub_download(model_id, "model.onnx")

        tokenizer_path = hf_hub_download(model_id, "tokenizer.json")
        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding(length=max_length)

        n_threads = max(
            1,
            int(
                os.environ.get("MNEMOS_ORT_THREADS")
                or os.environ.get("OMP_NUM_THREADS")
                or "4"
            ),
        )
        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = n_threads
        sess_opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            model_path,
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self._model_inputs = {inp.name for inp in self._session.get_inputs()}
        self._max_length = max_length
        test = self._infer(["test"])
        self._dim = int(test.shape[-1])
        logger.info("ONNX model ready: %s (dim=%d)", model_id, self._dim)

    def _infer(self, texts: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        inputs: dict = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._model_inputs:
            inputs["token_type_ids"] = np.zeros_like(input_ids)
        outputs = self._session.run(None, inputs)
        token_embs = outputs[0]  # (batch, seq, dim)
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        summed = np.sum(token_embs * mask, axis=1) / np.maximum(np.sum(mask, axis=1), 1e-9)
        norms = np.linalg.norm(summed, axis=1, keepdims=True)
        return summed / np.maximum(norms, 1e-9)

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in self._infer([text])[0]]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in row] for row in self._infer(texts)]

    @property
    def dimension(self) -> int:
        return self._dim


# ── sentence-transformers ─────────────────────────────────────────────────────


class SentenceTransformerProvider(EmbeddingProvider):
    """sentence-transformers backend (optional dependency)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        logger.info("Using sentence-transformers: %s", model_name)
        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension() or 384

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts).tolist()

    @property
    def dimension(self) -> int:
        return self._dim


# ── factory ───────────────────────────────────────────────────────────────────


def create_embedding_provider(cfg: EmbeddingConfig) -> EmbeddingProvider:
    """Instantiate the configured embedding provider."""
    provider = cfg.provider.lower()
    if provider in ("chromadb", "chroma", "default"):
        return ChromaDefaultProvider()
    if provider == "ollama":
        return OllamaProvider(cfg.model, cfg.ollama_url)
    if provider in ("onnx", "onnxhub"):
        return ONNXHubProvider(cfg.model, onnx_file=cfg.onnx_file)
    if provider in ("sentence-transformers", "st"):
        return SentenceTransformerProvider(cfg.model)
    raise ValueError(
        f"Unknown embedding provider: {provider!r}. "
        "Valid: chromadb, ollama, onnx, sentence-transformers"
    )

