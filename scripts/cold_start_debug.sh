#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# QuantumEdge — Cold-Start & Debug Script
# Purpose: Clean startup after server reboot with staged verification.
# Author : Senior HFT Python Developer
# Date   : 2026-03-05
# ──────────────────────────────────────────────────────────────────
set -euo pipefail
IFS=$'\n\t'

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly VENV="${PROJECT_ROOT}/venv"
readonly LOG_DIR="${PROJECT_ROOT}/logs"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

# ── ANSI Colours ─────────────────────────────────────────────────
RED='\033[0;31m'  GRN='\033[0;32m'  YEL='\033[1;33m'  NC='\033[0m'
info()  { printf "${GRN}[INFO]${NC}  %s\n" "$*"; }
warn()  { printf "${YEL}[WARN]${NC}  %s\n" "$*"; }
err()   { printf "${RED}[ERR]${NC}   %s\n" "$*"; }

# ── Ports table ──────────────────────────────────────────────────
# 5555  ZMQ PUB  — MarketDataHub → Bot
# 5556  ZMQ PUB  — Hub (snapshot/secondary)
# 5557  ZMQ PUB  — Bot telemetry → Supervisor
# 5558  ZMQ PUB  — Supervisor commands → Bot
# 9009  ILP/TCP  — QuestDB Ingestion
# 9000  HTTP     — QuestDB Console
# 8812  PG Wire  — QuestDB Postgres Wire
readonly ZMQ_PORTS=(5555 5556 5557 5558)
readonly QUESTDB_PORTS=(9009 9000 8812)

########################################################################
# PHASE 0 — Environment Pre-flight
########################################################################
phase_env() {
    info "PHASE 0 — Environment Pre-flight"

    if [[ ! -d "${VENV}" ]]; then
        err  "Virtual env not found at ${VENV}. Run: python3 -m venv ${VENV} && pip install -e ."
        exit 1
    fi
    # shellcheck disable=SC1091
    source "${VENV}/bin/activate"

    # .env
    if [[ -f "${PROJECT_ROOT}/.env" ]]; then
        # shellcheck disable=SC2046
        export $(grep -v '^#' "${PROJECT_ROOT}/.env" | xargs)
        info ".env loaded"
    else
        warn ".env missing — relying on shell env vars"
    fi

    mkdir -p "${LOG_DIR}" "${PROJECT_ROOT}/runtime"
    info "PYTHONPATH=${PYTHONPATH}"
}

########################################################################
# PHASE 1 — Kill stale processes & free ZMQ ports
########################################################################
phase_ports() {
    info "PHASE 1 — ZMQ / Port Guard (ports: ${ZMQ_PORTS[*]} ${QUESTDB_PORTS[*]})"

    local ALL_PORTS=("${ZMQ_PORTS[@]}" "${QUESTDB_PORTS[@]}")

    for port in "${ALL_PORTS[@]}"; do
        local pids
        pids=$(lsof -ti :"${port}" 2>/dev/null || true)
        if [[ -n "${pids}" ]]; then
            warn "Port ${port} held by PIDs: ${pids}. Killing..."
            # SIGTERM first, SIGKILL fallback
            echo "${pids}" | xargs kill -15 2>/dev/null || true
            sleep 1
            local remain
            remain=$(lsof -ti :"${port}" 2>/dev/null || true)
            if [[ -n "${remain}" ]]; then
                warn "Force-killing remaining PIDs on port ${port}: ${remain}"
                echo "${remain}" | xargs kill -9 2>/dev/null || true
            fi
        else
            info "Port ${port} — free ✓"
        fi
    done

    # Also kill any zombie Python procs that match our modules
    for pattern in "market_data.hub" "supervisor.py" "ai_scalper_bot.run_bot"; do
        local pids
        pids=$(pgrep -f "${pattern}" 2>/dev/null || true)
        if [[ -n "${pids}" ]]; then
            warn "Killing residual '${pattern}' processes: ${pids}"
            echo "${pids}" | xargs kill -9 2>/dev/null || true
        fi
    done

    info "All ports clear ✓"
}

########################################################################
# PHASE 2 — QuestDB (Docker)
########################################################################
phase_questdb() {
    info "PHASE 2 — Starting QuestDB via Docker Compose"

    if ! command -v docker &>/dev/null; then
        err "Docker CLI not found. Install Docker first."
        exit 1
    fi

    cd "${PROJECT_ROOT}"
    docker compose up -d questdb 2>&1 | sed 's/^/  [docker] /'

    # Wait for QuestDB HTTP health
    info "Waiting for QuestDB at localhost:9000 ..."
    local retries=20
    while (( retries > 0 )); do
        if curl -sf http://127.0.0.1:9000 >/dev/null 2>&1; then
            info "QuestDB ready ✓"
            return 0
        fi
        sleep 1
        (( retries-- ))
    done
    err "QuestDB did not become ready in 20s. Check: docker logs questdb"
    exit 1
}

