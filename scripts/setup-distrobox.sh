#!/bin/bash
# AI-Brain: setup dedicated distrobox container
# Run from host: ./scripts/setup-distrobox.sh
set -euo pipefail

CONTAINER_NAME="ai-brain"
IMAGE="docker.io/library/ubuntu:24.04"
BRAIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== AI-Brain: Creating distrobox container ==="
echo "Container: $CONTAINER_NAME"
echo "Image:     $IMAGE"
echo "Project:   $BRAIN_DIR"
echo ""

# Create container if it doesn't exist
if ! distrobox list | grep -q "$CONTAINER_NAME"; then
    echo "Creating distrobox container..."
    distrobox create \
        --name "$CONTAINER_NAME" \
        --image "$IMAGE" \
        --yes
    echo "Container created."
else
    echo "Container '$CONTAINER_NAME' already exists."
fi

echo ""
echo "=== Setting up Python environment inside container ==="

# Run setup inside the container
distrobox enter "$CONTAINER_NAME" -- bash -c "
    set -euo pipefail
    cd '$BRAIN_DIR'

    echo '--- Installing system packages ---'
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv python3-dev build-essential git curl >/dev/null 2>&1

    echo '--- Creating Python venv ---'
    python3 -m venv .venv
    source .venv/bin/activate

    echo '--- Installing ai-brain (core, no torch) ---'
    pip install -e '.[dev]'

    echo '--- Copying default config ---'
    if [ ! -f config.yaml ]; then
        cp config.example.yaml config.yaml
        echo 'Created config.yaml from example.'
    fi

    echo '--- Creating vault directory ---'
    mkdir -p ~/brain-vault
    mkdir -p ~/.ai-brain

    echo ''
    echo '============================================='
    echo '  AI-Brain setup complete!'
    echo '============================================='
    echo ''
    echo 'Usage:'
    echo '  distrobox enter $CONTAINER_NAME'
    echo '  cd $BRAIN_DIR && source .venv/bin/activate'
    echo '  brain --help'
    echo ''
    echo 'Or run as container:'
    echo '  podman-compose up -d'
    echo ''
"
