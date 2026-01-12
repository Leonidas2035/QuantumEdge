#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLM_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${LLM_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f "${LLM_DIR}/configs/engine_defaults.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${LLM_DIR}/configs/engine_defaults.env"
  set +a
fi

: "${ENGINE_DIR:?ENGINE_DIR is required}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if command -v trtllm-serve >/dev/null 2>&1; then
  echo "[serve] Starting trtllm-serve on ${HOST}:${PORT}"
  exec trtllm-serve --engine_dir "${ENGINE_DIR}" --host "${HOST}" --port "${PORT}"
fi

echo "[serve] trtllm-serve not found. Use smoke_local.py for local validation." >&2
exit 1
