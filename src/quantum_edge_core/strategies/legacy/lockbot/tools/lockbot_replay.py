"""CLI for LockBotBTC replay/backtest harness."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Optional

from LockBotBTC.lockbot_btc.config import LockbotConfig
from LockBotBTC.lockbot_btc.replay import (
    load_dataset,
    load_ddn_config,
    load_policy_config,
    run_replay,
)
from LockBotBTC.lockbot_btc.replay.scenarios import (
    SCENARIO_NAMES,
    ScenarioConfig,
    generate_scenario,
)


def _default_out_dir() -> Path:
    run_id = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    return Path("runtime/replay_runs") / run_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LockBotBTC replay/backtest harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run a replay scenario or dataset.")
    group = run.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", choices=sorted(SCENARIO_NAMES))
    group.add_argument("--dataset", help="Path to JSONL dataset.")
    run.add_argument(
        "--duration-s", type=int, default=7200, help="Scenario duration (seconds)."
    )
    run.add_argument("--start-ts-ms", type=int, default=1_730_000_000_000)
    run.add_argument("--out", type=Path, default=_default_out_dir())
    run.add_argument("--tick-s", type=int, default=1)
    run.add_argument("--realtime", action="store_true")
    run.add_argument("--paper-fill-model", choices=["tierA", "tierB"], default="tierA")
    run.add_argument(
        "--config",
        type=Path,
        default=Path("SupervisorAgent/configs/lockbot_btc_policy.yaml"),
    )
    run.add_argument(
        "--bot-config", type=Path, default=Path("LockBotBTC/config/lockbot_btc.yaml")
    )
    run.add_argument("--ddn-config", type=Path)
    run.add_argument(
        "--account-topics", help="Comma-separated account topics (optional)."
    )
    run.add_argument(
        "--execution-enabled",
        action="store_true",
        help="Allow EXEC_STEP intents in replay.",
    )
    run.add_argument("--time-min", type=int, help="Dataset filter: min ts_event (ms).")
    run.add_argument("--time-max", type=int, help="Dataset filter: max ts_event (ms).")
    return parser


def _parse_account_topics(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        if args.scenario:
            cfg = ScenarioConfig(
                name=args.scenario,
                duration_s=args.duration_s,
                start_ts_ms=args.start_ts_ms,
            )
            events = generate_scenario(cfg)
            meta = {
                "scenario": args.scenario,
                "duration_s": args.duration_s,
                "start_ts_ms": args.start_ts_ms,
            }
        else:
            dataset_path = Path(args.dataset)
            events = load_dataset(
                dataset_path, time_min=args.time_min, time_max=args.time_max
            )
            meta = {"dataset": str(dataset_path)}

        policy_cfg = load_policy_config(args.config)
        if args.execution_enabled:
            policy_cfg.execution_enabled = True
        bot_cfg = LockbotConfig.load(args.bot_config)
        ddn_cfg = (
            load_ddn_config(args.ddn_config, base=bot_cfg.ddn)
            if args.ddn_config
            else None
        )
        account_topics = _parse_account_topics(args.account_topics)

        run_replay(
            events,
            out_dir=Path(args.out),
            policy_cfg=policy_cfg,
            bot_cfg=bot_cfg,
            ddn_cfg=ddn_cfg,
            tick_s=args.tick_s,
            realtime=args.realtime,
            paper_fill_model=args.paper_fill_model,
            account_topics=account_topics,
            metadata=meta,
        )


if __name__ == "__main__":
    main()
