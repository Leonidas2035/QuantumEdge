#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
ENV_FILE="/etc/quantumedge/env"

if [ ! -x "$PYTHON_BIN" ]; then
  # Fallback to system python if venv missing
  PYTHON_BIN="python3"
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

export QE_ROOT="$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src:$ROOT_DIR/src/quantum_edge_core:$ROOT_DIR/src/quantum_edge_infra:$ROOT_DIR/src/quantum_edge_ml"

CLI_PATH="$ROOT_DIR/src/quantum_edge_core/supervisor/supervisor_main.py"

if [ "$#" -eq 0 ]; then
  exec "$PYTHON_BIN" "$CLI_PATH" run-foreground
fi

exec "$PYTHON_BIN" "$CLI_PATH" "$@"
