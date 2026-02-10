#!/usr/bin/env sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

export QE_ROOT="$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src:$ROOT_DIR/src/quantum_edge_core:$ROOT_DIR/src/quantum_edge_infra:$ROOT_DIR/src/quantum_edge_ml"

exec "$PYTHON_BIN" -m quantumedge bot --config "$ROOT_DIR/src/quantum_edge_core/config/bot.yaml" "$@"
