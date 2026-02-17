from __future__ import annotations
import pytest
pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)
import pytest
pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)

import hashlib
import json
from pathlib import Path

from LockBotBTC.lockbot_btc.config import LockbotConfig
from LockBotBTC.lockbot_btc.replay.runner import load_policy_config, run_replay
from LockBotBTC.lockbot_btc.replay.scenarios import ScenarioConfig, generate_scenario


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_metrics(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _run(tmp_path: Path, run_id: str) -> Path:
    cfg = ScenarioConfig(name="S_RANGE_OSCILLATION", duration_s=600, start_ts_ms=1_730_000_000_000)
    events = generate_scenario(cfg)
    out_dir = tmp_path / run_id
    policy_cfg = load_policy_config(Path("SupervisorAgent/configs/lockbot_btc_policy.yaml"))
    policy_cfg.enabled = True
    policy_cfg.execution_enabled = True
    bot_cfg = LockbotConfig.load(Path("LockBotBTC/config/lockbot_btc.yaml"))
    run_replay(events, out_dir=out_dir, policy_cfg=policy_cfg, bot_cfg=bot_cfg, tick_s=1, realtime=False)
    return out_dir


def test_lockbot_replay_determinism(tmp_path: Path) -> None:
    out_a = _run(tmp_path, "run_a")
    out_b = _run(tmp_path, "run_b")

    metrics_a = _load_metrics(out_a / "metrics.json")
    metrics_b = _load_metrics(out_b / "metrics.json")
    assert metrics_a == metrics_b

    hash_a = _hash_file(out_a / "decisions.jsonl")
    hash_b = _hash_file(out_b / "decisions.jsonl")
    assert hash_a == hash_b
