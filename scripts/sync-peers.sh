#!/usr/bin/env bash
# sync-peers.sh — cron-ready batch sync between two mnemos instances.
#
# Federation Phase 0 (ArchCom 2026-07-17 contract §3.1, issue #85 part 2b).
# Offline batch sync: export from SOURCE → transfer → import into TARGET.
# No network call is made by mnemos itself; the TRANSFER step uses the
# operator-chosen transport (rsync / scp / cp over a shared volume).
#
# Usage: set the required env vars below, then run this script. Edit the
# TRANSFER step for your transport. The script is a TEMPLATE — it is NOT
# executable without the env vars set (it will exit 2 with a usage message).
#
# Required env:
#   SOURCE_MNEMOS_DIR        — path to source mnemos repo (with venv at .venv)
#   TARGET_MNEMOS_DIR        — path to target mnemos repo (with venv at .venv)
#   SHARED_PROJECTS          — space-separated list of project slugs to sync
#
# Optional env:
#   MNEMOS_EXPORT_PASSPHRASE — encryption passphrase (required if ENCRYPT=1)
#   ENCRYPT                  — "1" to encrypt export (default: 0)
#   TRANSFER_METHOD          — "rsync" | "scp" | "cp" (default: cp)
#   TRANSFER_DEST_HOST       — target host for rsync/scp (empty → local cp)
#   SYNC_FILE                — path to export file (default: /tmp/mnemos-sync-$(date +%s).json)
#   DRY_RUN                  — "1" for end-to-end dry-run (no writes, no transfer)
#   SOURCE_CONFIG            — path to source config.yaml (default: discovery)
#   TARGET_CONFIG            — path to target config.yaml (default: discovery)
#
# Crontab example (hourly encrypted sync to a peer host over scp):
#   0 * * * * SOURCE_MNEMOS_DIR=/opt/mnemos-a TARGET_MNEMOS_DIR=/opt/mnemos-b \
#             SHARED_PROJECTS="project-umbra project-mnemos" \
#             MNEMOS_EXPORT_PASSPHRASE="$PASS" ENCRYPT=1 \
#             TRANSFER_METHOD=scp TRANSFER_DEST_HOST=peer.example.com \
#             /opt/mnemos-a/scripts/sync-peers.sh >> /var/log/mnemos-sync.log 2>&1
#
# Exit codes:
#   0 — sync completed (export + transfer + import all green)
#   1 — a sync step failed (mnemos sync export/import returned non-zero)
#   2 — env-var validation failed (script not configured)

set -euo pipefail

# ── env-var validation ───────────────────────────────────────────────────────

_err() {
    echo "ERROR: $*" >&2
}

if [[ -z "${SOURCE_MNEMOS_DIR:-}" ]]; then
    _err "SOURCE_MNEMOS_DIR is not set (path to source mnemos repo with venv)."
    exit 2
fi
if [[ -z "${TARGET_MNEMOS_DIR:-}" ]]; then
    _err "TARGET_MNEMOS_DIR is not set (path to target mnemos repo with venv)."
    exit 2
fi
if [[ -z "${SHARED_PROJECTS:-}" ]]; then
    _err "SHARED_PROJECTS is not set (space-separated project slugs to sync)."
    exit 2
fi

ENCRYPT="${ENCRYPT:-0}"
TRANSFER_METHOD="${TRANSFER_METHOD:-cp}"
TRANSFER_DEST_HOST="${TRANSFER_DEST_HOST:-}"
SYNC_FILE="${SYNC_FILE:-/tmp/mnemos-sync-$(date +%s).json}"
DRY_RUN="${DRY_RUN:-0}"
SOURCE_CONFIG="${SOURCE_CONFIG:-}"
TARGET_CONFIG="${TARGET_CONFIG:-}"

if [[ "$ENCRYPT" == "1" && -z "${MNEMOS_EXPORT_PASSPHRASE:-}" ]]; then
    _err "ENCRYPT=1 but MNEMOS_EXPORT_PASSPHRASE is not set."
    exit 2
fi

# Optional per-side config flags (only added when set).
_source_config_arg=()
if [[ -n "$SOURCE_CONFIG" ]]; then
    _source_config_arg=(--config "$SOURCE_CONFIG")
fi
_target_config_arg=()
if [[ -n "$TARGET_CONFIG" ]]; then
    _target_config_arg=(--config "$TARGET_CONFIG")
fi

# ── helpers ──────────────────────────────────────────────────────────────────

