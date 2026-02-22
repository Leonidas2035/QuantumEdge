"""Synthetic replay scenario generators for quantum_edge_core.strategies.legacy.lockbot."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

SCENARIO_NAMES = {
    "S_RANGE_OSCILLATION",
    "S_TREND_UP_PULLBACKS",
    "S_TREND_DOWN_PULLBACKS",
    "S_TREND_FLIP_FALSE_BREAK",
    "S_VOLATILITY_EXPANSION_ATR_SPIKE",
    "S_FUNDING_BLEED_LONG_DURATION",
}


@dataclass
class ScenarioConfig:
    name: str
    duration_s: int = 7200
    start_ts_ms: int = 1_730_000_000_000
    symbol: str = "BTCUSDT"


class EventFactory:
    def __init__(self, symbol: str) -> None:
        self._symbol = symbol
        self._seq = defaultdict(int)

    def make(
        self,
        topic: str,
        ts_event: int,
        payload: Dict[str, object],
        *,
        schema: str = "lockbot_md.v1",
        source: str = "replay_synth",
    ) -> Dict[str, object]:
        self._seq[topic] += 1
        return {
            "schema": schema,
            "topic": topic,
            "symbol": self._symbol,
            "ts_event": int(ts_event),
            "ts_pub": int(ts_event),
            "source": source,
            "seq": int(self._seq[topic]),
            "payload": payload,
        }


def generate_scenario(cfg: ScenarioConfig) -> List[Dict[str, object]]:
    name = cfg.name.upper()
    if name not in SCENARIO_NAMES:
        raise ValueError(f"Unknown scenario {cfg.name}")

    price_fn, funding_fn, liq_fn, risk_fn = _scenario_functions(name)
    factory = EventFactory(cfg.symbol)

    events: List[Dict[str, object]] = []
    sum_price = 0.0
    sum_sq = 0.0
    count = 0

    interval_5m = 300
    interval_15m = 900
    bar5 = _BarAccumulator(interval_5m)
    bar15 = _BarAccumulator(interval_15m)

    for t in range(cfg.duration_s):
        ts_event = cfg.start_ts_ms + t * 1000
        mark = price_fn(t)
        sum_price += mark
        sum_sq += mark * mark
        count += 1
        vwap = sum_price / max(count, 1)
        variance = max(sum_sq / max(count, 1) - vwap * vwap, 0.0)
        std = math.sqrt(variance)

        events.append(
            factory.make(
                f"{cfg.symbol}:mark_price_1s",
                ts_event,
                {"mark_price": mark},
            )
        )
        events.append(
            factory.make(
                f"{cfg.symbol}:vwap_bands_d",
                ts_event,
                {
                    "vwap": vwap,
                    "band_1u": vwap + std,
                    "band_1l": vwap - std,
                    "band_2u": vwap + 2.0 * std,
                    "band_2l": vwap - 2.0 * std,
                },
            )
        )

        if t % 5 == 0:
            events.append(
                factory.make(
                    f"{cfg.symbol}:avwap",
                    ts_event,
                    {
                        "anchors": [
                            {
                                "anchor_id": "lock_entry",
                                "anchor_ts": cfg.start_ts_ms,
                                "vwap": vwap,
                                "pv_sum": vwap * count,
                                "v_sum": float(count),
                                "n_trades": count,
                            }
                        ]
                    },
                )
            )

        if t % 60 == 0:
            events.append(
                factory.make(
                    f"{cfg.symbol}:funding_rate",
                    ts_event,
                    {"funding_rate": funding_fn(t), "funding_time": ts_event},
                )
            )

        if t % 5 == 0:
            above, below = liq_fn(t, mark)
            events.append(
                factory.make(
                    f"{cfg.symbol}:liq_heatmap",
                    ts_event,
                    {
                        "window_s": 3600,
                        "bin_type": "bps",
                        "bin_size": 10.0,
                        "decay": {"type": "exp", "half_life_s": 900},
                        "levels": [
                            {
                                "price": mark * 1.002,
                                "intensity": above,
                                "side": "SELL",
                                "n": 1,
                            },
                            {
                                "price": mark * 0.998,
                                "intensity": below,
                                "side": "BUY",
                                "n": 1,
                            },
                        ],
                        "intensity_above": above,
                        "intensity_below": below,
                        "last_force_order_ts": ts_event,
                    },
                )
            )

        bar5.update(t, mark)
        if bar5.should_emit(t):
            events.append(
                factory.make(f"{cfg.symbol}:ohlcv_5m", ts_event, bar5.emit(ts_event))
            )
        bar15.update(t, mark)
        if bar15.should_emit(t):
            events.append(
                factory.make(f"{cfg.symbol}:ohlcv_15m", ts_event, bar15.emit(ts_event))
            )

        if t % 1 == 0:
            positions, risk = risk_fn(t, mark)
            events.append(
                factory.make(
                    f"{cfg.symbol}:position_snapshot",
                    ts_event,
                    {"positions": positions, "risk": risk, "ts_event": ts_event},
                    schema="lockbot_account.v1",
                )
            )

    return events


def _scenario_functions(
    name: str,
) -> Tuple[
    Callable[[int], float],
    Callable[[int], float],
    Callable[[int, float], Tuple[float, float]],
    Callable[[int, float], Tuple[Dict[str, float], Dict[str, float]]],
]:
    base = 40_000.0
    if name == "S_RANGE_OSCILLATION":
        amp = 220.0
        period = 600.0

        def price_fn(t: int) -> float:
            return base + amp * math.sin(2.0 * math.pi * t / period)

        def funding_fn(t: int) -> float:
            return 0.00005

        def liq_fn(t: int, mark: float) -> Tuple[float, float]:
            return (1.0 + 0.5 * math.sin(t / 120.0), 1.0 + 0.5 * math.cos(t / 120.0))

    elif name == "S_TREND_UP_PULLBACKS":
        slope = 0.5
        amp = 120.0
        period = 900.0

        def price_fn(t: int) -> float:
            return base + slope * t + amp * math.sin(2.0 * math.pi * t / period)

        def funding_fn(t: int) -> float:
            return 0.0001

        def liq_fn(t: int, mark: float) -> Tuple[float, float]:
            return (2.0 + 0.3 * math.sin(t / 180.0), 1.0)

    elif name == "S_TREND_DOWN_PULLBACKS":
        slope = -0.5
        amp = 120.0
        period = 900.0

        def price_fn(t: int) -> float:
            return (
                base + 2000.0 + slope * t + amp * math.sin(2.0 * math.pi * t / period)
            )

        def funding_fn(t: int) -> float:
            return -0.0001

        def liq_fn(t: int, mark: float) -> Tuple[float, float]:
            return (1.0, 2.0 + 0.3 * math.cos(t / 180.0))

    elif name == "S_TREND_FLIP_FALSE_BREAK":
        slope = 0.6
        amp = 160.0
        period = 800.0

        def price_fn(t: int) -> float:
            if t < 1800:
                return base + slope * t + amp * math.sin(2.0 * math.pi * t / period)
            return (
                base
                + 1000.0
                - slope * (t - 1800)
                + amp * math.sin(2.0 * math.pi * t / period)
            )

        def funding_fn(t: int) -> float:
            return 0.00002 if t < 1800 else -0.00002

        def liq_fn(t: int, mark: float) -> Tuple[float, float]:
            return (3.0 if t % 600 < 60 else 1.5, 3.0 if t % 600 > 540 else 1.5)

    elif name == "S_VOLATILITY_EXPANSION_ATR_SPIKE":
        period = 500.0

        def price_fn(t: int) -> float:
            amp = 80.0 + 0.08 * t
            return base + amp * math.sin(2.0 * math.pi * t / period)

        def funding_fn(t: int) -> float:
            return 0.00008

        def liq_fn(t: int, mark: float) -> Tuple[float, float]:
            spike = 8.0 if 1800 <= t <= 1860 else 2.0
            return (spike, spike * 0.8)

    else:  # S_FUNDING_BLEED_LONG_DURATION
        amp = 60.0
        period = 1200.0

        def price_fn(t: int) -> float:
            return base + 400.0 + amp * math.sin(2.0 * math.pi * t / period)

        def funding_fn(t: int) -> float:
            return 0.00025

        def liq_fn(t: int, mark: float) -> Tuple[float, float]:
            return (1.2, 1.2)

    def risk_fn(t: int, mark: float) -> Tuple[Dict[str, float], Dict[str, float]]:
        distance = 600.0
        if name == "S_VOLATILITY_EXPANSION_ATR_SPIKE" and 1800 <= t <= 1860:
            distance = 120.0
        positions = {
            "long_qty": 0.2,
            "short_qty": 0.2,
            "long_avg_px": mark * 0.995,
            "short_avg_px": mark * 1.005,
            "liq_price_long": mark * 0.8,
            "liq_price_short": mark * 1.2,
        }
        risk = {
            "margin_usage": 0.32,
            "distance_to_liq_bps": distance,
            "equity": 10_000.0,
            "leverage": 2.0,
        }
        return positions, risk

    return price_fn, funding_fn, liq_fn, risk_fn


class _BarAccumulator:
    def __init__(self, interval_s: int) -> None:
        self.interval_s = interval_s
        self._reset()

    def _reset(self) -> None:
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = 0.0

    def update(self, t: int, price: float, volume: float = 1.0) -> None:
        if self.open is None:
            self.open = price
            self.high = price
            self.low = price
        self.close = price
        self.high = max(self.high, price) if self.high is not None else price
        self.low = min(self.low, price) if self.low is not None else price
        self.volume += volume

    def should_emit(self, t: int) -> bool:
        return (t + 1) % self.interval_s == 0

    def emit(self, ts_event: int) -> Dict[str, object]:
        if (
            self.open is None
            or self.high is None
            or self.low is None
            or self.close is None
        ):
            payload = {
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
                "volume": 0.0,
                "bar_start_ts": ts_event,
            }
            self._reset()
            return payload
        bar_start = ts_event - (self.interval_s - 1) * 1000
        payload = {
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": float(self.volume),
            "bar_start_ts": int(bar_start),
        }
        self._reset()
        return payload
