"""NM-1b eval jig: distilled student vs teacher vs chromadb-MiniLM.

Axes (nano-model-plan.md §5 "Инфраструктура обучения"):
  (a) cos-similarity student-vs-teacher on the val split:
      mean / median / p05 / min + a bucketed distribution;
  (b) retrieval-proxy on the judged corpus (benchmarks/corpus, 48 golden
      queries with slug judgments): query + entries are embedded by the
      model under test, entries ranked by cosine, recall@5 computed
      against the judged expectation — compared against a BM25 lexical
      reference computed over the same corpus with the same judgments.

Outputs (to --report-dir, default benchmarks/reports — gitignored):
  nm1-eval-<ts>.json  full metrics
  nm1-eval-<ts>.md    markdown summary with the comparative table
                      (student / teacher / chromadb-MiniLM / BM25)

Model surfaces:
  --onnx-dir    exported artefact (model.onnx + tokenizer.json) — the
                student AS SHIPPED (int8/fp32 per manifest);
  --run-dir     optional distillation run dir for the raw (pre-export)
                student checkpoint and the teacher id in the manifest;
  --teacher     HF id used for the teacher leg (needs transformers+torch;
                skipped with a recorded "skipped" reason when the lib is
                absent — the jig never crashes the report on a missing
                optional stack).

No network: models must already be local (HF cache / --onnx-dir).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRAIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRAIN_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

K_RECALL = 5
MAX_SEQ = 256


# ── BM25 lexical reference ───────────────────────────────────────────────────

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(_norm(text))


def bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Plain BM25 over word tokens (dependency-free)."""
    doc_tokens = [tokenize(d) for d in docs]
    n_docs = len(docs)
    avgdl = sum(len(t) for t in doc_tokens) / max(1, n_docs)
    q_terms = tokenize(query)
    scores = [0.0] * n_docs
    for term in set(q_terms):
        df = sum(1 for t in doc_tokens if term in t)
        if df == 0:
            continue
        idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        for i, t in enumerate(doc_tokens):
            tf = t.count(term)
            if tf == 0:
                continue
            denom = tf + k1 * (1 - b + b * len(t) / max(avgdl, 1e-9))
            scores[i] += idf * tf * (k1 + 1) / denom
    return scores


# ── Model backends ───────────────────────────────────────────────────────────


class OnnxEmbedder:
    """ONNX artefact embedder (model.onnx + tokenizer.json, static 1x256)."""

    def __init__(self, onnx_dir: Path) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        tok_path = onnx_dir / "tokenizer.json"
        model_path = onnx_dir / "model.onnx"
        if not tok_path.is_file() or not model_path.is_file():
            raise SystemExit(f"error: {onnx_dir} must contain model.onnx and tokenizer.json")
        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._tokenizer.enable_truncation(max_length=MAX_SEQ)
        self._tokenizer.enable_padding(length=MAX_SEQ)
        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = max(1, int(os.environ.get("MNEMOS_ORT_THREADS") or "4"))
        self._session = ort.InferenceSession(
            str(model_path), sess_options=sess_opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {inp.name for inp in self._session.get_inputs()}

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # The exported graph has STATIC batch 1 — embed text-by-text.
        np = self._np
        rows: list[list[float]] = []
        for text in texts:
            encodings = self._tokenizer.encode_batch([text])
            e = encodings[0]
            # enable_padding(length=MAX_SEQ) already pads ids to MAX_SEQ and
            # produces a correct attention_mask — use it directly (a manual
            # mask from len(e.ids) would be all-ones and pollute mean-pooling
            # with pad tokens; review F1 of PR #218).
            ids = np.array([e.ids[:MAX_SEQ]], dtype="int64")
            if ids.shape[1] < MAX_SEQ:
                pad = np.zeros((1, MAX_SEQ - ids.shape[1]), dtype="int64")
                ids = np.concatenate([ids, pad], axis=1)
            mask = np.array([e.attention_mask[:MAX_SEQ]], dtype="int64")
            if mask.shape[1] < MAX_SEQ:
                mask = np.concatenate(
                    [mask, np.zeros((1, MAX_SEQ - mask.shape[1]), dtype="int64")], axis=1
                )
            inputs = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                inputs["token_type_ids"] = np.zeros((1, MAX_SEQ), dtype="int64")
            emb = self._session.run(None, inputs)[0]
            rows.append([float(x) for x in emb[0]])
        return rows


class HFEmbedder:
    """torch/transformers embedder (teacher or raw student checkpoint)."""

    def __init__(self, model_id: str, max_length: int = MAX_SEQ) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(model_id)
        self._model = AutoModel.from_pretrained(model_id)
        self._model.eval()
        self._max_length = max_length

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        torch = self._torch
        enc = self._tokenizer(
            texts, padding=True, truncation=True, max_length=self._max_length, return_tensors="pt"
        )
        with torch.no_grad():
            out = self._model(**enc)
        mask = enc["attention_mask"].unsqueeze(-1).to(out.last_hidden_state.dtype)
        summed = torch.sum(out.last_hidden_state * mask, dim=1)
        counts = torch.clamp(mask.sum(dim=1), min=1e-9)
        pooled = summed / counts
        pooled = torch.nn.functional.normalize(pooled, p=2, dim=-1)
        return [[float(x) for x in row] for row in pooled]


class ChromaMiniLMEmbedder:
    """chromadb built-in all-MiniLM-L6-v2 (the current production contour)."""

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        self._fn = DefaultEmbeddingFunction()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in row] for row in self._fn(texts)]


