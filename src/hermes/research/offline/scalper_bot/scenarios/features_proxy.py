"""Proxy metrics for scenario detection (streaming friendly)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .io import Tick


@dataclass
class WindowMetrics:
    start_ts_ms: int
    end_ts_ms: int
    duration_s: float
    tick_count: int
    return_bps: Optional[float]
    trend_slope_bps_per_min: Optional[float]
    trend_r2: Optional[float]
    vol_bps: Optional[float]
    range_bps: Optional[float]
    spread_bps_mean: Optional[float]
    depth_usd_mean: Optional[float]
    imbalance: Optional[float]
    tick_rate: Optional[float]
    interarrival_cv: Optional[float]
    burstiness: Optional[float]
    alternation_rate: Optional[float]
    gap_bps_max: Optional[float]
    slope_first_bps_per_min: Optional[float]
    slope_last_bps_per_min: Optional[float]
    range_first_bps: Optional[float]
    range_last_bps: Optional[float]
    vol_first_bps: Optional[float]
    vol_last_bps: Optional[float]
    breakout: Optional[bool]
    fakeout: Optional[bool]
    depth_available: bool

    def to_dict(self) -> Dict[str, object]:
        return self.__dict__.copy()


def compute_metrics(ticks: List[Tick]) -> WindowMetrics:
    if not ticks:
        raise ValueError("No ticks provided for metrics.")

    start_ts_ms = ticks[0].ts_ms
    end_ts_ms = ticks[-1].ts_ms
    duration_s = max((end_ts_ms - start_ts_ms) / 1000.0, 1e-6)
    prices = [t.price for t in ticks]
    returns_bps = _returns_bps(prices)
    return_bps = _total_return_bps(prices)
    vol_bps = _std(returns_bps)
    range_bps = _range_bps(prices)
    slope, r2 = _trend_slope(prices, [t.ts for t in ticks])
    trend_slope_bps_per_min = slope * 60.0 * 10000.0 if slope is not None else None

    spreads = _spread_bps(ticks)
    spread_bps_mean = _mean(spreads) if spreads else None
    depth_vals = [t.depth_usd for t in ticks if t.depth_usd is not None]
    depth_usd_mean = _mean(depth_vals) if depth_vals else None
    depth_available = bool(depth_vals)

    imbalance = _imbalance(ticks)
    tick_rate = len(ticks) / duration_s if duration_s > 0 else None
    interarrival = _interarrival_ms(ticks)
    interarrival_cv = _cv(interarrival)
    burstiness = _burstiness(interarrival)
    alternation_rate = _alternation_rate(returns_bps)
    gap_bps_max = max([abs(r) for r in returns_bps], default=None)

    first, last = _split_ticks(ticks)
    slope_first, _ = _trend_slope([t.price for t in first], [t.ts for t in first])
    slope_last, _ = _trend_slope([t.price for t in last], [t.ts for t in last])
    range_first = _range_bps([t.price for t in first])
    range_last = _range_bps([t.price for t in last])
    vol_first = _std(_returns_bps([t.price for t in first]))
    vol_last = _std(_returns_bps([t.price for t in last]))

    breakout, fakeout = _breakout_flags(first, last)

    return WindowMetrics(
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        duration_s=duration_s,
        tick_count=len(ticks),
        return_bps=return_bps,
        trend_slope_bps_per_min=trend_slope_bps_per_min,
        trend_r2=r2,
        vol_bps=vol_bps,
        range_bps=range_bps,
        spread_bps_mean=spread_bps_mean,
        depth_usd_mean=depth_usd_mean,
        imbalance=imbalance,
        tick_rate=tick_rate,
        interarrival_cv=interarrival_cv,
        burstiness=burstiness,
        alternation_rate=alternation_rate,
        gap_bps_max=gap_bps_max,
        slope_first_bps_per_min=(
            slope_first * 60.0 * 10000.0 if slope_first is not None else None
        ),
        slope_last_bps_per_min=(
            slope_last * 60.0 * 10000.0 if slope_last is not None else None
        ),
        range_first_bps=range_first,
        range_last_bps=range_last,
        vol_first_bps=vol_first,
        vol_last_bps=vol_last,
        breakout=breakout,
        fakeout=fakeout,
        depth_available=depth_available,
    )


def _returns_bps(prices: List[float]) -> List[float]:
    returns = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev == 0:
            continue
        returns.append((prices[i] - prev) / prev * 10000.0)
    return returns


def _total_return_bps(prices: List[float]) -> Optional[float]:
    if len(prices) < 2 or prices[0] == 0:
        return None
    return (prices[-1] - prices[0]) / prices[0] * 10000.0


def _range_bps(prices: List[float]) -> Optional[float]:
    if not prices:
        return None
    low = min(prices)
    high = max(prices)
    mid = (high + low) / 2.0
    if mid == 0:
        return None
    return (high - low) / mid * 10000.0


def _trend_slope(
    prices: List[float], times_s: List[float]
) -> Tuple[Optional[float], Optional[float]]:
    if len(prices) < 3:
        return None, None
    xs = [t - times_s[0] for t in times_s]
    ys = [math.log(max(p, 1e-9)) for p in prices]
    x_mean = _mean(xs)
    y_mean = _mean(ys)
    if x_mean is None or y_mean is None:
        return None, None
    var_x = sum((x - x_mean) ** 2 for x in xs)
    if var_x == 0:
        return None, None
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    slope = cov / var_x
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else None
    return slope, r2


def _spread_bps(ticks: List[Tick]) -> List[float]:
    spreads = []
    for t in ticks:
        if t.bid is None or t.ask is None:
            continue
        mid = (t.bid + t.ask) / 2.0
        if mid <= 0:
            continue
        spreads.append((t.ask - t.bid) / mid * 10000.0)
    return spreads


def _imbalance(ticks: List[Tick]) -> Optional[float]:
    buy = 0.0
    sell = 0.0
    last_price = None
    for t in ticks:
        side = t.side
        if side is None:
            if last_price is None:
                last_price = t.price
            side = "buy" if t.price >= last_price else "sell"
        last_price = t.price
        if str(side).lower().startswith("buy"):
            buy += t.qty
        elif str(side).lower().startswith("sell"):
            sell += t.qty
    total = buy + sell
    if total <= 0:
        return None
    return (buy - sell) / total


def _interarrival_ms(ticks: List[Tick]) -> List[float]:
    vals = []
    for i in range(1, len(ticks)):
        vals.append(max(ticks[i].ts_ms - ticks[i - 1].ts_ms, 0))
    return vals


def _cv(values: List[float]) -> Optional[float]:
    if not values:
        return None
    mean = _mean(values)
    if mean is None or mean == 0:
        return None
    std = _std(values)
    if std is None:
        return None
    return std / mean


def _burstiness(values: List[float]) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    median = sorted_vals[len(sorted_vals) // 2]
    p90 = sorted_vals[int(len(sorted_vals) * 0.9) - 1]
    if median == 0:
        return None
    return p90 / median


def _alternation_rate(returns_bps: List[float]) -> Optional[float]:
    if len(returns_bps) < 3:
        return None
    signs = [0 if r == 0 else (1 if r > 0 else -1) for r in returns_bps]
    flips = 0
    total = 0
    last = signs[0]
    for s in signs[1:]:
        if s == 0:
            continue
        if last != 0 and s != last:
            flips += 1
        total += 1
        last = s
    return flips / total if total > 0 else None


def _split_ticks(ticks: List[Tick]) -> Tuple[List[Tick], List[Tick]]:
    if len(ticks) < 2:
        return ticks, ticks
    mid = len(ticks) // 2
    return ticks[:mid], ticks[mid:]


def _breakout_flags(
    first: List[Tick], last: List[Tick]
) -> Tuple[Optional[bool], Optional[bool]]:
    if not first or not last:
        return None, None
    first_prices = [t.price for t in first]
    last_prices = [t.price for t in last]
    low, high = min(first_prices), max(first_prices)
    final = last_prices[-1]
    breakout = final > high or final < low
    fakeout = False
    if breakout:
        midpoint = (high + low) / 2.0
        if final < midpoint and final < high:
            fakeout = True
        if final > midpoint and final > low and final > high:
            fakeout = False
    # Fakeout flag: breakout within last window but revert past midpoint.
    max_last = max(last_prices)
    min_last = min(last_prices)
    if max_last > high and final < (high + low) / 2.0:
        fakeout = True
    if min_last < low and final > (high + low) / 2.0:
        fakeout = True
    return breakout, fakeout


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _std(values: List[float]) -> Optional[float]:
    if not values or len(values) < 2:
        return None
    mean = _mean(values)
    if mean is None:
        return None
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(max(var, 0.0))
