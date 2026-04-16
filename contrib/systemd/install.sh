#!/usr/bin/env bash
# Install AI-Brain systemd user services.
# Usage: ./install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HOME/.config/systemd/user"

mkdir -p "$TARGET"
cp "$SCRIPT_DIR/ai-brain-server.service" "$TARGET/"
cp "$SCRIPT_DIR/ai-brain-watcher.service" "$TARGET/"

systemctl --user daemon-reload
echo "Installed. Enable with:"
echo "  systemctl --user enable --now ai-brain-server"
echo "  systemctl --user enable --now ai-brain-watcher"
