"""S2 timing stand — smoke-mode latency wrappers (ADR-0020, wave BF-2).

S2 is the ONLY wall-clock domain in the benchmark framework. ADR-0020
gate policy §3: **S2 never blocks locally** — the R=1 smoke run is
informational; the full repeat-mode (``--repeats N``) runs nightly on a
quiet isolated machine and only there becomes a corridor concern (a
noise band wider than the corridor yields status NOISE, de-escalated to
report + ticket — never a local block).

Workload (fixed, ~10³ operations): one temp store, the deterministic
lexical embedder, then measured wraps around the four core verbs:

* ``add``          — ingest write (scanner + vault + sqlite + embed);
* ``search``       — the hybrid RRF path (FTS + vector leg);
* ``assemble``     — the fixed pipeline on a budget;
* ``refine_single``— one full refine cycle (claim → artifact → swap).

Results land in ``benchmarks/reports/s2-smoke-<stamp>.json`` —
NEVER in ``benchmarks/baselines/``: the S2 baseline is a property of the
nightly quiet machine (noise band measured there first); recording a
developer-laptop number as the canonical baseline would poison every
future corridor. The README documents this.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.corpus.deterministic_embedder import LexicalHashEmbedder  # noqa: E402
from benchmarks.stands.s4_availability.fixture import fixture_settings  # noqa: E402
from mnemos.manager import MemoryManager  # noqa: E402
from mnemos.models import MemoryCreate, MemorySource, MemoryStatus  # noqa: E402
from mnemos.pipeline.refine import refine_single  # noqa: E402

STAND_VERSION = "s2-smoke-1"
REPORTS_DIR = ROOT / "benchmarks" / "reports"

#: Workload shape (smoke): ~10³ ops total by default. ``--repeats N``
#: (nightly) multiplies the whole measurement pass.
DEFAULT_OPS = 250  # x4 verbs ~ 1e3 operations

#: The four F1 verbs (ADR-0020 family F1) the smoke covers.
VERBS: tuple[str, ...] = ("add", "search", "assemble", "refine_single")

_TOKENS: dict[str, str] = {
    "a": "larkspire",
    "b": "glimmerford",
    "c": "thornwick",
}


def _timed(fn: Any, *args: Any, **kwargs: Any) -> tuple[float, Any]:
    """One wall-clock sample (monotonic; the ONLY clock in the stand)."""
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return time.perf_counter() - t0, out


def run_workload(ops: int) -> dict[str, list[float]]:
    """The fixed workload; returns per-verb wall-clock samples (seconds)."""
    with tempfile.TemporaryDirectory(prefix="mnemos-s2-") as tmp:
        root = Path(tmp)
        mgr = MemoryManager(fixture_settings(root))
        mgr._embedder = LexicalHashEmbedder()
        samples: dict[str, list[float]] = {v: [] for v in VERBS}
        try:
            for i in range(ops):
                token = _TOKENS["abc"[i % 3]]
                content = (
                    f"{token} workload entry {i}: the ingestion path takes a "
                    "moderately sized paragraph so the vault write, the "
                    "secrets scan and the FTS index all do real work; the "
                    "vector leg embeds title plus content plus tags."
                )
                dt, mem = _timed(
                    mgr.add,
                    data=MemoryCreate(
                        content=content,
                        title=f"{token} entry {i}",
                        tags=["project:s2-workload", "agent:s2-stand", "mnemos:rule"],
                        source=MemorySource.MCP,
                        status=MemoryStatus.PUBLISHED,
                    ),
                    project="s2-workload",
                    agent="s2-stand",
                )
                samples["add"].append(dt)
                if i % 3 == 2:  # every third entry completes a refine cycle
                    dt_r, _ = _timed(refine_single, mgr, mem.id)
                    samples["refine_single"].append(dt_r)
                if i % 2 == 0:  # search after every other write
                    dt_s, _ = _timed(mgr.search, query=token, limit=10)
                    samples["search"].append(dt_s)
            for i in range(ops // 5):
                dt_a, _ = _timed(
                    mgr.assemble_context,
                    session=f"s2-smoke-{i}",
                    project="s2-workload",
                    budget=2048,
                    query=_TOKENS["abc"[i % 3]],
                )
                samples["assemble"].append(dt_a)
        finally:
            mgr.close()
        return samples


def percentile(samples: list[float], p: float) -> float:
    """Nearest-rank percentile of a sample list (seconds)."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered)) - 1))
    return ordered[idx]