# Activate a venv if present; otherwise assume `mnemos` is on PATH.
_activate_venv() {
    local dir="$1"
    if [[ -f "$dir/.venv/bin/activate" ]]; then
        # shellcheck disable=SC1091
        source "$dir/.venv/bin/activate"
    fi
}

# ── 1. SOURCE: export ─────────────────────────────────────────────────────────

echo "[$(date -u +%FT%TZ)] sync-peers: exporting from $SOURCE_MNEMOS_DIR"
_activate_venv "$SOURCE_MNEMOS_DIR"

_export_args=(sync export --output "$SYNC_FILE" --shared-projects "$SHARED_PROJECTS")
_export_args+=("${_source_config_arg[@]}")
if [[ "$ENCRYPT" == "1" ]]; then
    _export_args+=(--encrypt)
fi
if [[ "$DRY_RUN" == "1" ]]; then
    _export_args+=(--dry-run)
fi

mnemos "${_export_args[@]}"

# ── 2. TRANSFER ───────────────────────────────────────────────────────────────
# Skip the transfer in dry-run (no file written) or when source == target
# (e.g. testing on a single instance).

if [[ "$DRY_RUN" == "1" ]]; then
    echo "[$(date -u +%FT%TZ)] sync-peers: dry-run — skipping transfer + import."
    exit 0
fi

if [[ ! -f "$SYNC_FILE" ]]; then
    _err "export produced no file at $SYNC_FILE (nothing to transfer)."
    exit 1
fi

echo "[$(date -u +%FT%TZ)] sync-peers: transferring via $TRANSFER_METHOD → $TARGET_MNEMOS_DIR"
case "$TRANSFER_METHOD" in
    cp)
        cp -- "$SYNC_FILE" "$TARGET_MNEMOS_DIR/${SYNC_FILE##*/}"
        TARGET_SYNC_FILE="$TARGET_MNEMOS_DIR/${SYNC_FILE##*/}"
        ;;
    rsync)
        if [[ -n "$TRANSFER_DEST_HOST" ]]; then
            rsync -az -- "$SYNC_FILE" "$TRANSFER_DEST_HOST:$SYNC_FILE"
        else
            rsync -az -- "$SYNC_FILE" "$TARGET_MNEMOS_DIR/${SYNC_FILE##*/}"
        fi
        TARGET_SYNC_FILE="$SYNC_FILE"
        ;;
    scp)
        if [[ -z "$TRANSFER_DEST_HOST" ]]; then
            _err "TRANSFER_METHOD=scp requires TRANSFER_DEST_HOST."
            exit 2
        fi
        scp -- "$SYNC_FILE" "$TRANSFER_DEST_HOST:$SYNC_FILE"
        TARGET_SYNC_FILE="$SYNC_FILE"
        ;;
    *)
        _err "unknown TRANSFER_METHOD: $TRANSFER_METHOD (use rsync | scp | cp)."
        exit 2
        ;;
esac

# ── 3. TARGET: import ─────────────────────────────────────────────────────────
# On a remote target (rsync/scp to a peer host), the operator runs the
# import step on the peer separately (this script cannot ssh in and run
# the target venv). The local-cp path runs the import here.

if [[ "$TRANSFER_METHOD" == "cp" ]]; then
    echo "[$(date -u +%FT%TZ)] sync-peers: importing into $TARGET_MNEMOS_DIR"
    _activate_venv "$TARGET_MNEMOS_DIR"

    _import_args=(sync import "$TARGET_SYNC_FILE")
    _import_args+=("${_target_config_arg[@]}")
    if [[ "$ENCRYPT" == "1" ]]; then
        _import_args+=(--passphrase-env MNEMOS_EXPORT_PASSPHRASE)
    fi
    if [[ "$DRY_RUN" == "1" ]]; then
        _import_args+=(--dry-run)
    fi

    mnemos "${_import_args[@]}"
else
    echo "[$(date -u +%FT%TZ)] sync-peers: remote transfer — run the import on $TRANSFER_DEST_HOST:"
    echo "    mnemos sync import $TARGET_SYNC_FILE"
    if [[ "$ENCRYPT" == "1" ]]; then
        echo "      --passphrase-env MNEMOS_EXPORT_PASSPHRASE"
    fi
fi

# ── 4. cleanup ────────────────────────────────────────────────────────────────
# Leave the sync file in place on success — the audit log captures the
# path, and an operator may want to re-import after a fix. The crontab
# should rotate / clean /tmp separately.

echo "[$(date -u +%FT%TZ)] sync-peers: done (sync file: $SYNC_FILE)"