#!/usr/bin/env bash
# Install Mnemos systemd user services.
# Usage: ./install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HOME/.config/systemd/user"

mkdir -p "$TARGET"
cp "$SCRIPT_DIR/mnemos-server.service" "$TARGET/"
cp "$SCRIPT_DIR/mnemos-watcher.service" "$TARGET/"

systemctl --user daemon-reload
echo "Installed. Enable with:"
echo "  systemctl --user enable --now mnemos-server"
echo "  systemctl --user enable --now mnemos-watcher"
