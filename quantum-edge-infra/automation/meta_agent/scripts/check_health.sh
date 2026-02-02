#!/usr/bin/env bash
set -euo pipefail

URL=${1:-http://localhost:8000/api/v1/dashboard/health}
echo "Checking SupervisorAgent health at $URL"
if command -v curl >/dev/null 2>&1; then
  curl -s "$URL" || { echo "curl failed"; exit 1; }
else
  python - "$URL" <<'PY'
import json, sys, urllib.request
url=sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=5) as resp:
        print(resp.read().decode())
except Exception as e:
    print(f"health check failed: {e}", file=sys.stderr)
    sys.exit(1)
PY
fi
