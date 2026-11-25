"""Offline unit tests for tools/sync_report_to_gdrive.sh."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "tools" / "sync_report_to_gdrive.sh"


def _create_mock_rclone(tmp_path: Path, remotes_output: str = "gdrive:\n") -> tuple[Path, Path]:
    log_file = tmp_path / "rclone_calls.log"
    mock_bin = tmp_path / "mock_rclone"
    mock_bin.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "listremotes" ]]; then
    printf %s "{remotes_output}"
    exit 0
fi
printf '%s\\n' "$*" >> "{log_file}"
exit 0
""",
        encoding="utf-8",
    )
    mock_bin.chmod(0o755)
    return mock_bin, log_file


class TestSyncReportToGdrive:
    @pytest.mark.parametrize(
        "secret_name",
        [".env", ".env.local", "firecrawl.key", "client.pem", "rclone.conf"],
    )
    def test_blocks_secret_files(self, tmp_path: Path, secret_name: str):
        secret_file = tmp_path / secret_name
        secret_file.write_text("SECRET=placeholder", encoding="utf-8")

        proc = subprocess.run(
            ["bash", str(SYNC_SCRIPT), str(secret_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 3
        assert "BLOCKED_SECRET_FILE" in proc.stderr

    def test_missing_report_file_returns_error(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.md"
        proc = subprocess.run(
            ["bash", str(SYNC_SCRIPT), str(missing)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 1
        assert "Report file not found" in proc.stderr

    def test_dry_run_and_copy_with_mock_rclone(self, tmp_path: Path):
        report = tmp_path / "QE_TEST_REPORT.md"
        report.write_text("# Test Report\n", encoding="utf-8")
        mock_rclone, log_file = _create_mock_rclone(tmp_path, remotes_output="gdrive:\n")

        env = {**os.environ, "RCLONE_BIN": str(mock_rclone)}

        # 1. --dry-run invocation
        p_dry = subprocess.run(
            [
                "bash",
                str(SYNC_SCRIPT),
                str(report),
                "--dry-run",
                "--mount-dir",
                str(tmp_path / "unmounted"),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert p_dry.returncode == 0, p_dry.stderr
        assert "DRY_RUN_RCLONE_OK" in p_dry.stdout

        # 2. Normal copy invocation
        p_real = subprocess.run(
            [
                "bash",
                str(SYNC_SCRIPT),
                str(report),
                "--mount-dir",
                str(tmp_path / "unmounted"),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert p_real.returncode == 0, p_real.stderr
        assert "SYNCED_VIA_RCLONE" in p_real.stdout

        calls = log_file.read_text(encoding="utf-8").splitlines()
        assert calls[0] == f"copy {report} gdrive:QuantumEdge-Reports --dry-run"
        assert calls[1] == f"copy {report} gdrive:QuantumEdge-Reports"

    def test_unconfigured_remote_returns_clear_status(self, tmp_path: Path):
        report = tmp_path / "QE_TEST_REPORT.md"
        report.write_text("# Test Report\n", encoding="utf-8")
        mock_rclone, _ = _create_mock_rclone(tmp_path, remotes_output="")

        proc = subprocess.run(
            [
                "bash",
                str(SYNC_SCRIPT),
                str(report),
                "--mount-dir",
                str(tmp_path / "unmounted"),
            ],
            env={**os.environ, "RCLONE_BIN": str(mock_rclone)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 4
        assert "GDRIVE_NOT_CONFIGURED" in proc.stderr
