"""CLI entrypoints for SupervisorAgent research suite."""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .backtest.engine import BacktestConfig, BacktestEngine
from .backtest.report import write_backtest_reports
from .replay.adapters import load_events
from .replay.replayer import ReplayConfig, replay_events
from .scenarios.runner import ScenarioRunConfig, run_scenario


def _default_run_id() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _resolve_data_file(data_dir: Optional[Path], data_file: Optional[Path], symbol: str, timeframe: str) -> Path:
    if data_file:
        return data_file
    if data_dir is None:
        raise ValueError("--data_dir or --data_file is required")
    candidates = [
        data_dir / f"{symbol}_ticks.csv",
        data_dir / f"{symbol}_ticks.jsonl",
        data_dir / f"{symbol}_{timeframe}.csv",
        data_dir / f"{symbol}_{timeframe}.jsonl",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for candidate in data_dir.glob("*.csv"):
        return candidate
    for candidate in data_dir.glob("*.jsonl"):
        return candidate
    raise FileNotFoundError(f"No data files found in {data_dir}")


def parse_research_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="supervisor.py research", description="Supervisor research suite")
    sub = parser.add_subparsers(dest="research_cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--symbol", type=str, default="BTCUSDT")
    common.add_argument("--run_id", type=str, default=None)
    common.add_argument("--out_dir", type=Path, default=None)
    common.add_argument("--seed", type=int, default=42)
    common.add_argument("--models_dir", type=Path, default=None)
    common.add_argument("--policy_mode", type=str, default="normal")
    common.add_argument("--disable_policy", action="store_true")
    common.add_argument("--ml_mode", type=str, default="auto", choices=["auto", "runtime", "disabled", "simple"])

    backtest = sub.add_parser("backtest", parents=[common], help="Run deterministic backtest")
    backtest.add_argument("--data_dir", type=Path, default=None)
    backtest.add_argument("--data_file", type=Path, default=None)
    backtest.add_argument("--timeframe", type=str, default="ticks")
    backtest.add_argument("--limit_rows", type=int, default=None)
    backtest.add_argument("--no_equity_curve", action="store_true")
    backtest.add_argument("--no_trades", action="store_true")

    replay = sub.add_parser("replay", parents=[common], help="Replay ticks/bars offline")
    replay.add_argument("--data_dir", type=Path, default=None)
    replay.add_argument("--data_file", type=Path, default=None)
    replay.add_argument("--timeframe", type=str, default="ticks")
    replay.add_argument("--limit_rows", type=int, default=None)
    replay.add_argument("--speed", type=float, default=0.0)
    replay.add_argument("--no_equity_curve", action="store_true")
    replay.add_argument("--no_trades", action="store_true")

    scenario = sub.add_parser("scenario", parents=[common], help="Run a scenario injection")
    scenario.add_argument("--name", type=str, required=True, choices=["spread_spike", "latency_spike", "volatility_spike"])
    scenario.add_argument("--data_dir", type=Path, default=None)
    scenario.add_argument("--data_file", type=Path, default=None)
    scenario.add_argument("--timeframe", type=str, default="ticks")
    scenario.add_argument("--limit_rows", type=int, default=None)

    return parser.parse_args(argv)


def _resolve_out_dir(run_id: str, out_dir: Optional[Path]) -> Path:
    if out_dir:
        return out_dir
    return Path("artifacts") / "research" / run_id


def _print_metrics(metrics: dict) -> None:
    print("[RESULT] total_pnl:", f"{metrics.get('total_pnl', 0.0):.4f}")
    print("[RESULT] max_drawdown:", f"{metrics.get('max_drawdown', 0.0):.4f}")
    print("[RESULT] trades:", metrics.get("trades", 0))
    print("[RESULT] win_rate:", f"{metrics.get('win_rate', 0.0):.2%}")


def run_research_command(args: argparse.Namespace) -> int:
    run_id = args.run_id or _default_run_id()
    out_dir = _resolve_out_dir(run_id, args.out_dir)
    data_file = _resolve_data_file(args.data_dir, args.data_file, args.symbol, args.timeframe)

    if args.research_cmd == "backtest":
        events = load_events(data_file, limit_rows=args.limit_rows)
        cfg = BacktestConfig(
            symbol=args.symbol,
            seed=args.seed,
            policy_mode=args.policy_mode,
            disable_policy=args.disable_policy,
            models_dir=args.models_dir,
            ml_mode=args.ml_mode,
        )
        engine = BacktestEngine(cfg)
        result = engine.run(events)
        write_backtest_reports(
            result,
            out_dir,
            write_equity_curve=not args.no_equity_curve,
            write_trades=not args.no_trades,
        )
        print(f"[INFO] Backtest outputs written to: {out_dir}")
        _print_metrics(result.metrics)
        return 0

    if args.research_cmd == "replay":
        events = load_events(data_file, limit_rows=args.limit_rows)
        cfg = BacktestConfig(
            symbol=args.symbol,
            seed=args.seed,
            policy_mode=args.policy_mode,
            disable_policy=args.disable_policy,
            models_dir=args.models_dir,
            ml_mode=args.ml_mode,
        )
        engine = BacktestEngine(cfg)
        started_at = time.time()
        replay_events(events, engine.process_event, ReplayConfig(speed=args.speed))
        finished_at = time.time()
        result = engine.finalize(started_at=started_at, finished_at=finished_at)
        write_backtest_reports(
            result,
            out_dir,
            write_equity_curve=not args.no_equity_curve,
            write_trades=not args.no_trades,
        )
        print(f"[INFO] Replay outputs written to: {out_dir}")
        _print_metrics(result.metrics)
        return 0

    if args.research_cmd == "scenario":
        scenario_out = run_scenario(
            ScenarioRunConfig(
                name=args.name,
                symbol=args.symbol,
                data_file=data_file,
                out_dir=out_dir,
                seed=args.seed,
                models_dir=args.models_dir,
                policy_mode=args.policy_mode,
                disable_policy=args.disable_policy,
                ml_mode=args.ml_mode,
                limit_rows=args.limit_rows,
            )
        )
        print(f"[INFO] Scenario outputs written to: {scenario_out}")
        return 0

    raise ValueError(f"Unknown research command: {args.research_cmd}")
