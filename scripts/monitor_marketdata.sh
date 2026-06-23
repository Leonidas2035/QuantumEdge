#!/usr/bin/env bash
# Monitor QuantumEdge MarketDataHub and ensure it stays running on port 5555
# Uses PYTHONPATH=src and the virtualenv located at .venv

set -euo pipefail

LOG_FILE="$(pwd)/hub_monitor.log"

function start_hub() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting MarketDataHub..." >> "$LOG_FILE"
  PYTHONPATH=src .venv/bin/python -m quantum_edge_core.market_data.hub >> "$LOG_FILE" 2>&1 &
  HUB_PID=$!
  echo "$(date '+%Y-%m-%d %H:%M:%S') - Hub PID $HUB_PID" >> "$LOG_FILE"
}

function is_port_open() {
  ss -ltnp | grep -q "0.0.0.0:5555"
}

# Initial start if not already running
if ! pgrep -f "quantum_edge_core.market_data.hub" > /dev/null; then
  start_hub
fi

# Monitoring loop
while true; do
  # Check if process is alive
  if ! kill -0 $HUB_PID 2>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Hub process $HUB_PID died, restarting..." >> "$LOG_FILE"
    start_hub
  else
    # Verify port is still bound
    if ! is_port_open; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') - Port 5555 not bound, killing PID $HUB_PID and restarting..." >> "$LOG_FILE"
      kill $HUB_PID || true
      start_hub
    fi
  fi
  sleep 30
done
