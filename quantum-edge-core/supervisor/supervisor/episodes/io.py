"""Tick IO utilities for episode tooling."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional


@dataclass
class Tick:
    ts: float
    price: float
    qty: Optional[float]
    side: Optional[str]
    bid: Optional[float]
    ask: Optional[float]
    symbol: Optional[str]
    raw: Dict[str, object]


def iter_tick_files(path: Path, fmt: Optional[str] = None) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"Ticks path not found: {path}")
    files: list[Path] = []
    if fmt == "csv":
        files = sorted(path.glob("*.csv"))
    elif fmt == "jsonl":
        files = sorted(path.glob("*.jsonl"))
    else:
        files = sorted(path.glob("*.jsonl")) + sorted(path.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No tick files found under {path}")
    return files


def guess_format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".csv":
        return "csv"
    return "jsonl"


def iter_ticks(
    path: Path,
    fmt: str,
    symbol_hint: Optional[str] = None,
    limit_rows: Optional[int] = None,
) -> Iterator[Tick]:
    fmt = fmt.lower()
    if fmt not in {"csv", "jsonl"}:
        raise ValueError(f"Unsupported tick format: {fmt}")
    if fmt == "jsonl":
        yield from _iter_jsonl(path, symbol_hint, limit_rows)
    else:
        yield from _iter_csv(path, symbol_hint, limit_rows)


def _iter_jsonl(path: Path, symbol_hint: Optional[str], limit_rows: Optional[int]) -> Iterator[Tick]:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit_rows is not None and count >= limit_rows:
                return
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            tick = _normalize_tick(raw, symbol_hint)
            if tick is None:
                continue
            yield tick
            count += 1


def _iter_csv(path: Path, symbol_hint: Optional[str], limit_rows: Optional[int]) -> Iterator[Tick]:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            if limit_rows is not None and count >= limit_rows:
                return
            tick = _normalize_tick(raw, symbol_hint)
            if tick is None:
                continue
            yield tick
            count += 1


def _normalize_tick(raw: Dict[str, object], symbol_hint: Optional[str]) -> Optional[Tick]:
    ts_value = _pick(raw, ["ts", "timestamp", "time", "datetime"])
    price_value = _pick(raw, ["price", "last", "last_price", "close"])
    if ts_value is None or price_value is None:
        return None
    ts = _parse_ts(ts_value)
    if ts is None:
        return None
    price = _coerce_float(price_value)
    if price is None:
        return None
    qty = _coerce_float(_pick(raw, ["qty", "size", "volume", "quantity"]))
    side = _coerce_side(_pick(raw, ["side", "isBuyerMaker", "is_buyer_maker"]))
    bid = _coerce_float(_pick(raw, ["bid", "best_bid", "bid_price"]))
    ask = _coerce_float(_pick(raw, ["ask", "best_ask", "ask_price"]))
    symbol = _pick(raw, ["symbol", "pair"]) or symbol_hint
    return Tick(
        ts=ts,
        price=price,
        qty=qty,
        side=side,
        bid=bid,
        ask=ask,
        symbol=symbol,
        raw=dict(raw),
    )


def _pick(raw: Dict[str, object], keys: list[str]) -> Optional[object]:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _parse_ts(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        return _normalize_ts(ts)
    text = str(value).strip()
    if not text:
        return None
    try:
        return _normalize_ts(float(text))
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def _normalize_ts(ts: float) -> float:
    if ts > 1e12:
        return ts / 1000.0
    return ts


def _coerce_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_side(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"buy", "sell"}:
        return text
    if text in {"true", "false"}:
        return "sell" if text == "true" else "buy"
    return None
