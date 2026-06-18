#!/usr/bin/env bash
# scripts/install.sh — one-command Mnemos install
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --version 1.1.3 --extra mcp
#   curl -fsSL .../install.sh | bash -s -- --venv ~/.mnemos-venv --extra mcp,ollama
#
# Flags:
#   --version VERSION   Mnemos version to install (default: latest from GitHub Releases)
#   --extra EXTRAS      Comma-separated extras: mcp,ollama,openai,anthropic,gemini,dev,all (default: mcp)
#   --venv PATH         Create a venv at PATH and install there (default: ~/.mnemos-venv)
#   --no-venv           Install into the current Python (system/user), no venv
#   --uv                Use uv instead of pip (auto-detected if available)
#   --container         Pull and run the container image instead of a Python install
#   --port PORT         Container host port (default: 8787, only with --container)
#   --help              Show this help
#
# After install, the `mnemos` CLI is available. For MCP integration run:
#   curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/mcp-setup.sh | bash
set -euo pipefail

VERSION=""
EXTRAS="mcp"
VENV_PATH="${HOME}/.mnemos-venv"
NO_VENV=false
USE_UV=false
CONTAINER=false
CONTAINER_PORT=8787

# ── Colour helpers ────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; CYAN=''; NC=''
fi
info()  { printf "${CYAN}ℹ${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}✓${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC}  %s\n" "$*"; }
die()   { printf "${RED}✗${NC}  %s\n" "$*" >&2; exit 1; }

# ── Parse args ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)  VERSION="$2"; shift 2 ;;
    --extra)    EXTRAS="$2"; shift 2 ;;
    --venv)     VENV_PATH="$2"; NO_VENV=false; shift 2 ;;
    --no-venv)  NO_VENV=true; shift ;;
    --uv)       USE_UV=true; shift ;;
    --container) CONTAINER=true; shift ;;
    --port)      CONTAINER_PORT="$2"; shift 2 ;;
    --help|-h)  sed -n '2,18p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *)          die "Unknown flag: $1 (use --help)" ;;
  esac
done

# ── Detect Python ─────────────────────────────────────────────────
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" &>/dev/null; then
    ver="$("$candidate" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo 0)"
    if [[ "$(printf '%s\n' "3.11" "$ver" | sort -V | head -1)" == "3.11" ]]; then
      PYTHON="$candidate"; break
    fi
  fi
done
[[ -z "$PYTHON" ]] && die "Python >= 3.11 not found. Install from https://python.org or your package manager."
info "Using Python: $PYTHON ($($PYTHON --version 2>&1))"

# ── Detect uv ─────────────────────────────────────────────────────
if [[ "$USE_UV" == false ]] && command -v uv &>/dev/null; then
  USE_UV=true
  info "Found uv — will use it for faster installs."
fi

# ── Resolve version ───────────────────────────────────────────────
if [[ -z "$VERSION" ]]; then
  info "Detecting latest Mnemos release…"
  VERSION="$(curl -fsSL "https://api.github.com/repos/Korrnals/mnemos/releases/latest" 2>/dev/null \
    | grep -m1 '"tag_name"' | sed -E 's/.*"v?([^"]+)".*/\1/' || true)"
  [[ -z "$VERSION" ]] && die "Could not detect latest version. Specify --version manually."
fi
info "Installing Mnemos v${VERSION} (extras: ${EXTRAS})"

