#!/usr/bin/env bash
# QuantumEdge 24/7 monitor and auto-adjuster
# Fetch current policy
policy=$(curl -s http://127.0.0.1:8765/api/v1/policy/current)
# Simple check: if risk_multiplier > 1.5, reduce to 1.0
risk=$(echo "$policy" | python -c "import sys, json; data=json.load(sys.stdin); print(data.get('risk_multiplier',0))")
if (( $(echo "$risk > 1.5" | bc -l) )); then
  echo "High risk_multiplier $risk detected, reducing to 1.0"
  curl -X POST -s -H 'Content-Type: application/json' -d '{"risk_multiplier":1.0}' http://127.0.0.1:8765/api/v1/policy/update
else
  echo "Risk multiplier OK: $risk"
fi
# Ensure hub is alive (port 5555)
if ! nc -z localhost 5555; then
  echo "Hub down, restarting..."
  export MARKET_DATA_L2_ENABLED=1
  export MARKET_DATA_ORDERBOOK_ENABLED=1
  ./.venv/bin/python -m quantum_edge_core.market_data.hub &
else
  echo "Hub OK"
fi
