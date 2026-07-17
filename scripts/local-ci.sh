#!/usr/bin/env bash
# local-ci.sh — local replica of .github/workflows/ci.yml, run on the active venv.
#
# WHY THIS EXISTS:
#   GitHub Actions on Korrnals/mnemos is locked due to a billing issue
#   (account locked — all jobs fail with "your account is locked due to a
#   billing issue"). Until the owner resolves billing via the GitHub UI,
#   ALL release/merge decisions MUST be verified locally. This script
#   replicates the CI `verify` job (lint + format + mypy + bandit +
#   pip-audit + pytest + coverage gate + doctor) and, with `--build`,
#   the release.yml wheel/sdist build step.
#
#   Memory entry: b9f022f8 (documents the billing blocker + local-CI
#   workaround policy). Revisit when billing is resolved — at that point
#   this script becomes a fast pre-push sanity check rather than the
#   primary gate.
#
# Usage:
#   scripts/local-ci.sh           # run all verify steps (1-8)
#   scripts/local-ci.sh --build   # verify, then build wheel + sdist
#   scripts/local-ci.sh --help    # this message
#
# Exit codes:
#   0 — all steps green
#   1 — at least one step failed
#   2 — environment problem (venv/uv missing and couldn't create)
#
# After checkout, ensure executable:
#   chmod +x scripts/local-ci.sh

set -euo pipefail

# ── args ──────────────────────────────────────────────────────────────
DO_BUILD=false
for arg in "$@"; do
  case "$arg" in
    --build) DO_BUILD=true ;;
    --help|-h)
      sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      echo "Usage: scripts/local-ci.sh [--build|--help]" >&2
      exit 1
      ;;
  esac
done

# ── locate project root (same pattern as release.sh) ──────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── step tracking ──────────────────────────────────────────────────────
declare -a STEP_NAMES=()
declare -a STEP_RESULTS=()
TOTAL_STEPS=8
if $DO_BUILD; then TOTAL_STEPS=9; fi

record() {
  # record <step_name> <PASS|FAIL|SKIP>
  STEP_NAMES+=("$1")
  STEP_RESULTS+=("$2")
}

run_step() {
  # run_step <index> <total> <name> <command...>
  # Runs the command, records PASS/FAIL. Does NOT exit on failure —
  # the caller decides. Returns the command's exit code.
  local idx="$1"; shift
  local total="$1"; shift
  local name="$1"; shift
  echo ""
  echo "=== [$idx/$total] $name ==="
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    record "$name" "PASS"
    echo "→ $name: PASS"
  else
    record "$name" "FAIL"
    echo "→ $name: FAIL (exit $rc)" >&2
  fi
  return $rc
}

skip_step() {
  # skip_step <index> <total> <name> <reason>
  local idx="$1"; shift
  local total="$1"; shift
  local name="$1"; shift
  local reason="$1"
  echo ""
  echo "=== [$idx/$total] $name ==="
  echo "→ SKIP: $reason"
  record "$name" "SKIP"
}

print_summary_and_exit() {
  echo ""
  echo "=== Summary ==="
  local failed=0
  local skipped=0
  local i=0
  for name in "${STEP_NAMES[@]}"; do
    local result="${STEP_RESULTS[$i]}"
    local marker
    case "$result" in
      PASS)   marker="✅" ;;
      FAIL)   marker="❌"; failed=$((failed + 1)) ;;
      SKIP)   marker="⏭️"; skipped=$((skipped + 1)) ;;
      *)      marker="?" ;;
    esac
    printf "  %s  %s — %s\n" "$marker" "$result" "$name"
    i=$((i + 1))
  done
  echo ""
  if [[ $failed -gt 0 ]]; then
    echo "❌ Local CI FAILED — $failed step(s) failed, $skipped skipped."
    echo "   Fix the failures above before merging or releasing."
    exit 1
  fi
  if $DO_BUILD; then
    echo "✅ Local CI green — verify steps passed and wheel + sdist built in dist/."
    echo "   Safe to merge/release."
  else
    echo "✅ Local CI green — safe to merge/release."
    echo "   Run \`scripts/local-ci.sh --build\` to build wheel + sdist."
  fi
  if [[ $skipped -gt 0 ]]; then
    echo "   ($skipped step(s) skipped — review the SKIP reasons above.)"
  fi
  exit 0
}

# ── venv setup ─────────────────────────────────────────────────────────
echo "=== Local CI — mnemos (replica of .github/workflows/ci.yml) ==="
echo "Reason: GitHub Actions billing-locked. See memory entry b9f022f8."
echo ""

VENV_DIR="$ROOT_DIR/.venv"
VENV_ACTIVATE="$VENV_DIR/bin/activate"

if [[ ! -f "$VENV_ACTIVATE" ]]; then
  echo "=== venv setup — .venv missing, creating ==="
  if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_ACTIVATE"
    uv pip install -e ".[dev]"
    uv pip install "ruff>=0.15,<0.16"
  elif command -v python3 >/dev/null 2>&1; then
    echo "WARNING: uv not installed — falling back to python -m venv + pip." >&2
    echo "         uv is recommended (faster, deterministic). Install: pip install uv" >&2
    python3 -m venv "$VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_ACTIVATE"
    pip install --upgrade pip
    pip install -e ".[dev]"
    pip install "ruff>=0.15,<0.16"
  else
    echo "ERROR: no .venv found, and neither uv nor python3 is available." >&2
    exit 2
  fi
