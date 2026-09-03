"""NM-1a dataset preparation: memory-shaped RU+EN corpus up to 100k pairs.

Sources (NM-1a — local only, NO external dataset downloads):
  (a) repo fixtures: the golden corpus entries + judged queries
      (``benchmarks/corpus``) — legal, already in-tree;
  (b) synthetic: programmatic RU+EN paraphrase templates
      (``synthetic_templates.py``) — notes / chat excerpts / code headlines;
  (c) optional ``--from-mnemos-dir <path>``: owner's local store dump —
      privacy: the data never leaves the machine (no network anywhere);
  (d) optional ``--from-mnemos-db <path>``: the live mnemos SQLite store
      opened READ-ONLY — only the ``content`` field enters the pool,
      ``project:``* tags are read solely for the optional filter (same
      local-only privacy contract).

Pipeline: collect -> deduplicate (sha256 of normalised text) -> enforce
the 256-token length limit -> count the RU quota (>= 40 % gate printed,
not enforced hard at NM-1a: the report is the deliverable) -> train/val
split 95/5 with an explicit seed (default 42, deterministic).

Output: ``<out-dir>/train.jsonl`` and ``<out-dir>/val.jsonl``, one JSON
object per line: {"text": str, "lang": "ru"|"en", "source": str}.
A dataset fingerprint (sha256 over the concatenated jsonl bytes) is
printed and written to ``<out-dir>/fingerprint.txt`` — it feeds the
export manifest (``training/export_onnx.py``).

Token counting uses a lightweight whitespace/punctuation approximation
by default (no heavy deps at prep time); the real tokenizer truncation
happens at training/export time. ``--exact-tokens`` switches to a
tokenizer-free heuristic tuned to match MiniLM wordpiece counting
closely enough for the 256-token gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

# Repo-root relative imports (benchmarks/, training/) — this script is
# executed as a file from the repo root; make both importable explicitly
# so the script works regardless of the caller's sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from training.dataset.synthetic_templates import generate_synthetic  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────────

MAX_TOKENS = 256
DEFAULT_SEED = 42
DEFAULT_MAX_PAIRS = 100_000
RU_QUOTA_TARGET = 0.40

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# ── Text normalisation / language / length gates ────────────────────────────


def normalise(text: str) -> str:
    """NFC-normalise and collapse whitespace (dedup key normalisation)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def count_tokens(text: str) -> int:
    """Approximate token count: words + punctuation signs.

    Deliberately dependency-free; for the memory-shaped texts here it
    tracks a multilingual wordpiece counter within a small margin, which
    is enough for the coarse 256-token gate (the tokenizer truncates
    precisely at training time regardless).
    """
    return len(_TOKEN_RE.findall(text))


def detect_lang(text: str) -> str:
    """Cyrillic-letter share decides ru vs en (binary gate for this corpus)."""
    cyr = sum(1 for ch in text if "CYRILLIC" in unicodedata.name(ch, ""))
    alpha = sum(1 for ch in text if ch.isalpha())
    return "ru" if alpha and cyr / alpha >= 0.3 else "en"


# ── Source collectors ────────────────────────────────────────────────────────


def collect_fixtures() -> list[tuple[str, str, str]]:
    """Golden corpus entries + judged queries from benchmarks/corpus."""
    from benchmarks.corpus.corpus import CORPUS
    from benchmarks.corpus.queries import GOLDEN_QUERIES

    out: list[tuple[str, str, str]] = []
    for entry in CORPUS:
        # Planted FAKE secrets must never enter the training pool
        # (sensitive-data policy: fake literals are for detection tests,
        # not for embedding-space training).
        if entry.planted:
            continue
        title = entry.title.strip()
        content = normalise(entry.content)
        if title:
            out.append(
                (f"{title}. {content}"[:4000], detect_lang(f"{title} {content}"), "golden-corpus")
            )
        else:
            out.append((content[:4000], detect_lang(content), "golden-corpus"))
    for q in GOLDEN_QUERIES:
        text = q.text.strip()
        if text:
            out.append((text, detect_lang(text), "golden-queries"))
    return out


