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
: "${CALIB_JSONL:?CALIB_JSONL is required}"
: "${CALIB_SIZE:?CALIB_SIZE is required}"
: "${CKPT_DIR:?CKPT_DIR is required}"
: "${OUT_DIR:?OUT_DIR is required}"

LOG_DIR="${OUT_DIR}/logs"
mkdir -p "${LOG_DIR}"

if [[ "${USE_DOCKER:-1}" == "1" && "${IN_CONTAINER:-0}" != "1" ]]; then
  : "${NGC_IMAGE:?NGC_IMAGE is required for docker}"
  echo "[quantize_awq] Running in container: ${NGC_IMAGE}"
  docker run --rm --gpus all \
    -v "${REPO_ROOT}:/workspace" \
    -v "${MODEL_DIR}:/models:ro" \
    -w /workspace \
    -e IN_CONTAINER=1 \
    -e USE_DOCKER=0 \
    -e MODEL_DIR=/models \
    -e CALIB_JSONL="${CALIB_JSONL}" \
    -e CALIB_SIZE="${CALIB_SIZE}" \
    -e CALIB_SEQ_LEN="${CALIB_SEQ_LEN:-256}" \
    -e CKPT_DIR="${CKPT_DIR}" \
    -e OUT_DIR="${OUT_DIR}" \
    "${NGC_IMAGE}" bash -lc "./llm_engine/scripts/quantize_awq.sh"
  exit $?
fi

echo "[quantize_awq] Preparing calibration set..."
python3 "${LLM_DIR}/scripts/prepare_calib.py" --out "${CALIB_JSONL}" --size "${CALIB_SIZE}"

QUANTIZE_CMD=""
if command -v trtllm-quantize >/dev/null 2>&1; then
  QUANTIZE_CMD="trtllm-quantize"
elif [[ -f /opt/tensorrt_llm/examples/quantization/quantize.py ]]; then
  QUANTIZE_CMD="python3 /opt/tensorrt_llm/examples/quantization/quantize.py"
elif python3 - <<'PY' >/dev/null 2>&1
import importlib.util
print(1 if importlib.util.find_spec('tensorrt_llm.quantization') else 0)
PY
  then
  QUANTIZE_CMD="python3 -m tensorrt_llm.quantization.awq"
fi

if [[ -z "${QUANTIZE_CMD}" ]]; then
  echo "[quantize_awq] ERROR: No supported TRT-LLM AWQ quantize entrypoint found." >&2
  echo "[quantize_awq] Install TensorRT-LLM or update QUANTIZE_CMD in this script." >&2
  exit 1
fi

mkdir -p "${CKPT_DIR}"

echo "[quantize_awq] Using: ${QUANTIZE_CMD}"
set -x
${QUANTIZE_CMD} \
  --model_dir "${MODEL_DIR}" \
  --output_dir "${CKPT_DIR}" \
  --quantize_mode awq \
  --calib_dataset "${CALIB_JSONL}" \
  --calib_size "${CALIB_SIZE}" \
  --calib_seq_len "${CALIB_SEQ_LEN:-256}" \
  2>&1 | tee "${LOG_DIR}/quantize.log"
set +x

echo "[quantize_awq] Done."
