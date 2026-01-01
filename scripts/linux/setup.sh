#!/usr/bin/env sh
set -eu

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[setup] Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "[setup] Upgrading pip"
"$PYTHON_BIN" -m pip install --upgrade pip

echo "[setup] Installing runtime dependencies"
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements/requirements.txt"

echo "[setup] Installing dev/test dependencies"
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements/requirements-dev.txt"

echo "[setup] Smoke test (PyYAML import)"
"$PYTHON_BIN" -c "import yaml; print('yaml ok')" || {
  echo "[setup] PyYAML import failed; verify requirements are installed."
  exit 1
}

echo "[setup] Done. Next: use scripts/run.sh to launch components."