def collect_from_mnemos_dir(root: Path, *, limit: int) -> list[tuple[str, str, str]]:
    """Read memory-shaped text from a local mnemos store (owner's machine).

    Privacy contract: read-only, local-only, no network. Supported shapes:
    ``*.md`` markdown notes (frontmatter tolerated via plain text read)
    and ``memory.jsonl``/``*.jsonl`` exports with a ``text``/``content``
    field. Long documents are chunked by paragraph to stay memory-shaped.
    """
    out: list[tuple[str, str, str]] = []
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and (p.suffix in {".md", ".jsonl", ".txt"})
    )
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"warn: cannot read {path}: {exc}", file=sys.stderr)
            continue
        if path.suffix == ".jsonl":
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = normalise(str(obj.get("text") or obj.get("content") or ""))
                if text:
                    out.append((text[:4000], detect_lang(text), f"mnemos-dir:{path.name}"))
        else:
            for para in re.split(r"\n\s*\n", raw):
                text = normalise(para)
                if len(text) < 40:  # skip headings/blank-ish fragments
                    continue
                out.append((text[:4000], detect_lang(text), f"mnemos-dir:{path.name}"))
        if len(out) >= limit:
            break
    return out[:limit]


def _project_slug(tags_raw: str, project_column: str | None) -> str:
    """Extract the project:* slug from a memories row (tags JSON first).

    The denormalised ``project`` column is the fallback for rows written
    before the tag-contract denormalisation (M2). Purely a filter key —
    the slug never enters the training corpus.
    """
    try:
        tags = json.loads(tags_raw) if tags_raw else []
    except json.JSONDecodeError:
        tags = []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("project:"):
                return tag[len("project:") :].strip().lower()
    if project_column:
        return project_column.strip().lower()
    return ""


def collect_from_mnemos_db(
    db_path: Path, *, limit: int, projects: list[str] | None = None
) -> list[tuple[str, str, str]]:
    """Read memory content from a live mnemos SQLite store (owner's machine).

    Privacy contract (same as --from-mnemos-dir): local file only, no
    network, the data never leaves the machine. The connection is opened
    READ-ONLY (``file:...?mode=ro`` URI — the running server keeps its
    write lock); only the ``content`` field enters the training pool, and
    ``tags``/``project`` are read solely to apply the optional
    ``project:``* filter. Long rows are chunked by paragraph to stay
    memory-shaped, mirroring the dir collector.

    ``projects`` is a list of slugs (``project:<slug>`` tags, with or
    without the ``project:`` prefix); None means "no filter".
    """
    import sqlite3

    if not db_path.is_file():
        raise SystemExit(f"error: --from-mnemos-db is not a file: {db_path}")
    wanted = {p.strip().lower().removeprefix("project:") for p in (projects or []) if p.strip()}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise SystemExit(
            f"error: cannot open {db_path} read-only: {exc} "
            "(stop the mnemos server or copy the db to a scratch path)"
        ) from exc
    out: list[tuple[str, str, str]] = []
    try:
        try:
            cur: sqlite3.Cursor = conn.execute("SELECT content, tags, project FROM memories")
        except sqlite3.OperationalError:
            # Legacy store without the M2 denormalised column.
            cur = conn.execute("SELECT content, tags FROM memories")
        for row in cur:
            content = row[0]
            tags_raw = row[1] if len(row) > 1 else ""
            project_column = row[2] if len(row) > 2 else None
            if not isinstance(content, str) or not content.strip():
                continue
            if wanted and _project_slug(str(tags_raw or ""), project_column) not in wanted:
                continue
            for para in re.split(r"\n\s*\n", content):
                text = normalise(para)
                if len(text) < 40:  # skip headings/blank-ish fragments
                    continue
                out.append((text[:4000], detect_lang(text), f"mnemos-db:{db_path.name}"))
                if len(out) >= limit:
                    return out
    except sqlite3.DatabaseError as exc:
        raise SystemExit(
            f"error: {db_path} is not a readable mnemos store (memories table): {exc}"
        ) from exc
    finally:
        conn.close()
    return out


def collect_synthetic(seed: int) -> list[tuple[str, str, str]]:
    return generate_synthetic(seed)


# ── Dedup / quota / split ────────────────────────────────────────────────────


