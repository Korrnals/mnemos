# Mnemos container
# Build: podman build -t mnemos .
# Run:   podman run -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 mnemos
FROM docker.io/library/python:3.12-slim AS base

LABEL maintainer="mnemos"
LABEL description="Mnemos: hybrid long-term memory system"

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
RUN pip install --no-cache-dir ".[mcp]"

# Default directories
RUN mkdir -p /data /vault

# Default config for container
COPY config.example.yaml /app/config.yaml

# Override paths for container layout
ENV MNEMOS_CONFIG=/app/config.yaml

EXPOSE 8787

# Use CLI entrypoint so host/port propagate correctly to the ASGI worker
CMD ["mnemos", "serve", "--config", "/app/config.yaml"]