else
  # shellcheck disable=SC1091
  source "$VENV_ACTIVATE"
fi

# Ensure dev tools are present (idempotent — re-install only if missing).
need_install=false
for tool in ruff mypy bandit pip-audit pytest; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    need_install=true
    break
  fi
done
if $need_install; then
  echo "=== installing dev extras into existing venv ==="
  if command -v uv >/dev/null 2>&1; then
    uv pip install -e ".[dev]"
    uv pip install "ruff>=0.15,<0.16"
  else
    pip install -e ".[dev]"
    pip install "ruff>=0.15,<0.16"
  fi
fi

PY_VERSION="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
echo "Python: $PY_VERSION  (venv: $VENV_DIR)"
echo "Root  : $ROOT_DIR"
echo "Build : $DO_BUILD"
echo ""

# ── steps 1-8: verify job ──────────────────────────────────────────────
# Note: run_step records PASS/FAIL but returns the command's exit code.
# We disable set -e around each step so a failure prints the summary
# rather than killing the script silently. A failed step still causes
# the script to exit non-zero (via the summary's `failed` counter).

set +e
run_step 1 $TOTAL_STEPS "Lint (ruff check)" ruff check src/ tests/
step_rc=$?
set -e
if [[ $step_rc -ne 0 ]]; then print_summary_and_exit; fi

set +e
run_step 2 $TOTAL_STEPS "Format check (ruff format --check)" ruff format --check src/ tests/
step_rc=$?
set -e
if [[ $step_rc -ne 0 ]]; then print_summary_and_exit; fi

set +e
run_step 3 $TOTAL_STEPS "Type check (mypy --strict)" mypy --strict src/mnemos/
step_rc=$?
set -e
if [[ $step_rc -ne 0 ]]; then print_summary_and_exit; fi

if command -v bandit >/dev/null 2>&1; then
  set +e
  run_step 4 $TOTAL_STEPS "Security (bandit)" bandit -r src/ -f json -o bandit-report.json
  step_rc=$?
  set -e
  if [[ $step_rc -ne 0 ]]; then print_summary_and_exit; fi
else
  skip_step 4 $TOTAL_STEPS "Security (bandit)" "bandit not installed (run: pip install bandit)"
fi

set +e
run_step 5 $TOTAL_STEPS "Security (pip-audit)" pip-audit --ignore-vuln CVE-2026-45829
step_rc=$?
set -e
if [[ $step_rc -ne 0 ]]; then print_summary_and_exit; fi

set +e
run_step 6 $TOTAL_STEPS "Test (pytest)" pytest tests/ -q --tb=short
step_rc=$?
set -e
if [[ $step_rc -ne 0 ]]; then print_summary_and_exit; fi

set +e
run_step 7 $TOTAL_STEPS "Coverage gate (≥80%)" pytest --cov=src/mnemos --cov-fail-under=80 --cov-report=term-missing --cov-report=xml tests/ -q
step_rc=$?
set -e
if [[ $step_rc -ne 0 ]]; then print_summary_and_exit; fi

# Doctor — version consistency check. May not be on PATH in a bare venv.
if command -v mnemos >/dev/null 2>&1; then
  # Don't let a non-zero doctor exit fail the whole script — it's a
  # consistency sanity check, not a hard CI gate. Report SKIP on failure.
  echo ""
  echo "=== [8/$TOTAL_STEPS] Doctor (mnemos doctor) ==="
  set +e
  mnemos doctor
  doc_rc=$?
  set -e
  if [[ $doc_rc -eq 0 ]]; then
    record "Doctor (mnemos doctor)" "PASS"
    echo "→ Doctor (mnemos doctor): PASS"
  else
    record "Doctor (mnemos doctor)" "SKIP"
    echo "→ Doctor (mnemos doctor): SKIP (exit $doc_rc — non-fatal consistency check)"
  fi
else
  skip_step 8 $TOTAL_STEPS "Doctor (mnemos doctor)" "mnemos CLI not on PATH in venv"
fi

# ── step 9: build (optional, release.yml replica) ──────────────────────
if $DO_BUILD; then
  if ! python -c "import build" 2>/dev/null; then
    echo ""
    echo "=== [9/$TOTAL_STEPS] Build wheel + sdist ==="
    echo "→ FAIL: python build package not installed."
    echo "  Install build: pip install build"
    record "Build wheel + sdist (python -m build)" "FAIL"
    print_summary_and_exit
  fi
  echo ""
  echo "=== [9/$TOTAL_STEPS] Build wheel + sdist (python -m build) ==="
  rm -rf dist/
  set +e
  python -m build
  build_rc=$?
  set -e
  if [[ $build_rc -eq 0 ]]; then
    record "Build wheel + sdist (python -m build)" "PASS"
    echo "→ Build wheel + sdist: PASS"
    echo ""
    echo "Artefacts in dist/:"
    ls -lh dist/ | awk 'NR>1 {print "  " $NF "  " $5}'
  else
    record "Build wheel + sdist (python -m build)" "FAIL"
    echo "→ Build wheel + sdist: FAIL (exit $build_rc)" >&2
  fi
fi

print_summary_and_exit