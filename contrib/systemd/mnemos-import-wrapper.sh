#!/usr/bin/env bash
# mnemos-import-wrapper.sh — restricted import trigger guard on B (#104).
#
# Pinned in ~mnemos-sync/.ssh/authorized_keys via command="" for the TRIGGER
# key (ssh-sync-hardening.md §2). SSH invokes this wrapper instead of a shell;
# the real import command arrives in $SSH_ORIGINAL_COMMAND. This wrapper:
#
#   1. Rejects anything that is not a `mnemos sync import` invocation.
#   2. Rewrites the positional source path to an absolute path under INCOMING_DIR
#      (the caller cannot read outside it).
#   3. Pins --passphrase-env to PASSPHRASE_ENV_NAME — the caller cannot inject
#      an arbitrary env var name to read another secret.
#   4. Appends an audit line to AUDIT_LOG for every invocation (§6).
#   5. Resolves the mnemos CLI on B via MNEMOS_SYNC_REMOTE_MNEMOS_BIN or PATH.
#
# mnemos itself stays offline — this runs on B's host SSH layer. Per ArchCom
# 2026-07-20 (mnemos memory 4dc7d96e).
#
# Install: chmod 0755, place at /usr/local/sbin/mnemos-import-wrapper.sh, pin in
# authorized_keys as:
#   command="/usr/local/sbin/mnemos-import-wrapper.sh",no-pty,no-agent-forwarding,... \
#   ssh-ed25519 AAAA... mnemos-sync-trigger@A
#
# Exit codes: 0 success, 1 import failed, 2 policy violation / parse error.

set -euo pipefail

# ── config ────────────────────────────────────────────────────────────────────
# INCOMING_DIR MUST match MNEMOS_SYNC_REMOTE_IMPORT_DIR on A and rsync-wrapper.sh.
# PASSPHRASE_ENV_NAME is the env var name that `mnemos sync import` reads the
# passphrase from on B. Provision the VALUE on B's systemd environment — never
# on A and never inline in this file. Override via /etc/mnemos/import-wrapper.env.
INCOMING_DIR="${MNEMOS_SYNC_INCOMING_DIR:-/var/lib/mnemos-sync/incoming}"
PASSPHRASE_ENV_NAME="${MNEMOS_SYNC_PASSPHRASE_ENV:-MNEMOS_EXPORT_PASSPHRASE}"
AUDIT_LOG="${MNEMOS_SYNC_AUDIT_LOG:-/var/log/mnemos-sync.log}"
MNEMOS_BIN="${MNEMOS_SYNC_REMOTE_MNEMOS_BIN:-}"

# ── audit helper (§6) ─────────────────────────────────────────────────────────
_audit() {
    local src_ip="${SSH_CLIENT%% *}"
    local ts
    ts="$(date -u +%FT%TZ)"
    printf '[%s] mnemos-import-wrapper src=%s %s %s\n' "$ts" "${src_ip:-unknown}" "$1" "${2:-}" \
        >>"$AUDIT_LOG" 2>/dev/null || true
}

_err() {
    printf '[%s] mnemos-import-wrapper: ERROR: %s\n' "$(date -u +%FT%TZ)" "$*" >&2
    _audit "REJECT" "$*"
}

# ── 1. only `mnemos sync import` is allowed ───────────────────────────────────
if [[ -z "${SSH_ORIGINAL_COMMAND:-}" ]]; then
    _err "no command provided — interactive shell refused."
    exit 2
fi

# Accept:  mnemos sync import <path> [--passphrase-env <name>] [--dry-run]
# Reject anything else (including `mnemos sync export`, shells, other CLIs).
if [[ "$SSH_ORIGINAL_COMMAND" != "mnemos sync import "* ]]; then
    _err "non-import command refused: ${SSH_ORIGINAL_COMMAND}"
    exit 2
fi

