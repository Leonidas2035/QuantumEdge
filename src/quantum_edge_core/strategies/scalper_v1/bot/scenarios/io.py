"""Input adapters for tick + depth data."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional


@dataclass
class Tick:
    ts_ms: int
    price: float
    qty: float
    side: Optional[str]
    bid: Optional[float] = None
    ask: Optional[float] = None
    depth_usd: Optional[float] = None

    @property
    def ts(self) -> float:
        return self.ts_ms / 1000.0


@dataclass
class DepthSnapshot:
    ts_ms: int
    bid: Optional[float]
    ask: Optional[float]
    depth_usd: Optional[float]


def load_ticks(
    path: Path, symbol: Optional[str] = None, limit: Optional[int] = None
) -> List[Tick]:
    files = _resolve_files(path, symbol, extensions={".csv", ".jsonl"})
    ticks: List[Tick] = []
    for file_path in files:
        for tick in iter_ticks(file_path):
            ticks.append(tick)
            if limit and len(ticks) >= limit:
                return _sorted_ticks(ticks)
    return _sorted_ticks(ticks)


def iter_ticks(path: Path) -> Iterator[Tick]:
    if path.suffix.lower() == ".csv":
        yield from _iter_ticks_csv(path)
    else:
        yield from _iter_ticks_jsonl(path)


def load_depth_snapshots(
    path: Path, symbol: Optional[str] = None, limit: Optional[int] = None
) -> List[DepthSnapshot]:
    files = _resolve_files(path, symbol, extensions={".json", ".jsonl"})
    snapshots: List[DepthSnapshot] = []
    for file_path in files:
        if file_path.suffix.lower() == ".jsonl":
            for snap in _iter_depth_jsonl(file_path):
                snapshots.append(snap)
                if limit and len(snapshots) >= limit:
                    return sorted(snapshots, key=lambda s: s.ts_ms)
        else:
            snap = _parse_depth_payload(_read_json(file_path))
            if snap:
                snapshots.append(snap)
                if limit and len(snapshots) >= limit:
                    break
    return sorted(snapshots, key=lambda s: s.ts_ms)


def attach_depth(
    ticks: List[Tick],
    depth: List[DepthSnapshot],
    max_age_ms: int = 3000,
) -> None:
    if not depth:
        return
    depth = sorted(depth, key=lambda d: d.ts_ms)
    idx = 0
    current: Optional[DepthSnapshot] = None
    for tick in ticks:
        while idx < len(depth) and depth[idx].ts_ms <= tick.ts_ms:
            current = depth[idx]
            idx += 1
        if current and (tick.ts_ms - current.ts_ms) <= max_age_ms:
            tick.bid = current.bid
            tick.ask = current.ask
            tick.depth_usd = current.depth_usd


def _iter_ticks_csv(path: Path) -> Iterator[Tick]:
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue
            if row[0].lower() == "timestamp":
                continue
            try:
                ts_ms = int(float(row[0]))
                price = float(row[1])
                qty = float(row[2])
            except (ValueError, IndexError):
                continue
            side = row[3].strip().lower() if len(row) > 3 else None
            yield Tick(ts_ms=ts_ms, price=price, qty=qty, side=side or None)


def _iter_ticks_jsonl(path: Path) -> Iterator[Tick]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            tick = _parse_tick_payload(payload)
            if tick:
                yield tick


def _parse_tick_payload(payload: Dict[str, object]) -> Optional[Tick]:
    ts_raw = (
        payload.get("timestamp")
        or payload.get("ts")
        or payload.get("T")
        or payload.get("E")
    )
    price_raw = payload.get("price") or payload.get("p")
    qty_raw = payload.get("qty") or payload.get("q")
    if ts_raw is None or price_raw is None or qty_raw is None:
        return None
    try:
        ts_ms = int(float(ts_raw))
        price = float(price_raw)
        qty = float(qty_raw)
    except (TypeError, ValueError):
        return None
    side = payload.get("side")
    if side is None:
        is_buyer_maker = payload.get("m")
        if is_buyer_maker is not None:
            side = "sell" if bool(is_buyer_maker) else "buy"
    side_str = str(side).lower() if side is not None else None
    return Tick(ts_ms=ts_ms, price=price, qty=qty, side=side_str)


def _iter_depth_jsonl(path: Path) -> Iterator[DepthSnapshot]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            snap = _parse_depth_payload(payload)
            if snap:
                yield snap


def _parse_depth_payload(payload: Dict[str, object]) -> Optional[DepthSnapshot]:
    ts_raw = payload.get("ts") or payload.get("T") or payload.get("E")
    if ts_raw is None:
        return None
    try:
        ts_ms = int(float(ts_raw))
    except (TypeError, ValueError):
        return None
    bids = payload.get("bids") or payload.get("b")
    asks = payload.get("asks") or payload.get("a")
    bid = _best_price(bids, want_max=True)
    ask = _best_price(asks, want_max=False)
    depth_usd = _sum_depth(bids) + _sum_depth(asks)
    return DepthSnapshot(
        ts_ms=ts_ms, bid=bid, ask=ask, depth_usd=depth_usd if depth_usd > 0 else None
    )


def _best_price(levels: Optional[object], want_max: bool) -> Optional[float]:
    if not isinstance(levels, list) or not levels:
        return None
    prices = []
    for level in levels:
        try:
            prices.append(float(level[0]))
        except (TypeError, ValueError, IndexError):
            continue
    if not prices:
        return None
    return max(prices) if want_max else min(prices)


def _sum_depth(levels: Optional[object]) -> float:
    total = 0.0
    if not isinstance(levels, list):
        return total
    for level in levels:
        try:
            price = float(level[0])
            qty = float(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        total += price * qty
    return total


def _resolve_files(
    path: Path, symbol: Optional[str], extensions: set[str]
) -> List[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"Data path not found: {path}")
    files = [
        p for p in path.iterdir() if p.is_file() and p.suffix.lower() in extensions
    ]
    if symbol:
        sym = symbol.replace("-", "").lower()
        files = [p for p in files if sym in p.name.replace("-", "").lower()]
    return sorted(files)


def _read_json(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _sorted_ticks(ticks: List[Tick]) -> List[Tick]:
    return sorted(ticks, key=lambda t: t.ts_ms)
