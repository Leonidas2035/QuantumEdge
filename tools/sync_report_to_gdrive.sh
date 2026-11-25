#!/usr/bin/env bash
# Sync a QuantumEdge task report (reports/tasks/<TASK_ID>.md) to Google Drive "QuantumEdge-Reports".
# Supports mounted GVFS/FUSE dir (/home/layman/mnt/gdrive-reports) or `rclone copy`.
# Blocks uploading any secret/credential files.

set -euo pipefail

REMOTE="gdrive:QuantumEdge-Reports"
MOUNT_DIR="/home/layman/mnt/gdrive-reports"
DRY_RUN=0
REPORT_FILE=""
RCLONE_BIN="${RCLONE_BIN:-rclone}"

usage() {
    cat <<'EOF'
Usage: sync_report_to_gdrive.sh <report_file> [--dry-run] [--remote <remote:path>] [--mount-dir <dir>]

Options:
  --dry-run           Simulate copy without modifying remote or mount directory
  --remote <target>   rclone remote target (default: gdrive:QuantumEdge-Reports)
  --mount-dir <dir>   Local mount point (default: /home/layman/mnt/gdrive-reports)
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --remote)
            REMOTE="$2"
            shift 2
            ;;
        --mount-dir)
            MOUNT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "ERROR: Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [[ -n "${REPORT_FILE}" ]]; then
                echo "ERROR: Multiple report files provided: '${REPORT_FILE}' and '$1'" >&2
                exit 2
            fi
            REPORT_FILE="$1"
            shift
            ;;
    esac
done

if [[ -z "${REPORT_FILE}" ]]; then
    echo "ERROR: Missing <report_file> argument." >&2
    usage >&2
    exit 2
fi

BASENAME="$(basename "${REPORT_FILE}")"
LOWER_NAME="$(echo "${BASENAME}" | tr '[:upper:]' '[:lower:]')"
LOWER_PATH="$(echo "${REPORT_FILE}" | tr '[:upper:]' '[:lower:]')"

# Block secrets, keys, and rclone/OAuth configs
if [[ "${LOWER_NAME}" == ".env" ]] || [[ "${LOWER_NAME}" == .env.* ]] || \
   [[ "${LOWER_NAME}" == *.key ]] || [[ "${LOWER_NAME}" == *.pem ]] || \
   [[ "${LOWER_NAME}" == "rclone.conf" ]] || [[ "${LOWER_NAME}" == *secrets* ]] || \
   [[ "${LOWER_PATH}" == */.config/rclone/* ]]; then
    echo "BLOCKED_SECRET_FILE: Refusing to sync sensitive file '${REPORT_FILE}'." >&2
    exit 3
fi

if [[ ! -f "${REPORT_FILE}" ]]; then
    echo "ERROR: Report file not found: '${REPORT_FILE}'" >&2
    exit 1
fi

# 1. Prefer active local mount point if mounted
if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "${MOUNT_DIR}"; then
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo "[DRY-RUN] cp -f \"${REPORT_FILE}\" \"${MOUNT_DIR}/${BASENAME}\""
    else
        cp -f "${REPORT_FILE}" "${MOUNT_DIR}/${BASENAME}"
        echo "SYNCED_VIA_MOUNT: ${MOUNT_DIR}/${BASENAME}"
    fi
    exit 0
fi

# 2. Fallback to rclone copy if rclone and target remote are configured
REMOTE_NAME="${REMOTE%%:*}:"
if command -v "${RCLONE_BIN}" >/dev/null 2>&1 || [[ -x "${RCLONE_BIN}" ]]; then
    if "${RCLONE_BIN}" listremotes 2>/dev/null | grep -Fxq "${REMOTE_NAME}"; then
        RCLONE_ARGS=("copy" "${REPORT_FILE}" "${REMOTE}")
        if [[ "${DRY_RUN}" -eq 1 ]]; then
            RCLONE_ARGS+=("--dry-run")
        fi
        "${RCLONE_BIN}" "${RCLONE_ARGS[@]}"
        if [[ "${DRY_RUN}" -eq 1 ]]; then
            echo "DRY_RUN_RCLONE_OK: ${REPORT_FILE} -> ${REMOTE}"
        else
            echo "SYNCED_VIA_RCLONE: ${REPORT_FILE} -> ${REMOTE}"
        fi
        exit 0
    fi
fi

echo "GDRIVE_NOT_CONFIGURED: Neither '${MOUNT_DIR}' is mounted nor rclone remote '${REMOTE_NAME}' is configured." >&2
echo "Run as operator:" >&2
echo "  sudo apt-get update && sudo apt-get install -y rclone" >&2
echo "  rclone config   # create remote 'gdrive' (type: drive, scope: drive)" >&2
echo "  rclone mkdir gdrive:QuantumEdge-Reports" >&2
echo "  rclone mount gdrive:QuantumEdge-Reports ${MOUNT_DIR} --vfs-cache-mode writes --daemon" >&2
exit 4
