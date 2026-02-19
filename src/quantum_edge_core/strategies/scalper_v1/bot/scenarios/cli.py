"""Unified CLI for scenario datasets."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from .build import build_scenarios_pipeline
from .report import build_report
from .validate import validate_scenarios


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scenario dataset tooling.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build")
    build.add_argument("--symbol", required=True)
    build.add_argument("--ticks", required=True)
    build.add_argument("--depth", default=None)
    build.add_argument("--out", required=True)
    build.add_argument("--episodes", type=int, default=200)
    build.add_argument("--workers", type=int, default=4)
    build.add_argument("--limit-rows", type=int, default=None)
    build.add_argument(
        "--format", dest="output_format", default=None, help="csv|parquet"
    )

    validate = sub.add_parser("validate")
    validate.add_argument("--symbol", required=True)
    validate.add_argument("--root", required=True)

    report = sub.add_parser("report")
    report.add_argument("--symbol", required=True)
    report.add_argument("--root", required=True)
    report.add_argument("--format", default="md", choices=["md", "json"])
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.cmd == "build":
        return build_scenarios_pipeline(
            symbol=args.symbol,
            ticks_path=Path(args.ticks),
            depth_path=Path(args.depth) if args.depth else None,
            out_root=Path(args.out),
            max_episodes=args.episodes,
            workers=args.workers,
            limit_rows=args.limit_rows,
            output_format=args.output_format,
        )
    if args.cmd == "validate":
        return validate_scenarios(args.symbol, Path(args.root))
    if args.cmd == "report":
        report_path = build_report(args.symbol, Path(args.root), fmt=args.format)
        print(f"Report written: {report_path}")
        return 0
    raise SystemExit("Unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
