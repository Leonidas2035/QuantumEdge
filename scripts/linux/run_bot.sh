#!/usr/bin/env sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[run_bot] Missing .venv. Run scripts/linux/setup.sh first."
  exit 1
fi

export QE_ROOT="$ROOT_DIR"

exec "$PYTHON_BIN" -m quantumedge bot --config "$ROOT_DIR/config/bot.yaml" "$@"
