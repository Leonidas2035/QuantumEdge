#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
ENV_FILE="/etc/quantumedge/env"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[run_supervisor] Missing .venv. Run scripts/linux/setup.sh first." >&2
  exit 1
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
else
  echo "[run_supervisor] Optional env file not found: $ENV_FILE"
  echo "[run_supervisor] Create it if you need to pass secrets or overrides."
fi

export QE_ROOT="$ROOT_DIR"

if [ "$#" -eq 0 ]; then
  exec "$PYTHON_BIN" "$ROOT_DIR/SupervisorAgent/supervisor.py" run-foreground
fi

exec "$PYTHON_BIN" "$ROOT_DIR/SupervisorAgent/supervisor.py" "$@"