# ── Metrics ──────────────────────────────────────────────────────────────────


def cosine_matrix_stats(emb_pairs: list[tuple[list[float], list[float]]]) -> dict:
    """cos-sim stats between paired embeddings + a bucketed distribution."""
    import numpy as np

    sims = np.array(
        [
            float(np.dot(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)))
            / max(
                1e-9,
                float(np.linalg.norm(np.asarray(a, dtype=np.float64)))
                * float(np.linalg.norm(np.asarray(b, dtype=np.float64))),
            )
            for a, b in emb_pairs
        ],
        dtype=np.float64,
    )
    buckets = {"0.95-1.00": 0, "0.90-0.95": 0, "0.80-0.90": 0, "<0.80": 0}
    for s in sims:
        if s >= 0.95:
            buckets["0.95-1.00"] += 1
        elif s >= 0.90:
            buckets["0.90-0.95"] += 1
        elif s >= 0.80:
            buckets["0.80-0.90"] += 1
        else:
            buckets["<0.80"] += 1
    n = max(1, sims.size)
    return {
        "n": int(sims.size),
        "mean": float(sims.mean()),
        "median": float(np.median(sims)),
        "p05": float(np.quantile(sims, 0.05)),
        "min": float(sims.min()),
        "distribution": {k: v / n for k, v in buckets.items()},
    }


def recall_at_k(ranked: list[str], expected: frozenset[str], k: int) -> float:
    if not expected:
        return 0.0
    return len(set(ranked[:k]) & set(expected)) / len(expected)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def run_retrieval_proxy(
    embedder: Any,
    entries: list[tuple[str, str]],  # (text, slug)
    queries: list[tuple[str, frozenset[str]]],
) -> dict:
    """recall@5 for the embedder ranking; BM25 reference on the same corpus."""
    doc_texts = [t for t, _ in entries]
    slugs = [s for _, s in entries]
    doc_embs = embedder.embed_batch(doc_texts)

    recalls: list[float] = []
    bm25_recalls: list[float] = []
    for q_text, q_expected in queries:
        if not q_expected:
            continue
        q_emb = embedder.embed_batch([q_text])[0]
        ranked = [
            slugs[i] for i in sorted(range(len(slugs)), key=lambda i: -_dot(q_emb, doc_embs[i]))
        ]
        recalls.append(recall_at_k(ranked, q_expected, K_RECALL))

        # BM25 lexical reference on the same corpus (corpus is small —
        # 84 entries — so a per-query full scan is fine).
        bm25_full = bm25_scores(q_text, doc_texts)
        bm25_ranked = [slugs[i] for i in sorted(range(len(slugs)), key=lambda i: -bm25_full[i])]
        bm25_recalls.append(recall_at_k(bm25_ranked, q_expected, K_RECALL))

    return {
        f"recall@{K_RECALL}": sum(recalls) / max(1, len(recalls)),
        "judged_queries": len(recalls),
        f"bm25_recall@{K_RECALL}": sum(bm25_recalls) / max(1, len(bm25_recalls)),
    }


# ── Driver ───────────────────────────────────────────────────────────────────


def load_judged_corpus() -> tuple[list[tuple[str, str]], list[tuple[str, frozenset[str]]]]:
    from benchmarks.corpus.corpus import CORPUS
    from benchmarks.corpus.queries import GOLDEN_QUERIES

    entries = [(e.content, e.slug) for e in CORPUS if e.status == "published"]
    queries = [(q.text, q.expected) for q in GOLDEN_QUERIES if q.expected]
    return entries, queries


