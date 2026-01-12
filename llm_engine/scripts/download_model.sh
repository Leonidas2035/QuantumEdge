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

: "${MODEL_DIR:?MODEL_DIR is required}"

if [[ -d "${MODEL_DIR}" ]]; then
  echo "[download_model] MODEL_DIR already exists: ${MODEL_DIR}"
  exit 0
fi

if ! command -v huggingface-cli >/dev/null 2>&1; then
  echo "[download_model] ERROR: huggingface-cli not found. Install it or download the model manually." >&2
  echo "[download_model] Expected MODEL_DIR: ${MODEL_DIR}" >&2
  exit 1
fi

if [[ -z "${HF_MODEL_ID:-}" ]]; then
  echo "[download_model] ERROR: HF_MODEL_ID is required to download via huggingface-cli." >&2
  echo "[download_model] Example text-only repo: gghfez/gemma-3-4b-novision" >&2
  exit 1
fi

echo "[download_model] Downloading ${HF_MODEL_ID} to ${MODEL_DIR}..."
huggingface-cli download "${HF_MODEL_ID}" --local-dir "${MODEL_DIR}" --local-dir-use-symlinks False

echo "[download_model] Done."
