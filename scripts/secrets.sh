#!/usr/bin/env sh
set -eu

ENV_FILE="${1:-config/secrets.local.env}"
ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
PATH_FILE="$ROOT_DIR/$ENV_FILE"

if [ ! -f "$PATH_FILE" ]; then
  echo "[secrets] Missing env file: $PATH_FILE" >&2
  exit 1
fi

loaded=""
while IFS= read -r line || [ -n "$line" ]; do
  line=$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  [ -z "$line" ] && continue
  case "$line" in
    \#*) continue ;;
  esac
  case "$line" in
    export\ *) line=$(printf '%s' "$line" | sed 's/^export[[:space:]]*//') ;;
  esac
  key=$(printf '%s' "$line" | cut -d= -f1 | sed 's/[[:space:]]*$//')
  value=$(printf '%s' "$line" | cut -d= -f2- | sed 's/^[[:space:]]*//')
  [ -z "$key" ] && continue
  case "$value" in
    \"*\") value=${value#\"}; value=${value%\"} ;;
    \'*\') value=${value#\'}; value=${value%\'} ;;
  esac
  export "$key=$value"
  loaded="${loaded}${loaded:+, }$key"
done < "$PATH_FILE"

if [ -z "$loaded" ]; then
  echo "[secrets] No keys loaded from $PATH_FILE"
else
  echo "[secrets] Loaded keys: $loaded"
fi

missing=""
for key in BINGX_DEMO_API_KEY BINGX_DEMO_API_SECRET; do
  eval "val=\${$key:-}"
  if [ -z "$val" ]; then
    missing="${missing}${missing:+, }$key"
  fi
done
if [ -n "$missing" ]; then
  echo "[secrets] Missing required keys: $missing"
fi
