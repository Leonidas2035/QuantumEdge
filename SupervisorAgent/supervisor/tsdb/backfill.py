"""Backfill TSDB from historical events."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from supervisor.audit_report import load_events_for_date
from supervisor.tsdb.base import TimeseriesStore
from supervisor.tsdb.mappers import event_to_points


def _load_checkpoint(path: Path) -> date | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        val = data.get("last_date")
        return date.fromisoformat(val) if val else None
    except Exception:
        return None


def _save_checkpoint(path: Path, last_date: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_date": last_date.isoformat()}, indent=2), encoding="utf-8")


def run_backfill(events_dir: Path, store: TimeseriesStore, days: int, checkpoint_path: Path, logger: logging.Logger) -> None:
    """Backfill last N days of events into TSDB."""

    start_day = date.today() - timedelta(days=days - 1)
    already = _load_checkpoint(checkpoint_path)

    for offset in range(days):
        current = start_day + timedelta(days=offset)
        if already and current <= already:
            continue
        events = load_events_for_date(events_dir, current)
        if not events:
            continue
        batch = []
        for ev in events:
            batch.extend(event_to_points(ev))
        if batch:
            store.write_points(batch)
            logger.info("Backfilled %s points for %s", len(batch), current.isoformat())
            _save_checkpoint(checkpoint_path, current)
