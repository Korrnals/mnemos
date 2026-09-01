#!/usr/bin/env python3
"""NM training manager — one CLI for the whole nano-model training lifecycle.

Subcommands (idempotent, resumable):
  status                      show run state: stage, progress, metrics, disk
  prepare [--max-pairs N]     build the RU+EN dataset (skips if fingerprint exists)
  train [--epochs N ...]      KD distillation; resumes from the last checkpoint
  export [--epoch N]          int8 ONNX export from a checkpoint (default: latest)
  eval [--epoch N]            eval rig vs teacher/BM25 baseline
  snapshot [NAME]             tag the current checkpoint set as a named snapshot
  stop                        write the stop flag — the running train loop exits
                              cleanly at the next epoch boundary
  doctor                      environment check: torch, IPEX, threads, disk

State lives in <out-dir>/state.json (written by the manager, read by train).
Resume contract: train reads `--start-epoch` from the state file written by
`train stop`/`snapshot`; metrics.jsonl is append-only, so a resumed run
continues the same history. Every stage is a no-op when its output already
exists (prepare/export/eval), so re-running the whole pipeline is safe.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TRAIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRAIN_DIR.parent
DEFAULT_RUN = TRAIN_DIR / "runs" / "nm1b"
STAGES = ("prepare", "train", "export", "eval")


def _python() -> str:
    return sys.executable


def _dataset_dir(run_dir: Path) -> Path:
    return run_dir / "dataset"


def _read_state(run_dir: Path) -> dict:
    state_path = run_dir / "state.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"snapshots": {}}


def _write_state(run_dir: Path, state: dict) -> None:
    state_path = run_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _epochs_done(run_dir: Path) -> list[int]:
    if not (run_dir / "metrics.jsonl").exists():
        return []
    epochs = []
    for line in (run_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            epochs.append(int(json.loads(line)["epoch"]))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return epochs


def _latest_epoch(run_dir: Path) -> int | None:
    done = _epochs_done(run_dir)
    return max(done) if done else None


def _run(cmd: list[str], env_extra: dict[str, str] | None = None) -> int:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    print("+", " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, env=env, cwd=REPO_ROOT)
    return completed.returncode


# ── subcommands ───────────────────────────────────────────────────────────────


def cmd_prepare(args: argparse.Namespace) -> int:
    run_dir = args.out_dir
    out = run_dir / "dataset"
    fp_file = out / "fingerprint.txt"
    if fp_file.exists() and not args.force:
        print(
            f"prepare: already done — fingerprint {fp_file.read_text().strip()[:16]}… "
            f"(use --force to rebuild)"
        )
        return 0
    cmd = [
        _python(),
        str(TRAIN_DIR / "dataset" / "prepare_dataset.py"),
        "--max-pairs",
        str(args.max_pairs),
        "--out-dir",
        str(out),
        "--seed",
        str(args.seed),
    ]
    rc = _run(cmd)
    if rc == 0:
        state = _read_state(run_dir)
        state["prepared_fingerprint"] = fp_file.read_text().strip() if fp_file.exists() else None
        _write_state(run_dir, state)
    return rc


def cmd_train(args: argparse.Namespace) -> int:
    run_dir = args.out_dir
    # The manager keeps the dataset alongside the run by default (prepare
    # writes to <run-dir>/dataset), but also accepts the dataset prepared
    # into a sibling default location (TRAIN_DIR/data per prepare default).
    candidates = [run_dir / "dataset", TRAIN_DIR / "data"]
    ds = next((c for c in candidates if (c / "train.jsonl").exists()), candidates[0])
    if not (ds / "train.jsonl").exists():
        print("error: dataset not found — run `train.py prepare` first", file=sys.stderr)
        return 2
    done = _epochs_done(run_dir)
    if done and args.epochs > max(done):
        # Resume: continue from the last completed epoch (idempotent).
        planned = max(0, args.epochs - max(done))
        if planned == 0:
            print(
                f"train: all {len(done)} epochs already done — nothing to do "
                f"(epochs done: {done}; use --force-restart for a fresh run)"
            )
            return 0
        print(f"train: resuming — epochs done {done}, running {planned} more")
    if args.stop_flag:
        (run_dir / "STOP").write_text("stop requested\n", encoding="utf-8")
        print("stop-flag written — distill will exit at the next epoch boundary")
    cmd = [
        _python(),
        str(TRAIN_DIR / "distill.py"),
        "--pairs",
        str(ds / "train.jsonl"),
        "--val",
        str(ds / "val.jsonl"),
        "--epochs",
        str(args.epochs),
        "--batch-size",
        str(args.batch_size),
        "--threads",
        str(args.threads),
        "--seed",
        str(args.seed),
        "--out-dir",
        str(run_dir),
    ]
    done = _epochs_done(run_dir)
    if done:
        # Resume contract: distill skips completed epochs via --start-epoch
        # and appends to the same metrics.jsonl.
        cmd += ["--start-epoch", str(max(done) + 1)]
    if args.teacher:
        cmd += ["--teacher", args.teacher]
    if args.student_init:
        cmd += ["--student-init", args.student_init]
    if args.max_pairs:
        cmd += ["--max-pairs", str(args.max_pairs)]
    return _run(cmd)


def cmd_stop(args: argparse.Namespace) -> int:
    run_dir = args.out_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "STOP").write_text("stop requested\n", encoding="utf-8")
    print(f"stop-flag written to {run_dir / 'STOP'} — distill exits at the next epoch boundary")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    run_dir = args.out_dir
    epoch = args.epoch or _latest_epoch(run_dir)
    if epoch is None:
        print("error: no completed epochs found — run `train` first", file=sys.stderr)
        return 2
    ckpt = run_dir / f"epoch{epoch}"
    if not ckpt_dir_exists(ckpt):
        print(f"error: checkpoint epoch{epoch} not found in {run_dir}", file=sys.stderr)
        return 2
    cmd = [
        _python(),
        str(TRAIN_DIR / "export_onnx.py"),
        "--model-dir",
        str(ckpt),
        "--pairs-dir",
        str(run_dir / "dataset"),
        "--out-dir",
        str(run_dir / "export"),
    ]
    return _run(cmd)


def ckpt_dir_exists(ckpt: Path) -> bool:
    return ckpt.is_dir() and any(ckpt.glob("*.json")) and any(ckpt.glob("*.safetensors"))


def cmd_eval(args: argparse.Namespace) -> int:
    run_dir = args.out_dir
    export = run_dir / "export"
    if not (export / "model.onnx").exists():
        print("error: exported model not found — run `export` first", file=sys.stderr)
        return 2
    cmd = [
        _python(),
        str(TRAIN_DIR / "eval_distilled.py"),
        "--onnx-dir",
        str(export),
        "--pairs-dir",
        str(run_dir / "dataset"),
        "--report-dir",
        str(REPO_ROOT / "benchmarks" / "reports"),
    ]
    if args.epoch:
        cmd += ["--label", f"nm1b-epoch{args.epoch}"]
    return _run(cmd)


def cmd_snapshot(args: argparse.Namespace) -> int:
    run_dir = args.out_dir
    state = _read_state(run_dir)
    epoch = _latest_epoch(run_dir)
    if epoch is None:
        print("error: nothing to snapshot — no completed epochs", file=sys.stderr)
        return 2
    name = args.name or f"epoch{epoch}"
    src = run_dir / f"epoch{epoch}"
    if not ckpt_dir_exists(src):
        print(
            f"error: checkpoint epoch{epoch} incomplete or missing in {run_dir} — cannot snapshot",
            file=sys.stderr,
        )
        return 2
    dst = run_dir / "snapshots" / name
    if dst.exists() and not args.force:
        print(f"snapshot: '{name}' already exists — use another name or --force")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    metrics = {}
    if (run_dir / "metrics.jsonl").exists():
        lines = (run_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
        epoch_records = [r for r in records if r.get("epoch") == epoch]
        if epoch_records:
            metrics = epoch_records[-1]
    state.setdefault("snapshots", {})[name] = {
        "epoch": epoch,
        "cos_sim_mean": (metrics.get("val_cosine") or {}).get("cos_sim_mean"),
        "created": Path(str(run_dir)).stat().st_mtime,
        "path": str(dst.relative_to(run_dir)),
    }
    _write_state(run_dir, state)
    print(f"snapshot '{name}': epoch{epoch} -> {dst}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run_dir = args.out_dir
    print(f"run: {run_dir}")
    if not run_dir.exists():
        print("state: not started (no run directory)")
        return 0
    ds = run_dir / "dataset"
    print(f"prepare: {'done' if (ds / 'fingerprint.txt').exists() else 'NOT DONE'}")
    done = _epochs_done(run_dir)
    latest = _latest_epoch(run_dir)
    print(f"epochs done: {done or '[]'}")
    if done:
        lines = (run_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
        for r in records[-3:]:
            vc = r.get("val_cosine", {}).get("cos_sim_mean")
            print(
                f"  epoch {r['epoch']}: kd_loss={r['avg_kd_loss']:.5f} val_cos={vc:.4f}"
                if vc is not None
                else f"  epoch {r['epoch']}"
            )
        print(f"latest checkpoint: epoch{latest}")
    ckpts = sorted(p.name for p in run_dir.glob("epoch*") if p.is_dir())
    print(f"checkpoints on disk: {ckpts or 'none'}")
    state = _read_state(run_dir)
    snaps = state.get("snapshots", {})
    if snaps:
        print("snapshots:")
        for name, meta in sorted(snaps.items()):
            print(f"  {name}: epoch {meta.get('epoch')} cos_sim={meta.get('cos_sim_mean')}")
    stop = run_dir / "STOP"
    stop_msg = "SET — train will exit at next epoch boundary" if stop.exists() else "not set"
    print(f"stop flag: {stop_msg}")
    if (run_dir / "export" / "model.onnx").exists():
        print("export: ready (export/model.onnx)")
    else:
        print("export: not exported yet")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    try:
        import torch  # noqa: F401

        checks.append(("torch", True, "installed"))
    except ImportError:
        checks.append(("torch", False, "missing — pip install -r training/requirements.txt"))
    try:
        import onnxruntime  # noqa: F401

        checks.append(("onnxruntime", True, "installed"))
    except ImportError:
        checks.append(("onnxruntime", False, "needed for export/eval"))
    try:
        import transformers  # noqa: F401

        checks.append(("transformers", True, "installed"))
    except ImportError:
        checks.append(("transformers", False, "needed for teacher/student load"))
    try:
        import intel_extension_for_pytorch as ipex  # noqa: F401

        checks.append(("ipex", True, "iGPU acceleration available"))
    except ImportError:
        checks.append(("ipex", False, "optional — CPU fallback works"))
    cores = os.cpu_count() or 1
    checks.append(("cpu threads default", True, f"{max(1, cores // 2)} of {cores}"))
    disk = shutil.disk_usage(TRAIN_DIR)
    free_gb = disk.free / 2**30
    checks.append(("disk free", free_gb > 5, f"{free_gb:.1f} GB (need >5 GB for runs)"))
    ok = True
    for name, passed, note in checks:
        print(f"{'OK  ' if passed else 'FAIL'} {name}: {note}")
        ok = ok and passed
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_RUN,
        help="run directory (state, dataset, checkpoints, export)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("prepare", help="build the RU+EN dataset (skips if done)")
    sp.add_argument("--max-pairs", type=int, default=100_000)
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--force", action="store_true", help="rebuild even if fingerprint exists")
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("train", help="KD distillation (resumes past completed epochs)")
    sp.add_argument("--epochs", type=int, default=3)
    sp.add_argument("--batch-size", type=int, default=32)
    sp.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    sp.add_argument("--seed", type=int, default=42)
    sp.add_argument("--teacher", default=None, help="override teacher HF id")
    sp.add_argument("--student-init", default=None, help="override student init HF id")
    sp.add_argument("--max-pairs", type=int, default=None)
    sp.add_argument(
        "--stop-flag",
        action="store_true",
        help="write STOP and start training — loop exits at next epoch boundary",
    )
    sp.set_defaults(func=cmd_train)

    sp = sub.add_parser("stop", help="write the STOP flag — running train exits at next epoch")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("export", help="int8 ONNX export from a checkpoint")
    sp.add_argument("--epoch", type=int, default=None, help="default: latest")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("eval", help="eval the exported model")
    sp.add_argument("--epoch", type=int, default=None)
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("snapshot", help="tag current checkpoints as a named snapshot")
    sp.add_argument("name", nargs="?", default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_snapshot)

    sub.add_parser("status", help="show run state, progress, metrics, snapshots").set_defaults(
        func=cmd_status
    )
    sub.add_parser("doctor", help="environment check").set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
