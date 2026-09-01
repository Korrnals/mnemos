#!/usr/bin/env bash
# NM-1b pilot pipeline: prepare (100k cap) -> distill -> export -> eval.
# One command; env knobs: THREADS, MAX_PAIRS, EPOCHS. Logs to training/logs/.
#
# Timeout hints (CPU-only toolbox container, half-core threads):
#   prepare   ~1 min (100k pairs)
#   distill   ~2-6 h per epoch at 100k pairs on 8 threads (MiniLM-class);
#             use MAX_PAIRS=100 EPOCHS=1 for a smoke run (~5-10 min)
#   export    ~5-15 min (trace + int8 calibration over 200 samples)
#   eval      ~2-5 min (corpus is small)
# Run it in tmux/nohup with the laptop plugged in and the lid OPEN —
# see training/README.md "Политика параллельной работы".
set -euo pipefail

TRAIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$TRAIN_DIR")"
LOG_DIR="$TRAIN_DIR/logs"
mkdir -p "$LOG_DIR"

THREADS="${THREADS:-$(( $(nproc 2>/dev/null || echo 4) / 2 ))}"
MAX_PAIRS="${MAX_PAIRS:-100000}"
EPOCHS="${EPOCHS:-3}"
PAIRS_DIR="$TRAIN_DIR/data"
RUN_DIR="$TRAIN_DIR/runs/nm1b"
ONNX_DIR="$RUN_DIR/onnx"
REPORT_DIR="$REPO_ROOT/benchmarks/reports"
STAMP="$(date -u +%Y%m%dT%H%M%S)"
LOG="$LOG_DIR/pilot-$STAMP.log"

echo "pilot: threads=$THREADS max_pairs=$MAX_PAIRS epochs=$EPOCHS log=$LOG"
cd "$REPO_ROOT"

echo "== [1/4] prepare dataset (cap $MAX_PAIRS) =="
python3 training/dataset/prepare_dataset.py \
  --out-dir "$PAIRS_DIR" --max-pairs "$MAX_PAIRS" --seed 42

echo "== [2/4] distill (epochs=$EPOCHS, threads=$THREADS) =="
python3 training/distill.py \
  --pairs "$PAIRS_DIR/train.jsonl" \
  --val "$PAIRS_DIR/val.jsonl" \
  --epochs "$EPOCHS" --batch-size 32 --threads "$THREADS" \
  --out-dir "$RUN_DIR"

echo "== [3/4] export ONNX + int8 PTQ =="
python3 training/export_onnx.py \
  --run-dir "$RUN_DIR" --pairs-dir "$PAIRS_DIR" \
  --calib-samples 200 --out-dir "$ONNX_DIR"

echo "== [4/4] eval =="
python3 training/eval_distilled.py \
  --onnx-dir "$ONNX_DIR" \
  --pairs-dir "$PAIRS_DIR" \
  --report-dir "$REPORT_DIR" \
  --label "nm1b-pilot"

echo "pilot done. log: $LOG"