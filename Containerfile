# Mnemos — Memory & Knowledge Server for AI Agents
# Build: podman build -t mnemos .
# Run:   podman run -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 mnemos
FROM docker.io/library/python:3.12-slim AS base

LABEL maintainer="abyss"
LABEL description="Mnemos: hybrid long-term memory system for AI agents"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer caching)
COPY pyproject.toml README.md ./
COPY src/ ./src/
# integrations/ is required at build time — pyproject.toml force-include
# ships it inside the wheel via [tool.hatch.build.targets.wheel.force-include].
COPY integrations/ ./integrations/
# scripts/ is required at build time — force-include ships it inside the wheel
# as mnemos/scripts/ so `mnemos integration setup` can find mcp-setup.sh.
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir ".[mcp]"

# Pre-download ChromaDB's default embedding model (all-MiniLM-L6-v2 ONNX, ~90MB)
# so vector search works offline out of the box.
# NOTE: /data is a volume mount at runtime — anything written there during build
# is hidden. We pre-download to /opt/model-cache and copy to /data on startup.
ENV HOME=/data
RUN mkdir -p /opt/model-cache/.cache/chroma/onnx_models/all-MiniLM-L6-v2 && \
    HOME=/opt/model-cache python3 -c "\
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; \
DefaultEmbeddingFunction() \
" && \
    echo 'Embedding model pre-downloaded to /opt/model-cache'

# Entrypoint script: copies pre-downloaded model into the mounted PVC on first boot
RUN cat > /app/entrypoint.sh <<'SCRIPT'
#!/bin/bash
set -e
# Copy embedding model cache to PVC if not already present
if [ ! -d /data/.cache/chroma/onnx_models/all-MiniLM-L6-v2 ]; then
    echo "[entrypoint] Copying pre-downloaded embedding model to /data ..."
    mkdir -p /data/.cache/chroma
    cp -r /opt/model-cache/.cache/chroma/* /data/.cache/chroma/ 2>/dev/null || true
    echo "[entrypoint] Embedding model ready."
fi
exec "$@"
SCRIPT
RUN chmod +x /app/entrypoint.sh

# Default directories
RUN mkdir -p /data /vault

# Copy container config (with auth + CORS configured for 0.0.0.0 bind)
COPY config.container.yaml /app/config.yaml

# Set config path for container environment
ENV MNEMOS_CONFIG=/app/config.yaml

EXPOSE 8787

# Default: HTTP API server (mnemos serve wraps fastapi + uvicorn)
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["mnemos", "serve"]
