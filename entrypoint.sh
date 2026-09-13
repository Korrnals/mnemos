#!/bin/bash
set -e

# The embedding model (mnema-embed-v1) ships inside the wheel — no
# pre-download step, no external model cache. /data and /vault are
# created on first run by the server itself. This entrypoint exists
# only for future pre-boot hooks (e.g. migration checks).

exec "$@"