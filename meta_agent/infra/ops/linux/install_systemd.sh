#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="/etc/systemd/system"
ENV_DIR="/etc/quantumedge"

echo "[*] Installing systemd units..."
sudo mkdir -p "$ENV_DIR"
sudo cp "$SCRIPT_DIR/quantumedge.service" "$UNIT_DIR/quantumedge.service"
sudo cp "$SCRIPT_DIR/supervisoragent.service" "$UNIT_DIR/supervisoragent.service"

if [ ! -f "$ENV_DIR/quantumedge.env" ]; then
  echo "BOT_ENTRYPOINT=run_bot.py --mode paper" | sudo tee "$ENV_DIR/quantumedge.env" >/dev/null
fi
if [ ! -f "$ENV_DIR/supervisor.env" ]; then
  echo "SUP_ENTRYPOINT=supervisor.py run-foreground" | sudo tee "$ENV_DIR/supervisor.env" >/dev/null
fi

sudo systemctl daemon-reload
sudo systemctl enable quantumedge.service supervisoragent.service
sudo systemctl start quantumedge.service supervisoragent.service

echo "[*] Services installed and started."
echo "Check status: sudo systemctl status quantumedge.service supervisoragent.service"
