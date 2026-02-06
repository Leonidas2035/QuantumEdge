"""File-based adapters for offline tick/bar data."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional


@dataclass
class MarketEvent:
    ts: int
    price: float
    bid: float
    ask: float
    qty: float
    side: str
    latency_ms: int = 0


def _coerce_float(value: Optional[str], default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Optional[str], default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _ensure_spread(price: float, bid: Optional[float], ask: Optional[float], spread_bps: float) -> tuple[float, float]:
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return bid, ask
    spread = price * (spread_bps / 10_000)
    half = spread / 2
    return price - half, price + half


def _event_from_row(row: dict, spread_bps: float) -> Optional[MarketEvent]:
    ts = _coerce_int(row.get("timestamp") or row.get("ts"), 0)
    price = _coerce_float(row.get("price") or row.get("close"), 0.0)
    if ts <= 0 or price <= 0:
        return None
    qty = _coerce_float(row.get("qty") or row.get("volume"), 0.0)
    side = str(row.get("side") or row.get("taker_side") or "buy").lower()
    bid = row.get("bid")
    ask = row.get("ask")
    bid_f = _coerce_float(bid, 0.0) if bid is not None else None
    ask_f = _coerce_float(ask, 0.0) if ask is not None else None
    bid_final, ask_final = _ensure_spread(price, bid_f, ask_f, spread_bps)
    return MarketEvent(
        ts=ts,
        price=price,
        bid=bid_final,
        ask=ask_final,
        qty=qty,
        side=side,
    )


def load_events_from_csv(path: Path, spread_bps: float = 2.0, limit_rows: Optional[int] = None) -> List[MarketEvent]:
    events: List[MarketEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader):
            if limit_rows is not None and idx >= limit_rows:
                break
            event = _event_from_row(row, spread_bps)
            if event:
                events.append(event)
    return sorted(events, key=lambda e: e.ts)


def load_events_from_jsonl(path: Path, spread_bps: float = 2.0, limit_rows: Optional[int] = None) -> List[MarketEvent]:
    events: List[MarketEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if limit_rows is not None and idx >= limit_rows:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            event = _event_from_row(payload, spread_bps)
            if event:
                events.append(event)
    return sorted(events, key=lambda e: e.ts)


def load_events(path: Path, spread_bps: float = 2.0, limit_rows: Optional[int] = None) -> List[MarketEvent]:
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        return load_events_from_jsonl(path, spread_bps=spread_bps, limit_rows=limit_rows)
    return load_events_from_csv(path, spread_bps=spread_bps, limit_rows=limit_rows)


def iter_events(events: Iterable[MarketEvent]) -> Iterator[MarketEvent]:
    for event in events:
        yield event
