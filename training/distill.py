"""NM-1b student distillation (45-60M) — KD on cosine similarity to a teacher.

Teacher: a pretrained multilingual MiniLM-class sentence embedding model
(license Apache-2.0/MIT — see training/README.md candidate table). The
student starts from a smaller pretrained multilingual checkpoint
(``--student-init``) and is trained so that its L2-normalised embedding
matches the teacher's embedding space: per-pair KD loss is MSE on the
cosine similarity between (student_i, teacher_i) after temperature
scaling of the cosine logits. Mean-pooling + L2 — the exact geometry the
runtime embedder contract uses (EmbeddingProvider, ADR-0021).

Runs on CPU (torch threads) or Intel iGPU via IPEX when importable.
Deterministic: explicit seeds for python/random/torch(+cuda if present),
``torch.use_deterministic_algorithms``; dataloader order is a seeded
generator. Checkpoints every epoch; a per-epoch metrics line (avg KD
loss + student-vs-teacher cos-sim on val) is appended to
``<out-dir>/metrics.jsonl``.

Dry-run/smoke: ``--max-pairs 100 --epochs 1`` finishes quickly even on
CPU. Downloading the teacher happens here (owner's NM-1b run) — never in
CI (ADR-0021 anti-scope).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any

TRAIN_DIR = Path(__file__).resolve().parent
DEFAULT_TEACHER = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Student init: a real sub-100M pretrained multilingual checkpoint. The
# earlier L6-v2 id does not exist on HF (HTTP 404). rubert-tiny2 (29.4M, MIT,
# RU-strong / EN-weak) is the pilot init — KD on the RU+EN corpus transfers
# the teacher's EN+cross-lingual geometry into it; 29M is below the 45-60M
# NM-1 target and grows with dataset scale (NM-1b+).
DEFAULT_STUDENT_INIT = "cointegrated/rubert-tiny2"
DEFAULT_SEED = 42
DEFAULT_MAX_LENGTH = 256


# ── Determinism ──────────────────────────────────────────────────────────────


def set_determinism(seed: int) -> None:
    """Seed every source of randomness we rely on; pin deterministic algos."""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    import numpy as np

    np.random.seed(seed)
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    # cuDNN determinism (harmless on CPU builds)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


# ── Model loading ────────────────────────────────────────────────────────────


def load_encoder(model_id: str, max_length: int, *, device: str) -> tuple[Any, Any]:
    """Load a HF encoder for mean-pooling embedding use.

    Returns (tokenizer, model) where model outputs token embeddings in
    ``last_hidden_state``. Uses AutoTokenizer/AutoModel — sentencepiece
    is only required if the chosen checkpoint needs it.
    """
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id).to(device)
    model.eval()
    if (
        hasattr(model, "config")
        and getattr(model.config, "max_position_embeddings", max_length) < max_length
    ):
        max_length = int(model.config.max_position_embeddings)
    return tokenizer, model


def mean_pool(last_hidden_state: Any, attention_mask: Any) -> Any:
    """Mask-aware mean pooling -> (batch, dim) unnormalised."""
    import torch

    mask = attention_mask.unsqueeze(-1).to(last_hidden_state.dtype)
    summed = torch.sum(last_hidden_state * mask, dim=1)
    counts = torch.clamp(mask.sum(dim=1), min=1e-9)
    return summed / counts


def l2_normalise(x: Any) -> Any:
    import torch

    return torch.nn.functional.normalize(x, p=2, dim=-1)


# ── Data ─────────────────────────────────────────────────────────────────────


def read_pairs(path: str, max_pairs: int | None) -> list[str]:
    """Load training texts from a prepare_dataset.py jsonl file."""
    if not Path(path).is_file():
        raise SystemExit(f"error: pairs file not found: {path}")
    out: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"error: malformed jsonl line in {path}: {exc}") from exc
            text = obj.get("text")
            if isinstance(text, str) and text.strip():
                out.append(text.strip())
            if max_pairs and len(out) >= max_pairs:
                break
    if not out:
        raise SystemExit(f"error: no usable pairs in {path}")
    return out


# ── KD loss ──────────────────────────────────────────────────────────────────


def kd_cosine_loss(
    student_emb: Any,
    teacher_emb: Any,
    temperature: float,
    projector: Any = None,
) -> Any:
    """MSE on cosine similarity, temperature-scaled.

    Per pair i: sim_s(i) = cos(student_i, student_mean_batch_reference)
    is NOT used — the canonical KD signal for embedding models is
    pairwise-alignment to the teacher on the SAME text: cos(student_i,
    teacher_i). We minimise MSE(student_i - teacher_i) on L2-normalised
    vectors, which equals a scaled cosine-alignment objective; the
    temperature divides the residual before the square, matching the
    temperature-scale convention in the plan.

    When student dim != teacher dim, `projector` (a trainable Linear) maps
    the student embedding to the teacher dimension before normalisation —
    the projector is part of the student and ships with the checkpoint.
    """
    import torch

    if projector is not None:
        student_emb = projector(student_emb)
    student_n = l2_normalise(student_emb)
    teacher_n = l2_normalise(teacher_emb)
    residual = (student_n - teacher_n) / temperature
    return torch.mean(torch.sum(residual * residual, dim=-1))


# ── Train loop ───────────────────────────────────────────────────────────────


def encode_batch(
    model: Any,
    tokenizer: Any,
    texts: list[str],
    device: str,
    max_length: int,
    requires_grad: bool = False,
) -> tuple[Any, Any]:
    import torch

    enc = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    enc = {k: v.to(device) for k, v in enc.items()}
    # Teacher inference and evaluation run under no_grad; the student leg in
    # the KD train loop must NOT (backward needs a live graph).
    if requires_grad:
        out = model(**enc)
    else:
        with torch.no_grad():
            out = model(**enc)
    return out.last_hidden_state, enc["attention_mask"]


def evaluate_cosine(
    student: Any,
    teacher: Any,
    tokenizer_s: Any,
    tokenizer_t: Any,
    val_texts: list[str],
    device: str,
    max_length: int,
    batch_size: int,
    projector: Any = None,
) -> dict[str, float]:
    """Student-vs-teacher cosine similarity over val texts (mean/median)."""
    import numpy as np
    import torch

    sims: list[float] = []
    student.eval()
    for start in range(0, len(val_texts), batch_size):
        chunk = val_texts[start : start + batch_size]
        enc_s = tokenizer_s(
            chunk, padding=True, truncation=True, max_length=max_length, return_tensors="pt"
        )
        enc_s = {k: v.to(device) for k, v in enc_s.items()}
        enc_t = tokenizer_t(
            chunk, padding=True, truncation=True, max_length=max_length, return_tensors="pt"
        )
        enc_t = {k: v.to(device) for k, v in enc_t.items()}
        with torch.no_grad():
            s = mean_pool(student(**enc_s).last_hidden_state, enc_s["attention_mask"])
            if projector is not None:
                s = projector(s)
            s = l2_normalise(s)
            t = l2_normalise(mean_pool(teacher(**enc_t).last_hidden_state, enc_t["attention_mask"]))
        sims.extend((s * t).sum(dim=-1).tolist())
    arr = np.asarray(sims, dtype=np.float64)
    return {
        "n": int(arr.size),
        "cos_sim_mean": float(arr.mean()),
        "cos_sim_median": float(np.median(arr)),
        "cos_sim_p05": float(np.quantile(arr, 0.05)),
        "cos_sim_min": float(arr.min()),
    }


def resolve_device(arg: str) -> str:
    """--device auto|cpu|ipex|cuda → a concrete torch device string."""
    import torch

    if arg == "cuda" or (arg == "auto" and torch.cuda.is_available()):
        return "cuda"
    if arg in ("ipex", "auto"):
        try:  # Intel iGPU via IPEX if installed
            import intel_extension_for_pytorch as ipex  # noqa: F401

            if hasattr(torch, "xpu") and torch.xpu.is_available():
                return "xpu"
        except ImportError:
            pass
    return "cpu"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NM-1b student distillation (KD on cos-sim).")
    p.add_argument(
        "--teacher", default=DEFAULT_TEACHER, help="HF id of the frozen teacher (Apache/MIT)"
    )
    p.add_argument(
        "--student-init", default=DEFAULT_STUDENT_INIT, help="HF id of the pretrained student init"
    )
    p.add_argument("--pairs", required=True, help="train jsonl from prepare_dataset.py")
    p.add_argument("--val", required=True, help="val jsonl from prepare_dataset.py")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument(
        "--start-epoch",
        type=int,
        default=1,
        help="first epoch number to run (manager resume: last completed + 1)",
    )
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument(
        "--temperature",
        type=float,
        default=0.05,
        help="KD temperature scale (divides the residual)",
    )
    p.add_argument(
        "--threads", type=int, default=None, help="torch CPU threads (default: half the cores)"
    )
    p.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--max-pairs", type=int, default=None, help="cap on train pairs (dry-run/smoke)")
    p.add_argument("--out-dir", type=Path, default=TRAIN_DIR / "runs" / "nm1b")
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "ipex", "cuda"])
    args = p.parse_args(argv)

    # Manager contract check BEFORE the heavy imports: `train.py stop` writes
    # this flag; honoring it here means the loop never even starts when the
    # operator stopped the run before launch (torch need not be installed).
    if (args.out_dir / "STOP").exists():
        print("stop-flag present before start — nothing to do (remove STOP to run)")
        return 0

    try:
        import torch
    except ImportError:
        print("error: torch is not installed — see training/requirements.txt", file=sys.stderr)
        return 3

    threads = args.threads or max(1, (os.cpu_count() or 2) // 2)
    torch.set_num_threads(threads)
    set_determinism(args.seed)
    device = resolve_device(args.device)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"distill: teacher={args.teacher} student-init={args.student_init} "
        f"device={device} threads={threads}"
    )

    tokenizer_s, student = load_encoder(args.student_init, args.max_length, device=device)
    tokenizer_t, teacher = load_encoder(args.teacher, args.max_length, device=device)
    teacher.eval()
    for param in teacher.parameters():
        param.requires_grad_(False)

    # Dimension projection: when the student embedding dim differs from the
    # teacher's, a trainable Linear head maps student -> teacher dim (part of
    # the student, saved with every checkpoint via save_pretrained on the
    # wrapped module below).
    import torch as _torch

    probe = student(_torch.zeros((1, args.max_length), dtype=_torch.long))
    student_dim = probe.last_hidden_state.shape[-1]
    probe_t = teacher(_torch.zeros((1, args.max_length), dtype=_torch.long))
    teacher_dim = probe_t.last_hidden_state.shape[-1]
    projector = None
    if student_dim != teacher_dim:
        projector = _torch.nn.Linear(student_dim, teacher_dim, bias=False).to(device)
        print(f"projection head: {student_dim} -> {teacher_dim}")

    train_texts = read_pairs(args.pairs, args.max_pairs)
    val_texts = read_pairs(args.val, None)
    print(f"pairs: train={len(train_texts)} val={len(val_texts)}")

    params = list(student.parameters())
    if projector is not None:
        params += list(projector.parameters())
    optimizer = _torch.optim.AdamW(params, lr=args.lr)
    metrics_path = args.out_dir / "metrics.jsonl"
    order_rng = random.Random(args.seed)

    for epoch in range(args.start_epoch, args.epochs + 1):
        if (args.out_dir / "STOP").exists():
            # Manager contract: `train.py stop` writes this flag; the loop
            # exits cleanly at the epoch boundary, checkpoints stay intact.
            print(f"stop-flag detected before epoch {epoch} — exiting cleanly")
            break
        epoch_order = list(train_texts)
        order_rng.shuffle(epoch_order)  # deterministic per-epoch order
        student.train()
        total_loss, n_batches = 0.0, 0
        for start in range(0, len(epoch_order), args.batch_size):
            chunk = epoch_order[start : start + args.batch_size]
            hs, mask_s = encode_batch(
                student, tokenizer_s, chunk, device, args.max_length, requires_grad=True
            )
            student_pooled = mean_pool(hs, mask_s)
            ht, mask_t = encode_batch(teacher, tokenizer_t, chunk, device, args.max_length)
            with torch.no_grad():
                teacher_pooled = mean_pool(ht, mask_t)
            loss = kd_cosine_loss(
                student_pooled, teacher_pooled, args.temperature, projector=projector
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            loss_value = float(loss.detach())
            if math.isnan(loss_value) or math.isinf(loss_value):
                print(
                    f"error: non-finite KD loss at epoch {epoch}, batch {n_batches}",
                    file=sys.stderr,
                )
                return 4
            total_loss += loss_value
            n_batches += 1

        eval_stats = evaluate_cosine(
            student,
            teacher,
            tokenizer_s,
            tokenizer_t,
            val_texts,
            device,
            args.max_length,
            args.batch_size,
            projector=projector,
        )
        record = {
            "epoch": epoch,
            "avg_kd_loss": total_loss / max(1, n_batches),
            "val_cosine": eval_stats,
        }
        with metrics_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        print(
            f"epoch {epoch}: avg_kd_loss={record['avg_kd_loss']:.6f} "
            f"cos_sim_mean={eval_stats['cos_sim_mean']:.4f}"
        )

        ckpt_dir = args.out_dir / f"epoch{epoch}"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        student.save_pretrained(ckpt_dir)
        tokenizer_s.save_pretrained(ckpt_dir)
        # The projector is co-adapted with the backbone — saving it apart
        # from the checkpoint loses the joint (pilot lesson 2026-09-03).
        if projector is not None:
            torch.save(projector.state_dict(), ckpt_dir / "projector.pt")
        print(f"checkpoint: {ckpt_dir}")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