def summarize(samples: dict[str, list[float]]) -> dict[str, Any]:
    """p50/p95 per verb + honest smoke-mode marks.

    R=1 (a single pass): the percentiles are IN-SAMPLE order statistics
    of one run — informative, never a corridor. ``--repeats N`` (N > 1)
    runs N full passes and reports the per-repeat p50/p95 spread so the
    nightly harness can measure its own noise band.
    """
    out: dict[str, Any] = {}
    for verb in VERBS:
        vs = samples.get(verb, [])
        if not vs:
            out[verb] = {"n": 0, "status": "no-samples"}
            continue
        out[verb] = {
            "n": len(vs),
            "p50_ms": round(percentile(vs, 50) * 1000, 3),
            "p95_ms": round(percentile(vs, 95) * 1000, 3),
            "max_ms": round(max(vs) * 1000, 3),
            "total_s": round(sum(vs), 3),
        }
    return out


def run(repeats: int) -> dict[str, Any]:
    """One S2 smoke run: ``repeats`` full workload passes."""
    per_repeat: list[dict[str, Any]] = []
    started = datetime.now(UTC).isoformat(timespec="seconds")
    for r in range(repeats):
        samples = run_workload(DEFAULT_OPS)
        per_repeat.append(summarize(samples))
        print(
            f"s2-smoke: repeat {r + 1}/{repeats} — "
            + " ".join(
                f"{v}={per_repeat[-1].get(v, {}).get('p50_ms', 0):.1f}ms"
                for v in VERBS
                if per_repeat[-1].get(v, {}).get("n")
            ),
            file=sys.stderr,
        )
    mode = "repeats" if repeats > 1 else "smoke"
    return {
        "stand_version": STAND_VERSION,
        "mode": mode,
        "gating": "informational — S2 never blocks locally (ADR-0020 §Gate policy 3)",
        "repeats": repeats,
        "ops_per_repeat": DEFAULT_OPS,
        "total_operations": DEFAULT_OPS * 4 * repeats,
        "created": started,
        "verb_metrics": per_repeat[0]
        if repeats == 1
        else {
            "note": (
                "per-repeat summaries in 'repeats'; the spread between "
                "repeat p50/p95 IS the measured noise band (nightly use)"
            ),
            "repeats": per_repeat,
        },
        "environment": {
            "python": ".".join(str(v) for v in sys.version_info[:3]),
            "deterministic_embedder": True,
            "note": (
                "timing on the deterministic lexical embedder — measures the "
                "pipeline, not any model"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mnemos S2 timing stand — smoke (ADR-0020)")
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="full-workload repeats (1 = smoke/informational; nightly uses N>1 on a quiet machine)",
    )
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    report = run(args.repeats)
    stamp = report["created"].replace(":", "").replace("-", "").replace("+", "_")
    report_path = REPORTS_DIR / f"s2-smoke-{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    print(f"s2-smoke: report → {report_path}", file=sys.stderr)

    m = report["verb_metrics"]
    if report["mode"] == "smoke":
        print(
            "s2-smoke: PASS (informational) — "
            + " ".join(
                f"{v}: p50={m[v]['p50_ms']:.2f}ms p95={m[v]['p95_ms']:.2f}ms (n={m[v]['n']})"
                for v in VERBS
                if isinstance(m.get(v), dict) and m[v].get("n")
            )
        )
    else:
        print(
            f"s2-smoke: PASS (informational) — {args.repeats} repeats recorded; "
            "noise band visible in the repeats section"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
