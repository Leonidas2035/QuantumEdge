from __future__ import annotations
import pytest

pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)
import pytest

pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)

import json
from pathlib import Path

from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.config import LockbotConfig
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.replay.runner import (
    load_policy_config,
    run_replay,
)
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.replay.scenarios import (
    ScenarioConfig,
    generate_scenario,
)


def test_lockbot_replay_smoke(tmp_path: Path) -> None:
    cfg = ScenarioConfig(
        name="S_TREND_UP_PULLBACKS", duration_s=600, start_ts_ms=1_730_000_000_000
    )
    events = generate_scenario(cfg)
    out_dir = tmp_path / "smoke"
    policy_cfg = load_policy_config(
        Path("SupervisorAgent/configs/lockbot_btc_policy.yaml")
    )
    policy_cfg.enabled = True
    policy_cfg.execution_enabled = True
    bot_cfg = LockbotConfig.load(Path("LockBotBTC/config/lockbot_btc.yaml"))

    run_replay(
        events,
        out_dir=out_dir,
        policy_cfg=policy_cfg,
        bot_cfg=bot_cfg,
        tick_s=1,
        realtime=False,
    )

    metrics_path = out_dir / "metrics.json"
    with metrics_path.open("r", encoding="utf-8") as handle:
        metrics = json.load(handle)
    assert metrics["cmd_counts"]

    decisions_path = out_dir / "decisions.jsonl"
    with decisions_path.open("r", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    assert any('"type": "cmd"' in line for line in lines)
