#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# generate_proto.sh
# Compiles proto/memory.proto into Python stubs.
# Run once after cloning and whenever memory.proto changes.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

mkdir -p "$ROOT/agentmemos/proto"
touch    "$ROOT/agentmemos/proto/__init__.py"

python -m grpc_tools.protoc \
    -I "$ROOT/proto" \
    --python_out="$ROOT/agentmemos/proto" \
    --grpc_python_out="$ROOT/agentmemos/proto" \
    "$ROOT/proto/memory.proto"

# Fix relative imports in generated files (grpc_tools generates absolute imports)
sed -i 's/^import memory_pb2/from . import memory_pb2/' \
    "$ROOT/agentmemos/proto/memory_pb2_grpc.py" 2>/dev/null || true

echo "Proto stubs generated at agentmemos/proto/"
