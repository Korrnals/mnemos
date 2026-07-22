#!/usr/bin/env bash
# Regenerate gRPC stubs from federation/proto/*.proto.
# Source of truth: federation/proto/*.proto (contract-first, mnemos-mesh Phase 3).
# Output: federation/gen/python/ (gitignored — regenerate after any proto change).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p federation/gen/python
python -m grpc_tools.protoc \
  -I federation/proto \
  --python_out=federation/gen/python \
  --grpc_python_out=federation/gen/python \
  --pyi_out=federation/gen/python \
  federation/proto/federation.proto \
  federation/proto/mnemos_core_api.proto
echo "regenerated federation/gen/python/ from federation/proto/*.proto"
