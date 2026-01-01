#!/usr/bin/env bash
set -euo pipefail

# AlmaLinux prerequisites (install manually as needed):
#   sudo dnf install -y python3 python3-venv python3-devel gcc gcc-c++ make git
# Optional scientific stack (if needed by models): openblas-devel lapack-devel

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[setup] Missing python3. Install Python 3.11+ and retry." >&2
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[setup] Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[setup] Upgrading pip tooling"
python -m pip install -U pip wheel setuptools

REQ_FILE="$ROOT_DIR/requirements.txt"
if [ ! -f "$REQ_FILE" ]; then
  if [ -f "$ROOT_DIR/requirements/requirements.txt" ]; then
    REQ_FILE="$ROOT_DIR/requirements/requirements.txt"
  else
    echo "[setup] Missing requirements file. Create requirements.txt and retry." >&2
    exit 1
  fi
fi

echo "[setup] Installing project dependencies from $REQ_FILE"
python -m pip install -r "$REQ_FILE"

if [ "${QE_INSTALL_DEV:-0}" = "1" ] && [ -f "$ROOT_DIR/requirements/requirements-dev.txt" ]; then
  echo "[setup] Installing dev/test dependencies"
  python -m pip install -r "$ROOT_DIR/requirements/requirements-dev.txt"
fi

echo "[setup] Done. Next steps:"
echo "  ./scripts/linux/run_supervisor.sh run-foreground"
echo "  # Optional: QE_INSTALL_DEV=1 ./scripts/linux/setup.sh"
