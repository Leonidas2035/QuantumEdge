"""Backfill runtime telemetry into TSDB for a time range."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

from quantum_edge_core.supervisor.supervisor.ingest.parsers import (
    event_hash,
    event_to_point,
    exec_to_point,
    parse_event_line,
    parse_exec_line,
)
from quantum_edge_core.supervisor.supervisor.tsdb.writer import TsdbWriter


def run_backfill(
    events_path: Path,
    exec_path: Optional[Path],
    start_ts: datetime,
    end_ts: datetime,
    writer: TsdbWriter,
    logger: logging.Logger,
    symbol: Optional[str] = None,
) -> Dict[str, int]:
    seen: Set[str] = set()
    counts = {"events": 0, "exec": 0}
    for path in _event_files(events_path):
        counts["events"] += _backfill_file(
            path, start_ts, end_ts, writer, seen, is_exec=False, symbol=symbol
        )
    if exec_path:
        for path in _event_files(exec_path):
            counts["exec"] += _backfill_file(
                path, start_ts, end_ts, writer, seen, is_exec=True, symbol=symbol
            )
    logger.info("TSDB backfill complete: %s", counts)
    return counts


def _event_files(base_path: Path) -> Iterable[Path]:
    if base_path.exists():
        yield base_path
    pattern = base_path.with_name(f"{base_path.stem}.*{base_path.suffix}")
    for path in sorted(base_path.parent.glob(pattern.name)):
        if path.is_file():
            yield path


def _backfill_file(
    path: Path,
    start_ts: datetime,
    end_ts: datetime,
    writer: TsdbWriter,
    seen: Set[str],
    is_exec: bool,
    symbol: Optional[str],
) -> int:
    ingested = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                digest = event_hash(line)
                if digest in seen:
                    continue
                payload = parse_exec_line(line) if is_exec else parse_event_line(line)
                if not payload:
                    continue
                if (
                    symbol
                    and str(payload.get("symbol") or "").upper() != symbol.upper()
                ):
                    continue
                point = (
                    exec_to_point(payload)
                    if is_exec
                    else event_to_point(payload, digest)
                )
                if not point:
                    continue
                if point.ts < start_ts or point.ts > end_ts:
                    continue
                writer.enqueue([point])
                ingested += 1
                seen.add(digest)
    except OSError:
        return ingested
    return ingested


def parse_range(start: str, end: str) -> tuple[datetime, datetime]:
    return _parse_dt(start), _parse_dt(end)


def _parse_dt(value: str) -> datetime:
    cleaned = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