# ── 2. discover the mnemos CLI on B ───────────────────────────────────────────
if [[ -z "$MNEMOS_BIN" ]]; then
    if command -v mnemos >/dev/null 2>&1; then
        MNEMOS_BIN="$(command -v mnemos)"
    elif [[ -x /usr/local/bin/mnemos ]]; then
        MNEMOS_BIN=/usr/local/bin/mnemos
    elif [[ -x /opt/mnemos/.venv/bin/mnemos ]]; then
        MNEMOS_BIN=/opt/mnemos/.venv/bin/mnemos
    else
        _err "mnemos CLI not found on B. Set MNEMOS_SYNC_REMOTE_MNEMOS_BIN."
        exit 2
    fi
fi

# ── 3. parse the import command ───────────────────────────────────────────────
# Tokenise and rebuild a sanitised argv. The positional source path is the
# first non-option token; --passphrase-env is FORCED to PASSPHRASE_ENV_NAME
# regardless of what the caller sent (defence-in-depth: even if A is
# compromised and tries to point at another env var, B refuses).
_source=""
_passphrase_env_seen=0
_dry_run_seen=0
_extra_args=()
# shellcheck disable=SC2206
_tokens=($SSH_ORIGINAL_COMMAND)
# Drop the leading "mnemos sync import" — already validated above.
_shift=3
for (( _i=$_shift; _i<${#_tokens[@]}; _i++ )); do
    _t="${_tokens[$_i]}"
    case "$_t" in
        --passphrase-env)
            _passphrase_env_seen=1
            ;;  # value follows — consumed by the next iteration
        --dry-run)
            _dry_run_seen=1
            ;;
        --*)
            _extra_args+=("$_t")
            ;;
        *)
            if [[ $_passphrase_env_seen -eq 1 ]]; then
                # Caller's passphrase-env value — IGNORE, we pin our own.
                _passphrase_env_seen=0
            elif [[ -z "$_source" ]]; then
                _source="$_t"
            else
                _err "unexpected extra positional argument: $_t"
                exit 2
            fi
            ;;
    esac
done

if [[ -z "$_source" ]]; then
    _err "missing source path in: ${SSH_ORIGINAL_COMMAND}"
    exit 2
fi

# ── 4. lock the source path under INCOMING_DIR ────────────────────────────────
# The caller may send a relative or absolute path; we force it absolute under
# INCOMING_DIR. realpath -m tolerates a not-yet-existing source file.
_incoming_real=""
_source_real=""
if command -v realpath >/dev/null 2>&1; then
    _incoming_real="$(realpath -m "$INCOMING_DIR")"
    # If the caller sent a path already inside INCOMING_DIR, keep it; else join.
    case "$_source" in
        "$_incoming_real"/*) _source_real="$(realpath -m "$_source")" ;;
        /*) _source_real="$(realpath -m "$_source")" ;;
        *)  _source_real="$(realpath -m "${_incoming_real%/}/${_source}")" ;;
    esac
else
    _incoming_real="$INCOMING_DIR"
    case "$_source" in
        "$_incoming_real"/*) _source_real="$_source" ;;
        /*) _source_real="$_source" ;;
        *)  _source_real="${_incoming_real%/}/${_source}" ;;
    esac
fi

case "$_source_real" in
    "${_incoming_real%/}/"*)
        : ;;  # inside incoming — allowed
    *)
        _err "source path outside INCOMING_DIR: $_source_real (expected under $_incoming_real)"
        exit 2
        ;;
esac

if [[ ! -f "$_source_real" ]]; then
    _err "source file does not exist: $_source_real"
    exit 2
fi

# ── 5. build and exec the sanitised import command ────────────────────────────
# --passphrase-env is ALWAYS pinned to PASSPHRASE_ENV_NAME (defence-in-depth:
# even a compromised A cannot redirect the passphrase read to another var).
_cmd=("$MNEMOS_BIN" sync import "$_source_real" --passphrase-env "$PASSPHRASE_ENV_NAME")
if [[ $_dry_run_seen -eq 1 ]]; then
    _cmd+=(--dry-run)
fi
_cmd+=("${_extra_args[@]}")

_audit "ACCEPT" "source=$_source_real passphrase-env=$PASSPHRASE_ENV_NAME dry_run=$_dry_run_seen"
exec "${_cmd[@]}"