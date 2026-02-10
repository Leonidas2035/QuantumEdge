import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
META_AGENT_DIR = ROOT_DIR / "src" / "quantum_edge_infra" / "automation" / "meta_agent"
if str(META_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(META_AGENT_DIR))

import meta_agent as meta_agent_mod
import watch as watch_mod
from task_contract import Report, ReportArtifacts, ReportChanges, ReportSafety, PatchInfo


class DummyLogger:
    def info(self, *args, **kwargs):
        return None


def _make_report(run_id: str, verdict: str, exit_code: int) -> Report:
    return Report(
        run_id=run_id,
        task_id="task",
        started_at="2026-01-02T00:00:00Z",
        finished_at="2026-01-02T00:00:01Z",
        verdict=verdict,
        exit_code=exit_code,
        summary="summary",
        changes=ReportChanges(patches=[PatchInfo(path="a", patch_file="b")], applied=False, files_changed=1),
        safety=ReportSafety(policy_version="safety_policy.yaml", checks=[]),
        artifacts=ReportArtifacts(
            report_path="runtime/runs/x/report.json",
            patches_dir="runtime/runs/x/patches",
            logs_path=None,
            context_manifest_path="runtime/runs/x/context_manifest.json",
            task_path="runtime/runs/x/task.yaml",
        ),
    )


def test_health_command_exit_codes(tmp_path: Path) -> None:
    ok, results = meta_agent_mod._health_check(str(tmp_path / "runtime"))
    assert ok is True
    assert all(item[1] for item in results)


def test_watch_moves_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    failed = tmp_path / "failed"
    inbox.mkdir()

    task1 = inbox / "001_task.yaml"
    task2 = inbox / "002_task.yaml"
    task1.write_text("task: one", encoding="utf-8")
    task2.write_text("task: two", encoding="utf-8")

    reports = [
        _make_report("run1", "allow", 0),
        _make_report("run2", "error", 30),
    ]

    def fake_run_task(path, **kwargs):
        return reports.pop(0)

    monkeypatch.setattr(watch_mod, "run_task", fake_run_task)

    result = watch_mod.process_inbox_once(
        inbox=str(inbox),
        archive=str(archive),
        failed=str(failed),
        logger=DummyLogger(),
    )
    assert result["processed"] == 2
    assert (archive / "run1_001_task.yaml").exists()
    assert (failed / "run2_002_task.yaml").exists()
    assert (failed / "run2_002_task.yaml.error.json").exists()


def test_run_task_json_stdout() -> None:
    report = _make_report("run_json", "warn", 10)
    payload = json.loads(meta_agent_mod._format_run_summary_json(report))
    assert payload["run_id"] == "run_json"
    assert payload["verdict"] == "warn"
    assert payload["exit_code"] == 10
    assert payload["report_path"]
    assert payload["patches_dir"]