########################################################################
# PHASE 3 — Data Plane: MarketDataHub
########################################################################
phase_hub() {
    info "PHASE 3 — Starting MarketDataHub (ZMQ PUB on :5555)"

    cd "${PROJECT_ROOT}"
    nohup python3 -m quantum_edge_core.market_data.hub \
        > "${LOG_DIR}/hub.log" 2>&1 &
    echo $! > "${PROJECT_ROOT}/runtime/hub.pid"
    info "Hub PID=$(cat "${PROJECT_ROOT}/runtime/hub.pid")"

    # Readiness: wait until port 5555 is bound
    local retries=15
    while (( retries > 0 )); do
        if lsof -i :5555 >/dev/null 2>&1; then
            info "Hub ZMQ port 5555 bound ✓"
            return 0
        fi
        sleep 1
        (( retries-- ))
    done
    err "Hub failed to bind port 5555 in 15s. Tail log: tail -50 ${LOG_DIR}/hub.log"
    exit 1
}

########################################################################
# PHASE 4 — Control Plane: SupervisorAgent
########################################################################
phase_supervisor() {
    info "PHASE 4 — Starting SupervisorAgent (telemetry SUB :5557, cmd PUB :5558)"

    # Pre-flight: validate risk.yaml
    local risk_cfg="${PROJECT_ROOT}/config/risk.yaml"
    if [[ ! -f "${risk_cfg}" ]]; then
        err "MISSING: ${risk_cfg} — Supervisor will crash."
        exit 1
    fi
    local max_loss
    max_loss=$(python3 -c "
import yaml, sys
with open('${risk_cfg}') as f:
    cfg = yaml.safe_load(f)
val = cfg.get('max_daily_loss_abs', 0.0)
if val == 0.0:
    print('INVALID', file=sys.stderr)
    sys.exit(1)
print(val)
" 2>&1) || {
        err "risk.yaml: max_daily_loss_abs is 0.0 or missing → ValueError. Fix before launching."
        exit 1
    }
    info "risk.yaml: max_daily_loss_abs=${max_loss} ✓"

    # Pre-flight: validate LLM config
    local llm_cfg="${PROJECT_ROOT}/config/llm_supervisor.yaml"
    if [[ ! -f "${llm_cfg}" ]]; then
        warn "MISSING: ${llm_cfg} — LLM analysis will be disabled."
    fi

    cd "${PROJECT_ROOT}"
    nohup python3 /home/korben/.hermes/hermes/supervisor.py run-foreground \
        > "${LOG_DIR}/supervisor.log" 2>&1 &
    echo $! > "${PROJECT_ROOT}/runtime/supervisor.pid"
    info "Supervisor PID=$(cat "${PROJECT_ROOT}/runtime/supervisor.pid")"
    sleep 2

    # Check alive
    if ! kill -0 "$(cat "${PROJECT_ROOT}/runtime/supervisor.pid")" 2>/dev/null; then
        err "Supervisor crashed! Tail log: tail -80 ${LOG_DIR}/supervisor.log"
        exit 1
    fi
    info "Supervisor running ✓"
}

########################################################################
# PHASE 5 — Execution Plane: AI Scalper Bot
########################################################################
phase_bot() {
    info "PHASE 5 — Starting AI Scalper Bot (market SUB :5555, telem PUB :5557, cmd SUB :5558)"

    cd "${PROJECT_ROOT}"
    nohup python3 -u -m quantum_edge_core.ai_scalper_bot.run_bot \
        > "${LOG_DIR}/bot.log" 2>&1 &
    echo $! > "${PROJECT_ROOT}/runtime/bot.pid"
    info "Bot PID=$(cat "${PROJECT_ROOT}/runtime/bot.pid")"
    sleep 3

    if ! kill -0 "$(cat "${PROJECT_ROOT}/runtime/bot.pid")" 2>/dev/null; then
        err "Bot crashed! Tail: tail -80 ${LOG_DIR}/bot.log"
        exit 1
    fi
    info "Bot running ✓"
}

########################################################################
# PHASE 6 — Post-launch diagnostics
########################################################################
phase_diagnostics() {
    info "PHASE 6 — Diagnostics"

    info "Port map:"
    for port in 5555 5556 5557 5558 9000 9009 8812; do
        local pid_info
        pid_info=$(lsof -ti :"${port}" 2>/dev/null || echo "FREE")
        printf "  :%s → %s\n" "${port}" "${pid_info}"
    done

    info "Running Python processes:"
    ps aux | grep -E "(hub|supervisor|run_bot)" | grep -v grep || true

    info "Log tails (last 5 lines):"
    for f in hub supervisor bot; do
        if [[ -f "${LOG_DIR}/${f}.log" ]]; then
            echo "--- ${f}.log ---"
            tail -5 "${LOG_DIR}/${f}.log" 2>/dev/null || true
        fi
    done

    info "Run Python probes separately:"
    echo "  python3 scripts/probe_zmq_hub.py       # Hub ZMQ data verification"
    echo "  python3 scripts/probe_questdb.py        # QuestDB connectivity"
    echo "  python3 scripts/probe_telemetry.py      # Bot telemetry on :5557"
}

########################################################################
# Main
########################################################################
main() {
    info "═══════ QuantumEdge Cold Start ═══════"
    info "Timestamp: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    info "Project:   ${PROJECT_ROOT}"

    phase_env
    phase_ports
    phase_questdb
    phase_hub
    phase_supervisor
    phase_bot
    phase_diagnostics

    info "═══════ All systems launched ═══════"
}

main "$@"
