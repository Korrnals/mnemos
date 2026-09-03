#!/usr/bin/env python
"""One-page owner report — the ADR-0020 §5 gate-policy-5 deliverable (BF-4).

Generates ``benchmarks/reports/latest.md``: a traffic light per owner
family F1-F7, 1-3 lines of key numbers per family with deltas to the
baseline, invariants as separate ``=1.000`` / ``=0`` lines, and trend
arrows when a previous snapshot exists.

Source of truth is BYTES, never memory: every number in the page is
read from ``benchmarks/baselines/*.json`` (the canonical baselines) and
— when a run report NEWER than its baseline exists — from the freshest
``benchmarks/reports/<stand>-<stamp>.json`` gate report (current-run
values + gate verdict + deltas). A run report older than the baseline
it would compare against is stale (the re-record superseded it) and is
ignored.

Traffic-light semantics:

* RED — a breached corridor or invariant: a run-report gate verdict
  ``pass: false`` for a stand feeding the family, or an invariant entry
  with ``ok: false`` in any consumed JSON (baseline or fresh report);
* YELLOW — skip / noise / not-yet: the S1m production-embedder contour
  skipped, S2 without a baseline (the timing baseline is a property of
  the nightly quiet machine), or an S2 nightly NOISE status (band wider
  than the corridor — de-escalated per ADR-0020 §5);
* GREEN — corridors hold and invariants meet their requirement.

Trends: each generation writes ``reports/latest-prev.json`` (machine
readable headline snapshot); the NEXT generation reads it back for the
↗/→/↘ arrows (the first report after a re-baseline honestly has none).

Family ↔ data mapping (ADR-0020 metric registry, condensed to what the
stands measure today):

| Family | Stands | Key numbers on the page |
|---|---|---|
| F1 Latencies | S2 | per-verb median p50/p95 + measured noise band |
| F2 Accuracy / quality | S1, S1m, S3 | recall@10, precision@5 (ref+prod), sufficiency@task |
| F3 Token economy | S3, S1 | context-growth-factor, rewrite hit/regret pair |
| F4 Composition cleanliness | S1, S3 | duplicate-rate invariants, retraction leaks, discards |
| F5 Session coherence | S3 | fact-retention@N,k, recall-drift, checkpoint integrity |
| F6 Availability | S4 | probe-pass-rate, memory-completeness, embed-staleness |
| F7 Extensibility | S1 | A9 scale-sensitivity delta, cross-principal leak, render-neutrality |
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]  # the repository root
BASELINES_DIR = ROOT / "benchmarks" / "baselines"
REPORTS_DIR = ROOT / "benchmarks" / "reports"
OUTPUT_PATH = REPORTS_DIR / "latest.md"
PREV_SNAPSHOT_PATH = REPORTS_DIR / "latest-prev.json"

#: Stand baselines consumed (s2.json may legitimately be absent).
STANDS: tuple[str, ...] = ("s1", "s2", "s3", "s4")

_LIGHT_ICON = {"red": "🔴", "yellow": "🟡", "green": "🟢"}


# ── loading ──────────────────────────────────────────────────────────────────


def load_baselines(baselines_dir: Path) -> dict[str, dict[str, Any]]:
    """Every existing baseline JSON (missing stands are simply absent)."""
    out: dict[str, dict[str, Any]] = {}
    for stand in STANDS:
        path = baselines_dir / f"{stand}.json"
        if path.exists():
            out[stand] = json.loads(path.read_text())
    return out


def load_fresh_reports(
    reports_dir: Path, baselines: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Freshest non-stale run report per stand.

    A report qualifies only when its ``created`` is >= the baseline's
    ``created`` — anything older was superseded by the re-record and
    must not paint the page (a stale gate run against an older corpus
    would show phantom corridor failures).
    """
    out: dict[str, dict[str, Any]] = {}
    for stand in STANDS:
        base_created = (baselines.get(stand) or {}).get("created") or ""
        candidates: list[tuple[str, dict[str, Any]]] = []
        for path in sorted(reports_dir.glob(f"{stand}-*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue  # partial write / foreign file — never crash the page
            if "metrics" not in payload and "verb_metrics" not in payload:
                continue  # not a stand run report
            if (payload.get("created") or "") >= base_created:
                candidates.append((payload.get("created") or "", payload))
        if candidates:
            out[stand] = max(candidates, key=lambda pair: pair[0])[1]
    return out


def _metrics(source: dict[str, Any] | None) -> dict[str, Any]:
    return (source or {}).get("metrics") or {}


def _invariant_lines(
    *sources: dict[str, Any] | None, names: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    """Invariant entries from every consumed source (first wins on name).

    ``names`` filters to a family's own invariants (the registry assigns
    each invariant to exactly one family); ``None`` returns them all.
    """
    seen: dict[str, dict[str, Any]] = {}
    for source in sources:
        for name, entry in _metrics(source).get("invariants", {}).items():
            if name not in seen and isinstance(entry, dict) and "ok" in entry:
                seen[name] = {"name": name, **entry}
    # S3/S4 carry top-level invariants outside "invariants" — fold them in.
    for source in sources:
        m = _metrics(source)
        for name in ("checkpoint_return_integrity", "quarantine_exclusion"):
            entry = m.get(name)
            if isinstance(entry, dict) and "ok" in entry and name not in seen:
                seen[name] = {"name": name, **entry}
    if names is None:
        return list(seen.values())
    return [seen[n] for n in names if n in seen]


def _any_gate_failed(*reports: dict[str, Any] | None) -> list[str]:
    """Gate failures recorded in consumed run reports (a breach → RED)."""
    failures: list[str] = []
    for report in reports:
        gate = (report or {}).get("gate")
        if isinstance(gate, dict) and not gate.get("pass", True):
            failures.extend(str(f) for f in gate.get("failures", []))
    return failures


# ── per-family evaluation ────────────────────────────────────────────────────


def _fmt(value: Any, digits: int = 4) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _delta(cur: Any, base: Any) -> str:
    if not isinstance(cur, (int, float)) or not isinstance(base, (int, float)):
        return ""
    diff = float(cur) - float(base)
    return f" (Δ {diff:+.{4}f} vs baseline)"


def evaluate_f1(
    baselines: dict[str, dict[str, Any]], fresh: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """F1 Latencies — S2 (nightly machine; no local baseline by design)."""
    base = _metrics(baselines.get("s2")).get("verbs")
    nightly = fresh.get("s2") or {}
    analysis = nightly.get("nightly_analysis") or {}
    lines: list[str] = []
    if base:
        for verb, entry in base.items():
            lines.append(
                f"- {verb}: median p50 {_fmt(entry.get('p50_ms'), 2)}ms / "
                f"p95 {_fmt(entry.get('p95_ms'), 2)}ms; measured noise band "
                f"{_fmt(entry.get('band_p50_ms'), 2)}ms "
                f"({entry.get('relative_band', 0):.1%})"
            )
    else:
        lines.append(
            "- no S2 baseline yet — the timing baseline is a property of the "
            "nightly quiet machine (`make bench-s2-nightly` with "
            "`S2_NIGHTLY_FLAGS=--record-nightly`); local smoke is "
            "informational only (ADR-0020 §5)"
        )
    verdict = analysis.get("overall")
    report_mode = str(nightly.get("mode") or "").strip().lower()
    if verdict == "REGRESSION":
        light = "red"
    elif verdict == "NOISE" or base is None:
        light = "yellow"
    elif report_mode == "smoke":
        # a local SMOKE report carries no nightly corridor — F1 must not
        # go green from smoke numbers alone (review #220 F4)
        light = "yellow"
    else:
        light = "green"
    if verdict in ("NOISE", "REGRESSION"):
        flagged = [
            f"{verb} ({entry.get('status')})"
            for verb, entry in (analysis.get("verbs") or {}).items()
            if isinstance(entry, dict) and entry.get("status") in ("NOISE", "REGRESSION")
        ]
        lines.append(
            f"- latest nightly verdict: {verdict} — {', '.join(flagged) or 'see report'}; "
            "NOISE de-escalates to report + ticket (ADR-0020 §5), REGRESSION blocks"
        )
    return {
        "light": light,
        "lines": lines[:3],
        "invariants": [],
        "headline": {"metric": "s2_add_p50_ms", "value": (base or {}).get("add", {}).get("p50_ms")},
        "direction": "lower-better",
    }


def evaluate_f2(
    baselines: dict[str, dict[str, Any]], fresh: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """F2 Accuracy/quality — S1 reference + S1m production + S3 sufficiency."""
    s1b, s1f = baselines.get("s1"), fresh.get("s1")
    s3b, s3f = baselines.get("s3"), fresh.get("s3")
    cur_r = _metrics(s1f).get("retrieval", {})
    base_r = _metrics(s1b).get("retrieval", {})
    s1m = _metrics(s1b).get("s1m") or {}
    s1m_live = _metrics(s1f).get("s1m") or {}
    s1m_src = s1m_live or s1m
    suff_base = _metrics(s3b).get("sufficiency_at_task", {}).get("rate")
    suff_cur = _metrics(s3f).get("sufficiency_at_task", {}).get("rate")

    r10 = cur_r.get("recall_at_10", base_r.get("recall_at_10"))
    p5 = cur_r.get("precision_at_5", base_r.get("precision_at_5"))
    lines = [
        f"- reference (BLAKE2b) recall@10 {_fmt(r10)}"
        f"{_delta(cur_r.get('recall_at_10'), base_r.get('recall_at_10'))} / "
        f"precision@5 {_fmt(p5)} over {base_r.get('judged_queries', '?')} judged queries"
    ]
    if s1m_src.get("status") == "measured":
        m = s1m_src.get("metrics") or {}
        lines.append(
            f"- production embedder (S1m): recall@10 {_fmt(m.get('recall_at_10'))} / "
            f"MRR {_fmt(m.get('mrr'))} — self-comparison corridor"
        )
    else:
        lines.append(f"- production embedder (S1m): SKIPPED — {s1m_src.get('reason', '?')}")
    if suff_base is not None or suff_cur is not None:
        lines.append(
            f"- sufficiency@task (S3): {_fmt(suff_cur if suff_cur is not None else suff_base)}"
            f"{_delta(suff_cur, suff_base)}"
        )

    invariants = _invariant_lines(
        s1b, s1f, names=("injection_acceptance", "non_admissible_surfaced", "quarantine_exclusion")
    )
    failures = _any_gate_failed(s1f, s3f)
    bad_invariants = [i for i in invariants if not i["ok"]]
    light = "red" if (failures or bad_invariants) else "green"
    if s1m_src.get("status") != "measured":
        light = "yellow" if light != "red" else light
    return {
        "light": light,
        "lines": lines[:3],
        "invariants": invariants,
        "headline": {"metric": "recall_at_10", "value": base_r.get("recall_at_10")},
        "direction": "higher-better",
    }


def evaluate_f3(
    baselines: dict[str, dict[str, Any]], fresh: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """F3 Token economy — S3 growth factor + the S1 rewrite pair."""
    s1b, s1f = baselines.get("s1"), fresh.get("s1")
    s3b, s3f = baselines.get("s3"), fresh.get("s3")
    growth_base = _metrics(s3b).get("context_growth", {}).get("factor")
    growth_cur = _metrics(s3f).get("context_growth", {}).get("factor")
    rw_base = _metrics(s1b).get("rewrite", {})
    rw_cur = _metrics(s1f).get("rewrite", {})
    growth_now = growth_cur if growth_cur is not None else growth_base
    lines = [
        f"- context-growth-factor (S3, stop-signal): {_fmt(growth_now)}"
        f"{_delta(growth_cur, growth_base)} — ceiling +0.02 over baseline",
        f"- rewrite pair (S1): hit {_fmt(rw_cur.get('hit_rate', rw_base.get('hit_rate')))}"
        f"{_delta(rw_cur.get('hit_rate'), rw_base.get('hit_rate'))} / regret "
        f"{_fmt(rw_cur.get('regret_rate', rw_base.get('regret_rate')))}",
    ]
    failures = _any_gate_failed(s1f, s3f)
    light = "red" if failures else "green"
    return {
        "light": light,
        "lines": lines[:3],
        "invariants": [],
        "headline": {"metric": "context_growth_factor", "value": growth_base},
        "direction": "lower-better",
    }


def evaluate_f4(
    baselines: dict[str, dict[str, Any]], fresh: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """F4 Composition cleanliness — duplicates, retraction leaks, discards."""
    s1b, s1f = baselines.get("s1"), fresh.get("s1")
    discard = _metrics(baselines.get("s3")).get("stage_discard_profile", {})
    inv_s1 = _metrics(s1b).get("invariants") or {}
    dup5 = inv_s1.get("duplicate_occurrences_at_5", {}).get("value", "?")
    dup10 = inv_s1.get("duplicate_occurrences_at_10", {}).get("value", "?")
    lines = [
        f"- assembled-noise: duplicate occurrences at k=5/10 = {dup5}/{dup10}",
        f"- stage-discard profile (S3, informational): {discard.get('assembles', '?')} assembles, "
        f"{discard.get('scan_blocks_refused', '?')} scan-refused, "
        f"{discard.get('budget_blocks_skipped', '?')} budget-skipped",
    ]
    invariants = _invariant_lines(
        s1b,
        s1f,
        names=(
            "duplicate_occurrences_at_5",
            "duplicate_occurrences_at_10",
            "rewrite_redemption_leaks",
        ),
    )
    failures = _any_gate_failed(s1f)
    bad = [i for i in invariants if not i["ok"]]
    light = "red" if (failures or bad) else "green"
    return {
        "light": light,
        "lines": lines[:3],
        "invariants": invariants,
        "headline": {"metric": "duplicate_occurrences_at_10", "value": 0},
        "direction": "lower-better",
    }


def evaluate_f5(
    baselines: dict[str, dict[str, Any]], fresh: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """F5 Session coherence — S3 retention / drift / checkpoint."""
    s3b, s3f = baselines.get("s3"), fresh.get("s3")
    ret_base = _metrics(s3b).get("fact_retention", {}).get("overall", {})
    ret_cur = _metrics(s3f).get("fact_retention", {}).get("overall", {})
    drift_base = _metrics(s3b).get("recall_drift", {})
    drift_cur = _metrics(s3f).get("recall_drift", {})
    lines = [
        f"- fact-retention@N,k: {_fmt(ret_cur.get('rate', ret_base.get('rate')))}"
        f"{_delta(ret_cur.get('rate'), ret_base.get('rate'))} "
        f"({ret_base.get('hits', '?')}/{ret_base.get('probes', '?')} probes)",
        f"- recall-drift-over-session: {_fmt(drift_cur.get('delta', drift_base.get('delta')))}"
        f"{_delta(drift_cur.get('delta'), drift_base.get('delta'))} (negative = degradation)",
    ]
    invariants = _invariant_lines(s3b, s3f, names=("checkpoint_return_integrity",))
    failures = _any_gate_failed(s3f)
    bad = [i for i in invariants if not i["ok"]]
    light = "red" if (failures or bad) else "green"
    return {
        "light": light,
        "lines": lines[:3],
        "invariants": invariants,
        "headline": {"metric": "fact_retention", "value": ret_base.get("rate")},
        "direction": "higher-better",
    }


def evaluate_f6(
    baselines: dict[str, dict[str, Any]], fresh: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """F6 Availability — S4 probes / completeness / staleness."""
    s4b, s4f = baselines.get("s4"), fresh.get("s4")
    mb, mf = _metrics(s4b), _metrics(s4f)
    pass_cur = mf.get("probe_pass_rate", mb.get("probe_pass_rate"))
    pass_base = mb.get("probe_pass_rate")
    comp_cur = mf.get("memory_completeness", mb.get("memory_completeness"))
    comp_base = mb.get("memory_completeness")
    staleness = mb.get("embed_staleness", {})
    lines = [
        f"- probe-pass-rate: {_fmt(pass_cur)}{_delta(pass_cur, pass_base)}; "
        f"memory-completeness: {_fmt(comp_cur)}{_delta(comp_cur, comp_base)}",
        f"- embed-staleness: {staleness.get('stale', '?')} stale of "
        f"{staleness.get('checked_refined', '?')} checked refined; read-only invariant "
        f"{'ok' if mb.get('read_only_invariant', {}).get('ok') else 'BREACH'}",
    ]
    invariants = _invariant_lines(s4b, s4f, names=("quarantine_exclusion",))
    failures = _any_gate_failed(s4f)
    bad = [i for i in invariants if not i["ok"]]
    if not mb.get("read_only_invariant", {}).get("ok", True):
        bad = bad or [{"name": "read_only_invariant", "ok": False}]
    light = "red" if (failures or bad) else "green"
    return {
        "light": light,
        "lines": lines[:3],
        "invariants": invariants,
        "headline": {"metric": "memory_completeness", "value": comp_base},
        "direction": "higher-better",
    }


def evaluate_f7(
    baselines: dict[str, dict[str, Any]], fresh: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """F7 Extensibility — scale sensitivity, leak, render-neutrality."""
    s1b, s1f = baselines.get("s1"), fresh.get("s1")
    a9_base = _metrics(s1b).get("a9", {})
    a9_cur = _metrics(s1f).get("a9", {})
    a9_now = a9_cur.get(
        "delta_recall10_current_vs_pre_a9",
        a9_base.get("delta_recall10_current_vs_pre_a9"),
    )
    lines = [
        f"- scale-sensitivity (A9 recall@10 delta current vs pre-A9): {a9_now:+.4f} (floor -0.02)",
    ]
    invariants = _invariant_lines(s1b, s1f, names=("foreign_project_surfaced", "render_neutrality"))
    failures = _any_gate_failed(s1f)
    bad = [i for i in invariants if not i["ok"]]
    light = "red" if (failures or bad) else "green"
    return {
        "light": light,
        "lines": lines[:3],
        "invariants": invariants,
        "headline": {
            "metric": "a9_delta",
            "value": a9_base.get("delta_recall10_current_vs_pre_a9"),
        },
        "direction": "higher-better",
    }


_FAMILY_DOMAINS: dict[str, str] = {
    "F1": "Latencies (S2 nightly)",
    "F2": "Accuracy / quality (S1 + S1m + S3)",
    "F3": "Token economy (S3 + S1)",
    "F4": "Composition cleanliness (S1 + S3)",
    "F5": "Session coherence (S3)",
    "F6": "Availability (S4)",
    "F7": "Extensibility (S1)",
}

_FAMILY_EVALUATORS: dict[str, Any] = {
    "F1": evaluate_f1,
    "F2": evaluate_f2,
    "F3": evaluate_f3,
    "F4": evaluate_f4,
    "F5": evaluate_f5,
    "F6": evaluate_f6,
    "F7": evaluate_f7,
}


# ── rendering ────────────────────────────────────────────────────────────────


def _arrow(value: Any, prev: Any, direction: str) -> str:
    if not isinstance(value, (int, float)) or not isinstance(prev, (int, float)):
        return ""
    if value == prev:
        return " →"
    better = value > prev if direction == "higher-better" else value < prev
    return f" {'↗' if better else '↘'}"


def render_report(
    baselines: dict[str, dict[str, Any]],
    fresh: dict[str, dict[str, Any]],
    prev: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """The one-page markdown + the next snapshot (headlines for arrows)."""
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    prev_heads = (prev or {}).get("headlines") or {}
    sections: dict[str, dict[str, Any]] = {}
    for fid, evaluate in _FAMILY_EVALUATORS.items():
        sections[fid] = evaluate(baselines, fresh)

    lights = {fid: s["light"] for fid, s in sections.items()}
    worst = (
        "red" if "red" in lights.values() else "yellow" if "yellow" in lights.values() else "green"
    )

    out: list[str] = [
        "# mnemos benchmark report — one page per wave (ADR-0020 §5)",
        "",
        f"Generated {generated} from `benchmarks/baselines/*.json` (bytes, not memory).",
        "Traffic light: 🟢 corridor holds / invariant meets requirement · "
        "🟡 skip, noise, or baseline not born yet · 🔴 breach.",
        "",
        "| Family | Domain | Light |",
        "|---|---|---|",
    ]
    for fid in _FAMILY_EVALUATORS:
        out.append(f"| {fid} | {_FAMILY_DOMAINS[fid]} | {_LIGHT_ICON[lights[fid]]} |")
    out += [
        "",
        f"**Overall: {_LIGHT_ICON[worst]} {worst.upper()}**",
        "",
    ]

    for fid in _FAMILY_EVALUATORS:
        section = sections[fid]
        head = section["headline"]
        arrow = _arrow(
            head.get("value"), (prev_heads.get(fid) or {}).get("value"), section["direction"]
        )
        out.append(f"## {fid} — {_FAMILY_DOMAINS[fid]} {_LIGHT_ICON[lights[fid]]}")
        out.append("")
        out.extend(section["lines"])
        if head.get("value") is not None and arrow:
            out.append(f"- trend vs previous wave: {arrow.strip()}")
        invariants = section["invariants"]
        if invariants:
            out.append("")
            out.append("**Invariants:**")
            for inv in invariants:
                mark = "OK" if inv["ok"] else "BREACH"
                out.append(
                    f"- {inv['name']} = {_fmt(inv.get('value'))} "
                    f"(required = {_fmt(inv.get('expect'))}) — {mark}"
                )
        out.append("")

    snapshot = {
        "generated": generated,
        "headlines": {
            fid: {"metric": s["headline"].get("metric"), "value": s["headline"].get("value")}
            for fid, s in sections.items()
        },
    }
    return "\n".join(out).rstrip() + "\n", snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="one-page owner report from all benchmark baselines (ADR-0020 §5)"
    )
    parser.add_argument("--baselines", type=Path, default=BASELINES_DIR)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args(argv)

    baselines = load_baselines(args.baselines)
    if not any(stand in baselines for stand in ("s1", "s3", "s4")):
        print(
            "bench-report: no deterministic baselines found in "
            f"{args.baselines} — record them first (make bench-s1-record …)",
            file=sys.stderr,
        )
        return 1
    fresh = load_fresh_reports(args.reports, baselines)
    prev: dict[str, Any] | None = None
    prev_path = args.out.parent / "latest-prev.json"
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text())
        except json.JSONDecodeError:
            prev = None  # a corrupt snapshot never blocks the page

    page, snapshot = render_report(baselines, fresh, prev)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    prev_path.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(f"bench-report: page → {args.out}", file=sys.stderr)

    lights: dict[str, str] = {}
    for fid, evaluate in _FAMILY_EVALUATORS.items():
        lights[fid] = evaluate(baselines, fresh)["light"]
    reds = [fid for fid, light in lights.items() if light == "red"]
    if reds:
        print(f"bench-report: RED families — {', '.join(reds)}", file=sys.stderr)
        return 1
    print("bench-report: no red families", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
