#!/usr/bin/env bash
set -euo pipefail

echo "[*] Stopping and disabling services..."
sudo systemctl stop quantumedge.service supervisoragent.service || true
sudo systemctl disable quantumedge.service supervisoragent.service || true

echo "[*] Removing unit files..."
sudo rm -f /etc/systemd/system/quantumedge.service
sudo rm -f /etc/systemd/system/supervisoragent.service
sudo systemctl daemon-reload

echo "Done."
