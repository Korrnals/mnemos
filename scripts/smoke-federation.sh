#!/usr/bin/env bash
# scripts/smoke-federation.sh — local federation smoke test.
#
# Verifies the full Phase 0 federation roundtrip on a single host using
# two isolated mnemos-home directories:
#
#   1. Seed peer B with a clean decision memory.
#   2. Export B's memories as a compact federation payload.
#   3. Import the payload into peer A.
#   4. Search on A — the imported record must be findable.
#   5. Re-import the same payload — idempotent (skip, no duplicate).
#
# Prerequisites: mnemos CLI on PATH (or set MNEMOS_BIN), jq, mktemp.
# Runtime: < 10 s. Exits 0 on success, non-zero on any failure.
#
# See docs/en/admin/federation-testing.md for the cross-host variant.
set -euo pipefail

MNEMOS_BIN="${MNEMOS_BIN:-mnemos}"

command -v "$MNEMOS_BIN" >/dev/null 2>&1 || { echo "FATAL: $MNEMOS_BIN not on PATH"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "FATAL: jq not on PATH"; exit 1; }

TMPDIR="$(mktemp -d -t mnemos-smoke-XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

HOME_A="$TMPDIR/homeA"
HOME_B="$TMPDIR/homeB"
mkdir -p "$HOME_A" "$HOME_B"

PROJECT="smoke-fed"
AGENT_B="smoke-b"
PAYLOAD="$TMPDIR/compact.json"

echo "1. Seed peer B with a clean decision memory"
MNEMOS_HOME="$HOME_B" "$MNEMOS_BIN" add \
  "Smoke test: mnemos federation v2.12.1 verified via local roundtrip." \
  --tags "project:$PROJECT,agent:$AGENT_B,mnemos:decision" \
  --title "Federation smoke seed" >/dev/null

echo "2. Export B's memories as a compact federation payload"
MNEMOS_HOME="$HOME_B" "$MNEMOS_BIN" sync export \
  --output "$PAYLOAD" \
  --shared-projects "$PROJECT" >/dev/null

RECORD_COUNT=$(jq '.records | length' "$PAYLOAD")
[[ "$RECORD_COUNT" -ge 1 ]] || { echo "FAIL: export produced 0 records"; exit 1; }
echo "   exported $RECORD_COUNT record(s)"

echo "3. Import the payload into peer A"
IMPORT_OUT="$(MNEMOS_HOME="$HOME_A" "$MNEMOS_BIN" sync import "$PAYLOAD" 2>&1)"
IMPORTED=$(echo "$IMPORT_OUT" | grep -oE 'Imported: [0-9]+' | grep -oE '[0-9]+')
[[ "$IMPORTED" -ge 1 ]] || { echo "FAIL: import did not import any record"; echo "$IMPORT_OUT"; exit 1; }
echo "   imported $IMPORTED record(s)"

echo "4. Search on A — imported record must be findable"
SEARCH_OUT="$(MNEMOS_HOME="$HOME_A" "$MNEMOS_BIN" search "federation smoke" --limit 3 2>&1)"
echo "$SEARCH_OUT" | grep -q "Federation smoke seed" \
  || { echo "FAIL: imported record not found in search"; echo "$SEARCH_OUT"; exit 1; }
echo "   search found the imported record"

echo "5. Re-import the same payload — idempotent (skip, no duplicate)"
REIMPORT_OUT="$(MNEMOS_HOME="$HOME_A" "$MNEMOS_BIN" sync import "$PAYLOAD" 2>&1)"
REIMPORTED=$(echo "$REIMPORT_OUT" | grep -oE 'Imported: [0-9]+' | grep -oE '[0-9]+')
SKIPPED=$(echo "$REIMPORT_OUT" | grep -oE 'skipped: [0-9]+' | grep -oE '[0-9]+')
[[ "$REIMPORTED" -eq 0 ]] || { echo "FAIL: re-import duplicated records"; echo "$REIMPORT_OUT"; exit 1; }
[[ "$SKIPPED" -ge 1 ]] || { echo "FAIL: re-import did not skip existing"; echo "$REIMPORT_OUT"; exit 1; }
echo "   re-import idempotent: imported=$REIMPORTED, skipped=$SKIPPED"

echo
echo "SMOKE PASS: federation A→B compact→import→search roundtrip works, idempotent re-import OK"