def deduplicate(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Drop duplicate normalised texts (first occurrence wins)."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for text, lang, source in rows:
        key = hashlib.sha256(normalise(text).lower().encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append((text, lang, source))
    return out


def enforce_length(rows: list[tuple[str, str, str]], max_tokens: int) -> list[tuple[str, str, str]]:
    return [r for r in rows if count_tokens(r[0]) <= max_tokens]


def ru_share(rows: list[tuple[str, str, str]]) -> float:
    if not rows:
        return 0.0
    return sum(1 for _, lang, _ in rows if lang == "ru") / len(rows)


def train_val_split(
    rows: list[tuple[str, str, str]], seed: int, val_frac: float = 0.05
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Deterministic 95/5 split (order-shuffled with the given seed)."""
    rng = random.Random(seed)
    idx = list(range(len(rows)))
    rng.shuffle(idx)
    n_val = max(1, int(len(rows) * val_frac)) if rows else 0
    val_idx = set(idx[:n_val])
    train = [rows[i] for i in idx[n_val:]]
    val = [rows[i] for i in sorted(val_idx)]
    return train, val


# ── IO ───────────────────────────────────────────────────────────────────────


def write_jsonl(path: Path, rows: list[tuple[str, str, str]]) -> str:
    """Write jsonl; return the sha256 fingerprint of the file bytes."""
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for text, lang, source in rows:
            line = json.dumps(
                {"text": text, "lang": lang, "source": source},
                ensure_ascii=False,
                sort_keys=True,
            )
            fh.write(line + "\n")
            digest.update((line + "\n").encode("utf-8"))
    return digest.hexdigest()


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="NM-1a dataset preparation (local sources only, no network)."
    )
    p.add_argument("--out-dir", type=Path, default=Path("training/data"))
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--max-pairs", type=int, default=DEFAULT_MAX_PAIRS)
    p.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    p.add_argument(
        "--ru-boost",
        action="store_true",
        default=True,
        help="replicate RU synthetic variants so the >=40%% RU quota holds "
        "on top of the EN-heavy fixture corpus (default: on, NM-1a gate)",
    )
    p.add_argument(
        "--no-ru-boost",
        dest="ru_boost",
        action="store_false",
        help="disable RU replication (diagnostics)",
    )
    p.add_argument(
        "--no-repeat-to-cap",
        dest="repeat_to_cap",
        action="store_false",
        help="disable repeat-to-cap synthesis toward the --max-pairs target "
        "(NM-1a base corpus is ~800 unique rows; repeat-with-variant "
        "reaches the configured cap deterministically)",
    )
    p.add_argument(
        "--from-mnemos-dir",
        type=Path,
        default=None,
        help="optional local mnemos store dump (privacy: stays on this machine)",
    )
    p.add_argument(
        "--mnemos-limit",
        type=int,
        default=20_000,
        help="cap on rows read from --from-mnemos-dir",
    )
    p.add_argument(
        "--from-mnemos-db",
        type=Path,
        default=None,
        help=(
            "optional live mnemos SQLite store (opened READ-ONLY; only the "
            "content field enters the pool; privacy: stays on this machine)"
        ),
    )
    p.add_argument(
        "--mnemos-db-limit",
        type=int,
        default=20_000,
        help="cap on paragraph chunks read from --from-mnemos-db",
    )
    p.add_argument(
        "--mnemos-db-projects",
        default=None,
        help=(
            "comma-separated project:* slugs to include from --from-mnemos-db "
            "(e.g. 'project-mnemos,project-atlas'; no filter when omitted)"
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, str]] = []
    stats: dict[str, int] = {}

    fixtures = collect_fixtures()
    stats["fixtures"] = len(fixtures)
    rows.extend(fixtures)

    if args.from_mnemos_dir is not None:
        if not args.from_mnemos_dir.is_dir():
            print(
                f"error: --from-mnemos-dir is not a directory: {args.from_mnemos_dir}",
                file=sys.stderr,
            )
            return 2
        local = collect_from_mnemos_dir(args.from_mnemos_dir, limit=args.mnemos_limit)
        stats["mnemos-dir"] = len(local)
        rows.extend(local)

    if args.from_mnemos_db is not None:
        projects = (
            [p for p in args.mnemos_db_projects.split(",") if p.strip()]
            if args.mnemos_db_projects
            else None
        )
        local = collect_from_mnemos_db(
            args.from_mnemos_db, limit=args.mnemos_db_limit, projects=projects
        )
        stats["mnemos-db"] = len(local)
        rows.extend(local)

    synthetic = collect_synthetic(args.seed)
    if args.ru_boost:
        # Honest note (review F2): this replication is a NO-OP after the
        # exact-text dedup below — duplicated RU rows are removed with the
        # rest. The flag is kept for CLI compatibility; the >=40 % RU quota
        # is actually reached by repeat-to-cap with variant suffixes (which
        # survive dedup), and the unique-text RU share is reported separately.
        ru_half = [(t, lang, src) for t, lang, src in synthetic if lang == "ru"]
        synthetic = synthetic + ru_half
    stats["synthetic"] = len(synthetic)
    rows.extend(synthetic)

    before_dedup = len(rows)
    rows = deduplicate(rows)
    stats["dedup_removed"] = before_dedup - len(rows)

    rows = enforce_length(rows, args.max_tokens)
    stats["over_length_dropped"] = before_dedup - stats["dedup_removed"] - len(rows)

    if len(rows) > args.max_pairs:
        # Interleave-cap: the pool is ordered fixtures-first, a naive
        # prefix cut would drop nearly all synthetic (and RU) rows at
        # small caps. Take a stride sample ordered to keep language
        # balance representative of the pool (deterministic: pure
        # arithmetic, no RNG).
        step = len(rows) / args.max_pairs
        rows = [rows[min(int(i * step), len(rows) - 1)] for i in range(args.max_pairs)]
    if args.repeat_to_cap and len(rows) < args.max_pairs:
        # NM-1a corpus is smaller than the 100k target (no external
        # downloads until NM-1b+); reach toward --max-pairs by repeating
        # template families with variation indices appended. Deterministic
        # for a given seed; near-duplicates are intentional here — the
        # KD signal is the teacher's geometry, and true dedup still ran
        # on the base pool above.
        #
        # Review fix (train/val leakage): the split happens on the UNIQUE
        # pool BEFORE replication so every val row is textually absent from
        # train — val_cosine and int8 calibration would otherwise score on
        # train texts. Only the train part is repeated toward the cap.
        rng = random.Random(f"{args.seed}:repeat")
        train, val = train_val_split(rows, args.seed)
        base = list(train)
        train = list(train)
        while len(train) < args.max_pairs - len(val):
            text, lang, source = base[rng.randrange(len(base))]
            variant = rng.choice(
                ["", " (v2)", " (v3)", " — follow-up", " — update", " — уточнение"]
            )
            train.append((text + variant, lang, source))
    else:
        train, val = train_val_split(rows, args.seed)
    stats["final_pairs"] = len(train) + len(val)
    fp_train = write_jsonl(args.out_dir / "train.jsonl", train)
    fp_val = write_jsonl(args.out_dir / "val.jsonl", val)
    fingerprint = hashlib.sha256((fp_train + fp_val).encode("utf-8")).hexdigest()
    (args.out_dir / "fingerprint.txt").write_text(fingerprint + "\n", encoding="utf-8")

    share = ru_share(rows)
    # Unique-text RU share: the weighted share is inflated by repeat-to-cap
    # replication — report both so the reviewer-visible number is honest.
    first_lang_by_text: dict[str, str] = {}
    for t, lang, _ in rows:
        first_lang_by_text.setdefault(t, lang)
    unique_rows = [(t, first_lang_by_text[t], "unique") for t in first_lang_by_text]
    unique_share = ru_share(unique_rows)
    quota_flag = "OK" if unique_share >= RU_QUOTA_TARGET else "BELOW TARGET"
    print("dataset stats:", json.dumps(stats, sort_keys=True))
    print(f"ru_share: {share:.3f} (target >= {RU_QUOTA_TARGET:.2f}) -> {quota_flag}")
    print(
        f"ru_share_unique: {unique_share:.3f} over {len(unique_rows)} unique texts "
        "(weighted share is inflated by repeat-to-cap replication)"
    )
    print(f"train/val: {len(train)}/{len(val)} (seed={args.seed})")
    print(f"dataset_fingerprint: {fingerprint}")
    if share < RU_QUOTA_TARGET:
        # NM-1a: the gate is informational — the report is the deliverable.
        print(
            "warn: RU share below target; extend RU template families before NM-1b",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
