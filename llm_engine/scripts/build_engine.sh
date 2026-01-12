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

: "${CKPT_DIR:?CKPT_DIR is required}"
: "${ENGINE_DIR:?ENGINE_DIR is required}"
: "${OUT_DIR:?OUT_DIR is required}"
: "${MAX_INPUT_LEN:?MAX_INPUT_LEN is required}"
: "${MAX_OUTPUT_LEN:?MAX_OUTPUT_LEN is required}"
: "${MAX_BATCH_SIZE:?MAX_BATCH_SIZE is required}"
: "${TOTAL_TOKENS:?TOTAL_TOKENS is required}"

LOG_DIR="${OUT_DIR}/logs"
mkdir -p "${LOG_DIR}"

if [[ "${USE_DOCKER:-1}" == "1" && "${IN_CONTAINER:-0}" != "1" ]]; then
  : "${NGC_IMAGE:?NGC_IMAGE is required for docker}"
  echo "[build_engine] Running in container: ${NGC_IMAGE}"
  docker run --rm --gpus all \
    -v "${REPO_ROOT}:/workspace" \
    -w /workspace \
    -e IN_CONTAINER=1 \
    -e USE_DOCKER=0 \
    -e CKPT_DIR="${CKPT_DIR}" \
    -e ENGINE_DIR="${ENGINE_DIR}" \
    -e OUT_DIR="${OUT_DIR}" \
    -e MAX_INPUT_LEN="${MAX_INPUT_LEN}" \
    -e MAX_OUTPUT_LEN="${MAX_OUTPUT_LEN}" \
    -e MAX_BATCH_SIZE="${MAX_BATCH_SIZE}" \
    -e TOTAL_TOKENS="${TOTAL_TOKENS}" \
    "${NGC_IMAGE}" bash -lc "./llm_engine/scripts/build_engine.sh"
  exit $?
fi

if ! command -v trtllm-build >/dev/null 2>&1; then
  echo "[build_engine] ERROR: trtllm-build not found." >&2
  exit 1
fi

HELP_TEXT="$(trtllm-build --help 2>&1)"
KV_FLAGS="$(printf '%s' "${HELP_TEXT}" | python3 "${LLM_DIR}/scripts/trtllm_flags.py" --kv-cache-flags)"

EXTRA_ARGS=()
if [[ -n "${KV_FLAGS}" ]]; then
  read -r -a KV_FLAGS_ARR <<< "${KV_FLAGS}"
  EXTRA_ARGS+=("${KV_FLAGS_ARR[@]}")
else
  echo "[build_engine] WARNING: No paged KV cache flag detected in trtllm-build --help" >&2
fi

if echo "${HELP_TEXT}" | grep -q -- "--max_input_len"; then
  EXTRA_ARGS+=("--max_input_len" "${MAX_INPUT_LEN}")
elif echo "${HELP_TEXT}" | grep -q -- "--max_input_length"; then
  EXTRA_ARGS+=("--max_input_length" "${MAX_INPUT_LEN}")
fi

if echo "${HELP_TEXT}" | grep -q -- "--max_output_len"; then
  EXTRA_ARGS+=("--max_output_len" "${MAX_OUTPUT_LEN}")
elif echo "${HELP_TEXT}" | grep -q -- "--max_output_length"; then
  EXTRA_ARGS+=("--max_output_length" "${MAX_OUTPUT_LEN}")
fi

if echo "${HELP_TEXT}" | grep -q -- "--max_batch_size"; then
  EXTRA_ARGS+=("--max_batch_size" "${MAX_BATCH_SIZE}")
fi

if echo "${HELP_TEXT}" | grep -q -- "--max_num_tokens"; then
  EXTRA_ARGS+=("--max_num_tokens" "${TOTAL_TOKENS}")
fi

if echo "${HELP_TEXT}" | grep -q -- "--gemm_plugin"; then
  EXTRA_ARGS+=("--gemm_plugin" "fp16")
fi

if echo "${HELP_TEXT}" | grep -q -- "--attention_plugin"; then
  EXTRA_ARGS+=("--attention_plugin" "fp16")
fi

mkdir -p "${ENGINE_DIR}"

echo "[build_engine] Building engine in ${ENGINE_DIR}..."
set -x
trtllm-build \
  --checkpoint_dir "${CKPT_DIR}" \
  --output_dir "${ENGINE_DIR}" \
  "${EXTRA_ARGS[@]}" \
  2>&1 | tee "${LOG_DIR}/build.log"
set +x

TRTLLM_VERSION="unknown"
TENSORRT_VERSION="unknown"
if command -v python3 >/dev/null 2>&1; then
  TRTLLM_VERSION="$(python3 - <<'PY'
try:
    import tensorrt_llm
    print(tensorrt_llm.__version__)
except Exception:
    print("unknown")
PY
  )"
  TENSORRT_VERSION="$(python3 - <<'PY'
try:
    import tensorrt
    print(tensorrt.__version__)
except Exception:
    print("unknown")
PY
  )"
fi

KV_MODE="unknown"
if [[ -n "${KV_FLAGS}" ]]; then
  KV_MODE="paged"
fi

BUILD_TIME_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "${ENGINE_DIR}/engine_manifest.json" <<EOF
{
  "model_name": "${MODEL_NAME:-gemma3-4b-text}",
  "quant": "int4_awq",
  "max_batch": ${MAX_BATCH_SIZE},
  "max_input_len": ${MAX_INPUT_LEN},
  "max_output_len": ${MAX_OUTPUT_LEN},
  "kv_cache_mode": "${KV_MODE}",
  "trtllm_version": "${TRTLLM_VERSION}",
  "tensorrt_version": "${TENSORRT_VERSION}",
  "build_time_utc": "${BUILD_TIME_UTC}"
}
EOF

echo "[build_engine] Done."
