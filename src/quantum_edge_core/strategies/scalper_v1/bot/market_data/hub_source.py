"""ZeroMQ SUB-based market data source for bots (consumes MarketDataHub)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

import msgspec
import zmq
import zmq.asyncio

from bot.core.config_loader import config
from market_data.models import (
    Bar1sEvent,
    L1Event,
    MarketEvent,
    MicrostructureEvent,
    SnapshotRequest,
    SnapshotResponse,
    TradeEvent,
    decode_event,
)
from market_data.models.orderbook import DEPTH_EVENT_TYPE, DepthL2Event


class HubSourceConfig:
    def __init__(self, data: Dict[str, Any], symbols: Iterable[str]) -> None:
        hub = (data.get("hub") or {}) if isinstance(data, dict) else {}
        self.pub_endpoint = hub.get(
            "pub_endpoint", "ipc:///tmp/quantum_market_data.ipc"
        )
        self.snapshot_endpoint = hub.get(
            "snapshot_endpoint", "ipc:///tmp/quantum_market_snapshot.ipc"
        )
        self.rcvhwm = int(hub.get("rcvhwm", 1000))
        self.conflate_l1 = bool(hub.get("conflate_l1", False))
        self.include_microstructure = bool(hub.get("include_microstructure", True))
        self.microstructure_topic_suffix = str(
            hub.get("microstructure_topic_suffix", "microstructure.v1")
        )
        self.topics = hub.get("topics") or self._default_topics(symbols)
        self.snapshot_timeout_ms = int(hub.get("snapshot_timeout_ms", 500))

    def _default_topics(self, symbols: Iterable[str]) -> List[str]:
        types = ["trade", "l1", "bar1s"]
        if self.include_microstructure:
            types.append(self.microstructure_topic_suffix)
        return [f"{symbol}:{etype}" for symbol in symbols for etype in types]


class HubSnapshotClient:
    def __init__(
        self, endpoint: str, timeout_ms: int = 500, connect: bool = True
    ) -> None:
        self._ctx = zmq.asyncio.Context.instance()
        self._socket = self._ctx.socket(zmq.REQ)
        self._socket.setsockopt(zmq.RCVTIMEO, 2000)
        self._socket.setsockopt(zmq.SNDTIMEO, 2000)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        if connect:
            self._socket.connect(endpoint)

    async def request(
        self, symbol: str, event_type: str, limit: int = 0
    ) -> Optional[SnapshotResponse]:
        req = SnapshotRequest(symbol=symbol, event_type=event_type, limit=limit)
        await self._socket.send(msgspec.msgpack.encode(req))
        try:
            raw = await self._socket.recv()
        except zmq.error.Again:
            logging.warning("Snapshot request timeout for %s:%s", symbol, event_type)
            return None
        return msgspec.msgpack.decode(raw, type=SnapshotResponse)

    def close(self) -> None:
        self._socket.close()


class HubMarketDataSource:

    def __init__(
        self,
        symbols: List[str],
        source_cfg: Optional[Dict[str, Any]] = None,
        connect_pub: bool = True,
        connect_snapshot: bool = True,
    ) -> None:
        self.symbols = symbols
        self._config = HubSourceConfig(
            source_cfg or config.get("market_data", {}), symbols
        )
        self._event_map = {
            "trade": TradeEvent,
            "l1": L1Event,
            "bar1s": Bar1sEvent,
            DEPTH_EVENT_TYPE: DepthL2Event,
            self._config.microstructure_topic_suffix: MicrostructureEvent,
        }
        self._ctx = zmq.asyncio.Context.instance()
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVTIMEO, 2000)
        self._sub.setsockopt(zmq.SNDTIMEO, 2000)
        self._sub.setsockopt(zmq.RCVHWM, self._config.rcvhwm)
        self._sub.setsockopt(zmq.LINGER, 0)
        if self._config.conflate_l1:
            self._sub.setsockopt(zmq.CONFLATE, 1)
        for topic in self._config.topics:
            self._sub.setsockopt(zmq.SUBSCRIBE, topic.encode("utf-8"))
        if connect_pub:
            self._sub.connect(self._config.pub_endpoint)
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._seq_tracker: Dict[Tuple[str, str], int] = {}
        self._snapshot_client = HubSnapshotClient(
            self._config.snapshot_endpoint,
            self._config.snapshot_timeout_ms,
            connect_snapshot,
        )
        self._reader_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self.gaps_total = 0

    async def start(self) -> None:
        self._stop_event.clear()
        if not self._reader_task or self._reader_task.done():
            self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._snapshot_client.close()
        self._sub.close()

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        await self.start()
        while True:
            item = await self._queue.get()
            yield item

    async def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                topic, payload = await self._sub.recv_multipart()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logging.warning("Hub source read error: %s", exc)
                continue
            symbol, event_type = self._parse_topic(topic)
            event = self._decode_event(event_type, payload)
            if event is None:
                continue
            gap_event = await self._maybe_handle_gap(symbol, event_type, event.seq)
            if gap_event:
                await self._queue.put(self._normalize_event(gap_event))
            self._seq_tracker[(symbol, event_type)] = event.seq
            await self._queue.put(self._normalize_event(event))

    async def _maybe_handle_gap(
        self, symbol: str, event_type: str, seq: int
    ) -> Optional[MarketEvent]:
        key = (symbol, event_type)
        last_seq = self._seq_tracker.get(key)
        if last_seq is not None and seq != last_seq + 1:
            self.gaps_total += 1
            logging.warning(
                "Gap detected for %s:%s (got %s expected %s)",
                symbol,
                event_type,
                seq,
                last_seq + 1,
            )
            snapshot = await self._snapshot_client.request(symbol, event_type)
            if snapshot and snapshot.ok and snapshot.payload:
                event_cls = self._event_map.get(snapshot.payload_type)
                if event_cls:
                    cached = decode_event(snapshot.payload, event_cls)
                    self._seq_tracker[key] = cached.seq
                    return cached
            logging.warning("Failed to recover snapshot for %s:%s", symbol, event_type)
        return None

    def _decode_event(self, event_type: str, payload: bytes) -> Optional[MarketEvent]:
        event_cls = self._event_map.get(event_type)
        if not event_cls:
            logging.debug("Unknown event_type %s", event_type)
            return None
        return decode_event(payload, type_=event_cls)

    def _normalize_event(self, event: MarketEvent) -> Dict[str, Any]:
        base = {
            "s": event.symbol,
            "sequence": event.seq,
            "event_type": event.event_type,
        }
        if isinstance(event, TradeEvent):
            base.update({"p": event.price, "q": event.size, "side": event.taker_side})
        elif isinstance(event, L1Event):
            mid = None
            if event.best_bid and event.best_ask:
                mid = (event.best_bid + event.best_ask) / 2.0
            depth = None
            if mid and event.bid_size and event.ask_size:
                depth = mid * (event.bid_size + event.ask_size)
            base.update(
                {
                    "b": event.best_bid,
                    "a": event.best_ask,
                    "quant_bid": event.bid_size,
                    "quant_ask": event.ask_size,
                    "bid_qty": event.bid_size,
                    "ask_qty": event.ask_size,
                    "depth": depth,
                }
            )
        elif isinstance(event, DepthL2Event):
            best_bid_px = None
            best_bid_qty = None
            if event.bids:
                best_bid_px = max(level.price for level in event.bids)
                best_bid_qty = sum(
                    level.qty for level in event.bids if level.price == best_bid_px
                )
            best_ask_px = None
            best_ask_qty = None
            if event.asks:
                best_ask_px = min(level.price for level in event.asks)
                best_ask_qty = sum(
                    level.qty for level in event.asks if level.price == best_ask_px
                )
            mid = event.mid
            if mid is None and best_bid_px is not None and best_ask_px is not None:
                mid = (best_bid_px + best_ask_px) / 2.0
            depth = None
            if mid and best_bid_qty and best_ask_qty:
                depth = mid * (best_bid_qty + best_ask_qty)
            base.update(
                {
                    "b": best_bid_px,
                    "a": best_ask_px,
                    "bid_qty": best_bid_qty,
                    "ask_qty": best_ask_qty,
                    "depth": depth,
                }
            )
        elif isinstance(event, Bar1sEvent):
            base.update(
                {
                    "open": event.open,
                    "high": event.high,
                    "low": event.low,
                    "close": event.close,
                    "volume": event.volume,
                }
            )
        elif isinstance(event, MicrostructureEvent):
            mid = None
            if event.best_bid_px and event.best_ask_px:
                mid = (event.best_bid_px + event.best_ask_px) / 2.0
            depth = None
            if mid and event.top_qty_sum:
                depth = mid * event.top_qty_sum
            base.update(
                {
                    "b": event.best_bid_px,
                    "a": event.best_ask_px,
                    "depth": depth,
                    "best_bid_qty": event.best_bid_qty,
                    "best_ask_qty": event.best_ask_qty,
                    "ofi_raw": event.ofi_raw,
                    "ofi_z": event.ofi_z,
                    "ofi_ma5": event.ofi_ma5,
                    "spread_bps": event.spread_bps,
                    "top_qty_sum": event.top_qty_sum,
                    "trade_rate_1s": event.trade_rate_1s,
                    "volume_1s": event.volume_1s,
                    "is_gap": event.is_gap,
                    "is_resynced": event.is_resynced,
                    "ts_event": event.ts_event,
                    "ts_ingest": event.ts_ingest,
                }
            )
        return base

    @staticmethod
    def _parse_topic(topic: bytes) -> Tuple[str, str]:
        text = topic.decode("utf-8", errors="ignore")
        if ":" in text:
            symbol, event_type = text.split(":", 1)
        else:
            symbol, event_type = text, ""
        return symbol, event_type
