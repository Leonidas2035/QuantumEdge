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

: "${OUT_DIR:?OUT_DIR is required}"

if [[ "${USE_DOCKER:-1}" == "1" && "${IN_CONTAINER:-0}" != "1" ]]; then
  : "${NGC_IMAGE:?NGC_IMAGE is required for docker}"
  echo "[collect_versions] Running in container: ${NGC_IMAGE}"
  docker run --rm --gpus all \
    -v "${REPO_ROOT}:/workspace" \
    -w /workspace \
    -e IN_CONTAINER=1 \
    -e USE_DOCKER=0 \
    -e OUT_DIR="${OUT_DIR}" \
    "${NGC_IMAGE}" bash -lc "./llm_engine/scripts/collect_versions.sh"
  exit 0
fi

mkdir -p "${OUT_DIR}"
OUT_FILE="${OUT_DIR}/versions.txt"

echo "[collect_versions] Writing ${OUT_FILE}"
{
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia_smi=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | tr -d '\r')"
  else
    echo "nvidia_smi=not_found"
  fi
  echo "container_image=${NGC_IMAGE:-unknown}"
  if command -v trtllm-build >/dev/null 2>&1; then
    echo "trtllm_build_version=$(trtllm-build --version 2>/dev/null || echo unknown)"
  else
    echo "trtllm_build_version=not_found"
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "trtllm_version=$(python3 - <<'PY'
try:
    import tensorrt_llm
    print(tensorrt_llm.__version__)
except Exception:
    print("unknown")
PY
)"
    echo "tensorrt_version=$(python3 - <<'PY'
try:
    import tensorrt
    print(tensorrt.__version__)
except Exception:
    print("unknown")
PY
)"
    echo "cuda_version=$(python3 - <<'PY'
try:
    import torch
    print(torch.version.cuda or "unknown")
except Exception:
    print("unknown")
PY
)"
  else
    echo "python3=not_found"
  fi
} | tee "${OUT_FILE}"

