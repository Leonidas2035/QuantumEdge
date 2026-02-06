"""CLI helpers for episode cutter/runner/report."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from .cutter import cut_episodes
from .report import generate_report
from .runner import EpisodeRunConfig, run_episode_set
from .synthetic_ticks import write_synthetic_ticks


def parse_episodes_args(command: str, argv: Optional[list[str]] = None) -> argparse.Namespace:
    if command == "episodes-cut":
        parser = argparse.ArgumentParser(prog="supervisor.py episodes-cut")
        parser.add_argument("--episode-set", required=True)
        parser.add_argument("--ticks-path", required=True, type=Path)
        parser.add_argument("--format", dest="fmt", choices=["csv", "jsonl"], default=None)
        parser.add_argument("--symbols", type=str, default=None)
        parser.add_argument("--max-episodes-per-scenario", type=int, default=20)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--out-dir", type=Path, default=None)
        parser.add_argument("--scenarios-path", type=Path, default=None)
        parser.add_argument("--synthetic", action="store_true", help="Generate synthetic ticks if path is missing.")
        return parser.parse_args(argv)

    if command == "episodes-run":
        parser = argparse.ArgumentParser(prog="supervisor.py episodes-run")
        parser.add_argument("--episode-set", required=True)
        parser.add_argument("--episodes-manifest", required=True, type=Path)
        parser.add_argument("--scenario-id", dest="scenario_id", default=None)
        parser.add_argument("--replay-speed", choices=["instant", "realtime"], default="instant")
        parser.add_argument("--runs-path", type=Path, default=Path("SupervisorAgent") / "runtime" / "runs")
        parser.add_argument("--scenarios-path", type=Path, default=None)
        parser.add_argument("--policy-path", type=Path, default=None)
        parser.add_argument("--signal-window-s", type=int, default=5)
        parser.add_argument("--stats-interval-s", type=int, default=30)
        parser.add_argument("--directives-interval-s", type=int, default=10)
        return parser.parse_args(argv)

    if command == "episodes-report":
        parser = argparse.ArgumentParser(prog="supervisor.py episodes-report")
        parser.add_argument("--episode-set", required=True)
        parser.add_argument("--runs-path", type=Path, default=Path("SupervisorAgent") / "runtime" / "runs")
        parser.add_argument("--out-dir", type=Path, default=None)
        return parser.parse_args(argv)

    raise ValueError(f"Unknown episodes command: {command}")


def run_episodes_command(command: str, args: argparse.Namespace) -> int:
    if command == "episodes-cut":
        symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
        ticks_path = args.ticks_path
        if args.synthetic and not ticks_path.exists():
            out_dir = args.out_dir or (Path("SupervisorAgent") / "runtime" / "episodes" / args.episode_set)
            ticks_path = out_dir / "synthetic_ticks.jsonl"
            write_synthetic_ticks(ticks_path)
        manifest_path = cut_episodes(
            episode_set=args.episode_set,
            ticks_path=ticks_path,
            fmt=args.fmt,
            symbols=symbols,
            max_episodes_per_scenario=args.max_episodes_per_scenario,
            seed=args.seed,
            out_dir=args.out_dir,
            scenarios_path=args.scenarios_path,
        )
        print(f"[INFO] Episodes written. Manifest: {manifest_path}")
        return 0

    if command == "episodes-run":
        cfg = EpisodeRunConfig(
            episode_set=args.episode_set,
            manifest_path=args.episodes_manifest,
            runs_path=args.runs_path,
            scenario_filter=args.scenario_id,
            replay_speed=args.replay_speed,
            signal_window_s=args.signal_window_s,
            stats_interval_s=args.stats_interval_s,
            directives_interval_s=args.directives_interval_s,
            scenarios_path=args.scenarios_path,
            policy_path=args.policy_path,
        )
        return run_episode_set(cfg)

    if command == "episodes-report":
        out_dir = args.out_dir or (Path("SupervisorAgent") / "runtime" / "reports" / args.episode_set)
        report_path = generate_report(args.episode_set, args.runs_path, out_dir)
        print(f"[INFO] Report written to: {report_path}")
        return 0

    raise ValueError(f"Unknown episodes command: {command}")
