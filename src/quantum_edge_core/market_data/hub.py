"""Entry point for the MarketDataHub service."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


from quantum_edge_core.core.service import BaseService
from quantum_edge_core.logging_setup import setup_logging
from quantum_edge_core.utils.async_runner import run_service
from quantum_edge_core.events import (
    MarketTrade,
    KlineEvent,
    OrderBookUpdate,
    LiquidationEvent,
)

from quantum_edge_core.market_data.account.account_state import (
    AccountState,
    FINAL_ORDER_STATUSES,
)
from quantum_edge_core.market_data.account.binance_spot_userstream import (
    BinanceSpotUserStream,
)
from quantum_edge_core.market_data.account.binance_usdm_userstream import (
    BinanceUsdmUserStream,
)
from quantum_edge_core.market_data.account.publisher import AccountPublisher
from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.market_data.feeds.mock_feed import MockLiveFeed
from quantum_edge_core.market_data.feeds.binance_futures import BinanceFuturesFeed
from quantum_edge_core.market_data.feeds.liquidations import LiquidationFeed
from quantum_edge_core.market_data.analytics.alpha_engine import AlphaEngine
from quantum_edge_core.market_data.ipc.publisher import ZmqPublisher
from quantum_edge_core.market_data.ipc.snapshot_server import (
    SnapshotCache,
    SnapshotServer,
)
from quantum_edge_core.market_data.models import HeartbeatEvent, Priority, TradeEvent
from quantum_edge_core.market_data.models.account_snapshot import AccountSnapshot
from quantum_edge_core.market_data.models.account_delta import AccountDelta
from quantum_edge_core.market_data.orderbook.aggregator import OrderBookAggregator
from quantum_edge_core.market_data.microstructure.publisher import (
    MicrostructurePublisher,
)
from quantum_edge_core.market_data.lockbot.engine import LockbotDerivedEngine
from quantum_edge_core.market_data.lockbot.publisher import LockbotPublisher
from quantum_edge_core.market_data.spool.status import summarize_spool
from quantum_edge_core.market_data.tsdb.quest_writer import QuestILPWriter

# Forward references for type hinting (Legacy components)
MicrostructureAnalyzer = Any


class StatusReporter:
    """Writes periodic status JSON for MarketDataHub."""

    def __init__(
        self,
        path: Path,
        interval: float,
        status_fn: Callable[[], dict],
        stop_event: asyncio.Event,
    ) -> None:
        self._path = path
        self._interval = max(interval, 1.0)
        self._status_fn = status_fn
        self._stop_event = stop_event
        self._task: Optional[asyncio.Task] = None

    @property
    def task(self) -> Optional[asyncio.Task]:
        return self._task

    async def start(self) -> None:
        if self._task:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._write_status()
            except Exception as exc:
                logging.warning("Status reporter failed: %s", exc)
            await asyncio.sleep(self._interval)

    def _write_status(self) -> None:
        payload = self._status_fn()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)


class MarketDataHubService(BaseService):
    """Service orchestrating the data-plane components."""

    def __init__(self, config: HubConfig | None = None) -> None:
        super().__init__("MarketDataHub")
        self.config = config or HubConfig.load()
        self.bus = EventBus(l0_hwm=self.config.l0_hwm, l1_hwm=self.config.l1_hwm)
        # Use new ZmqPublisher (port 5555 default, or config if we had it)
        self.publisher = ZmqPublisher(port=5555)
        self.writer: Optional[QuestILPWriter] = (
            QuestILPWriter(host="127.0.0.1", port=9009)
            if self.config.tsdb.enabled
            else None
        )
        self.snapshot_cache = SnapshotCache(trade_tail=self.config.snapshot.trade_tail)
        self.snapshot_server = SnapshotServer(self.config, self.snapshot_cache)
        # self.feeds = [
        #     BinanceSpotFeed(self.config, self.bus),
        #     BinanceFuturesFeed(self.config, self.bus),
        # ]

        # Force Mock mode if requested to bypass Binance bans
        if self.config.mode == "mock":
            self.feeds = [
                MockLiveFeed(self.config, self.bus)
                # LiquidationFeed(self.config, self.bus) # Disabled to prevent Binance connections
            ]
        else:
            self.feeds = [BinanceFuturesFeed(self.config, self.bus)]

        self.alpha_engine = AlphaEngine(symbol="BTCUSDT")  # Default symbol
        self.ob_aggregator = OrderBookAggregator(self.config, self.publisher, self.bus, self.snapshot_cache)
        self.last_metrics_pub = 0.0
        self._last_mid_price: float = 0.0  # Cache for OB aggregator

        # Legacy components commented out
        self.microstructure_analyzer: Optional[MicrostructureAnalyzer] = None
        self.microstructure_publisher: Optional[MicrostructurePublisher] = None
        self.lockbot_publisher: Optional[LockbotPublisher] = None
        self.lockbot_engine: Optional[LockbotDerivedEngine] = None
        if self.config.microstructure.enabled:
            pass
            # self.microstructure_analyzer = MicrostructureAnalyzer(
            #     window_n=self.config.microstructure.ofi_window_n,
            #     eps=self.config.microstructure.zscore_eps,
            #     trade_window_sec=self.config.microstructure.trade_window_sec,
            # )
            # self.microstructure_publisher = MicrostructurePublisher(
            #     self.publisher,
            #     self.bus,
            #     # writer=self.writer, # Disable legacy writer integration for now
            #     event_type=self.config.microstructure.publish_topic_suffix,
            # )
        if self.config.lockbot.enabled:
            # self.lockbot_publisher = LockbotPublisher(self.publisher, self.bus, self.writer)
            # lockbot_cfg = self.config.lockbot
            # self.lockbot_engine = LockbotDerivedEngine(...) (Disabled to avoid writer conflict)
            pass
            lockbot_cfg = self.config.lockbot
            self.lockbot_engine = LockbotDerivedEngine(
                self.lockbot_publisher,
                vwap_publish_interval_ms=lockbot_cfg.vwap_publish_interval_ms,
                avwap_publish_interval_ms=lockbot_cfg.avwap_publish_interval_ms,
                heatmap_publish_interval_ms=lockbot_cfg.heatmap_publish_interval_ms,
                heatmap_window_s=lockbot_cfg.heatmap_window_s,
                heatmap_bin_type=lockbot_cfg.heatmap_bin_type,
                heatmap_bin_size=lockbot_cfg.heatmap_bin_size,
                heatmap_half_life_s=lockbot_cfg.heatmap_half_life_s,
                heatmap_top_n=lockbot_cfg.heatmap_top_n,
            )
        # Legacy OrderBookAggregator disabled — replaced by self.ob_aggregator (L138)
        self.orderbook: Optional[OrderBookAggregator] = None
        self.account_state = AccountState(self.config.account)
        self.account_publisher = AccountPublisher(self.publisher)
        self._account_repair_manager = AccountRepairManager(
            state=self.account_state,
            publisher=self.account_publisher,
            symbols=self.config.symbols,
            include_market=self.config.account_runtime.publish_market_prices,
        )
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._start_ts = time.time()
        self._last_event_ts: Dict[Tuple[Optional[str], str], int] = {}
        self._status_reporter = StatusReporter(
            Path(self.config.status_file),
            float(self.config.status_interval_sec),
            self._collect_status,
            self._stop_event,
        )
        self._account_streams: List[Any] = []
        self._account_stream_tasks: List[asyncio.Task] = []
        self._account_lock = asyncio.Lock()

    async def run(self) -> None:
        """Main service loop."""
        setup_logging()
        # logging.basicConfig removed - using structslog via setup_logging()
        self.logger.info("Initializing MarketDataHub")
        if self.writer:
            await self.writer.connect()
        if self.orderbook:
            await self.orderbook.start()
        await self._status_reporter.start()
        if self._status_reporter.task:
            self._tasks.append(self._status_reporter.task)
        self.snapshot_server.start()
        # await self._publish_initial_account_snapshot()
        # self._start_account_streams()
        for feed in self.feeds:
            await feed.start()
        self._tasks.extend(
            [
                asyncio.create_task(self._dispatcher_loop()),
                asyncio.create_task(self._heartbeat_loop()),
            ]
        )
        # Status loop disabled as writer metrics changed
        # if self.writer:
        #     self._tasks.append(asyncio.create_task(self._status_loop()))
        # Main service "wait" loop - we wait on shutdown event which BaseService handles
        await self._shutdown_event.wait()

    async def cleanup(self) -> None:
        """Clean up resources on shutdown."""
        self._stop_event.set()  # Ensure internal stop event is set
        for feed in self.feeds:
            await feed.stop()
        await self.publisher.stop()
        self.snapshot_server.stop()
        await self._status_reporter.stop()
        if self.orderbook:
            await self.orderbook.stop()
        if self.writer:
            await self.writer.stop()
        for stream in self._account_streams:
            await stream.stop()
        for task in self._account_stream_tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._account_repair_manager.stop()
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _dispatcher_loop(self) -> None:
        while not self._stop_event.is_set():
            event = await self.bus.get_event()
            try:
                key = (getattr(event, "symbol", None), getattr(event, "event_type", ""))
                self._last_event_ts[key] = getattr(event, "ts_ns", time.time_ns())
                self.snapshot_cache.update(event)

                # Derive topic
                symbol = getattr(event, "symbol", "global") or "global"  # Handle None
                ev_type = getattr(event, "event_type", "unknown")
                topic = f"{ev_type}.{symbol}".lower()

                await self.publisher.publish(topic, event)
                self.logger.debug("Hub broadcasted tick to ZMQ", topic=topic)
                if self.microstructure_analyzer and isinstance(event, TradeEvent):
                    self.microstructure_analyzer.update_trade(event.ts_ns, event.size)
                if self.lockbot_engine and isinstance(event, TradeEvent):
                    ts_event_ms = int(event.ts_ns / 1_000_000)
                    taker_side = str(event.taker_side or "").lower()
                    is_buyer_maker = (
                        True
                        if taker_side == "sell"
                        else False if taker_side == "buy" else None
                    )
                    self.lockbot_engine.on_trade(
                        symbol=event.symbol,
                        price=event.price,
                        qty=event.size,
                        ts_event_ms=ts_event_ms,
                        is_buyer_maker=is_buyer_maker,
                        agg_trade_id=event.seq,
                        source="binance_ws",
                    )
                if isinstance(
                    event, (TradeEvent, MarketTrade, KlineEvent, OrderBookUpdate)
                ):
                    # 4. Analytics — ONLY for trade-like events (not OrderBookUpdate)
                    if isinstance(event, (TradeEvent, MarketTrade)):
                        whale_event = self.alpha_engine.update_trade(event)
                        if whale_event:
                            await self.publisher.publish(
                                "market.alpha.whale", whale_event
                            )

                    # 4b. Order Book Aggregation (Whale Wall Detection)
                    if isinstance(event, OrderBookUpdate):
                        bids = getattr(event, "bids", [])
                        asks = getattr(event, "asks", [])
                        # Use mid-price from best bid/ask, or cached price
                        if bids and asks:
                            mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
                            self._last_mid_price = mid
                        walls = self.ob_aggregator.process_book(
                            bids, asks, self._last_mid_price
                        )
                        if any(v != 0.0 for v in walls.values()):
                            await self.publisher.publish("market.walls", walls)

                    # Update cached mid price from kline close
                    if isinstance(event, KlineEvent):
                        self._last_mid_price = float(
                            getattr(event, "close", self._last_mid_price)
                        )

                    # Publish Metrics (Throttled 100ms)
                    now = time.time()
                    if now - self.last_metrics_pub > 0.1:
                        metrics = self.alpha_engine.compute_metrics()
                        await self.publisher.publish("market.metrics", metrics)
                        self.last_metrics_pub = now

                    # 5. Persist (only if QuestDB writer is available)
                    if self.writer:
                        if isinstance(event, KlineEvent):
                            self._persist_kline(event)
                        elif isinstance(event, OrderBookUpdate):
                            self._persist_orderbook(event)
                        elif isinstance(event, (TradeEvent, MarketTrade)):
                            self._persist_trade(event)

                if isinstance(event, LiquidationEvent):
                    # Liquidation events: ZMQ broadcast + QuestDB persist
                    # (topic already derived as liquidation.btcusdt at L267)
                    if self.writer:
                        self._persist_liquidation(event)

            except Exception as e:
                self.logger.error(
                    "Pipeline crashed on tick — event survived",
                    error=str(e),
                    event_type=getattr(event, "event_type", "?"),
                    exc_info=True,
                )

    def _get_next_offset(self) -> int:
        """Helper to generate a rolling nanosecond offset to prevent QuestDB WAL duplication."""
        self._ns_offset = getattr(self, "_ns_offset", 0) + 1
        return self._ns_offset % 1_000_000  # Wrap around at 1ms

    def _persist_trade(self, event: Any) -> None:
        # Map event to ILP structure
        # Support both legacy TradeEvent and new MarketTrade
        # new: symbol, price, quantity/size, side
        symbol = str(getattr(event, "symbol", "unknown"))
        side = str(getattr(event, "side", getattr(event, "taker_side", "unknown")))
        price = float(getattr(event, "price", 0.0))
        qty = float(getattr(event, "quantity", getattr(event, "size", 0.0)))

        # Fallback to server ingest time if no timestamp found
        ts_ns = None
        ts_sec = float(getattr(event, "timestamp", 0.0))
        if ts_sec > 0:
            ts_ns = int(ts_sec * 1_000_000_000) + self._get_next_offset()
        else:
            ts_ns_val = getattr(event, "ts_ns", None)  # Legacy support
            if ts_ns_val:
                ts_ns = int(float(ts_ns_val)) + self._get_next_offset()

        self.writer.enqueue(
            table="trades",
            symbols={"symbol": symbol, "side": side},
            columns={"price": price, "qty": qty},
            timestamp_ns=ts_ns,
        )

    def _persist_kline(self, event: Any) -> None:
        """Write KlineEvent to klines_1m table via ILP."""
        symbol = str(getattr(event, "symbol", "unknown"))
        ts_sec = float(getattr(event, "timestamp", 0.0))
        ts_ns = int(ts_sec * 1_000_000_000) if ts_sec > 0 else None

        self.writer.enqueue(
            table="klines_1m",
            symbols={"symbol": symbol},
            columns={
                "open": float(getattr(event, "open", 0.0)),
                "high": float(getattr(event, "high", 0.0)),
                "low": float(getattr(event, "low", 0.0)),
                "close": float(getattr(event, "close", 0.0)),
                "volume": float(getattr(event, "volume", 0.0)),
                "trades_count": int(getattr(event, "trades", 0)),
            },
            timestamp_ns=ts_ns,
        )

    def _persist_orderbook(self, event: Any) -> None:
        """Write OrderBookUpdate to orderbook_snapshots table via ILP.

        Each bid/ask level is a separate ILP row for efficient querying.
        Per-row nanosecond offset prevents QuestDB timestamp collision.
        """
        symbol = str(getattr(event, "symbol", "unknown"))
        ts_sec = float(getattr(event, "timestamp", 0.0))
        base_ts_ns = int(ts_sec * 1_000_000_000) if ts_sec > 0 else None

        bids = getattr(event, "bids", [])
        asks = getattr(event, "asks", [])

        self.logger.info(
            "PERSIST_OB: symbol=%s bids=%d asks=%d ts=%.3f base_ns=%s",
            symbol,
            len(bids),
            len(asks),
            ts_sec,
            base_ts_ns,
        )

        if base_ts_ns is None:
            self.logger.warning("PERSIST_OB: skipping — no timestamp")
            return

        row_offset = 0  # nanosecond offset to avoid timestamp collision

        try:
            for depth, level in enumerate(bids):
                if len(level) < 2:
                    continue
                self.writer.enqueue(
                    table="orderbook_snapshots",
                    symbols={"symbol": symbol, "side": "BUY"},
                    columns={
                        "price": float(level[0]),
                        "qty": float(level[1]),
                        "wall_size": 0.0,
                        "wall_distance_pct": 0.0,
                        "depth_level": depth,
                    },
                    timestamp_ns=base_ts_ns + row_offset,
                )
                row_offset += 1

            for depth, level in enumerate(asks):
                if len(level) < 2:
                    continue
                self.writer.enqueue(
                    table="orderbook_snapshots",
                    symbols={"symbol": symbol, "side": "SELL"},
                    columns={
                        "price": float(level[0]),
                        "qty": float(level[1]),
                        "wall_size": 0.0,
                        "wall_distance_pct": 0.0,
                        "depth_level": depth,
                    },
                    timestamp_ns=base_ts_ns + row_offset,
                )
                row_offset += 1

            self.logger.info(
                "PERSIST_OB: enqueued %d ILP rows for %s", row_offset, symbol
            )
        except Exception as exc:
            self.logger.error("PERSIST_OB: FAILED — %s", exc, exc_info=True)

    def _persist_liquidation(self, event: Any) -> None:
        # Map liquidation event to ILP
        symbol = str(getattr(event, "symbol", "unknown"))
        side = str(getattr(event, "side", "unknown"))
        price = float(getattr(event, "price", 0.0))
        qty = float(getattr(event, "qty", 0.0))
        usd_size = float(getattr(event, "usd_size", 0.0))
        ts_sec = float(getattr(event, "timestamp", 0.0))

        # Convert epoch seconds to nanoseconds for ILP, add offset
        ts_ns = (
            int(ts_sec * 1_000_000_000) + self._get_next_offset()
            if ts_sec > 0
            else None
        )

        self.writer.enqueue(
            table="liquidations",
            symbols={"symbol": symbol, "side": side},
            columns={"price": price, "qty": qty, "usd_size": usd_size},
            timestamp_ns=ts_ns,
        )

    async def _status_loop(self) -> None:
        while not self._stop_event.is_set():
            metrics = self.writer.metrics  # type: ignore[attr-defined]
            logging.info(
                "TSDB warm-path rows=%d dropped=%d last_flush=%s queue=%d",
                metrics.written_rows,
                metrics.dropped_rows,
                metrics.last_flush_ts,
                self.writer.queue_depth(),  # type: ignore[attr-defined]
            )
            await asyncio.sleep(5)

    async def _heartbeat_loop(self) -> None:
        symbol = self.config.symbols[0]
        while not self._stop_event.is_set():
            event = HeartbeatEvent(
                ts_ns=time.time_ns(),
                symbol=symbol,
                event_type="heartbeat",
                seq=self.bus.assign_sequence(symbol, "heartbeat"),
                priority=Priority.L2,
                peer="heart",
                extra={"status": "ok"},
            )
            await self.bus.publish(event)
            await asyncio.sleep(5)

    def _collect_status(self) -> dict:
        now = time.time()
        status = {
            "uptime_seconds": now - self._start_ts,
            "endpoints": {
                "zmq_pub": self.config.zmq.endpoint,
                "snapshot": self.config.snapshot.endpoint,
            },
            "last_events": [
                {
                    "symbol": symbol,
                    "event_type": event_type,
                    "ts_ns": ts_ns,
                }
                for (symbol, event_type), ts_ns in sorted(
                    self._last_event_ts.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            ],
        }
        if self.writer and hasattr(self.writer, "metrics"):
            metrics = self.writer.metrics
            status["tsdb"] = {
                "written_rows": metrics.written_rows,
                "dropped_rows": metrics.dropped_rows,
                "last_flush_ts": metrics.last_flush_ts,
                "errors": metrics.errors,
                "queue_depth": (
                    self.writer.queue_depth()
                    if hasattr(self.writer, "queue_depth")
                    else 0
                ),
            }
            l2_info = {
                "enabled": self.config.l2.enabled,
                "spool_dir": self.config.l2.spool_dir,
                "spooled_total": metrics.l2_spooled_total,
                "buffered_total": metrics.l2_buffered_total,
                "written_total": metrics.l2_written_total,
                "write_errors": metrics.l2_write_errors_total,
                "buffer_overflow": metrics.l2_buffer_overflow_total,
                "spool_summary": self._spool_summary_dict(),
                "replay_state": self._read_replay_state(),
            }
            status["l2"] = l2_info
        else:
            status["tsdb"] = None
            status["l2"] = {
                "enabled": self.config.l2.enabled,
                "spool_dir": self.config.l2.spool_dir,
            }
        if self.orderbook:
            stats = self.orderbook.stats
            status["orderbook"] = {
                "enabled": True,
                "orderbook_updates_total": stats.orderbook_updates_total,
                "depth_publish_total": stats.depth_publish_total,
                "walls_publish_total": stats.walls_publish_total,
                "orderbook_resync_total": stats.orderbook_resync_total,
                "last_depth_ts_ns": self.orderbook.last_depth_ts,
                "last_walls_ts_ns": self.orderbook.last_walls_ts,
            }
        else:
            status["orderbook"] = {"enabled": False}
        return status

    def _read_replay_state(self) -> Optional[dict]:
        state_path = Path(self.config.l2.spool_dir) / ".replay_state.json"
        if not state_path.exists():
            return None
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"error": str(exc)}

    def _spool_summary_dict(self) -> dict:
        summary = summarize_spool(Path(self.config.l2.spool_dir))
        return {
            "bytes": summary.bytes,
            "files": summary.files,
            "oldest": str(summary.oldest) if summary.oldest else None,
            "newest": str(summary.newest) if summary.newest else None,
        }

    async def _publish_initial_account_snapshot(self) -> None:
        snapshot = await self._account_repair_manager.run_snapshot_once()
        self.account_publisher.publish_snapshot(snapshot)

    def _start_account_streams(self) -> None:
        if self.config.mode == "mock":
            self.logger.info(
                "Mock mode: Skipping account stream connections to Binance"
            )
            return

        if self.config.account_runtime.enable_spot:
            spot_stream = BinanceSpotUserStream(
                self.config.account,
                handler=self._handle_spot_event,
                on_reconnect=self._handle_account_reconnect,
            )
            self._account_streams.append(spot_stream)
            task = asyncio.create_task(spot_stream.run())
            self._account_stream_tasks.append(task)
            self._tasks.append(task)
        if self.config.account_runtime.enable_usdm:
            usdm_stream = BinanceUsdmUserStream(
                self.config.account,
                handler=self._handle_usdm_event,
                on_reconnect=self._handle_account_reconnect,
            )
            self._account_streams.append(usdm_stream)
            task = asyncio.create_task(usdm_stream.run())
            self._account_stream_tasks.append(task)
            self._tasks.append(task)

    async def _handle_spot_event(self, payload: Dict[str, Any]) -> None:
        event_type = payload.get("e")
        delta: Optional[AccountDelta] = None
        repair_needed = False
        async with self._account_lock:
            if event_type == "outboundAccountPosition":
                delta = self.account_state.apply_spot_outboundAccountPosition(payload)
            elif event_type == "executionReport":
                raw_order = payload.get("o") or payload
                order_id = str(raw_order.get("orderId", "")) if raw_order else ""
                existed = bool(
                    order_id and order_id in self.account_state.spot_open_orders
                )
                delta = self.account_state.apply_spot_execution_report(payload)
                if (
                    delta
                    and order_id
                    and delta.patch.spot
                    and delta.patch.spot.orders_update
                ):
                    status = delta.patch.spot.orders_update[0].status
                    repair_needed = status in FINAL_ORDER_STATUSES and not existed
        if delta:
            self.account_publisher.publish_delta(delta)
        if repair_needed:
            await self._account_repair_manager.request_repair()

    async def _handle_usdm_event(self, payload: Dict[str, Any]) -> None:
        event_type = payload.get("e")
        delta: Optional[AccountDelta] = None
        repair_needed = False
        async with self._account_lock:
            if event_type == "ACCOUNT_UPDATE":
                delta = self.account_state.apply_usdm_ACCOUNT_UPDATE(payload)
            elif event_type == "ORDER_TRADE_UPDATE":
                raw_order = payload.get("o") or payload
                order_id = str(raw_order.get("orderId", "")) if raw_order else ""
                existed = bool(
                    order_id and order_id in self.account_state.usdm_open_orders
                )
                delta = self.account_state.apply_usdm_ORDER_TRADE_UPDATE(payload)
                if (
                    delta
                    and order_id
                    and delta.patch.usdm
                    and delta.patch.usdm.orders_update
                ):
                    status = delta.patch.usdm.orders_update[0].status
                    repair_needed = status in FINAL_ORDER_STATUSES and not existed
        if delta:
            self.account_publisher.publish_delta(delta)
        if repair_needed:
            await self._account_repair_manager.request_repair()

    async def _handle_account_reconnect(self) -> None:
        await self._account_repair_manager.request_repair()


async def run() -> None:
    service = MarketDataHubService()
    await service.start()


def _status_command(file: Optional[str], raw: bool) -> int:
    config = HubConfig.load()
    path = Path(file or config.status_file)
    if not path.exists():
        print(f"Status file not found: {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Failed to read status file: {exc}", file=sys.stderr)
        return 1
    if raw:
        print(json.dumps(data, separators=(",", ":")))
    else:
        print(json.dumps(data, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="MarketDataHub service")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Run the MarketDataHub service")
    status_parser = subparsers.add_parser("status", help="Print latest status JSON")
    status_parser.add_argument("--file", "-f", help="Status file override")
    status_parser.add_argument("--json", action="store_true", help="Print compact JSON")
    parser.set_defaults(command="run")
    args = parser.parse_args()
    if args.command == "status":
        sys.exit(_status_command(args.file, args.json))
    try:
        if args.command == "run":
            # Initialize service and run via async_runner
            service = MarketDataHubService()
            run_service(service._runner_wrapper())
        else:
            run_service(run())
    except KeyboardInterrupt:
        pass


class AccountRepairManager:
    """Runs canonical REST snapshots on demand while throttling duplicates."""

    def __init__(
        self,
        state: AccountState,
        publisher: AccountPublisher,
        symbols: List[str],
        include_market: bool,
    ) -> None:
        self._state = state
        self._publisher = publisher
        self._symbols = symbols
        self._include_market = include_market
        self._lock = asyncio.Lock()
        self._repair_task: Optional[asyncio.Task] = None
        self._pending = False

    async def run_snapshot_once(self) -> AccountSnapshot:
        return await self._execute_snapshot()

    async def request_repair(self) -> None:
        async with self._lock:
            if self._repair_task and not self._repair_task.done():
                self._pending = True
                return
            self._repair_task = asyncio.create_task(self._repair_worker())

    async def stop(self) -> None:
        if self._repair_task:
            self._repair_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._repair_task

    async def _repair_worker(self) -> None:
        try:
            await self._execute_snapshot()
        finally:
            async with self._lock:
                if self._pending:
                    self._pending = False
                    self._repair_task = asyncio.create_task(self._repair_worker())
                else:
                    self._repair_task = None

    async def _execute_snapshot(self) -> AccountSnapshot:
        snapshot = await asyncio.to_thread(
            self._state.build_snapshot, self._symbols, self._include_market
        )
        self._publisher.publish_snapshot(snapshot)
        return snapshot


if __name__ == "__main__":
    main()
