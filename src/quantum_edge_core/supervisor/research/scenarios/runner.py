"""Scenario runner for research workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..backtest.engine import BacktestConfig, BacktestEngine
from ..backtest.report import write_backtest_reports
from ..replay.adapters import load_events

from .definitions import get_scenario
from .injector import inject_scenario


@dataclass
class ScenarioRunConfig:
    name: str
    symbol: str
    data_file: Path
    out_dir: Path
    seed: int = 42
    models_dir: Optional[Path] = None
    policy_mode: str = "normal"
    disable_policy: bool = False
    ml_mode: str = "auto"
    limit_rows: Optional[int] = None


def run_scenario(config: ScenarioRunConfig) -> Path:
    scenario = get_scenario(config.name)
    events = load_events(config.data_file, limit_rows=config.limit_rows)
    injected = inject_scenario(events, scenario, seed=config.seed)
    bt_cfg = BacktestConfig(
        symbol=config.symbol,
        seed=config.seed,
        policy_mode=config.policy_mode,
        disable_policy=config.disable_policy,
        models_dir=config.models_dir,
        ml_mode=config.ml_mode,
    )
    engine = BacktestEngine(bt_cfg)
    result = engine.run(injected)
    write_backtest_reports(result, config.out_dir)
    return config.out_dir
