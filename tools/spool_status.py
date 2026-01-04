#!/usr/bin/env python3
"""Inspect the L2 spool budget and replay cursor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from MarketDataHub.config import L2Config
from MarketDataHub.spool.status import summarize_spool


def _format_bytes(value: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


def _read_state(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Report L2 spool usage and cursor.")
    parser.add_argument("--spool-dir", help="Spool root (defaults to config)", default=None)
    parser.add_argument("--state-file", help="Replay cursor file", default=None)
    args = parser.parse_args()
    config = L2Config()
    spool_dir = Path(args.spool_dir or config.spool_dir)
    state_path = Path(args.state_file or Path(config.spool_dir) / ".replay_state.json")
    summary = summarize_spool(spool_dir)
    print(f"L2 spool: {spool_dir}")
    print(f"Files: {summary.files}")
    print(f"Size: {_format_bytes(summary.bytes)} / {config.max_spool_gb} GiB budget")
    if summary.oldest:
        print(f"Oldest file: {summary.oldest} (modified {summary.oldest.stat().st_mtime})")
    if summary.newest:
        print(f"Newest file: {summary.newest} (modified {summary.newest.stat().st_mtime})")
    print(f"Budget mode: {config.on_budget_exceeded or 'block'}")
    state = _read_state(state_path)
    if state:
        print(f"Cursor: {state_path}")
        print(f"  last_file: {state.get('last_file')}")
        print(f"  last_line: {state.get('last_line')}")
        print(f"  updated: {state.get('updated')}")
    else:
        print(f"Cursor file {state_path} missing or invalid")


if __name__ == "__main__":
    main()
