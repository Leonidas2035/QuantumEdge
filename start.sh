#!/usr/bin/env bash

set -e

echo "=== QuantumEdge Startup Script ==="

# Check if docker is accessible
if ! docker info > /dev/null 2>&1; then
  echo "Error: Docker is not running or not accessible. Please start Docker."
  exit 1
fi

echo "[1/5] Starting Docker containers (QuestDB)..."
docker-compose up -d

echo "[2/5] Checking Python Virtual Environment..."
if [ ! -d "venv" ]; then
  echo "Creating virtual environment 'venv'..."
  python3 -m venv venv
else
  echo "Virtual environment 'venv' already exists."
fi

echo "[3/5] Activating virtual environment and registering modules..."
source venv/bin/activate
pip install -e .

echo "[4/5] Cleaning up stale PID and status files..."
mkdir -p runtime logs/processes
rm -f runtime/*.pid
rm -f runtime/bot_status.json
rm -f runtime/state/process_state.json

echo "[5/5] Starting QuantumEdge Orchestrator..."
python3 QuantumEdge.py start

echo "=== System startup initiated successfully! ==="
