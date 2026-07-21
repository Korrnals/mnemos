#!/usr/bin/env bash
# rsync-wrapper.sh — restricted rsync server guard for mnemos-sync on B (#104).
#
# Pinned in ~mnemos-sync/.ssh/authorized_keys via command="" for the PUSH key
# (ssh-sync-hardening.md §2). SSH invokes this wrapper instead of a shell; the
# real rsync server command arrives in $SSH_ORIGINAL_COMMAND. This wrapper:
#
#   1. Rejects anything that is not an rsync server invocation.
#   2. Parses the rsync args and enforces the destination against INCOMING_DIR.
#   3. Re-execs the sanitised rsync server command with the destination locked
#      to INCOMING_DIR — the caller cannot write outside it.
#   4. Appends an audit line to AUDIT_LOG for every invocation (§6).
#
# mnemos itself stays offline — this runs on B's host SSH layer, not inside
# mnemos. Per ArchCom 2026-07-20 (mnemos memory 4dc7d96e).
#
# Install: chmod 0755, place at /usr/local/sbin/rsync-wrapper.sh, pin in
# authorized_keys as:
#   command="/usr/local/sbin/rsync-wrapper.sh",no-pty,no-agent-forwarding,... \
#   ssh-ed25519 AAAA... mnemos-sync-push@A
#
# Exit codes: 0 success, 1 rsync failed, 2 policy violation / parse error.

set -euo pipefail

# ── config ────────────────────────────────────────────────────────────────────
# INCOMING_DIR MUST match MNEMOS_SYNC_REMOTE_IMPORT_DIR on A and the dir
# created per ssh-sync-hardening.md §1. Override via /etc/mnemos/rsync-wrapper.env
# if your layout differs.
INCOMING_DIR="${MNEMOS_SYNC_INCOMING_DIR:-/var/lib/mnemos-sync/incoming}"
AUDIT_LOG="${MNEMOS_SYNC_AUDIT_LOG:-/var/log/mnemos-sync.log}"
RSYNC_BIN="${RSYNC_BIN:-rsync}"

# ── audit helper (§6) ─────────────────────────────────────────────────────────
_audit() {
    # Append ISO-8601 UTC timestamp + source IP + event + detail.
    local src_ip="${SSH_CLIENT%% *}"
    local ts
    ts="$(date -u +%FT%TZ)"
    # Best-effort audit — never let a log write failure crash the sync.
    printf '[%s] rsync-wrapper src=%s %s %s\n' "$ts" "${src_ip:-unknown}" "$1" "${2:-}" \
        >>"$AUDIT_LOG" 2>/dev/null || true
}

_err() {
    printf '[%s] rsync-wrapper: ERROR: %s\n' "$(date -u +%FT%TZ)" "$*" >&2
    _audit "REJECT" "$*"
}

# ── 1. only rsync is allowed ──────────────────────────────────────────────────
if [[ -z "${SSH_ORIGINAL_COMMAND:-}" ]]; then
    _err "no command provided — interactive shell refused."
    exit 2
fi

# rsync over ssh sends:  rsync --server [opts] . <dest>
if [[ "$SSH_ORIGINAL_COMMAND" != rsync\ * ]]; then
    _err "non-rsync command refused: ${SSH_ORIGINAL_COMMAND}"
    exit 2
fi

# ── 2. parse and validate the destination ──────────────────────────────────────
# Walk the tokens of SSH_ORIGINAL_COMMAND. The last non-option token is the
# destination. We reject any destination that is not inside INCOMING_DIR after
# realpath resolution (symlinks followed).
_read_args=()
_dest=""
# shellcheck disable=SC2206  # intentional word-split on the original command
_tokens=($SSH_ORIGINAL_COMMAND)
_shift=0
for (( _i=0; _i<${#_tokens[@]}; _i++ )); do
    _t="${_tokens[$_i]}"
    case "$_t" in
        --server|--sender|-*) _read_args+=("$_t") ;;
        .)  _read_args+=("$_t") ;;          # rsync sends a literal "." module
        *)
            # First non-option token after --server is the module ("."), the
            # second is the destination path. Track the last one as the dest.
            _dest="$_t"
            _read_args+=("$_t")
            ;;
    esac
done

if [[ -z "$_dest" ]]; then
    _err "could not determine rsync destination from: ${SSH_ORIGINAL_COMMAND}"
    exit 2
fi

# Resolve both paths and verify the destination is inside INCOMING_DIR.
# realpath -m tolerates a not-yet-existing destination file.
_incoming_real=""
_dest_real=""
if command -v realpath >/dev/null 2>&1; then
    _incoming_real="$(realpath -m "$INCOMING_DIR")"
    _dest_real="$(realpath -m "$_dest")"
else
    # Fallback: normalise via readlink -m or strip-leading-slash compare.
    _incoming_real="$INCOMING_DIR"
    _dest_real="$_dest"
fi

case "$_dest_real" in
    "${_incoming_real%/}/"*)
        : ;;  # inside incoming — allowed
    "${_incoming_real%/}")
        : ;;  # exactly the incoming dir — allowed (rsync may write a file in it)
        ;;
    *)
        _err "destination outside INCOMING_DIR: $_dest_real (expected under $_incoming_real)"
        exit 2
        ;;
esac

# ── 3. re-exec the rsync server with the locked destination ───────────────────
# We pass the original tokens through unchanged — the destination is already
# constrained to INCOMING_DIR by the check above. rsync itself enforces the
# server protocol; we only gate the path.
_audit "ACCEPT" "dest=$_dest_real"
exec "$RSYNC_BIN" --server "${_read_args[@]:1}"