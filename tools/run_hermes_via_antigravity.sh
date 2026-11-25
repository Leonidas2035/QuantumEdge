#!/usr/bin/env bash
# Isolated Hermes Agent launcher routed through a local Antigravity OpenAI-compatible proxy.
# SAFETY GUARANTEE: Never reads, touches, or mutates ~/.hermes (config.yaml or .env).
# All runtime state is strictly confined to /home/layman/QuantumEdge/runtime/hermes_ag_home.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

AG_BASE_URL="${AG_BASE_URL:-http://127.0.0.1:8045/v1}"
AG_MODEL="${AG_MODEL:-gemini-4-hydrogen}"
AG_API_KEY="${AG_API_KEY:-sk-antigravity-local}"
ISOLATED_HERMES_HOME="${QE_HERMES_HOME:-${REPO_ROOT}/runtime/hermes_ag_home}"

CHECK_ONLY=0
HERMES_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-url)
            AG_BASE_URL="$2"
            shift 2
            ;;
        --model)
            AG_MODEL="$2"
            shift 2
            ;;
        --api-key)
            AG_API_KEY="$2"
            shift 2
            ;;
        --hermes-home)
            ISOLATED_HERMES_HOME="$2"
            shift 2
            ;;
        --check-only)
            CHECK_ONLY=1
            shift
            ;;
        --)
            shift
            HERMES_ARGS+=("$@")
            break
            ;;
        *)
            HERMES_ARGS+=("$1")
            shift
            ;;
    esac
done

# Normalize and enforce strict safety guard: NEVER allow global ~/.hermes
mkdir -p "${ISOLATED_HERMES_HOME}"
RESOLVED_HERMES_HOME="$(cd "${ISOLATED_HERMES_HOME}" && pwd)"
GLOBAL_DOT_HERMES_CANDIDATE="${HOME%/}/.hermes"

if [[ "${RESOLVED_HERMES_HOME}" == "${GLOBAL_DOT_HERMES_CANDIDATE}" ]] || [[ "${RESOLVED_HERMES_HOME}" == "${GLOBAL_DOT_HERMES_CANDIDATE}/"* ]]; then
    echo "FATAL: Refusing to use global ~/.hermes directory (${RESOLVED_HERMES_HOME})." >&2
    exit 2
fi

case "${RESOLVED_HERMES_HOME}" in
    "${REPO_ROOT}/runtime"/*|/tmp/*)
        ;;
    *)
        echo "FATAL: Isolated HERMES_HOME must reside inside ${REPO_ROOT}/runtime/ (got: ${RESOLVED_HERMES_HOME})." >&2
        exit 2
        ;;
esac

chmod 700 "${RESOLVED_HERMES_HOME}"

CONFIG_FILE="${RESOLVED_HERMES_HOME}/config.yaml"
ENV_FILE="${RESOLVED_HERMES_HOME}/.env"

cat > "${CONFIG_FILE}" <<EOF
# Auto-generated isolated Hermes configuration for QuantumEdge Antigravity Bridge
model:
  provider: "custom"
  base_url: "${AG_BASE_URL}"
  default: "${AG_MODEL}"
  api_key: "${AG_API_KEY}"
  context_length: 262144
providers:
  custom:
    request_timeout_seconds: 180
    stale_timeout_seconds: 300
EOF
chmod 600 "${CONFIG_FILE}"

cat > "${ENV_FILE}" <<EOF
OPENAI_API_KEY=${AG_API_KEY}
HERMES_API_TIMEOUT=180
EOF
chmod 600 "${ENV_FILE}"

export HERMES_HOME="${RESOLVED_HERMES_HOME}"
export OPENAI_API_KEY="${AG_API_KEY}"
export OPENAI_BASE_URL="${AG_BASE_URL}"
export HERMES_API_TIMEOUT=180

PYTHON_VERIFY="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_VERIFY}" ]]; then
    PYTHON_VERIFY="python3"
fi

# Resolve Hermes executable / interpreter without touching ~/.hermes
if [[ -n "${HERMES_PYTHON:-}" ]]; then
    LAUNCH_PREVIEW="HERMES_HOME=${RESOLVED_HERMES_HOME} PYTHONPATH=${REPO_ROOT}/hermes_agent ${HERMES_PYTHON} ${REPO_ROOT}/hermes_agent/hermes --provider custom --model ${AG_MODEL}"
elif [[ -x "${REPO_ROOT}/runtime/hermes_venv/bin/python" ]]; then
    HERMES_PYTHON="${REPO_ROOT}/runtime/hermes_venv/bin/python"
    LAUNCH_PREVIEW="HERMES_HOME=${RESOLVED_HERMES_HOME} PYTHONPATH=${REPO_ROOT}/hermes_agent ${HERMES_PYTHON} ${REPO_ROOT}/hermes_agent/hermes --provider custom --model ${AG_MODEL}"
elif [[ -x "${HOME}/.local/bin/hermes" ]]; then
    LAUNCH_PREVIEW="HERMES_HOME=${RESOLVED_HERMES_HOME} ${HOME}/.local/bin/hermes --provider custom --model ${AG_MODEL}"
else
    LAUNCH_PREVIEW="HERMES_HOME=${RESOLVED_HERMES_HOME} PYTHONPATH=${REPO_ROOT}/hermes_agent <HERMES_PYTHON> ${REPO_ROOT}/hermes_agent/hermes --provider custom --model ${AG_MODEL}"
fi

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
    echo "=== QuantumEdge Isolated Hermes Bridge Check ==="
    echo "HERMES_HOME=${RESOLVED_HERMES_HOME}"
    echo "CONFIG_FILE=${CONFIG_FILE}"
    echo "ENV_FILE=${ENV_FILE}"
    echo "AG_BASE_URL=${AG_BASE_URL}"
    echo "AG_MODEL=${AG_MODEL}"
    echo "READY_LAUNCH_CMD=${LAUNCH_PREVIEW}"
    set +e
    "${PYTHON_VERIFY}" "${SCRIPT_DIR}/verify_antigravity_bridge.py" \
        --base-url "${AG_BASE_URL}" \
        --model "${AG_MODEL}" \
        --api-key "${AG_API_KEY}"
    VERIFY_RC=$?
    set -e
    exit "${VERIFY_RC}"
fi

if [[ -n "${HERMES_PYTHON:-}" ]]; then
    export PYTHONPATH="${REPO_ROOT}/hermes_agent${PYTHONPATH:+:${PYTHONPATH}}"
    exec "${HERMES_PYTHON}" "${REPO_ROOT}/hermes_agent/hermes" \
        --provider custom \
        --model "${AG_MODEL}" \
        "${HERMES_ARGS[@]}"
elif [[ -x "${HOME}/.local/bin/hermes" ]]; then
    exec "${HOME}/.local/bin/hermes" \
        --provider custom \
        --model "${AG_MODEL}" \
        "${HERMES_ARGS[@]}"
else
    echo "ERROR: No Hermes runtime found. Set HERMES_PYTHON to an interpreter with hermes_agent dependencies." >&2
    exit 1
fi
