"""Entry point for the MarketDataHub service."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from MarketDataHub.account.account_state import AccountState, FINAL_ORDER_STATUSES
from MarketDataHub.account.binance_spot_userstream import BinanceSpotUserStream
from MarketDataHub.account.binance_usdm_userstream import BinanceUsdmUserStream
from MarketDataHub.account.publisher import AccountPublisher
from MarketDataHub.bus.event_bus import EventBus
from MarketDataHub.config import HubConfig
from MarketDataHub.feeds.binance_futures import BinanceFuturesFeed
from MarketDataHub.feeds.binance_spot import BinanceSpotFeed
from MarketDataHub.ipc.publisher import ZmqPublisher
from MarketDataHub.ipc.snapshot_server import SnapshotCache, SnapshotServer
from MarketDataHub.models import HeartbeatEvent, Priority, TradeEvent
from MarketDataHub.models.account_snapshot import AccountSnapshot
from MarketDataHub.orderbook.aggregator import OrderBookAggregator
from MarketDataHub.microstructure.ofi import MicrostructureAnalyzer
from MarketDataHub.microstructure.publisher import MicrostructurePublisher
from MarketDataHub.lockbot.engine import LockbotDerivedEngine
from MarketDataHub.lockbot.publisher import LockbotPublisher
from MarketDataHub.spool.status import summarize_spool
from MarketDataHub.tsdb.quest_writer import QuestILPWriter


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


class MarketDataHubService:
    """Service orchestrating the data-plane components."""

    def __init__(self, config: HubConfig | None = None) -> None:
        self.config = config or HubConfig.load()
        self.bus = EventBus(l0_hwm=self.config.l0_hwm, l1_hwm=self.config.l1_hwm)
        self.publisher = ZmqPublisher(self.config)
        self.writer: Optional[QuestILPWriter] = (
            QuestILPWriter(self.config.tsdb, self.config.l2) if self.config.tsdb.enabled else None
        )
        self.snapshot_cache = SnapshotCache(trade_tail=self.config.snapshot.trade_tail)
        self.snapshot_server = SnapshotServer(self.config, self.snapshot_cache)
        self.feeds = [
            BinanceSpotFeed(self.config, self.bus),
            BinanceFuturesFeed(self.config, self.bus),
        ]
        self.microstructure_analyzer: Optional[MicrostructureAnalyzer] = None
        self.microstructure_publisher: Optional[MicrostructurePublisher] = None
        self.lockbot_publisher: Optional[LockbotPublisher] = None
        self.lockbot_engine: Optional[LockbotDerivedEngine] = None
        if self.config.microstructure.enabled:
            self.microstructure_analyzer = MicrostructureAnalyzer(
                window_n=self.config.microstructure.ofi_window_n,
                eps=self.config.microstructure.zscore_eps,
                trade_window_sec=self.config.microstructure.trade_window_sec,
            )
            self.microstructure_publisher = MicrostructurePublisher(
                self.publisher,
                self.bus,
                self.writer,
                event_type=self.config.microstructure.publish_topic_suffix,
            )
        if self.config.lockbot.enabled:
            self.lockbot_publisher = LockbotPublisher(self.publisher, self.bus, self.writer)
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
        self.orderbook: Optional[OrderBookAggregator] = (
            OrderBookAggregator(
                self.config.orderbook,
                self.publisher,
                self.bus,
                self.snapshot_cache,
                microstructure=self.microstructure_analyzer,
                micro_publisher=self.microstructure_publisher,
            )
            if self.config.orderbook.enabled
            else None
        )
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
        self._repair_timer_task: Optional[asyncio.Task] = None
        self._account_lock = asyncio.Lock()

    async def start(self) -> None:
        logging.basicConfig(level=self.config.log_level)
        if self.writer:
            await self.writer.start()
        if self.orderbook:
            await self.orderbook.start()
        await self._status_reporter.start()
        if self._status_reporter.task:
            self._tasks.append(self._status_reporter.task)
        self.snapshot_server.start()
        await self._publish_initial_account_snapshot()
        self._start_account_streams()
        for feed in self.feeds:
            await feed.start()
        if self.config.account_runtime.repair_interval_sec > 0:
            self._repair_timer_task = asyncio.create_task(self._account_repair_timer())
            self._tasks.append(self._repair_timer_task)
        self._tasks.extend(
            [
                asyncio.create_task(self._dispatcher_loop()),
                asyncio.create_task(self._heartbeat_loop()),
            ]
        )
        if self.writer:
            self._tasks.append(asyncio.create_task(self._status_loop()))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.stop()))
            except NotImplementedError:
                logging.debug("Signal handlers not supported for %s", sig)
        await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        for feed in self.feeds:
            await feed.stop()
        self.publisher.close()
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
        if self._repair_timer_task:
            self._repair_timer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._repair_timer_task
        await self._account_repair_manager.stop()
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _dispatcher_loop(self) -> None:
        while not self._stop_event.is_set():
            event = await self.bus.get_event()
            key = (getattr(event, "symbol", None), getattr(event, "event_type", ""))
            self._last_event_ts[key] = getattr(event, "ts_ns", time.time_ns())
            self.snapshot_cache.update(event)
            self.publisher.publish(event)
            if self.microstructure_analyzer and isinstance(event, TradeEvent):
                self.microstructure_analyzer.update_trade(event.ts_ns, event.size)
            if self.lockbot_engine and isinstance(event, TradeEvent):
                ts_event_ms = int(event.ts_ns / 1_000_000)
                taker_side = str(event.taker_side or "").lower()
                is_buyer_maker = True if taker_side == "sell" else False if taker_side == "buy" else None
                self.lockbot_engine.on_trade(
                    symbol=event.symbol,
                    price=event.price,
                    qty=event.size,
                    ts_event_ms=ts_event_ms,
                    is_buyer_maker=is_buyer_maker,
                    agg_trade_id=event.seq,
                    source="binance_ws",
                )
            if self.writer:
                await self.writer.enqueue(event)

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
        if self.writer:
            metrics = self.writer.metrics
            status["tsdb"] = {
                "written_rows": metrics.written_rows,
                "dropped_rows": metrics.dropped_rows,
                "last_flush_ts": metrics.last_flush_ts,
                "errors": metrics.errors,
                "queue_depth": self.writer.queue_depth(),
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
                existed = bool(order_id and order_id in self.account_state.spot_open_orders)
                delta = self.account_state.apply_spot_execution_report(payload)
                if delta and order_id and delta.patch.spot and delta.patch.spot.orders_update:
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
                existed = bool(order_id and order_id in self.account_state.usdm_open_orders)
                delta = self.account_state.apply_usdm_ORDER_TRADE_UPDATE(payload)
                if delta and order_id and delta.patch.usdm and delta.patch.usdm.orders_update:
                    status = delta.patch.usdm.orders_update[0].status
                    repair_needed = status in FINAL_ORDER_STATUSES and not existed
        if delta:
            self.account_publisher.publish_delta(delta)
        if repair_needed:
            await self._account_repair_manager.request_repair()

    async def _handle_account_reconnect(self) -> None:
        await self._account_repair_manager.request_repair()

    async def _account_repair_timer(self) -> None:
        interval = self.config.account_runtime.repair_interval_sec
        while not self._stop_event.is_set():
            await asyncio.sleep(interval)
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
        asyncio.run(run())
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
        snapshot = await asyncio.to_thread(self._state.build_snapshot, self._symbols, self._include_market)
        self._publisher.publish_snapshot(snapshot)
        return snapshot


if __name__ == "__main__":
    main()
