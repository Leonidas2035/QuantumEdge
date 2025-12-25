#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "WARN: root .venv not found. Using system python." >&2
  PY="python3"
fi

"$PY" "$ROOT/QuantumEdge.py" stop "$@"
