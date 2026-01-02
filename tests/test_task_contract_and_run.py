import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
META_AGENT_DIR = ROOT_DIR / "meta_agent"
if str(META_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(META_AGENT_DIR))

from meta_core import _exit_code_for, run_task


class FakeLLMClient:
    def __init__(self, response: str):
        self._response = response

    def send(self, prompt: str) -> str:
        return self._response


def _write_task(path: Path, payload: dict) -> None:
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def test_task_contract_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_dir = tmp_path / "repo"
    project_root = base_dir / "project"
    project_root.mkdir(parents=True)

    monkeypatch.setenv("QE_ROOT", str(base_dir))
    monkeypatch.setenv("QE_RUNTIME_DIR", str(base_dir / "runtime"))

    invalid_task = {
        "task_id": "invalid_task",
        "created_at": "2026-01-02T22:40:00Z",
        "project_id": "meta_agent",
        "project_root": "project",
        "objective": "",
        "instructions": "Do something",
    }
    task_path = base_dir / "invalid_task.yaml"
    _write_task(task_path, invalid_task)

    report = run_task(str(task_path), llm_client=FakeLLMClient(""))
    assert report.exit_code == 40
    assert report.verdict == "error"


def test_run_task_warn_no_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_dir = tmp_path / "repo"
    project_root = base_dir / "project"
    target_file = project_root / "config" / "settings.yaml"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("old", encoding="utf-8")

    monkeypatch.setenv("QE_ROOT", str(base_dir))
    monkeypatch.setenv("QE_RUNTIME_DIR", str(base_dir / "runtime"))

    task = {
        "task_id": "warn_task",
        "created_at": "2026-01-02T22:41:00Z",
        "project_id": "meta_agent",
        "project_root": "project",
        "objective": "Update config settings",
        "instructions": "Update config/settings.yaml",
    }
    task_path = base_dir / "warn_task.yaml"
    _write_task(task_path, task)

    response = "===FILE: config/settings.yaml===\nnew\n"
    report = run_task(str(task_path), llm_client=FakeLLMClient(response))

    assert report.verdict == "warn"
    assert report.exit_code == 10
    assert report.changes.applied is False
    assert target_file.read_text(encoding="utf-8") == "old"
    patches_dir = base_dir / report.artifacts.patches_dir
    assert patches_dir.exists()
    assert report.changes.patches


def test_run_task_allow_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_dir = tmp_path / "repo"
    project_root = base_dir / "project"
    target_file = project_root / "bot" / "helper.py"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("old", encoding="utf-8")

    monkeypatch.setenv("QE_ROOT", str(base_dir))
    monkeypatch.setenv("QE_RUNTIME_DIR", str(base_dir / "runtime"))

    task = {
        "task_id": "allow_task",
        "created_at": "2026-01-02T22:42:00Z",
        "project_id": "meta_agent",
        "project_root": "project",
        "objective": "Update helper",
        "instructions": "Update bot/helper.py",
    }
    task_path = base_dir / "allow_task.yaml"
    _write_task(task_path, task)

    response = "===FILE: bot/helper.py===\nnew\n"
    report = run_task(str(task_path), llm_client=FakeLLMClient(response))

    assert report.verdict == "allow"
    assert report.exit_code == 0
    assert report.changes.applied is True
    assert target_file.read_text(encoding="utf-8") == "new"
    assert report.changes.patches
    patch_path = base_dir / report.changes.patches[0].patch_file
    assert patch_path.exists()


def test_exit_codes() -> None:
    assert _exit_code_for("allow") == 0
    assert _exit_code_for("warn") == 10
    assert _exit_code_for("block") == 20
    assert _exit_code_for("error") == 30
    assert _exit_code_for("error", "invalid_task") == 40
    assert _exit_code_for("error", "lock_busy") == 50
