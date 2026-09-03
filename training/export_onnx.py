"""NM-1b export: distilled student -> ONNX (static shapes 1x256) + int8 PTQ.

Inputs: a distillation run directory (``--run-dir``; the epoch checkpoint
is chosen with ``--epoch``, default: the highest recorded epoch in
``metrics.jsonl``) or a direct ``--model-dir``. Outputs into ``--out-dir``:

  model.onnx      student encoder, opset pinned via ONNX_OPSET, static
                  batch=1 x seq=MAX_SEQ, mean-pool + L2 baked into the graph
  tokenizer.json  the student tokenizer (fast-tokenizer export)
  manifest.json   {base_teacher, student_params, dataset_fingerprint,
                   weights_sha256 (post-export, over model.onnx bytes),
                   opset, created, license, quantized}

int8 static PTQ (``--int8``, default on): calibrate over a representative
batch drawn deterministically from the val split (``--calib-samples``).
If onnxruntime quantization is unavailable in the environment, the export
falls back to fp32 model.onnx and records ``"quantized": false`` in the
manifest (fail-honest, never a silently missing artefact).

The dataset fingerprint is read from ``<pairs-dir>/fingerprint.txt``
(written by training/dataset/prepare_dataset.py) or computed on the fly
from the train/val jsonl pair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRAIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRAIN_DIR.parent

# Opset pin: frozen at NM-1a; changing this is a manifest-visible event
# (onnx pin discipline — same rationale as the runtime ONNXHubProvider).
ONNX_OPSET = 15
# Static shape pin matching the runtime embedder contract.
MAX_SEQ = 256


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_fingerprint(pairs_dir: Path) -> str:
    """Fingerprint written by prepare_dataset.py, or computed from jsonl."""
    fp_file = pairs_dir / "fingerprint.txt"
    if fp_file.is_file():
        return fp_file.read_text(encoding="utf-8").strip()
    parts: list[str] = []
    for name in ("train.jsonl", "val.jsonl"):
        path = pairs_dir / name
        if path.is_file():
            parts.append(sha256_file(path))
    if not parts:
        raise SystemExit(
            f"error: no fingerprint.txt / train.jsonl / val.jsonl under {pairs_dir} "
            "(run training/dataset/prepare_dataset.py first)"
        )
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def count_params(model_dir: Path) -> int | None:
    """Student parameter count from config.json (hidden/layer geometry)."""
    cfg_path = model_dir / "config.json"
    if not cfg_path.is_file():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    hidden = cfg.get("hidden_size")
    layers = cfg.get("num_hidden_layers")
    vocab = cfg.get("vocab_size")
    if not (isinstance(hidden, int) and isinstance(layers, int) and isinstance(vocab, int)):
        return None
    # Transformer block estimate: embeddings + per-layer attention/FFN
    # + final LN. Close enough for the 45-60M budget check; the exact
    # count is asserted by eval, not here.
    embeddings = vocab * hidden + cfg.get("max_position_embeddings", 512) * hidden
    per_layer = 4 * hidden * hidden + 2 * hidden * (4 * hidden)
    return embeddings + layers * per_layer + 2 * hidden


def export_onnx_fp32(model_dir: Path, out_path: Path, tokenizer: Any) -> None:
    """Trace the encoder + mean-pool + L2 graph with static shapes 1x256."""
    import torch
    from transformers import AutoModel

    model = AutoModel.from_pretrained(model_dir)
    model.eval()

    # Dimension projection head (distill.py trains 312->384 alongside the
    # student when dims differ; retrained projector-only runs save it as
    # projector.pt next to the checkpoint). Without it the artifact would
    # export raw student dim and diverge from the trained geometry.
    projector_path = Path(model_dir) / "projector.pt"
    projector = None
    if projector_path.exists():
        import torch as _torch

        state = _torch.load(projector_path, map_location="cpu", weights_only=True)
        in_features = state["weight"].shape[1]
        out_features = state["weight"].shape[0]
        projector = _torch.nn.Linear(in_features, out_features, bias=False)
        projector.load_state_dict(state)
        projector.eval()
        print(f"projection head loaded: {in_features} -> {out_features}")

    class StudentEmbedder(torch.nn.Module):
        def __init__(self, inner: Any) -> None:
            super().__init__()
            self.inner = inner

        def forward(
            self,
            input_ids: torch.Tensor,
            attention_mask: torch.Tensor,
            token_type_ids: torch.Tensor,
        ) -> torch.Tensor:
            out = self.inner(
                input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids
            )
            mask = attention_mask.unsqueeze(-1).to(out.last_hidden_state.dtype)
            summed = torch.sum(out.last_hidden_state * mask, dim=1)
            counts = torch.clamp(mask.sum(dim=1), min=1e-9)
            pooled = summed / counts
            if projector is not None:
                pooled = projector(pooled)
            return torch.nn.functional.normalize(pooled, p=2, dim=-1)

    wrapper = StudentEmbedder(model)
    dummy = {
        "input_ids": torch.ones(1, MAX_SEQ, dtype=torch.int64),
        "attention_mask": torch.ones(1, MAX_SEQ, dtype=torch.int64),
        "token_type_ids": torch.zeros(1, MAX_SEQ, dtype=torch.int64),
    }
    # token_type_ids only when the tokenizer produces them (some models reject it)
    probe = tokenizer("probe", return_tensors="pt")
    forward_inputs = tuple(dummy[k] for k in ("input_ids", "attention_mask", "token_type_ids"))
    if "token_type_ids" not in probe:

        class StudentEmbedderNoTTI(StudentEmbedder):
            def forward(
                self, input_ids: torch.Tensor, attention_mask: torch.Tensor
            ) -> torch.Tensor:
                return super().forward(
                    input_ids, attention_mask, token_type_ids=torch.zeros_like(input_ids)
                )

        wrapper = StudentEmbedderNoTTI(model)
        forward_inputs = (dummy["input_ids"], dummy["attention_mask"])

    with torch.inference_mode():
        torch.onnx.export(
            wrapper,
            forward_inputs,
            str(out_path),
            opset_version=ONNX_OPSET,
            input_names=[
                "input_ids",
                "attention_mask",
                *(["token_type_ids"] if "token_type_ids" not in probe else []),
            ],
            output_names=["embedding"],
            dynamic_axes=None,  # static shapes: 1 x 256
            do_constant_folding=True,
        )

    _dedupe_output_tensor_names(out_path)


def _dedupe_output_tensor_names(onnx_path: Path) -> None:
    """Post-process: the torch exporter can emit TWO producers writing the
    graph-output tensor (e.g. Gather + Div both named ``embedding`` when
    output_names forces the name) — ORT rejects such graphs and quantization
    dies with "Duplicate definition of name". Rename the first producer's
    output to <name>_prenorm and rewire its consumers (except the final
    producer, which keeps producing the graph output).
    """
    from collections import Counter

    import onnx

    model = onnx.load(str(onnx_path), load_external_data=True)
    graph = model.graph
    tensor_writers = Counter()
    for node in graph.node:
        for out in node.output:
            tensor_writers[out] += 1
    dupes = [name for name, count in tensor_writers.items() if count > 1]
    for dup in dupes:
        producers = [n for n in graph.node if dup in n.output]
        first, last = producers[0], producers[-1]
        temp = f"{dup}_prenorm"
        first.output[list(first.output).index(dup)] = temp
        for node in graph.node:
            if node is last:
                continue
            rewired = [temp if i == dup else i for i in node.input]
            if rewired != list(node.input):
                del node.input[:]
                node.input.extend(rewired)
        last_inputs = [temp if i == dup else i for i in last.input]
        del last.input[:]
        last.input.extend(last_inputs)
    if dupes:
        onnx.save(model, str(onnx_path))
        print(f"dedup: renamed first producers of {dupes} -> *_prenorm")


def quantize_int8(onnx_path: Path, tokenizer: Any, calib_texts: list[str], out_path: Path) -> bool:
    """Static PTQ int8 with a deterministic representative batch.

    Returns True on success; False when quantization is unavailable
    (manifest records it instead of failing the whole export).
    """
    try:
        from onnxruntime.quantization import (
            CalibrationDataReader,
            QuantFormat,
            QuantType,
            quantize_static,
        )
    except ImportError:
        print("warn: onnxruntime quantization unavailable -> fp32 export only", file=sys.stderr)
        return False
    import numpy as np
    import onnx

    # torch.onnx.export with external weight data (model.onnx + model.onnx.data)
    # trips ORT quantization with "Duplicate definition of name" — consolidate
    # into a single in-memory model before quantizing.
    consolidated = onnx.load(str(onnx_path), load_external_data=True)
    onnx.save_model(consolidated, str(onnx_path))

    class TextCalibReader(CalibrationDataReader):
        def __init__(self, texts: list[str]) -> None:
            self._inputs = []
            # The exported graph has STATIC batch 1 — calibrate text-by-text
            # (a batch>1 reader trips "invalid dimensions for input").
            for text in texts:
                enc = tokenizer(
                    [text],
                    padding="max_length",
                    truncation=True,
                    max_length=MAX_SEQ,
                    return_tensors="np",
                )
                batch = {
                    "input_ids": enc["input_ids"].astype("int64"),
                    "attention_mask": enc["attention_mask"].astype("int64"),
                }
                if "token_type_ids" in enc:
                    batch["token_type_ids"] = enc["token_type_ids"].astype("int64")
                self._inputs.append(batch)
            self._idx = 0

        def get_next(self) -> dict[str, np.ndarray] | None:
            if self._idx >= len(self._inputs):
                return None
            out = self._inputs[self._idx]
            self._idx += 1
            return out

    try:
        quantize_static(
            str(onnx_path),
            str(out_path),
            calibration_data_reader=TextCalibReader(calib_texts),
            quant_format=QuantFormat.QDQ,
            per_channel=False,
            weight_type=QuantType.QInt8,
            activation_type=QuantType.QInt8,
        )
    except Exception as exc:  # quantization is best-effort at NM-1b; fp32 stays
        print(f"warn: int8 quantization failed ({exc}) -> fp32 export kept", file=sys.stderr)
        return False
    return True


def export_tokenizer_json(tokenizer: Any, out_path: Path) -> None:
    """Write tokenizer.json (fast-tokenizer backend serialization)."""
    vocab = getattr(tokenizer, "backend_tokenizer", None)
    if vocab is None:
        raise SystemExit(
            "error: student tokenizer is not a fast tokenizer; cannot export tokenizer.json"
        )
    vocab.to_str()  # validates serializability before writing
    out_path.write_text(vocab.to_str(), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NM-1b ONNX export + int8 PTQ + manifest.")
    p.add_argument(
        "--run-dir", type=Path, default=None, help="distillation run dir (epoch<N> checkpoints)"
    )
    p.add_argument(
        "--model-dir", type=Path, default=None, help="explicit checkpoint dir (overrides --run-dir)"
    )
    p.add_argument(
        "--epoch",
        type=int,
        default=None,
        help="which epoch checkpoint to export (default: highest)",
    )
    p.add_argument(
        "--pairs-dir",
        type=Path,
        default=TRAIN_DIR / "data",
        help="dir with fingerprint.txt/train/val jsonl",
    )
    p.add_argument("--out-dir", type=Path, default=None, help="default: <run-dir>/onnx")
    p.add_argument(
        "--calib-samples", type=int, default=200, help="representative batch size for int8 PTQ"
    )
    p.add_argument("--no-int8", action="store_true", help="skip int8 PTQ, export fp32 only")
    p.add_argument(
        "--teacher", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    p.add_argument("--license", default="Apache-2.0", help="license of the student artefact")
    p.add_argument("--student-name", default=None, help="student HF id recorded in the manifest")
    args = p.parse_args(argv)

    try:
        from transformers import AutoTokenizer
    except ImportError:
        print("error: transformers not installed — see training/requirements.txt", file=sys.stderr)
        return 3

    model_dir = args.model_dir
    if model_dir is None:
        if args.run_dir is None:
            p.error("either --run-dir or --model-dir is required")
        run_dir = args.run_dir
        if args.epoch is not None:
            model_dir = run_dir / f"epoch{args.epoch}"
            if not model_dir.is_dir():
                raise SystemExit(f"error: checkpoint not found: {model_dir}")
        else:
            metrics = run_dir / "metrics.jsonl"
            if not metrics.is_file():
                p.error(f"no metrics.jsonl under {run_dir}; pass --epoch or --model-dir")
            last_epoch = 0
            for line in metrics.read_text(encoding="utf-8").splitlines():
                try:
                    last_epoch = max(last_epoch, int(json.loads(line)["epoch"]))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            if last_epoch == 0:
                p.error(f"metrics.jsonl under {run_dir} has no epochs; pass --epoch or --model-dir")
            model_dir = run_dir / f"epoch{last_epoch}"

    out_dir = args.out_dir or (args.run_dir / "onnx" if args.run_dir else model_dir / "onnx")
    out_dir.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    fp32_path = out_dir / "model.onnx"
    print(
        f"exporting fp32 ONNX: {model_dir} -> {fp32_path} (opset={ONNX_OPSET}, shape=1x{MAX_SEQ})"
    )
    export_onnx_fp32(model_dir, fp32_path, tokenizer)

    final_path = fp32_path
    quantized = False
    if not args.no_int8:
        calib_texts = read_val_texts(args.pairs_dir, args.calib_samples)
        int8_path = out_dir / "model.int8.onnx"
        if quantize_int8(fp32_path, tokenizer, calib_texts, int8_path):
            final_path = int8_path
            quantized = True
            fp32_path.unlink(missing_ok=True)
            final_path = out_dir / "model.onnx"
            int8_path.rename(final_path)

    export_tokenizer_json(tokenizer, out_dir / "tokenizer.json")

    params = count_params(model_dir)
    manifest = {
        "base_teacher": args.teacher,
        "student_params": params,
        "student_name": args.student_name or model_dir.name,
        "dataset_fingerprint": dataset_fingerprint(args.pairs_dir),
        "weights_sha256": sha256_file(final_path),
        "opset": ONNX_OPSET,
        "max_seq": MAX_SEQ,
        "quantized": quantized,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "license": args.license,
        "source_checkpoint": str(model_dir),
        "git_commit": git_commit(),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def read_val_texts(pairs_dir: Path, limit: int) -> list[str]:
    """Representative calibration batch: first N val rows (deterministic)."""
    val = pairs_dir / "val.jsonl"
    if not val.is_file():
        raise SystemExit(f"error: {val} missing — run training/dataset/prepare_dataset.py first")
    texts: list[str] = []
    with val.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = obj.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
            if len(texts) >= limit:
                break
    if not texts:
        raise SystemExit(f"error: no usable calibration texts in {val}")
    return texts


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


if __name__ == "__main__":
    raise SystemExit(main())
