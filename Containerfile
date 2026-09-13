# Mnemos — Memory & Knowledge Server for AI Agents
# Build: podman build -t ghcr.io/korrnals/mnemos:4.1.0 .
# Run:   podman run -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 ghcr.io/korrnals/mnemos:4.1.0
FROM docker.io/library/python:3.12-slim AS base

LABEL maintainer="abyss"
LABEL description="Mnemos: hybrid long-term memory system for AI agents"
LABEL org.opencontainers.image.source="https://github.com/Korrnals/mnemos"
LABEL org.opencontainers.image.licenses="Apache-2.0"

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

# Install the package (the MCP SDK rides in core since 4.1.0 — ADR-0023;
# the bundled mnema-embed-v1 model ships inside the wheel, so vector
# search works offline out of the box with no downloads).
COPY pyproject.toml README.md ./
COPY src/ ./src/
# integrations/ and scripts/ are force-included into the wheel via
# pyproject.toml [tool.hatch.build.targets.wheel.force-include].
COPY integrations/ ./integrations/
COPY scripts/ ./scripts/
COPY NOTICE LICENSE ./
RUN pip install --no-cache-dir "."

# Entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Default directories (/data and /vault are volume mounts at runtime)
RUN mkdir -p /data /vault

# Container config (0.0.0.0 bind + auth + CORS; TOTP master key via env)
COPY config.container.yaml /app/config.yaml
ENV MNEMOS_CONFIG=/app/config.yaml

EXPOSE 8787

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["mnemos", "serve"]