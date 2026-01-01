#!/usr/bin/env sh
set -eu

UNIT_NAME="quantumedge-supervisor.service"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SRC_UNIT="$SCRIPT_DIR/$UNIT_NAME"
DEST_DIR="/etc/systemd/system"

if [ ! -f "$SRC_UNIT" ]; then
  echo "[install] Missing unit file: $SRC_UNIT" >&2
  exit 1
fi

echo "[install] Copying $UNIT_NAME to $DEST_DIR"
sudo cp "$SRC_UNIT" "$DEST_DIR/$UNIT_NAME"
sudo systemctl daemon-reload
echo "[install] Done. Enable with: sudo systemctl enable --now $UNIT_NAME"
