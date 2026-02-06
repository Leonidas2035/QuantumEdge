"""Episode cutter for tick-based scenario slicing."""

from __future__ import annotations

import json
import math
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import yaml

from .io import Tick, iter_tick_files, iter_ticks, guess_format


@dataclass
class ScenarioSpec:
    scenario_id: str
    name: str
    description: str
    tags: list[str]
    detect: Dict[str, object]
    pre_roll_s: int
    event_window_s: int
    post_roll_s: int


@dataclass
class EventCandidate:
    scenario_id: str
    event_ts: float
    source_file: Path
    stats: Dict[str, Optional[float]]


@dataclass
class EpisodeSlice:
    scenario_id: str
    episode_id: str
    event_ts: float
    t0: float
    t1: float
    source_file: Path
    stats: Dict[str, Optional[float]]


@dataclass
class EpisodeStats:
    tick_count: int = 0
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None
    return_bps: Optional[float] = None
    volatility_bps: Optional[float] = None
    _returns_sum: float = 0.0
    _returns_sumsq: float = 0.0
    _returns_count: int = 0
    _last_price: Optional[float] = None

    def update(self, tick: Tick) -> None:
        price = tick.price
        if self.first_ts is None:
            self.first_ts = tick.ts
            self.min_price = price
            self.max_price = price
        else:
            if self.min_price is None or price < self.min_price:
                self.min_price = price
            if self.max_price is None or price > self.max_price:
                self.max_price = price
        if self._last_price is not None and self._last_price != 0:
            ret_bps = (price - self._last_price) / self._last_price * 10000.0
            self._returns_sum += ret_bps
            self._returns_sumsq += ret_bps * ret_bps
            self._returns_count += 1
        self._last_price = price
        self.tick_count += 1
        self.last_ts = tick.ts

    def finalize(self) -> Dict[str, Optional[float]]:
        if self.first_ts is not None and self.last_ts is not None and self.min_price is not None:
            if self._last_price is not None and self.min_price is not None and self.min_price != 0:
                self.return_bps = (self._last_price - self.min_price) / self.min_price * 10000.0
        vol = None
        if self._returns_count > 1:
            mean = self._returns_sum / self._returns_count
            var = (self._returns_sumsq / self._returns_count) - mean * mean
            vol = math.sqrt(max(var, 0.0))
        self.volatility_bps = vol
        return {
            "tick_count": self.tick_count,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "return_bps": self.return_bps,
            "volatility_bps": self.volatility_bps,
        }


class RollingWindow:
    def __init__(self, window_s: int) -> None:
        self.window_s = max(window_s, 1)
        self._prices = deque()
        self._returns = deque()
        self._sum_returns = 0.0
        self._sum_returns_sq = 0.0
        self._last_price: Optional[float] = None

    def add(self, tick: Tick) -> None:
        ts = tick.ts
        price = tick.price
        if self._last_price is not None and self._last_price != 0:
            ret_bps = (price - self._last_price) / self._last_price * 10000.0
            self._returns.append((ts, ret_bps))
            self._sum_returns += ret_bps
            self._sum_returns_sq += ret_bps * ret_bps
        self._prices.append((ts, price))
        self._last_price = price
        self._prune(ts)

    def stats(self) -> Dict[str, Optional[float]]:
        if len(self._prices) >= 2:
            first_price = self._prices[0][1]
            last_price = self._prices[-1][1]
            return_bps = (last_price - first_price) / first_price * 10000.0 if first_price else None
        else:
            return_bps = None
        vol = None
        if len(self._returns) > 1:
            mean = self._sum_returns / len(self._returns)
            var = (self._sum_returns_sq / len(self._returns)) - mean * mean
            vol = math.sqrt(max(var, 0.0))
        trade_rate = len(self._prices) / float(self.window_s) if self.window_s else None
        return {
            "return_bps": return_bps,
            "volatility_bps": vol,
            "trade_rate": trade_rate,
            "window_s": float(self.window_s),
        }

    def _prune(self, now_ts: float) -> None:
        cutoff = now_ts - self.window_s
        while self._prices and self._prices[0][0] < cutoff:
            self._prices.popleft()
        while self._returns and self._returns[0][0] < cutoff:
            _, ret = self._returns.popleft()
            self._sum_returns -= ret
            self._sum_returns_sq -= ret * ret


