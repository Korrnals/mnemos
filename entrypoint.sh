#!/bin/bash
set -e

# Copy pre-downloaded embedding model cache to PVC if not already present.
# /data is a volume mount at runtime, so pre-downloaded files inside the
# image layer at /opt/model-cache need to be copied to /data on first boot.
if [ ! -d /data/.cache/chroma/onnx_models/all-MiniLM-L6-v2 ]; then
    echo "[entrypoint] Copying pre-downloaded embedding model to /data ..."
    mkdir -p /data/.cache/chroma
    cp -r /opt/model-cache/.cache/chroma/* /data/.cache/chroma/ 2>/dev/null || true
    echo "[entrypoint] Embedding model ready."
fi

exec "$@"