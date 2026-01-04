#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SCHEMA="$ROOT_DIR/deploy/questdb/schema.sql"

HOST="${1:-127.0.0.1}"
PORT="${2:-9000}"

if [ ! -f "$SCHEMA" ]; then
  echo "Missing schema file: $SCHEMA" >&2
  exit 1
fi

echo "[schema] Applying QuestDB schema to $HOST:$PORT"
curl -sf --data-binary @"$SCHEMA" "http://$HOST:$PORT/exec" >/dev/null
echo "[schema] Applied."
