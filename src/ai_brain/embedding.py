"""Embedding providers — generate vector embeddings from text.

Default: ChromaDB built-in ONNX embeddings (no torch required).
Optional: ONNX Hub (any HuggingFace model), Ollama, sentence-transformers.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

from ai_brain.config import EmbeddingConfig

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...


class ChromaDefaultProvider(EmbeddingProvider):
    """Uses ChromaDB's built-in ONNX embedding function (all-MiniLM-L6-v2).

    Zero extra dependencies — onnxruntime is already pulled by chromadb.
    Fast on CPU, ~80MB model.
    """

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        logger.info("Using ChromaDB default ONNX embeddings (all-MiniLM-L6-v2)")
        self._fn = DefaultEmbeddingFunction()
        self._dim = 384  # all-MiniLM-L6-v2 dimension

    def embed(self, text: str) -> list[float]:
        result = self._fn([text])
        return [float(x) for x in result[0]]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        results = self._fn(texts)
        return [[float(x) for x in r] for r in results]

    @property
    def dimension(self) -> int:
        return self._dim


class OllamaProvider(EmbeddingProvider):
    """Ollama embeddings — works on CPU, supports multilingual models."""

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


class ONNXHubProvider(EmbeddingProvider):
    """Load any ONNX embedding model from HuggingFace Hub.

    Zero extra dependencies — uses onnxruntime, tokenizers, numpy, huggingface_hub
    already installed via chromadb.

    Recommended models:
    - sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  (384d, multilingual, RU/EN)
    - BAAI/bge-small-en-v1.5                                       (384d, English, fast)
    - BAAI/bge-base-en-v1.5                                        (768d, English, better quality)
    - intfloat/multilingual-e5-small                                (384d, multilingual)

    For Intel AVX-512/VNNI CPUs, use quantized ONNX files:
        onnx_file="onnx/model_qint8_avx512_vnni.onnx"
    """

    def __init__(
        self, model_id: str, onnx_file: str = "onnx/model.onnx", max_length: int = 512
    ) -> None:
        import numpy as _np
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        logger.info("Loading ONNX model: %s (%s)", model_id, onnx_file)
        self._np = _np

        # Download ONNX model file
        try:
            model_path = hf_hub_download(model_id, onnx_file)
        except Exception:
            # Fallback: try model.onnx at repo root
            model_path = hf_hub_download(model_id, "model.onnx")

        # Download tokenizer
        tokenizer_path = hf_hub_download(model_id, "tokenizer.json")

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding(length=max_length)

        self._session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
        self._model_inputs = {inp.name for inp in self._session.get_inputs()}
        self._max_length = max_length

        # Detect output dimension
        test_output = self._infer(["test"])
        self._dim = int(test_output.shape[-1])
        logger.info("ONNX model ready: %s (dim=%d)", model_id, self._dim)

    def _infer(self, texts: list[str]) -> np.ndarray:
        np = self._np
        encodings = self._tokenizer.encode_batch(texts)

        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        inputs: dict = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self._model_inputs:
            inputs["token_type_ids"] = np.zeros_like(input_ids)

        outputs = self._session.run(None, inputs)

        # Mean pooling over token embeddings
        token_embeddings = outputs[0]  # (batch, seq_len, dim)
        mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
        sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
        sum_mask = np.sum(mask_expanded, axis=1)
        mean_pooled = sum_embeddings / np.maximum(sum_mask, 1e-9)

        # L2 normalize
        norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        return mean_pooled / np.maximum(norms, 1e-9)

    def embed(self, text: str) -> list[float]:
        result = self._infer([text])
        return [float(x) for x in result[0]]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        result = self._infer(texts)
        return [[float(x) for x in row] for row in result]

    @property
    def dimension(self) -> int:
        return self._dim


class SentenceTransformerProvider(EmbeddingProvider):
    """sentence-transformers — requires torch (~2GB). Install: pip install 'ai-brain[sentence-transformers]'"""

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install: pip install 'ai-brain[sentence-transformers]'"
            )

        logger.info("Loading sentence-transformers model: %s", model_name)
        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
        return [e.tolist() for e in embeddings]

    @property
    def dimension(self) -> int:
        return int(self._dim)


def create_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """Factory: create the appropriate embedding provider from config."""
    if config.provider == "chromadb":
        return ChromaDefaultProvider()
    elif config.provider == "onnx":
        return ONNXHubProvider(config.model, config.onnx_file)
    elif config.provider == "ollama":
        return OllamaProvider(config.model, config.ollama_url)
    elif config.provider == "sentence-transformers":
        return SentenceTransformerProvider(config.model)
    else:
        raise ValueError(
            f"Unknown embedding provider: {config.provider}. "
            "Available: chromadb, onnx, ollama, sentence-transformers"
        )
