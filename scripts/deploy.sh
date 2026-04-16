#!/bin/bash
# AI-Brain: container deployment helper
# Usage: ./scripts/deploy.sh [command]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="localhost/ai-brain:latest"

cd "$PROJECT_DIR"

usage() {
    cat <<EOF
AI-Brain container deployment

Usage: $0 <command>

Commands:
  build          Build container image
  up             Start with podman-compose
  down           Stop podman-compose
  logs           Show logs
  up-ollama      Start with Ollama sidecar
  kube-up        Deploy via podman kube play
  kube-down      Stop kube deployment
  quadlet        Install systemd quadlet unit
  shell          Open shell in running container
  cli <args>     Run brain CLI in container
  status         Show container status

EOF
}

cmd_build() {
    echo "Building $IMAGE_NAME..."
    podman build -t "$IMAGE_NAME" -f Containerfile .
    echo "Done. Image: $IMAGE_NAME"
}

cmd_up() {
    podman-compose up -d
    echo "AI-Brain API: http://localhost:8787"
    echo "Swagger UI:   http://localhost:8787/docs"
}

cmd_down() {
    podman-compose down
}

cmd_logs() {
    podman-compose logs -f ai-brain
}

cmd_up_ollama() {
    podman-compose --profile ollama up -d
    echo "Pulling nomic-embed-text model..."
    podman exec ai-brain-ollama ollama pull nomic-embed-text
    echo ""
    echo "AI-Brain API: http://localhost:8787"
    echo "Ollama:       http://localhost:11434"
    echo ""
    echo "Update config.container.yaml:"
    echo "  embedding.provider: ollama"
    echo "  embedding.ollama_url: http://ollama:11434"
}

cmd_kube_up() {
    # Ensure volumes exist
    podman volume create brain-data 2>/dev/null || true
    podman volume create brain-vault 2>/dev/null || true
    podman kube play deploy/kube/ai-brain-pod.yaml
    echo "AI-Brain API: http://localhost:8787"
}

cmd_kube_down() {
    podman kube down deploy/kube/ai-brain-pod.yaml
}

cmd_quadlet() {
    local target_dir="$HOME/.config/containers/systemd"
    mkdir -p "$target_dir"
    cp deploy/quadlet/ai-brain.container "$target_dir/"
    systemctl --user daemon-reload
    echo "Quadlet installed. Start with:"
    echo "  systemctl --user start ai-brain"
    echo "  systemctl --user enable ai-brain  # autostart"
}

cmd_shell() {
    podman exec -it ai-brain /bin/bash
}

cmd_cli() {
    podman exec ai-brain brain "$@"
}

cmd_status() {
    echo "=== Containers ==="
    podman ps --filter name=ai-brain --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    echo ""
    echo "=== Volumes ==="
    podman volume ls --filter name=brain
}

case "${1:-}" in
    build)      cmd_build ;;
    up)         cmd_up ;;
    down)       cmd_down ;;
    logs)       cmd_logs ;;
    up-ollama)  cmd_up_ollama ;;
    kube-up)    cmd_kube_up ;;
    kube-down)  cmd_kube_down ;;
    quadlet)    cmd_quadlet ;;
    shell)      cmd_shell ;;
    cli)        shift; cmd_cli "$@" ;;
    status)     cmd_status ;;
    *)          usage ;;
esac
