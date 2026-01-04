#!/usr/bin/env python3
"""Safely prune spooled L2 files that have already been replayed."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


def _read_state(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _candidate_files(
    spool_dir: Path,
    last_file: Optional[str],
    retention_seconds: float,
    skip_today_hour: bool,
) -> Iterable[Path]:
    now = time.time()
    today_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_hour = datetime.now(timezone.utc).strftime("%H")
    for path in sorted(spool_dir.rglob("*.jsonl.gz")):
        try:
            rel = path.relative_to(spool_dir)
        except ValueError:
            continue
        if len(rel.parts) < 2:
            continue
        date_part, hour_part = rel.parts[0], rel.parts[1]
        if skip_today_hour and date_part == today_label and hour_part == current_hour:
            continue
        rel_str = rel.as_posix()
        if last_file and rel_str >= last_file:
            continue
        if now - path.stat().st_mtime <= retention_seconds:
            continue
        yield path


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune replayed L2 spool files safely.")
    parser.add_argument("--spool-dir", default="spool/l2", help="Spool directory root")
    parser.add_argument("--state-file", default="spool/l2/.replay_state.json", help="Replay cursor file")
    parser.add_argument("--retention-days", type=int, default=7, help="Minimum age in days")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletions; omit to run in dry-run mode",
    )
    parser.add_argument(
        "--include-today",
        action="store_true",
        help="Allow pruning files from the current hour (use with caution)",
    )
    args = parser.parse_args()

    spool_dir = Path(args.spool_dir)
    state = _read_state(Path(args.state_file))
    if not state or not state.get("last_file"):
        raise SystemExit("Replay state missing or incomplete; replay before pruning.")
    last_file = state.get("last_file")
    retention_seconds = args.retention_days * 24 * 60 * 60
    candidates = list(
        _candidate_files(
            spool_dir,
            last_file,
            retention_seconds,
            skip_today_hour=not args.include_today,
        )
    )
    if not candidates:
        print("No spool files qualify for pruning.")
        return
    print(f"Found {len(candidates)} candidate files (retention {args.retention_days} days).")
    for path in candidates:
        print("  ", path)
    if args.apply:
        for path in candidates:
            path.unlink()
        print("Deleted candidate files.")
    else:
        print("Dry run only; rerun with --apply to delete files.")


if __name__ == "__main__":
    main()
