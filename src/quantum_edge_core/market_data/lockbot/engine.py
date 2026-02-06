"""Derived market-data engines for LockBot (VWAP, AVWAP, heatmap, OHLCV)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from quantum_edge_core.market_data.lockbot.publisher import LockbotPublisher
from quantum_edge_core.market_data.models.lockbot_md_contract import (
    TOPIC_AVWAP,
    TOPIC_FORCE_ORDER,
    TOPIC_FUNDING_RATE,
    TOPIC_LIQ_HEATMAP,
    TOPIC_MARK_PRICE_1S,
    TOPIC_OHLCV_15M,
    TOPIC_OHLCV_1M,
    TOPIC_OHLCV_5M,
    TOPIC_TRADES_AGG,
    TOPIC_VWAP_BANDS_D,
    TOPIC_VWAP_D,
)


@dataclass
class VwapState:
    session_start_ms: int
    session_end_ms: int
    pv_sum: float = 0.0
    v_sum: float = 0.0
    p2v_sum: float = 0.0
    n_trades: int = 0
    last_emit_ms: int = 0


@dataclass
class OhlcvState:
    interval_ms: int
    interval_label: str
    bar_start_ms: Optional[int] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: float = 0.0
    trades: int = 0


@dataclass
class AnchorState:
    anchor_id: str
    anchor_ts_ms: Optional[int] = None
    pv_sum: float = 0.0
    v_sum: float = 0.0
    p2v_sum: float = 0.0
    n_trades: int = 0


@dataclass
class HeatmapBin:
    intensity: float
    count: int


@dataclass
class HeatmapState:
    window_s: int
    bin_type: str
    bin_size: float
    half_life_s: int
    top_n: int
    bins: Dict[str, HeatmapBin] = field(default_factory=dict)
    last_decay_ms: Optional[int] = None
    last_force_order_ts: Optional[int] = None
    last_price: Optional[float] = None
    last_emit_ms: int = 0


class LockbotDerivedEngine:
    """Maintains derived signals for LockBot market data."""

    def __init__(
        self,
        publisher: LockbotPublisher,
        *,
        vwap_publish_interval_ms: int = 1000,
        avwap_publish_interval_ms: int = 1000,
        heatmap_publish_interval_ms: int = 2000,
        heatmap_window_s: int = 3600,
        heatmap_bin_type: str = "fixed_price",
        heatmap_bin_size: float = 50.0,
        heatmap_half_life_s: int = 900,
        heatmap_top_n: int = 20,
    ) -> None:
        self._publisher = publisher
        self._vwap_interval_ms = int(vwap_publish_interval_ms)
        self._avwap_interval_ms = int(avwap_publish_interval_ms)
        self._heatmap_interval_ms = int(heatmap_publish_interval_ms)
        self._vwap: Dict[str, VwapState] = {}
        self._avwap_emit: Dict[str, int] = {}
        self._ohlcv: Dict[str, Dict[str, OhlcvState]] = {}
        self._anchors: Dict[str, Dict[str, AnchorState]] = {}
        self._heatmap: Dict[str, HeatmapState] = {}
        self._heatmap_cfg = {
            "window_s": int(heatmap_window_s),
            "bin_type": str(heatmap_bin_type),
            "bin_size": float(heatmap_bin_size),
            "half_life_s": int(heatmap_half_life_s),
            "top_n": int(heatmap_top_n),
        }
        self._anchor_ids = ("lock_entry", "trend_start", "liq_sweep")

    def on_trade(
        self,
        *,
        symbol: str,
        price: float,
        qty: float,
        ts_event_ms: int,
        is_buyer_maker: Optional[bool] = None,
        agg_trade_id: Optional[int] = None,
        source: str = "binance_ws",
    ) -> None:
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_TRADES_AGG,
            payload={
                "price": float(price),
                "qty": float(qty),
                "is_buyer_maker": bool(is_buyer_maker) if is_buyer_maker is not None else False,
                "agg_trade_id": agg_trade_id,
            },
            ts_event_ms=ts_event_ms,
            source=source,
        )
        self._update_vwap(symbol, price, qty, ts_event_ms)
        self._update_ohlcv(symbol, price, qty, ts_event_ms)
        self._update_avwap(symbol, price, qty, ts_event_ms)

    def on_mark_price(
        self,
        *,
        symbol: str,
        mark_price: float,
        ts_event_ms: int,
        source: str = "binance_ws",
        index_price: Optional[float] = None,
        funding_rate: Optional[float] = None,
        next_funding_time: Optional[int] = None,
    ) -> None:
        payload = {
            "mark_price": float(mark_price),
            "index_price": float(index_price) if index_price is not None else None,
            "funding_rate": float(funding_rate) if funding_rate is not None else None,
            "next_funding_time": int(next_funding_time) if next_funding_time is not None else None,
        }
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_MARK_PRICE_1S,
            payload=payload,
            ts_event_ms=ts_event_ms,
            source=source,
        )

    def on_funding_rate(
        self,
        *,
        symbol: str,
        funding_rate: float,
        funding_time_ms: int,
        ts_event_ms: int,
        source: str = "binance_rest",
    ) -> None:
        payload = {
            "funding_rate": float(funding_rate),
            "funding_time": int(funding_time_ms),
        }
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_FUNDING_RATE,
            payload=payload,
            ts_event_ms=ts_event_ms,
            source=source,
        )

    def on_force_order(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        ts_liq_ms: int,
        source: str = "binance_ws",
        order_status: Optional[str] = None,
    ) -> None:
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_FORCE_ORDER,
            payload={
                "side": str(side).upper(),
                "price": float(price),
                "qty": float(qty),
                "order_status": order_status,
                "ts_liq": int(ts_liq_ms),
            },
            ts_event_ms=ts_liq_ms,
            source=source,
        )
        self._update_heatmap(symbol, side, price, qty, ts_liq_ms)

    def set_anchor(self, symbol: str, anchor_id: str, anchor_ts_ms: int) -> None:
        anchor_id = str(anchor_id)
        anchors = self._anchors.setdefault(symbol, {})
        state = anchors.get(anchor_id) or AnchorState(anchor_id=anchor_id)
        state.anchor_ts_ms = int(anchor_ts_ms)
        state.pv_sum = 0.0
        state.v_sum = 0.0
        state.p2v_sum = 0.0
        state.n_trades = 0
        anchors[anchor_id] = state

    def _update_vwap(self, symbol: str, price: float, qty: float, ts_event_ms: int) -> None:
        state = self._vwap.get(symbol)
        session_start_ms, session_end_ms = _session_bounds(ts_event_ms)
        session_reset = False
        if state is None or state.session_start_ms != session_start_ms:
            state = VwapState(session_start_ms=session_start_ms, session_end_ms=session_end_ms)
            self._vwap[symbol] = state
            session_reset = True
            self.set_anchor(symbol, "lock_entry", session_start_ms)
        state.pv_sum += price * qty
        state.v_sum += qty
        state.p2v_sum += (price * price) * qty
        state.n_trades += 1
        if not _should_emit(ts_event_ms, state.last_emit_ms, self._vwap_interval_ms):
            return
        state.last_emit_ms = ts_event_ms
        vwap = state.pv_sum / state.v_sum if state.v_sum > 0 else 0.0
        payload = {
            "session": {
                "type": "UTC_DAY",
                "start_ts": state.session_start_ms,
                "end_ts": state.session_end_ms,
            },
            "vwap": vwap,
            "pv_sum": state.pv_sum,
            "v_sum": state.v_sum,
            "n_trades": state.n_trades,
            "session_reset": session_reset,
        }
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_VWAP_D,
            payload=payload,
            ts_event_ms=ts_event_ms,
            source="hub_derived",
        )
        std, bands = _vwap_bands(state, vwap)
        bands_payload = {
            **payload,
            "std": std,
            "band_1u": bands[0],
            "band_1l": bands[1],
            "band_2u": bands[2],
            "band_2l": bands[3],
            "method": "weighted_variance",
        }
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_VWAP_BANDS_D,
            payload=bands_payload,
            ts_event_ms=ts_event_ms,
            source="hub_derived",
        )

    def _update_ohlcv(self, symbol: str, price: float, qty: float, ts_event_ms: int) -> None:
        intervals = {
            TOPIC_OHLCV_1M: (60_000, "1m"),
            TOPIC_OHLCV_5M: (300_000, "5m"),
            TOPIC_OHLCV_15M: (900_000, "15m"),
        }
        bucket_map = self._ohlcv.setdefault(symbol, {})
        for topic, (interval_ms, label) in intervals.items():
            state = bucket_map.get(topic)
            if state is None:
                state = OhlcvState(interval_ms=interval_ms, interval_label=label)
                bucket_map[topic] = state
            bucket_start = ts_event_ms - (ts_event_ms % interval_ms)
            if state.bar_start_ms is None:
                _init_bar(state, bucket_start, price, qty)
                continue
            if bucket_start != state.bar_start_ms:
                self._publish_bar(symbol, topic, state)
                _init_bar(state, bucket_start, price, qty)
                continue
            _update_bar(state, price, qty)

    def _publish_bar(self, symbol: str, topic: str, state: OhlcvState) -> None:
        if state.bar_start_ms is None or state.open is None:
            return
        payload = {
            "open": state.open,
            "high": state.high,
            "low": state.low,
            "close": state.close,
            "volume": state.volume,
            "interval": state.interval_label,
            "bar_start_ts": state.bar_start_ms,
        }
        ts_event_ms = state.bar_start_ms + state.interval_ms
        self._publisher.publish(
            symbol=symbol,
            event_type=topic,
            payload=payload,
            ts_event_ms=ts_event_ms,
            source="hub_derived",
        )

    def _update_avwap(self, symbol: str, price: float, qty: float, ts_event_ms: int) -> None:
        anchors = self._anchors.setdefault(symbol, {aid: AnchorState(anchor_id=aid) for aid in self._anchor_ids})
        active = []
        for anchor in anchors.values():
            if anchor.anchor_ts_ms is None or ts_event_ms < anchor.anchor_ts_ms:
                continue
            anchor.pv_sum += price * qty
            anchor.v_sum += qty
            anchor.p2v_sum += (price * price) * qty
            anchor.n_trades += 1
            vwap = anchor.pv_sum / anchor.v_sum if anchor.v_sum > 0 else 0.0
            active.append(
                {
                    "anchor_id": anchor.anchor_id,
                    "anchor_ts": anchor.anchor_ts_ms,
                    "vwap": vwap,
                    "pv_sum": anchor.pv_sum,
                    "v_sum": anchor.v_sum,
                    "n_trades": anchor.n_trades,
                }
            )
        if not _should_emit(ts_event_ms, self._avwap_last_emit(symbol), self._avwap_interval_ms):
            return
        self._avwap_last_emit(symbol, ts_event_ms)
        payload = {"anchors": active}
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_AVWAP,
            payload=payload,
            ts_event_ms=ts_event_ms,
            source="hub_derived",
        )

    def _avwap_last_emit(self, symbol: str, set_ms: Optional[int] = None) -> int:
        if set_ms is not None:
            self._avwap_emit[symbol] = int(set_ms)
        return self._avwap_emit.get(symbol, 0)

    def _update_heatmap(self, symbol: str, side: str, price: float, qty: float, ts_event_ms: int) -> None:
        state = self._heatmap.get(symbol)
        if state is None:
            state = HeatmapState(**self._heatmap_cfg)
            self._heatmap[symbol] = state
        _apply_decay(state, ts_event_ms)
        bin_key, bin_price = _bin_key(state, price, side)
        existing = state.bins.get(bin_key)
        if existing:
            existing.intensity += qty
            existing.count += 1
        else:
            state.bins[bin_key] = HeatmapBin(intensity=float(qty), count=1)
        state.last_force_order_ts = ts_event_ms
        state.last_price = price
        if not _should_emit(ts_event_ms, state.last_emit_ms, self._heatmap_interval_ms):
            return
        state.last_emit_ms = ts_event_ms
        payload = _heatmap_payload(state)
        self._publisher.publish(
            symbol=symbol,
            event_type=TOPIC_LIQ_HEATMAP,
            payload=payload,
            ts_event_ms=ts_event_ms,
            source="hub_derived",
        )


def _session_bounds(ts_ms: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = start_ms + 24 * 60 * 60 * 1000
    return start_ms, end_ms


def _should_emit(ts_ms: int, last_emit_ms: int, interval_ms: int) -> bool:
    if interval_ms <= 0:
        return True
    if last_emit_ms == 0:
        return True
    return ts_ms - last_emit_ms >= interval_ms


def _vwap_bands(state: VwapState, vwap: float) -> tuple[float, List[float]]:
    if state.v_sum <= 0:
        return 0.0, [vwap, vwap, vwap, vwap]
    mean_sq = state.p2v_sum / state.v_sum
    variance = max(mean_sq - vwap * vwap, 0.0)
    std = math.sqrt(variance)
    return std, [vwap + std, vwap - std, vwap + 2 * std, vwap - 2 * std]


def _init_bar(state: OhlcvState, bucket_start: int, price: float, qty: float) -> None:
    state.bar_start_ms = bucket_start
    state.open = price
    state.high = price
    state.low = price
    state.close = price
    state.volume = qty
    state.trades = 1


def _update_bar(state: OhlcvState, price: float, qty: float) -> None:
    state.high = price if state.high is None else max(state.high, price)
    state.low = price if state.low is None else min(state.low, price)
    state.close = price
    state.volume += qty
    state.trades += 1


def _apply_decay(state: HeatmapState, ts_ms: int) -> None:
    if state.last_decay_ms is None:
        state.last_decay_ms = ts_ms
        return
    delta_s = max(0.0, (ts_ms - state.last_decay_ms) / 1000.0)
    if delta_s <= 0.0:
        return
    half_life = max(state.half_life_s, 1)
    factor = 0.5 ** (delta_s / half_life)
    if factor >= 0.999:
        return
    for key in list(state.bins.keys()):
        entry = state.bins[key]
        entry.intensity *= factor
        if entry.intensity < 1e-6:
            state.bins.pop(key, None)
    state.last_decay_ms = ts_ms


def _bin_key(state: HeatmapState, price: float, side: str) -> tuple[str, float]:
    if state.bin_type == "bps":
        bin_size = max(price * state.bin_size / 10000.0, 0.01)
        bin_price = math.floor(price / bin_size) * bin_size
    else:
        bin_size = max(state.bin_size, 0.01)
        bin_price = math.floor(price / bin_size) * bin_size
    key = f"{str(side).upper()}:{bin_price:.6f}"
    return key, bin_price


def _heatmap_payload(state: HeatmapState) -> Dict[str, object]:
    levels = []
    for key, entry in state.bins.items():
        side, price_str = key.split(":", 1)
        try:
            price = float(price_str)
        except ValueError:
            continue
        levels.append(
            {
                "price": price,
                "intensity": entry.intensity,
                "side": side,
                "n": entry.count,
            }
        )
    levels.sort(key=lambda item: item["intensity"], reverse=True)
    levels = levels[: state.top_n]
    intensity_above = None
    intensity_below = None
    if state.last_price is not None:
        above = sum(item["intensity"] for item in levels if item["price"] > state.last_price)
        below = sum(item["intensity"] for item in levels if item["price"] < state.last_price)
        intensity_above = above
        intensity_below = below
    return {
        "window_s": state.window_s,
        "bin_type": state.bin_type,
        "bin_size": state.bin_size,
        "decay": {"type": "exp", "half_life_s": state.half_life_s},
        "levels": levels,
        "intensity_above": intensity_above,
        "intensity_below": intensity_below,
        "last_force_order_ts": state.last_force_order_ts or 0,
    }
