#!/usr/bin/env bash
set -euo pipefail

UNIT_NAME="quantumedge-supervisor.service"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SRC_UNIT="$SCRIPT_DIR/$UNIT_NAME"
DEST_DIR="/etc/systemd/system"

if [ ! -f "$SRC_UNIT" ]; then
  echo "[install] Missing unit file: $SRC_UNIT" >&2
  exit 1
fi

echo "[install] Copying $UNIT_NAME to $DEST_DIR"
sudo install -m 0644 "$SRC_UNIT" "$DEST_DIR/$UNIT_NAME"
sudo systemctl daemon-reload
echo "[install] Done. Next steps:"
echo "  sudo systemctl enable $UNIT_NAME"
echo "  sudo systemctl start $UNIT_NAME"
echo "  sudo systemctl status $UNIT_NAME"
