#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# QuantumEdge Orchestrator — manage.sh
# Usage: ./scripts/manage.sh {start|stop|restart|status}
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/runtime/pids"

# ── Ports ────────────────────────────────────────────────────────
PORT_HUB=5555
PORT_SUPERVISOR=5556
PORT_DASHBOARD=8501

# ── Colors ───────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'  # No Color
BOLD='\033[1m'

log_info()  { echo -e "${CYAN}[QuantumEdge]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[✅ OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[⚠️  WARN]${NC} $1"; }
log_err()   { echo -e "${RED}[❌ ERR]${NC} $1"; }

# ═══════════════════════════════════════════════════════════════════
#  STOP — kill zombie processes and free ports
# ═══════════════════════════════════════════════════════════════════
do_stop() {
    echo -e "\n${BOLD}═══ Stopping QuantumEdge Services ═══${NC}\n"

    # Kill by port
    for port in $PORT_HUB $PORT_SUPERVISOR $PORT_DASHBOARD; do
        pids=$(lsof -ti ":$port" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            log_warn "Port $port occupied by PID(s): $pids — killing..."
            echo "$pids" | xargs kill -9 2>/dev/null || true
            sleep 0.3
            log_ok "Port $port freed."
        else
            log_info "Port $port already free."
        fi
    done

    # Kill any remaining quantum_edge_core processes
    local qe_pids
    qe_pids=$(pgrep -f "quantum_edge_core" 2>/dev/null || true)
    if [ -n "$qe_pids" ]; then
        log_warn "Killing remaining quantum_edge_core processes: $qe_pids"
        pkill -9 -f "quantum_edge_core" 2>/dev/null || true
        sleep 0.5
    fi

    # Kill any remaining streamlit processes
    local st_pids
    st_pids=$(pgrep -f "streamlit" 2>/dev/null || true)
    if [ -n "$st_pids" ]; then
        log_warn "Killing remaining streamlit processes: $st_pids"
        pkill -9 -f "streamlit" 2>/dev/null || true
    fi

    # Clean PID files
    rm -f "$PID_DIR"/*.pid 2>/dev/null || true

    echo ""
    log_ok "All services stopped and ports cleaned."
}

# ═══════════════════════════════════════════════════════════════════
#  START — launch infrastructure services in background
# ═══════════════════════════════════════════════════════════════════
do_start() {
    echo -e "\n${BOLD}═══ Starting QuantumEdge Services ═══${NC}\n"

    # Ensure directories exist
    mkdir -p "$LOG_DIR" "$PID_DIR"

    # Source .env if it exists
    if [ -f "$PROJECT_ROOT/.env" ]; then
        log_info "Loading environment from .env ..."
        set -a
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.env"
        set +a
        log_ok ".env loaded."
    else
        log_warn "No .env file found at $PROJECT_ROOT/.env — using current environment."
    fi

    # ── 1. MarketDataHub ─────────────────────────────────────────
    log_info "Starting MarketDataHub (port $PORT_HUB)..."
    nohup python3 -m quantum_edge_core.market_data.hub \
        > "$LOG_DIR/hub.log" 2>&1 &
    local hub_pid=$!
    echo "$hub_pid" > "$PID_DIR/hub.pid"
    log_ok "MarketDataHub started (PID: $hub_pid, log: logs/hub.log)"

    sleep 2  # Let Hub bind port before Supervisor connects

    # ── 2. LLM Supervisor ────────────────────────────────────────
    log_info "Starting LLM Supervisor (port $PORT_SUPERVISOR)..."
    nohup python3 -m hermes.supervisor.llm_supervisor \
        run-foreground --mode paper \
        > "$LOG_DIR/supervisor.log" 2>&1 &
    local sup_pid=$!
    echo "$sup_pid" > "$PID_DIR/supervisor.pid"
    log_ok "LLM Supervisor started (PID: $sup_pid, log: logs/supervisor.log)"

    sleep 1

    # ── 3. Streamlit Dashboard ───────────────────────────────────
    log_info "Starting Streamlit Dashboard (port $PORT_DASHBOARD)..."
    nohup streamlit run \
        "$PROJECT_ROOT/src/quantum_edge_core/dashboard/app.py" \
        --server.port "$PORT_DASHBOARD" \
        --server.headless true \
        > "$LOG_DIR/dashboard.log" 2>&1 &
    local dash_pid=$!
    echo "$dash_pid" > "$PID_DIR/dashboard.pid"
    log_ok "Dashboard started (PID: $dash_pid, http://localhost:$PORT_DASHBOARD)"

    echo ""
    echo -e "${BOLD}═══ All Services Running ═══${NC}"
    echo -e "  MarketDataHub : PID $hub_pid  │ Port $PORT_HUB"
    echo -e "  LLM Supervisor: PID $sup_pid  │ Port $PORT_SUPERVISOR"
    echo -e "  Dashboard     : PID $dash_pid │ Port $PORT_DASHBOARD"
    echo ""
    echo -e "${YELLOW}  LockBot: start manually to see live logs:${NC}"
    echo -e "  ${CYAN}python3 -m quantum_edge_core.lock_bot.main${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════
#  STATUS — check which services are running
# ═══════════════════════════════════════════════════════════════════
do_status() {
    echo -e "\n${BOLD}═══ QuantumEdge Service Status ═══${NC}\n"

    _check_service "MarketDataHub"  "$PORT_HUB"       "$PID_DIR/hub.pid"
    _check_service "LLM Supervisor" "$PORT_SUPERVISOR" "$PID_DIR/supervisor.pid"
    _check_service "Dashboard"      "$PORT_DASHBOARD"  "$PID_DIR/dashboard.pid"

    echo ""
}

_check_service() {
    local name="$1"
    local port="$2"
    local pid_file="$3"

    local port_pids
    port_pids=$(lsof -ti ":$port" 2>/dev/null || true)

    local saved_pid=""
    if [ -f "$pid_file" ]; then
        saved_pid=$(cat "$pid_file" 2>/dev/null || true)
    fi

    if [ -n "$port_pids" ]; then
        echo -e "  ${GREEN}● $name${NC}  port=$port  pids=$port_pids"
    elif [ -n "$saved_pid" ] && kill -0 "$saved_pid" 2>/dev/null; then
        echo -e "  ${YELLOW}◐ $name${NC}  port=$port (not bound)  pid=$saved_pid (alive)"
    else
        echo -e "  ${RED}○ $name${NC}  port=$port  (stopped)"
    fi
}

# ═══════════════════════════════════════════════════════════════════
#  RESTART
# ═══════════════════════════════════════════════════════════════════
do_restart() {
    do_stop
    sleep 1
    do_start
}

# ═══════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════
cd "$PROJECT_ROOT"

case "${1:-}" in
    start)   do_start   ;;
    stop)    do_stop    ;;
    restart) do_restart ;;
    status)  do_status  ;;
    *)
        echo -e "${BOLD}Usage:${NC} $0 {start|stop|restart|status}"
        echo ""
        echo "  start   — Clean ports, load .env, launch Hub + Supervisor + Dashboard"
        echo "  stop    — Kill all QuantumEdge processes and free ports"
        echo "  restart — Stop then start"
        echo "  status  — Show running services and port status"
        exit 1
        ;;
esac
