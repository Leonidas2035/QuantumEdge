#!/usr/bin/env sh
set -eu

MODE=${1:-}
shift || true

if [ -z "$MODE" ]; then
  echo "Usage: ./scripts/run.sh {supervisor|bot|meta} [args...]"
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
CLI="$ROOT_DIR/tools/qe_cli.py"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[run] Missing .venv. Run scripts/setup.sh first."
  exit 1
fi

PYTHONPATH="$ROOT_DIR:$ROOT_DIR/ai_scalper_bot:$ROOT_DIR/SupervisorAgent:$ROOT_DIR/meta_agent${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
export QE_ROOT="$ROOT_DIR"

echo "[run] Environment variables to set (do not commit secrets):"
echo "  SCALPER_SECRETS_PASSPHRASE"
echo "  BINANCE_API_KEY / BINANCE_API_SECRET"
echo "  BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET"
echo "  BINGX_API_KEY / BINGX_API_SECRET"
echo "  BINGX_DEMO_API_KEY / BINGX_DEMO_API_SECRET"
echo "  OPENAI_API_KEY / OPENAI_API_KEY_SUPERVISOR"
echo "  OPENAI_API_KEY_DEV / OPENAI_API_KEY_PROD"

case "$MODE" in
  supervisor)
    "$PYTHON_BIN" "$CLI" supervisor --config "$ROOT_DIR/config/supervisor.yaml" "$@"
    ;;
  bot)
    "$PYTHON_BIN" "$CLI" bot --config "$ROOT_DIR/config/bot.yaml" "$@"
    ;;
  meta)
    "$PYTHON_BIN" "$CLI" meta --config "$ROOT_DIR/config/meta_agent.yaml" "$@"
    ;;
  *)
    echo "Unknown mode: $MODE"
    exit 1
    ;;
 esac
