import sys
import os
import pytest
from pathlib import Path
import yaml

# Determine repo root
REPO_ROOT = Path(__file__).resolve().parents[1]
META_AGENT_DIR = REPO_ROOT / "src" / "quantum_edge_infra" / "automation" / "meta_agent"

# Add to sys.path if not present
if str(META_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(META_AGENT_DIR))

# Now we can import from meta_core
try:
    from meta_core import run_task, _exit_code_for
except ImportError:
    # Fallback for if meta_agent is not in path correctly
    print(f"Failed to import meta_core from {META_AGENT_DIR}")
    raise


class FakeLLMClient:
    def __init__(self, response: str):
        self._response = response

    def send(self, prompt: str, **kwargs) -> str:
        return self._response


def test_run_task_with_raw_string(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Setup environment
    base_dir = tmp_path / "repo"
    base_dir.mkdir()

    # Mock environment variables to point to our temp repo
    monkeypatch.setenv("QE_ROOT", str(base_dir))
    monkeypatch.setenv("QE_RUNTIME_DIR", str(base_dir / "runtime"))

    # Create some dummy project structure to avoid scanner errors
    # Use 'bot' folder which is in default safety policy allowed paths
    (base_dir / "bot").mkdir()
    (base_dir / "bot" / "main.py").write_text("print('hello')", encoding="utf-8")

    instruction = "Please update the documentation."

    # Mock LLM response - simulate a successful change
    response = "===FILE: bot/main.py===\nprint('hello world')\n"
    client = FakeLLMClient(response)

    # Call run_task with string
    report = run_task(instruction, llm_client=client)

    # Assertions
    if report.exit_code != 0:
        print(f"Report verdict: {report.verdict}")
        print(f"Report summary: {report.summary}")
        print(f"Report safety checks: {report.safety.checks}")
        print(f"Report errors: {report.errors}")

    assert report.exit_code == 0
    assert report.verdict == "allow"

    # Verify artifacts
    artifacts_dir = base_dir / "runtime" / "runs" / report.run_id
    task_yaml_path = artifacts_dir / "task.yaml"
    assert task_yaml_path.exists()

    with open(task_yaml_path, "r", encoding="utf-8") as f:
        task_data = yaml.safe_load(f)

    assert task_data["instructions"] == instruction
    assert task_data["objective"] == "CLI Request"
    assert task_data["project_id"] == "monorepo"
    assert task_data["mode"] == "task"
