# AI-Brain container
# Build: podman build -t ai-brain .
# Run:   podman run -v brain-data:/data -v brain-vault:/vault -p 8787:8787 ai-brain
FROM docker.io/library/python:3.12-slim AS base

LABEL maintainer="ai-brain"
LABEL description="AI-Brain: hybrid long-term memory system"

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
ENV AI_BRAIN_CONFIG=/app/config.yaml

EXPOSE 8787

# Default: API server
CMD ["python", "-m", "uvicorn", "ai_brain.api:app", "--host", "0.0.0.0", "--port", "8787"]
