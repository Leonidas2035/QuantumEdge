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

echo "[env_check] Checking NVIDIA GPU..."
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "[env_check] ERROR: nvidia-smi not found. Install NVIDIA drivers." >&2
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

driver_version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1 | tr -d '\r')"
if [[ -n "${NGC_IMAGE:-}" && "${NGC_IMAGE}" == *":25.12"* ]]; then
  required_driver="580.95.05"
  version_ge() {
    local a="$1" b="$2"
    [[ "$(printf '%s\n' "$b" "$a" | sort -V | head -n1)" == "$b" ]]
  }
  if [[ -n "${driver_version}" ]] && ! version_ge "${driver_version}" "${required_driver}"; then
    echo "[env_check] WARNING: Driver ${driver_version} may be too old for ${NGC_IMAGE} (needs >= ${required_driver})." >&2
    echo "[env_check] WARNING: Consider 25.01-trtllm-python-py3 (~570.86.10) or 24.12-trtllm-python-py3 (~560.35.05)." >&2
  fi
fi

if [[ "${USE_DOCKER:-1}" == "1" ]]; then
  echo "[env_check] Checking Docker..."
  if ! command -v docker >/dev/null 2>&1; then
    echo "[env_check] ERROR: docker not found. Install Docker or set USE_DOCKER=0." >&2
    exit 1
  fi
  docker --version
fi

if [[ -n "${MODEL_DIR:-}" ]]; then
  echo "[env_check] Checking MODEL_DIR..."
  if [[ ! -d "${MODEL_DIR}" ]]; then
    echo "[env_check] ERROR: MODEL_DIR does not exist: ${MODEL_DIR}" >&2
    exit 1
  fi
  echo "[env_check] MODEL_DIR ok: ${MODEL_DIR}"
fi

echo "[env_check] Done."
