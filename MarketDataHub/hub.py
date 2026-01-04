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
from typing import Callable, Dict, Optional, Tuple

from MarketDataHub.bus.event_bus import EventBus
from MarketDataHub.config import HubConfig
from MarketDataHub.feeds.binance_futures import BinanceFuturesFeed
from MarketDataHub.feeds.binance_spot import BinanceSpotFeed
from MarketDataHub.ipc.publisher import ZmqPublisher
from MarketDataHub.ipc.snapshot_server import SnapshotCache, SnapshotServer
from MarketDataHub.models import HeartbeatEvent, Priority
from MarketDataHub.spool.status import summarize_spool
from MarketDataHub.orderbook.aggregator import OrderBookAggregator
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
        self.orderbook: Optional[OrderBookAggregator] = (
            OrderBookAggregator(self.config.orderbook, self.publisher, self.bus, self.snapshot_cache)
            if self.config.orderbook.enabled
            else None
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
        for feed in self.feeds:
            await feed.start()
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


if __name__ == "__main__":
    main()
