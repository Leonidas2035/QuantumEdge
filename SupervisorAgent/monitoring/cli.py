"""CLI helpers for telemetry summary/alerts/events."""

from __future__ import annotations

import argparse
import json


def parse_telemetry_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="supervisor.py telemetry", description="Telemetry commands")
    sub = parser.add_subparsers(dest="telemetry_cmd", required=True)
    sub.add_parser("summary")
    sub.add_parser("alerts")
    events = sub.add_parser("events")
    events.add_argument("--limit", type=int, default=200)
    return parser.parse_args(argv)


def run_telemetry_command(app, args: argparse.Namespace) -> int:
    if args.telemetry_cmd == "summary":
        payload = app.get_telemetry_summary()
    elif args.telemetry_cmd == "alerts":
        payload = app.get_telemetry_alerts()
    elif args.telemetry_cmd == "events":
        payload = {"events": app.get_telemetry_events(limit=args.limit)}
    else:
        raise ValueError(f"Unknown telemetry command: {args.telemetry_cmd}")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
