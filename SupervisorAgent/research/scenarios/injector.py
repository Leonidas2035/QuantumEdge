"""Scenario injector for offline events."""

from __future__ import annotations

import copy
import random
from typing import Iterable, List, Tuple

from ..replay.adapters import MarketEvent

from .definitions import ScenarioSpec


def _window_indices(total: int, start_ratio: float, duration_ratio: float) -> Tuple[int, int]:
    if total <= 0:
        return 0, 0
    start = int(total * start_ratio)
    duration = max(1, int(total * duration_ratio))
    end = min(total, start + duration)
    return max(0, start), max(start, end)


def _apply_spread(event: MarketEvent, factor: float) -> MarketEvent:
    mid = (event.bid + event.ask) / 2 if event.bid and event.ask else event.price
    half = abs(event.ask - event.bid) / 2 if event.bid and event.ask else event.price * 0.0001
    half *= max(factor, 1.0)
    event.bid = mid - half
    event.ask = mid + half
    return event


def _apply_latency(event: MarketEvent, extra_ms: int) -> MarketEvent:
    event.latency_ms = int(event.latency_ms + max(extra_ms, 0))
    return event


def _apply_volatility(event: MarketEvent, factor: float, rng: random.Random) -> MarketEvent:
    if factor <= 0:
        return event
    shock = rng.uniform(-1.0, 1.0) * factor * event.price * 0.001
    event.price = max(event.price + shock, 0.01)
    spread = max(event.ask - event.bid, event.price * 0.0001)
    half = spread / 2
    event.bid = event.price - half
    event.ask = event.price + half
    return event


def inject_scenario(
    events: Iterable[MarketEvent],
    scenario: ScenarioSpec,
    seed: int = 42,
) -> List[MarketEvent]:
    events_list = [copy.deepcopy(evt) for evt in events]
    if not events_list:
        return events_list
    start, end = _window_indices(len(events_list), scenario.start_ratio, scenario.duration_ratio)
    rng = random.Random(seed)
    for idx in range(start, end):
        evt = events_list[idx]
        if scenario.spread_factor and scenario.spread_factor > 1.0:
            evt = _apply_spread(evt, scenario.spread_factor)
        if scenario.latency_ms and scenario.latency_ms > 0:
            evt = _apply_latency(evt, scenario.latency_ms)
        if scenario.volatility_factor and scenario.volatility_factor > 0:
            evt = _apply_volatility(evt, scenario.volatility_factor, rng)
        events_list[idx] = evt
    return events_list
