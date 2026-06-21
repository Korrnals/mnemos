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
RUN pip install --no-cache-dir ".[mcp]"

# Default directories
RUN mkdir -p /data /vault

# Copy container config (with auth + CORS configured for 0.0.0.0 bind)
COPY config.container.yaml /app/config.yaml

# Set config path for container environment
ENV MNEMOS_CONFIG=/app/config.yaml

EXPOSE 8787

# Default: HTTP API server (mnemos serve wraps fastapi + uvicorn)
CMD ["mnemos", "serve"]
