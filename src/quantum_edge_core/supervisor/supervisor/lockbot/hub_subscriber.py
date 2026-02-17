"""MarketDataHub subscriber for LockBot policy runner."""

from __future__ import annotations

import threading
from collections import deque
from typing import Iterable, Optional

import msgspec
import zmq

from market_data.lockbot.schema import LockbotMarketEvent
from supervisor.lockbot.models import LiqHeatmapSummary, MarketSnapshot, OhlcvBar


class MarketDataCache:
    def __init__(self, symbol: str, max_bars: int = 300) -> None:
        self._lock = threading.Lock()
        self.symbol = symbol
        self.mark_price: Optional[float] = None
        self.mark_ts: Optional[int] = None
        self.vwap: Optional[float] = None
        self.band_1u: Optional[float] = None
        self.band_1l: Optional[float] = None
        self.band_2u: Optional[float] = None
        self.band_2l: Optional[float] = None
        self.avwap: Optional[float] = None
        self.avwap_anchor: Optional[str] = None
        self.avwap_anchors: dict[str, float] = {}
        self.funding_rate: Optional[float] = None
        self.funding_ts: Optional[int] = None
        self.liq_heatmap: Optional[dict] = None
        self.liq_summary = LiqHeatmapSummary()
        self.ohlcv_5m = deque(maxlen=max_bars)
        self.ohlcv_15m = deque(maxlen=max_bars)

    def update_event(self, event: LockbotMarketEvent | dict) -> None:
        with self._lock:
            event_type = _event_attr(event, "event_type", "")
            payload = _event_attr(event, "payload", {}) or {}
            ts_event = int(_event_attr(event, "ts_event", 0))
            if event_type == "mark_price_1s":
                price = payload.get("mark_price")
                if price is not None:
                    self.mark_price = float(price)
                self.mark_ts = ts_event
            elif event_type == "vwap_bands_d":
                self.vwap = _to_float(payload.get("vwap"))
                self.band_1u = _to_float(payload.get("band_1u"))
                self.band_1l = _to_float(payload.get("band_1l"))
                self.band_2u = _to_float(payload.get("band_2u"))
                self.band_2l = _to_float(payload.get("band_2l"))
            elif event_type == "vwap_d":
                self.vwap = _to_float(payload.get("vwap"))
            elif event_type == "avwap":
                self.avwap, self.avwap_anchor, self.avwap_anchors = _select_avwap(
                    payload
                )
            elif event_type == "funding_rate":
                self.funding_rate = _to_float(payload.get("funding_rate"))
                self.funding_ts = int(payload.get("funding_time") or ts_event)
            elif event_type == "liq_heatmap":
                self.liq_heatmap = payload
                self.liq_summary = _summarize_heatmap(
                    payload, self.mark_price, ts_event
                )
            elif event_type in {"ohlcv_5m", "ohlcv_15m"}:
                bar = _ohlcv_bar(payload)
                if bar:
                    if event_type == "ohlcv_5m":
                        self.ohlcv_5m.append(bar)
                    else:
                        self.ohlcv_15m.append(bar)

    def snapshot(self) -> MarketSnapshot:
        with self._lock:
            return MarketSnapshot(
                symbol=self.symbol,
                mark_price=self.mark_price,
                mark_ts=self.mark_ts,
                vwap=self.vwap,
                band_1u=self.band_1u,
                band_1l=self.band_1l,
                band_2u=self.band_2u,
                band_2l=self.band_2l,
                avwap=self.avwap,
                avwap_anchor=self.avwap_anchor,
                avwap_anchors=dict(self.avwap_anchors),
                funding_rate=self.funding_rate,
                funding_ts=self.funding_ts,
                liq=self.liq_summary,
                ohlcv_5m=list(self.ohlcv_5m),
                ohlcv_15m=list(self.ohlcv_15m),
            )


class LockbotHubSubscriber:
    def __init__(
        self,
        endpoint: str,
        topics: Iterable[str],
        cache: MarketDataCache,
        rcv_hwm: int = 1000,
    ) -> None:
        self._endpoint = endpoint
        self._topics = list(topics)
        self._cache = cache
        self._ctx = zmq.Context.instance()
        self._socket: Optional[zmq.Socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._rcv_hwm = rcv_hwm

    def start(self) -> None:
        if self._thread:
            return
        self._socket = self._ctx.socket(zmq.SUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.RCVHWM, self._rcv_hwm)
        for topic in self._topics:
            self._socket.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
        self._socket.connect(self._endpoint)
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._socket:
            self._socket.close()
        self._thread = None
        self._socket = None

    def _reader_loop(self) -> None:
        if not self._socket:
            return
        poller = zmq.Poller()
        poller.register(self._socket, zmq.POLLIN)
        while not self._stop.is_set():
            try:
                socks = dict(poller.poll(200))
            except Exception:
                continue
            if self._socket in socks:
                try:
                    _topic, payload = self._socket.recv_multipart()
                except Exception:
                    continue
                try:
                    event = msgspec.msgpack.decode(payload, type=LockbotMarketEvent)
                except Exception:
                    continue
                if event.symbol != self._cache.symbol:
                    continue
                self._cache.update_event(event)


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ohlcv_bar(payload: dict) -> Optional[OhlcvBar]:
    try:
        return OhlcvBar(
            ts_ms=int(payload.get("bar_start_ts")),
            open=float(payload.get("open")),
            high=float(payload.get("high")),
            low=float(payload.get("low")),
            close=float(payload.get("close")),
            volume=float(payload.get("volume")),
        )
    except (TypeError, ValueError):
        return None


def _select_avwap(
    payload: dict,
) -> tuple[Optional[float], Optional[str], dict[str, float]]:
    anchors = payload.get("anchors")
    if not isinstance(anchors, list):
        return None, None, {}
    selected: Optional[float] = None
    selected_id: Optional[str] = None
    anchor_map: dict[str, float] = {}
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        vwap = _to_float(anchor.get("vwap"))
        if vwap is None:
            continue
        anchor_id = str(anchor.get("anchor_id") or "")
        anchor_map[anchor_id] = vwap
        if selected is None:
            selected = vwap
            selected_id = anchor_id
    return selected, selected_id, anchor_map


def _summarize_heatmap(
    payload: dict, mark_price: Optional[float], ts_event: int
) -> LiqHeatmapSummary:
    intensity_above = _to_float(payload.get("intensity_above")) or 0.0
    intensity_below = _to_float(payload.get("intensity_below")) or 0.0
    if (
        intensity_above
        or intensity_below
        or not isinstance(payload.get("levels"), list)
    ):
        return LiqHeatmapSummary(
            intensity_above=intensity_above,
            intensity_below=intensity_below,
            last_ts=ts_event,
        )
    if mark_price is None:
        return LiqHeatmapSummary(
            intensity_above=0.0, intensity_below=0.0, last_ts=ts_event
        )
    above = 0.0
    below = 0.0
    for level in payload.get("levels", []):
        if not isinstance(level, dict):
            continue
        price = _to_float(level.get("price"))
        intensity = _to_float(level.get("intensity")) or 0.0
        if price is None:
            continue
        if price >= mark_price:
            above += intensity
        else:
            below += intensity
    return LiqHeatmapSummary(
        intensity_above=above, intensity_below=below, last_ts=ts_event
    )


def _event_attr(event: LockbotMarketEvent | dict, key: str, default: object) -> object:
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)