# ── Container path ────────────────────────────────────────────────
if [[ "$CONTAINER" == true ]]; then
  info "Container mode — pulling image ghcr.io/korrnals/mnemos:${VERSION}…"

  RUNTIME=""
  for r in podman docker; do
    if command -v "$r" &>/dev/null; then RUNTIME="$r"; break; fi
  done
  [[ -z "$RUNTIME" ]] && die "Neither podman nor docker found. Install one to use --container."

  "$RUNTIME" pull "ghcr.io/korrnals/mnemos:${VERSION}" || die "Failed to pull image."

  if "$RUNTIME" ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^mnemos$'; then
    warn "Container 'mnemos' already exists. Remove it first: $RUNTIME rm -f mnemos"
    die "Aborting to avoid clobbering existing container."
  fi

  "$RUNTIME" volume create mnemos-data 2>/dev/null || true
  "$RUNTIME" volume create mnemos-vault 2>/dev/null || true

  if [[ -z "${MNEMOS_API__TOTP_MASTER_KEY:-}" ]]; then
    warn "MNEMOS_API__TOTP_MASTER_KEY is not set."
    warn "The container binds 0.0.0.0 and requires auth — it will refuse to start without the key."
    warn "Generate one: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
    die "Set MNEMOS_API__TOTP_MASTER_KEY and re-run, or see docs/en/admin/runbooks/container-deployment.md"
  fi

  "$RUNTIME" run -d \
    --name mnemos \
    -p "${CONTAINER_PORT}:8787" \
    -v mnemos-data:/data \
    -v mnemos-vault:/vault \
    -e MNEMOS_API__TOTP_MASTER_KEY="${MNEMOS_API__TOTP_MASTER_KEY}" \
    "ghcr.io/korrnals/mnemos:${VERSION}" || die "Failed to start container."

  ok "Mnemos container started on port ${CONTAINER_PORT}."
  echo ""
  printf "${GREEN}✓${NC}  Done. Next steps:\n"
  printf "    curl -s http://localhost:${CONTAINER_PORT}/health | jq\n"
  printf "    # Swagger UI: http://localhost:${CONTAINER_PORT}/docs\n"
  printf "    # Logs: %s logs -f mnemos\n" "$RUNTIME"
  printf "    # Stop: %s stop mnemos\n" "$RUNTIME"
  printf "    Docs: docs/en/admin/runbooks/container-deployment.md\n"
  exit 0
fi

# ── Build wheel URL ───────────────────────────────────────────────
WHEEL_URL="https://github.com/Korrnals/mnemos/releases/download/v${VERSION}/mnemos-${VERSION}-py3-none-any.whl"

# ── Create venv (unless --no-venv) ────────────────────────────────
if [[ "$NO_VENV" == false ]]; then
  info "Creating virtual environment at ${VENV_PATH}…"
  if [[ "$USE_UV" == true ]]; then
    uv venv "$VENV_PATH"
  else
    "$PYTHON" -m venv "$VENV_PATH"
  fi
  # shellcheck disable=SC1091
  source "$VENV_PATH/bin/activate"
  ok "Virtual environment activated: $(which python)"
fi

# ── Install ───────────────────────────────────────────────────────
EXTRA_BRACKET="[${EXTRAS}]"
info "Installing mnemos${EXTRA_BRACKET} from ${WHEEL_URL}…"

if [[ "$USE_UV" == true ]]; then
  uv pip install "${WHEEL_URL}${EXTRA_BRACKET}"
else
  pip install --upgrade pip
  pip install "${WHEEL_URL}${EXTRA_BRACKET}"
fi

# ── Verify ────────────────────────────────────────────────────────
if command -v mnemos &>/dev/null; then
  INSTALLED_VER="$(mnemos --version 2>/dev/null || echo 'unknown')"
  ok "Mnemos ${INSTALLED_VER} installed successfully!"
else
  if [[ -x "${VENV_PATH}/bin/mnemos" ]]; then
    INSTALLED_VER="$("${VENV_PATH}/bin/mnemos" --version 2>/dev/null || echo 'unknown')"
    ok "Mnemos ${INSTALLED_VER} installed at ${VENV_PATH}/bin/mnemos"
  else
    warn "mnemos CLI not found on PATH. Check your venv activation."
  fi
fi

echo ""
printf "${GREEN}✓${NC}  Done. Next steps:\n"
printf "    mnemos add --content 'Hello' --tags project:test agent:setup gcw:learning\n"
printf "    mnemos search 'Hello'\n"
if [[ "${EXTRAS}" == *mcp* ]]; then
  printf "    # Register MCP server in VS Code:\n"
  printf "    curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/mcp-setup.sh | bash\n"
fi
if [[ "$NO_VENV" == false ]]; then
  echo ""
  printf "${YELLOW}⚠${NC}  Activate the venv in new shells:\n"
  printf "    source ${VENV_PATH}/bin/activate\n"
fi