def load_scenarios(path: Path) -> list[ScenarioSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("scenarios", [])
    scenarios: list[ScenarioSpec] = []
    for item in items:
        scenario_id = str(item.get("id"))
        scenarios.append(
            ScenarioSpec(
                scenario_id=scenario_id,
                name=str(item.get("name") or scenario_id),
                description=str(item.get("description") or ""),
                tags=list(item.get("tags") or []),
                detect=dict(item.get("detect") or {}),
                pre_roll_s=int(item.get("pre_roll_s", 10)),
                event_window_s=int(item.get("event_window_s", 30)),
                post_roll_s=int(item.get("post_roll_s", 10)),
            )
        )
    return scenarios


def cut_episodes(
    episode_set: str,
    ticks_path: Path,
    fmt: Optional[str] = None,
    symbols: Optional[list[str]] = None,
    max_episodes_per_scenario: int = 20,
    seed: int = 42,
    out_dir: Optional[Path] = None,
    scenarios_path: Optional[Path] = None,
) -> Path:
    scenario_path = scenarios_path or (Path(__file__).resolve().parents[2] / "episodes" / "scenarios_v1.yaml")
    scenarios = load_scenarios(scenario_path)
    episode_root = out_dir or (Path(__file__).resolve().parents[2] / "runtime" / "episodes" / episode_set)
    episode_root.mkdir(parents=True, exist_ok=True)

    files = iter_tick_files(ticks_path, fmt)
    candidates: list[EventCandidate] = []
    for tick_file in files:
        tick_fmt = fmt or guess_format(tick_file)
        candidates.extend(_detect_candidates(tick_file, tick_fmt, scenarios, symbols))

    selected = _select_candidates(candidates, max_episodes_per_scenario, seed)
    slices = _build_slices(selected, scenarios)
    manifest_path = _write_episodes(slices, episode_root, fmt, episode_set, scenarios, symbols, ticks_path)
    return manifest_path


def _detect_candidates(
    tick_file: Path,
    fmt: str,
    scenarios: list[ScenarioSpec],
    symbols: Optional[list[str]],
) -> list[EventCandidate]:
    window_sizes = sorted({int(s.detect.get("window_s", 5)) for s in scenarios})
    windows = {size: RollingWindow(size) for size in window_sizes}
    last_match: Dict[str, float] = {}
    candidates: list[EventCandidate] = []

    for tick in iter_ticks(tick_file, fmt):
        if symbols and tick.symbol and tick.symbol not in symbols:
            continue
        for window in windows.values():
            window.add(tick)
        spread_bps = _spread_bps(tick)
        for scenario in scenarios:
            detect = scenario.detect
            window_s = int(detect.get("window_s", 5))
            stats = windows[window_s].stats()
            if not _match_scenario(stats, spread_bps, detect):
                continue
            min_gap = int(detect.get("min_gap_s", 0))
            last_ts = last_match.get(scenario.scenario_id)
            if last_ts is not None and (tick.ts - last_ts) < min_gap:
                continue
            last_match[scenario.scenario_id] = tick.ts
            candidates.append(
                EventCandidate(
                    scenario_id=scenario.scenario_id,
                    event_ts=tick.ts,
                    source_file=tick_file,
                    stats={
                        "return_bps": stats.get("return_bps"),
                        "volatility_bps": stats.get("volatility_bps"),
                        "trade_rate": stats.get("trade_rate"),
                        "spread_bps": spread_bps,
                    },
                )
            )
    return candidates


def _match_scenario(stats: Dict[str, Optional[float]], spread_bps: Optional[float], detect: Dict[str, object]) -> bool:
    ret = stats.get("return_bps")
    vol = stats.get("volatility_bps")
    trade_rate = stats.get("trade_rate")
    direction = str(detect.get("direction", "any")).lower()

    if not _check_threshold(ret, detect, "return_bps_min", "return_bps_max", direction):
        return False
    if not _check_threshold(vol, detect, "volatility_bps_min", "volatility_bps_max", "any"):
        return False
    if not _check_threshold(trade_rate, detect, "trade_rate_min", "trade_rate_max", "any"):
        return False
    if not _check_threshold(spread_bps, detect, "spread_bps_min", "spread_bps_max", "any"):
        return False
    return True


def _check_threshold(
    value: Optional[float],
    detect: Dict[str, object],
    min_key: str,
    max_key: str,
    direction: str,
) -> bool:
    min_val = detect.get(min_key)
    max_val = detect.get(max_key)
    if min_val is None and max_val is None:
        return True
    if value is None:
        return False
    value_cmp = _apply_direction(value, direction)
    if min_val is not None and value_cmp < float(min_val):
        return False
    if max_val is not None and value_cmp > float(max_val):
        return False
    return True


def _apply_direction(value: float, direction: str) -> float:
    if direction == "up":
        return value
    if direction == "down":
        return -value
    return abs(value)


def _select_candidates(
    candidates: list[EventCandidate],
    max_per_scenario: int,
    seed: int,
) -> list[EventCandidate]:
    grouped: Dict[str, list[EventCandidate]] = {}
    for cand in candidates:
        grouped.setdefault(cand.scenario_id, []).append(cand)
    selected: list[EventCandidate] = []
    for scenario_id, items in grouped.items():
        items_sorted = sorted(items, key=lambda c: c.event_ts)
        if len(items_sorted) <= max_per_scenario:
            selected.extend(items_sorted)
            continue
        rng = random.Random(seed + sum(ord(ch) for ch in scenario_id))
        rng.shuffle(items_sorted)
        subset = sorted(items_sorted[:max_per_scenario], key=lambda c: c.event_ts)
        selected.extend(subset)
    return sorted(selected, key=lambda c: (c.source_file.as_posix(), c.event_ts))


def _build_slices(candidates: list[EventCandidate], scenarios: list[ScenarioSpec]) -> list[EpisodeSlice]:
    scenario_lookup = {s.scenario_id: s for s in scenarios}
    slices: list[EpisodeSlice] = []
    counters: Dict[str, int] = {}
    for cand in candidates:
        spec = scenario_lookup[cand.scenario_id]
        counters.setdefault(cand.scenario_id, 0)
        counters[cand.scenario_id] += 1
        episode_id = f"{cand.scenario_id}_{counters[cand.scenario_id]:03d}_{int(cand.event_ts)}"
        t0 = cand.event_ts - spec.pre_roll_s
        t1 = cand.event_ts + spec.event_window_s + spec.post_roll_s
        slices.append(
            EpisodeSlice(
                scenario_id=cand.scenario_id,
                episode_id=episode_id,
                event_ts=cand.event_ts,
                t0=t0,
                t1=t1,
                source_file=cand.source_file,
                stats=cand.stats,
            )
        )
    return slices


def _write_episodes(
    slices: list[EpisodeSlice],
    episode_root: Path,
    fmt: Optional[str],
    episode_set: str,
    scenarios: list[ScenarioSpec],
    symbols: Optional[list[str]],
    ticks_path: Path,
) -> Path:
    by_file: Dict[Path, list[EpisodeSlice]] = {}
    for episode in slices:
        by_file.setdefault(episode.source_file, []).append(episode)

    manifest = {
        "episode_set": episode_set,
        "episodes_root": str(episode_root),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "ticks_path": str(ticks_path),
            "symbols": symbols or [],
        },
        "episodes": [],
    }
    scenario_lookup = {s.scenario_id: s for s in scenarios}

    for source_file, episodes in by_file.items():
        episodes_sorted = sorted(episodes, key=lambda e: e.t0)
        tick_fmt = fmt or guess_format(source_file)
        _write_episodes_for_file(
            source_file,
            tick_fmt,
            episodes_sorted,
            episode_root,
            manifest,
            scenario_lookup,
        )

    manifest_path = episode_root / "episodes_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def _write_episodes_for_file(
    source_file: Path,
    fmt: str,
    episodes: list[EpisodeSlice],
    episode_root: Path,
    manifest: Dict[str, object],
    scenario_lookup: Dict[str, ScenarioSpec],
) -> None:
    active: list[tuple[EpisodeSlice, EpisodeStats, object]] = []
    ep_iter = iter(episodes)
    next_ep = next(ep_iter, None)

    for tick in iter_ticks(source_file, fmt):
        ts = tick.ts
        while next_ep and ts >= next_ep.t0:
            scenario_dir = episode_root / next_ep.scenario_id
            scenario_dir.mkdir(parents=True, exist_ok=True)
            out_path = scenario_dir / f"{next_ep.episode_id}.jsonl"
            handle = out_path.open("w", encoding="utf-8")
            active.append((next_ep, EpisodeStats(), handle))
            next_ep = next(ep_iter, None)

        still_active: list[tuple[EpisodeSlice, EpisodeStats, object]] = []
        for episode, stats, handle in active:
            if ts <= episode.t1:
                _write_tick(handle, tick)
                stats.update(tick)
                still_active.append((episode, stats, handle))
            else:
                handle.close()
                _finalize_episode(episode, stats, source_file, manifest, scenario_lookup)
        active = still_active

    for episode, stats, handle in active:
        handle.close()
        _finalize_episode(episode, stats, source_file, manifest, scenario_lookup)