def load_val_texts(pairs_dir: Path, limit: int) -> list[str]:
    val = pairs_dir / "val.jsonl"
    if not val.is_file():
        raise SystemExit(f"error: {val} missing — run training/dataset/prepare_dataset.py first")
    texts: list[str] = []
    for line in val.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj.get("text"), str):
            texts.append(obj["text"])
        if len(texts) >= limit:
            break
    return texts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="NM-1b eval jig (cos-sim + retrieval-proxy).")
    p.add_argument(
        "--onnx-dir",
        type=Path,
        default=None,
        help="exported artefact dir (model.onnx + tokenizer.json)",
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="distillation run dir (optional; for raw student)",
    )
    p.add_argument("--pairs-dir", type=Path, default=TRAIN_DIR / "data")
    p.add_argument(
        "--teacher", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    p.add_argument(
        "--student-hf", default=None, help="raw student HF id/checkpoint for the cos-sim axis"
    )
    p.add_argument("--val-samples", type=int, default=500)
    p.add_argument("--report-dir", type=Path, default=REPO_ROOT / "benchmarks" / "reports")
    p.add_argument("--label", default="nm1-eval")
    args = p.parse_args(argv)

    report: dict[str, Any] = {
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": args.label,
        "teacher": args.teacher,
        "k": K_RECALL,
    }

    # ── retrieval-proxy (student ONNX vs BM25; optional teacher/chromadb) ──
    entries, queries = load_judged_corpus()
    table: dict[str, Any] = {}

    if args.onnx_dir is not None:
        embedder = OnnxEmbedder(args.onnx_dir)
        proxy = run_retrieval_proxy(embedder, entries, queries)
        table["student-onnx"] = proxy
    else:
        print("warn: --onnx-dir not given; student retrieval-proxy skipped", file=sys.stderr)

    # chromadb-MiniLM contour (current production embedder) if available
    try:
        table["chromadb-minilm"] = run_retrieval_proxy(ChromaMiniLMEmbedder(), entries, queries)
    except ImportError:
        table["chromadb-minilm"] = {"skipped": "chromadb not installed"}

    # Teacher leg via transformers (optional stack)
    try:
        teacher = HFEmbedder(args.teacher)
        table["teacher"] = run_retrieval_proxy(teacher, entries, queries)
    except ImportError as exc:
        table["teacher"] = {"skipped": f"transformers/torch unavailable: {exc}"}

    # ── cos-sim axis: student vs teacher on val (needs both HF-able) ──────
    if args.student_hf is not None and args.run_dir is not None:
        try:
            student = HFEmbedder(args.student_hf)
            teacher_leg = HFEmbedder(args.teacher)
            texts = load_val_texts(args.pairs_dir, args.val_samples)
            if texts:
                se = student.embed_batch(texts)
                te = teacher_leg.embed_batch(texts)
                report["cosine_student_vs_teacher"] = cosine_matrix_stats(
                    list(zip(se, te, strict=True))
                )
        except ImportError as exc:
            report["cosine_student_vs_teacher"] = {
                "skipped": f"transformers/torch unavailable: {exc}"
            }
    else:
        report["cosine_student_vs_teacher"] = {
            "skipped": "needs --student-hf and --run-dir (transformers stack)"
        }

    report["retrieval_proxy"] = table

    # report files
    args.report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    json_path = args.report_dir / f"{args.label}-{ts}.json"
    md_path = args.report_dir / f"{args.label}-{ts}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_md(report), encoding="utf-8")
    print(f"report: {json_path}")
    print(f"report: {md_path}")
    print(render_md(report))
    return 0


def render_md(report: dict[str, Any]) -> str:
    header = (
        f"Created: {report.get('created')} · "
        f"teacher: `{report.get('teacher')}` · recall@{report.get('k')}"
    )
    lines = [
        f"# NM-1 eval — {report.get('label', 'eval')}",
        "",
        header,
        "## Retrieval-proxy (judged corpus, recall@5)",
        "",
        "| Model | recall@5 | BM25 recall@5 | judged | note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, m in report.get("retrieval_proxy", {}).items():
        if "skipped" in m:
            lines.append(f"| {name} | — | — | — | {m['skipped']} |")
        else:
            lines.append(
                f"| {name} | {m.get('recall@5', 0):.3f} | {m.get('bm25_recall@5', 0):.3f} "
                f"| {m.get('judged_queries', 0)} | |"
            )
    cos = report.get("cosine_student_vs_teacher", {})
    lines += ["", "## Cos-sim student vs teacher (val)"]
    if "skipped" in cos:
        lines += ["", f"skipped: {cos['skipped']}"]
    else:
        stats_line = (
            f"n={cos.get('n')} · mean={cos.get('mean', 0):.4f} "
            f"· median={cos.get('median', 0):.4f} · p05={cos.get('p05', 0):.4f} "
            f"· min={cos.get('min', 0):.4f}"
        )
        lines += [
            "",
            stats_line,
            "| bucket | share |",
            "| --- | --- |",
        ]
        for bucket, share in cos.get("distribution", {}).items():
            lines.append(f"| {bucket} | {share:.3f} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
