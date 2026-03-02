import json
from pathlib import Path

from quantum_edge_core.supervisor.supervisor.autopilot.policy_manager import PolicyManager


def test_policy_validation_and_rollout(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    runtime = tmp_path / "runtime"
    history = tmp_path / "history"
    artifacts.mkdir()
    policy_path = artifacts / "policy.json"
    policy_path.write_text(
        json.dumps({"schema_hash": "abc", "thresholds": {"h1": 0.55}}), encoding="utf-8"
    )

    manager = PolicyManager(artifacts, runtime, history, history_keep=3)
    dest = manager.rollout(policy_path, reason="test")
    assert dest.exists()
    assert (history / "history.jsonl").exists()

    rolled_back = manager.rollback("test")
    assert rolled_back is None
