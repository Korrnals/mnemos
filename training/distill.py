"""NM-1b student distillation (45-60M) — KD on cosine similarity to a teacher.

Teacher: default ``Qwen/Qwen3-Embedding-0.6B`` (Apache-2.0, MTEB-MM
64.33, dim 1024, MRL 32-1024); the MiniLM-class teacher of earlier
rounds stays supported via ``--teacher``. The student starts from a
smaller pretrained multilingual checkpoint (``--student-init``) and is
trained so that its L2-normalised embedding matches the teacher's
embedding space: per-pair KD loss is MSE on the cosine similarity
between (student_i, teacher_i) after temperature scaling of the cosine
logits. Mean-pooling + L2 — the exact geometry the runtime embedder
contract uses (EmbeddingProvider, ADR-0021); Qwen3-Embedding teachers
switch to last-token pooling automatically (its official geometry).

Round-3 additions:

* ``--teacher-instruct-template`` — Qwen3-Embedding requires an
  instruction prefix on the QUERY side ("Instruct: <task>\\nQuery: <t>");
  corpus texts are formatted through the template before the teacher
  leg. Without the flag the teacher input is the bare text (MiniLM
  behaviour).
* ``--mrl-dims 64,128,256,384`` — Matryoshka heads: the KD loss is a
  weighted sum over several truncated dims at once (both student and
  teacher slices re-normalised), so ONE model serves four dims at
  inference (truncate + re-normalise). Default ``384`` = plain mode.

Runs on CPU (torch threads) or Intel iGPU via IPEX when importable.
Deterministic: explicit seeds for python/random/torch(+cuda if present),
``torch.use_deterministic_algorithms``; dataloader order is a seeded
generator. Checkpoints every epoch; a per-epoch metrics line (avg KD
loss + student-vs-teacher cos-sim on val, per-MRL-dim when active) is
appended to ``<out-dir>/metrics.jsonl``.

Dry-run/smoke: ``--max-pairs 100 --epochs 1`` finishes quickly even on
CPU. Downloading the teacher happens here (owner's NM-1b run) — never in
CI (ADR-0021 anti-scope).
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any

TRAIN_DIR = Path(__file__).resolve().parent
# Round-3 default teacher (NM-1b+): Qwen3-Embedding-0.6B — Apache-2.0,
# MTEB-MM 64.33, native dim 1024 with Matryoshka (MRL) support 32-1024.
# Two mechanical consequences handled below:
#   (a) it is a causal-LM embedder — official pooling is LAST TOKEN with
#       left padding, not mean pooling (auto-detected, --teacher-pooling);
#   (b) queries need an instruction prefix ("Instruct: ...\nQuery: ...")
#       while documents go bare — at distillation time the corpus texts
#       take the query side of that contract (--teacher-instruct-template).
DEFAULT_TEACHER = "Qwen/Qwen3-Embedding-0.6B"
# KD target dim, decoupled from the teacher dim: an MRL-trained teacher
# can be truncated to --embed-dim and re-normalised into a valid
# lower-dim target (Qwen3-Embedding model card, Matryoshka section) —
# the student ships 384d by default, matching the runtime contract.
DEFAULT_EMBED_DIM = 384
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


# ── Teacher-side formatting / pooling (round 3) ──────────────────────────────


def format_teacher_input(text: str, template: str | None) -> str:
    """Apply the teacher instruction template to one corpus text.

    Two accepted forms (both without str.format — no brace-escaping traps):

    * template contains ``{text}`` — free-form template, the placeholder
      is replaced with the text, e.g. ``"Instruct: <task>\\nQuery: {text}"``;
    * template WITHOUT ``{text}`` — treated as the task instruction and
      wrapped into the canonical Qwen3-Embedding shape
      ``"Instruct: <template>\\nQuery: <text>"``.

    ``None``/empty template -> bare text (MiniLM-class teacher behaviour).
    """
    if not template:
        return text
    if "{text}" in template:
        return template.replace("{text}", text)
    return f"Instruct: {template}\nQuery: {text}"


def detect_teacher_pooling(model: Any, model_id: str) -> str:
    """'last_token' for Qwen3-Embedding-class causal embedders, else 'mean'."""
    cfg = getattr(model, "config", None)
    model_type = str(getattr(cfg, "model_type", "") or "")
    archs = [str(a) for a in (getattr(cfg, "architectures", None) or [])]
    if (
        model_type == "qwen3"
        or "Qwen3ForCausalLM" in archs
        or "Qwen3-Embedding" in model_id
    ):
        return "last_token"
    return "mean"


def last_token_pool(last_hidden_state: Any, attention_mask: Any) -> Any:
    """Official Qwen3-Embedding pooling: the final non-pad token vector.

    Correct under both padding sides: with left padding the last position
    is always the sequence end; with right padding the per-row length
    comes from the attention mask.
    """
    import torch

    left_padded = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padded:
        return last_hidden_state[:, -1]
    lengths = attention_mask.sum(dim=1) - 1
    batch = last_hidden_state.shape[0]
    return last_hidden_state[torch.arange(batch, device=last_hidden_state.device), lengths]


def pool_teacher(last_hidden_state: Any, attention_mask: Any, mode: str) -> Any:
    """Teacher pooling dispatch: 'last_token' (Qwen3) or 'mean' (BERT-class)."""
    if mode == "last_token":
        return last_token_pool(last_hidden_state, attention_mask)
    return mean_pool(last_hidden_state, attention_mask)


# ── MRL (Matryoshka) heads ────────────────────────────────────────────────────


def parse_mrl_dims(spec: str, *, full_dim: int | None = None) -> list[int]:
    """Parse '--mrl-dims 64,128,256,384' into a validated ascending list.

    Raises ValueError (loud, pre-torch) on empty/negative/duplicate/
    non-ascending entries, empty segments between commas (``64,,128``),
    or dims beyond ``full_dim`` (the embed dim).
    """
    parts = [part.strip() for part in spec.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError(f"--mrl-dims is empty or has empty segments: {spec!r}")
    try:
        dims = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"--mrl-dims is not a comma-separated int list: {spec!r}") from exc
    if any(d <= 0 for d in dims):
        raise ValueError(f"--mrl-dims entries must be positive: {dims}")
    if any(b <= a for a, b in itertools.pairwise(dims)):
        raise ValueError(f"--mrl-dims must be strictly ascending: {dims}")
    if full_dim is not None and dims[-1] > full_dim:
        raise ValueError(f"--mrl-dims max {dims[-1]} exceeds the embed dim {full_dim}")
    return dims


def parse_mrl_weights(spec: str | None, n_dims: int) -> list[float]:
    """Normalised per-dim loss weights (uniform when spec is None).

    '--mrl-weights 4,2,1,1' -> [0.5, 0.25, 0.125, 0.125]; the vector always
    sums to 1 so the total loss scale is comparable across configurations.
    """
    if spec is None:
        return [1.0 / n_dims] * n_dims
    parts = [part.strip() for part in spec.split(",")]
    if any(not part for part in parts):
        raise ValueError(f"--mrl-weights has empty segments: {spec!r}")
    try:
        weights = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"--mrl-weights is not a comma-separated float list: {spec!r}") from exc
    if len(weights) != n_dims:
        raise ValueError(f"--mrl-weights needs exactly {n_dims} entries, got {len(weights)}")
    if any(w < 0 for w in weights) or sum(weights) <= 0:
        raise ValueError(f"--mrl-weights must be non-negative and sum > 0: {weights}")
    total = sum(weights)
    return [w / total for w in weights]


def aggregate_mrl_losses(per_dim: list[Any], weights: list[float]) -> Any:
    """Weighted sum of per-dim losses.

    Duck-typed on purpose: works for python floats (torch-free unit
    tests) and for torch tensors (``0 + tensor`` is valid torch).
    """
    if len(per_dim) != len(weights):
        raise ValueError(f"per_dim/weights length mismatch: {len(per_dim)} != {len(weights)}")
    return sum(loss * w for loss, w in zip(per_dim, weights, strict=True))


def mrl_kd_loss(
    student_emb: Any,
    teacher_emb: Any,
    dims: list[int],
    temperature: float,
    projector: Any = None,
    weights: list[float] | None = None,
) -> Any:
    """Weighted sum of KD losses over truncated dims (Matryoshka heads).

    For each d in ``dims``: truncate BOTH the (projected) student vector
    and the teacher vector to the first d components, L2-renormalise the
    slices, apply the plain temperature-scaled KD residual. Truncating an
    MRL-trained teacher (Qwen3-Embedding, MRL 32-1024) yields a valid
    lower-dim teacher target; with a plain (non-MRL) teacher keep
    ``dims == [embed_dim]`` (the default) so the slice is the full vector
    and the loss equals the round-2 KD objective exactly.
    """
    if projector is not None:
        student_emb = projector(student_emb)
    if weights is None:
        weights = parse_mrl_weights(None, len(dims))
    elif sum(weights) <= 0:
        raise ValueError(f"mrl weights must sum > 0: {weights}")
    else:
        # Self-contained normalisation: the loss scale must not depend on
        # how the caller spelled the weights.
        total = sum(weights)
        weights = [w / total for w in weights]
    per_dim = [
        kd_cosine_loss(student_emb[..., :d], teacher_emb[..., :d], temperature)
        for d in dims
    ]
    return aggregate_mrl_losses(per_dim, weights)


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
    *,
    teacher_template: str | None = None,
    teacher_pooling: str = "mean",
    embed_dim: int | None = None,
    mrl_dims: list[int] | None = None,
) -> dict[str, Any]:
    """Student-vs-teacher cosine similarity over val texts (mean/median).

    Teacher inputs go through the same instruct-template + pooling as in
    training (consistency contract: the eval measures exactly the taught
    geometry). The teacher vector is truncated to ``embed_dim`` and
    re-normalised when the teacher is wider than the student target
    (MRL slice). When ``mrl_dims`` has more than one entry, a per-dim
    mean cos-sim is added under ``by_dim``.
    """
    import numpy as np
    import torch

    sims: list[float] = []
    per_dim_sims: dict[int, list[float]] = {d: [] for d in (mrl_dims or [])}
    student.eval()
    for start in range(0, len(val_texts), batch_size):
        chunk = val_texts[start : start + batch_size]
        enc_s = tokenizer_s(
            chunk, padding=True, truncation=True, max_length=max_length, return_tensors="pt"
        )
        enc_s = {k: v.to(device) for k, v in enc_s.items()}
        teacher_texts = [format_teacher_input(t, teacher_template) for t in chunk]
        enc_t = tokenizer_t(
            teacher_texts, padding=True, truncation=True, max_length=max_length,
            return_tensors="pt",
        )
        enc_t = {k: v.to(device) for k, v in enc_t.items()}
        with torch.no_grad():
            s = mean_pool(student(**enc_s).last_hidden_state, enc_s["attention_mask"])
            if projector is not None:
                s = projector(s)
            s_full = l2_normalise(s)
            t_pooled = pool_teacher(
                teacher(**enc_t).last_hidden_state, enc_t["attention_mask"], teacher_pooling
            )
            if embed_dim is not None:
                t_pooled = t_pooled[..., :embed_dim]
            t_full = l2_normalise(t_pooled)
        sims.extend((s_full * t_full).sum(dim=-1).tolist())
        for d in per_dim_sims:
            s_d = l2_normalise(s_full[..., :d])
            t_d = l2_normalise(t_full[..., :d])
            per_dim_sims[d].extend((s_d * t_d).sum(dim=-1).tolist())
    arr = np.asarray(sims, dtype=np.float64)
    stats: dict[str, Any] = {
        "n": int(arr.size),
        "cos_sim_mean": float(arr.mean()),
        "cos_sim_median": float(np.median(arr)),
        "cos_sim_p05": float(np.quantile(arr, 0.05)),
        "cos_sim_min": float(arr.min()),
    }
    if len(per_dim_sims) > 1:
        stats["by_dim"] = {
            str(d): float(np.asarray(v, dtype=np.float64).mean())
            for d, v in per_dim_sims.items()
        }
    return stats


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
        "--teacher-instruct-template",
        default=None,
        help=(
            "instruction prefix for the teacher's QUERY side (Qwen3-Embedding "
            "requires it): either a free-form template with a {text} "
            "placeholder, or a bare task description which is wrapped into "
            "'Instruct: <task>\\nQuery: <text>'. Default: none (bare text, "
            "MiniLM-class teachers)"
        ),
    )
    p.add_argument(
        "--teacher-pooling",
        default="auto",
        choices=["auto", "mean", "last_token"],
        help="teacher pooling: auto detects Qwen3-Embedding (last_token) vs BERT-class (mean)",
    )
    p.add_argument(
        "--embed-dim",
        type=int,
        default=DEFAULT_EMBED_DIM,
        help=(
            "student output dim / KD target dim (default 384, the runtime "
            "contract). An MRL-trained teacher wider than this is truncated "
            "and re-normalised into the target space"
        ),
    )
    p.add_argument(
        "--mrl-dims",
        default=str(DEFAULT_EMBED_DIM),
        help=(
            "Matryoshka dims trained simultaneously, comma-separated, "
            "ascending, <= --embed-dim (e.g. '64,128,256,384'). "
            "Default '384' = plain single-dim mode"
        ),
    )
    p.add_argument(
        "--mrl-weights",
        default=None,
        help="per-dim loss weights, comma-separated (default: uniform); normalised to sum 1",
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

    # Round-3 argument validation BEFORE the heavy imports (torch need not
    # be installed for a loud, actionable CLI error).
    try:
        mrl_dims = parse_mrl_dims(args.mrl_dims, full_dim=args.embed_dim)
        mrl_weights = parse_mrl_weights(args.mrl_weights, len(mrl_dims))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

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

    teacher_pool = (
        detect_teacher_pooling(teacher, args.teacher)
        if args.teacher_pooling == "auto"
        else args.teacher_pooling
    )
    if teacher_pool == "last_token":
        # Last-token pooling is only position-correct under left padding.
        tokenizer_t.padding_side = "left"
    print(
        f"teacher: {args.teacher} (pooling={teacher_pool}, "
        f"instruct-template={'on' if args.teacher_instruct_template else 'off'})"
    )

    # Dimension projection: when the student embedding dim differs from the
    # KD target dim (--embed-dim, decoupled from the teacher dim since
    # round 3), a trainable Linear head maps student -> embed-dim (part of
    # the student, saved with every checkpoint via save_pretrained on the
    # wrapped module below).
    import torch as _torch

    probe = student(_torch.zeros((1, args.max_length), dtype=_torch.long))
    student_dim = probe.last_hidden_state.shape[-1]
    probe_t = teacher(_torch.zeros((1, args.max_length), dtype=_torch.long))
    teacher_dim = probe_t.last_hidden_state.shape[-1]
    if args.embed_dim > teacher_dim:
        print(
            f"error: --embed-dim {args.embed_dim} exceeds the teacher dim {teacher_dim} "
            "(the KD target is the teacher vector; it can be truncated, never widened)",
            file=sys.stderr,
        )
        return 2
    projector = None
    if student_dim != args.embed_dim:
        projector = _torch.nn.Linear(student_dim, args.embed_dim, bias=False).to(device)
        print(f"projection head: {student_dim} -> {args.embed_dim}")
    if teacher_dim != args.embed_dim:
        print(
            f"MRL teacher slice: teacher {teacher_dim} -> {args.embed_dim} "
            "(truncated + re-normalised per dim; requires an MRL-trained teacher)"
        )
    if len(mrl_dims) > 1:
        print(f"mrl heads: {mrl_dims} (weights={[round(w, 4) for w in mrl_weights]})")

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
            # Teacher leg: corpus texts take the teacher's QUERY side
            # (instruct template) and the teacher's native pooling.
            teacher_texts = [format_teacher_input(t, args.teacher_instruct_template) for t in chunk]
            ht, mask_t = encode_batch(
                teacher, tokenizer_t, teacher_texts, device, args.max_length
            )
            with torch.no_grad():
                teacher_pooled = pool_teacher(ht, mask_t, teacher_pool)
                # KD target: the teacher vector in the student's embed
                # space (MRL slice when the teacher is wider).
                teacher_target = teacher_pooled[..., : args.embed_dim]
            loss = mrl_kd_loss(
                student_pooled,
                teacher_target,
                mrl_dims,
                args.temperature,
                projector=projector,
                weights=mrl_weights,
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
            teacher_template=args.teacher_instruct_template,
            teacher_pooling=teacher_pool,
            embed_dim=args.embed_dim,
            mrl_dims=mrl_dims,
        )
        record = {
            "epoch": epoch,
            "avg_kd_loss": total_loss / max(1, n_batches),
            "val_cosine": eval_stats,
            "embed_dim": args.embed_dim,
            "mrl_dims": mrl_dims,
        }
        by_dim = eval_stats.get("by_dim")
        dim_note = (
            " " + " ".join(f"dim{d}={v:.4f}" for d, v in sorted(by_dim.items()))
            if by_dim
            else ""
        )
        with metrics_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        print(
            f"epoch {epoch}: avg_kd_loss={record['avg_kd_loss']:.6f} "
            f"cos_sim_mean={eval_stats['cos_sim_mean']:.4f}{dim_note}"
        )

        ckpt_dir = args.out_dir / f"epoch{epoch}"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        student.save_pretrained(ckpt_dir)
        tokenizer_s.save_pretrained(ckpt_dir)
        # The projector is co-adapted with the backbone — saving it apart
        # from the checkpoint loses the joint (pilot lesson 2026-09-03).
        if projector is not None:
            torch.save(projector.state_dict(), ckpt_dir / "projector.pt")
        # MRL contract: export reads the trained dims back from the
        # checkpoint (see export_onnx.py mrl_dims detection).
        (ckpt_dir / "mrl_dims.json").write_text(
            json.dumps({"embed_dim": args.embed_dim, "mrl_dims": mrl_dims}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"checkpoint: {ckpt_dir}")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
