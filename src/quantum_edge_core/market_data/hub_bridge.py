"""WS-First HubSnapshotBridge subscribing to MarketDataHubService ZMQ PUB (:5555).

Maintains RAM-only RingMarketBuffer snapshots per symbol without creating a
secondary WebSocket client or secondary CCXT exchange adapter. When fresh ZMQ
snapshots are available, PreFetchCoordinator reads top-of-book, orderbook, and
last price straight from RAM (0 REST calls), falling back seamlessly to REST
when MarketDataHubService is offline or stale.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import Any, Mapping

import zmq
import zmq.asyncio

from quantum_edge_core.execution.bingx_vst_adapter import normalize_compact_symbol
from quantum_edge_core.execution.storage_policy import RingMarketBuffer
from quantum_edge_core.market_data.wakeup_gate import decode_zmq_payload

logger = logging.getLogger("quantum_edge_core.market_data.hub_bridge")

HUB_BRIDGE_SUB_PREFIXES: tuple[bytes, ...] = (
    b"trade.",
    b"orderbook.",
    b"depth.",
    b"market.depth.",
    b"kline.",
)


def _normalize_levels(raw_levels: Any, max_levels: int = 50) -> list[list[float]]:
    if not isinstance(raw_levels, (list, tuple)):
        return []
    out: list[list[float]] = []
    for lvl in raw_levels[:max_levels]:
        if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            try:
                p, q = float(lvl[0]), float(lvl[1])
                if p > 0 and q >= 0:
                    out.append([p, q])
            except (TypeError, ValueError):
                continue
        elif isinstance(lvl, Mapping):
            try:
                p = float(lvl.get("price") or lvl.get("p") or 0.0)
                q = float(lvl.get("qty") or lvl.get("size") or lvl.get("q") or 0.0)
                if p > 0 and q >= 0:
                    out.append([p, q])
            except (TypeError, ValueError):
                continue
    return out


class HubSnapshotBridge:
    """Thread-safe RAM cache fed by MarketDataHubService ZMQ PUB messages."""

    def __init__(
        self,
        *,
        endpoint: str = "tcp://127.0.0.1:5555",
        ring_maxlen: int = 128,
        default_max_age_sec: float = 15.0,
    ) -> None:
        self.endpoint = endpoint
        self.ring_maxlen = max(8, int(ring_maxlen))
        self.default_max_age_sec = float(default_max_age_sec)
        self._lock = threading.Lock()
        self._buffers: dict[str, RingMarketBuffer[dict[str, Any]]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.messages_ingested: int = 0

    def _extract_symbol(self, topic: str, payload: Mapping[str, Any]) -> str:
        raw_sym = str(payload.get("symbol") or "").strip()
        if not raw_sym and "." in topic:
            raw_sym = topic.rsplit(".", 1)[-1].strip()
        compact = normalize_compact_symbol(raw_sym) or raw_sym.replace("-", "").replace("/", "").upper()
        return compact

    def ingest_message(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        now_ts: float | None = None,
    ) -> dict[str, Any] | None:
        """Ingest one decoded ZMQ message deterministically into RAM RingMarketBuffer."""
        if not isinstance(payload, Mapping):
            return None
        sym = self._extract_symbol(topic, payload)
        if not sym or sym == "GLOBAL":
            return None

        ref_now = time.time() if now_ts is None else float(now_ts)
        bids = _normalize_levels(payload.get("bids"))
        asks = _normalize_levels(payload.get("asks"))

        price_cand = (
            payload.get("price")
            or payload.get("last_price")
            or payload.get("last")
            or payload.get("close")
            or payload.get("mid_price")
            or payload.get("mid")
        )
        parsed_last: float | None = None
        if price_cand is not None:
            try:
                val = float(price_cand)
                if val > 0:
                    parsed_last = val
            except (TypeError, ValueError):
                parsed_last = None

        with self._lock:
            prev = dict(self._latest.get(sym) or {})
            eff_bids = bids if bids else list(prev.get("bids") or [])
            eff_asks = asks if asks else list(prev.get("asks") or [])

            bid_raw = payload.get("bid") or payload.get("best_bid")
            ask_raw = payload.get("ask") or payload.get("best_ask")
            eff_bid = (
                float(bid_raw)
                if bid_raw is not None and float(bid_raw) > 0
                else (eff_bids[0][0] if eff_bids else float(prev.get("bid") or 0.0))
            )
            eff_ask = (
                float(ask_raw)
                if ask_raw is not None and float(ask_raw) > 0
                else (eff_asks[0][0] if eff_asks else float(prev.get("ask") or 0.0))
            )
            eff_last = parsed_last or (
                (eff_bid + eff_ask) / 2.0
                if (eff_bid > 0 and eff_ask > 0)
                else float(prev.get("last_price") or eff_bid or eff_ask or 0.0)
            )
            if eff_last <= 0:
                return None

            if eff_bid <= 0:
                eff_bid = eff_last
            if eff_ask <= 0:
                eff_ask = eff_last
            mid = (eff_bid + eff_ask) / 2.0 if (eff_bid > 0 and eff_ask > 0) else eff_last
            spread_bps = (
                round(((eff_ask - eff_bid) / mid) * 10_000.0, 4)
                if (mid > 0 and eff_ask >= eff_bid)
                else float(payload.get("spread_bps") or prev.get("spread_bps") or 0.0)
            )

            snap = {
                "symbol": sym,
                "last_price": eff_last,
                "bid": eff_bid,
                "ask": eff_ask,
                "spread_bps": spread_bps,
                "bids": eff_bids,
                "asks": eff_asks,
                "timestamp": payload.get("timestamp") or int(ref_now * 1000),
                "updated_at": ref_now,
                "last_topic": topic,
            }
            buf = self._buffers.get(sym)
            if buf is None:
                buf = RingMarketBuffer(maxlen=self.ring_maxlen)
                self._buffers[sym] = buf
            buf.append(snap)
            self._latest[sym] = snap
            self.messages_ingested += 1
            return dict(snap)

    def get_fresh_snapshot(
        self,
        symbol: str,
        max_age_sec: float | None = None,
        *,
        now_ts: float | None = None,
    ) -> dict[str, Any] | None:
        """Return fresh RAM market snapshot if younger than max_age_sec, else None."""
        sym = normalize_compact_symbol(symbol) or str(symbol).strip().upper()
        limit_age = self.default_max_age_sec if max_age_sec is None else float(max_age_sec)
        ref_now = time.time() if now_ts is None else float(now_ts)
        with self._lock:
            snap = self._latest.get(sym)
            if snap is None:
                return None
            age = max(0.0, ref_now - float(snap.get("updated_at") or 0.0))
            if age > limit_age:
                return None
            out = dict(snap)
            out["bids"] = [list(x) for x in (snap.get("bids") or [])]
            out["asks"] = [list(x) for x in (snap.get("asks") or [])]
            out["age_sec"] = round(age, 4)
            return out

    def get_ring_history(self, symbol: str) -> list[dict[str, Any]]:
        sym = normalize_compact_symbol(symbol) or str(symbol).strip().upper()
        with self._lock:
            buf = self._buffers.get(sym)
            return buf.snapshot() if buf is not None else []

    async def run_subscriber(
        self,
        *,
        zmq_context: zmq.asyncio.Context | None = None,
        poll_timeout_ms: int = 100,
    ) -> None:
        """Fail-soft ZMQ SUB loop over existing MarketDataHubService."""
        self._stop_event.clear()
        ctx = zmq_context or zmq.asyncio.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.LINGER, 0)
        try:
            sock.connect(self.endpoint)
            for prefix in HUB_BRIDGE_SUB_PREFIXES:
                sock.setsockopt(zmq.SUBSCRIBE, prefix)
            poller = zmq.asyncio.Poller()
            poller.register(sock, zmq.POLLIN)
            while not self._stop_event.is_set():
                events = dict(await poller.poll(timeout=poll_timeout_ms))
                if sock not in events:
                    continue
                frames = await sock.recv_multipart()
                if len(frames) < 2:
                    continue
                topic = frames[0].decode("utf-8", errors="ignore")
                try:
                    payload = decode_zmq_payload(frames[1])
                except Exception:
                    continue
                self.ingest_message(topic, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("HubSnapshotBridge ZMQ subscriber stopped softly: %s", exc)
        finally:
            sock.close(linger=0)

    def start_background(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_subscriber())
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=1.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                with contextlib.suppress(Exception):
                    await self._task
            self._task = None


__all__ = ["HUB_BRIDGE_SUB_PREFIXES", "HubSnapshotBridge"]
