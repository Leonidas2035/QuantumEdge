from __future__ import annotations

import json
from pathlib import Path

from LockBotBTC.lockbot_btc.config import LockbotConfig
from LockBotBTC.lockbot_btc.replay.runner import load_policy_config, run_replay
from LockBotBTC.lockbot_btc.replay.scenarios import ScenarioConfig, generate_scenario


def test_lockbot_replay_safety_invariants(tmp_path: Path) -> None:
    cfg = ScenarioConfig(name="S_VOLATILITY_EXPANSION_ATR_SPIKE", duration_s=1900, start_ts_ms=1_730_000_000_000)
    events = generate_scenario(cfg)
    out_dir = tmp_path / "safety"
    policy_cfg = load_policy_config(Path("SupervisorAgent/configs/lockbot_btc_policy.yaml"))
    policy_cfg.enabled = True
    policy_cfg.execution_enabled = True
    bot_cfg = LockbotConfig.load(Path("LockBotBTC/config/lockbot_btc.yaml"))

    run_replay(events, out_dir=out_dir, policy_cfg=policy_cfg, bot_cfg=bot_cfg, tick_s=1, realtime=False)

    metrics_path = out_dir / "metrics.json"
    with metrics_path.open("r", encoding="utf-8") as handle:
        metrics = json.load(handle)

    assert metrics["min_distance_to_liq_bps"] is not None
    assert metrics["min_distance_to_liq_bps"] < bot_cfg.ddn.min_distance_to_liq_bps
    assert metrics["panic_count"] > 0