def _finalize_episode(
    episode: EpisodeSlice,
    stats: EpisodeStats,
    source_file: Path,
    manifest: Dict[str, object],
    scenario_lookup: Dict[str, ScenarioSpec],
) -> None:
    spec = scenario_lookup.get(episode.scenario_id)
    manifest["episodes"].append(
        {
            "episode_set": manifest.get("episode_set"),
            "scenario_id": episode.scenario_id,
            "episode_id": episode.episode_id,
            "episode_path": str(Path(episode.scenario_id) / f"{episode.episode_id}.jsonl"),
            "event_ts": episode.event_ts,
            "t0": episode.t0,
            "t1": episode.t1,
            "source_file": str(source_file),
            "scenario_name": spec.name if spec else episode.scenario_id,
            "tags": spec.tags if spec else [],
            "stats": {
                **episode.stats,
                **stats.finalize(),
            },
        }
    )


def _write_tick(handle, tick: Tick) -> None:
    payload = {
        "ts": tick.ts,
        "price": tick.price,
        "qty": tick.qty,
        "side": tick.side,
        "bid": tick.bid,
        "ask": tick.ask,
        "symbol": tick.symbol,
    }
    handle.write(json.dumps(payload, ensure_ascii=True))
    handle.write("\n")


def _spread_bps(tick: Tick) -> Optional[float]:
    if tick.bid is None or tick.ask is None:
        return None
    mid = (tick.bid + tick.ask) / 2.0
    if mid == 0:
        return None
    return (tick.ask - tick.bid) / mid * 10000.0
