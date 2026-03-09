"""Order book aggregator that publishes depth and whale wall signals."""

from __future__ import annotations

import asyncio
import random
from contextlib import suppress
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.config import OrderbookConfig
from quantum_edge_core.market_data.ipc.publisher import ZmqPublisher
from quantum_edge_core.market_data.models import (
    DepthL2Event,
    DEPTH_EVENT_TYPE,
    Priority,
    WallLevel,
    WALLS_EVENT_TYPE,
    WallsEvent,
    WallsSummary,
)
from quantum_edge_core.market_data.models.orderbook import DepthLevel
from quantum_edge_core.market_data.orderbook.book import OrderBook
from quantum_edge_core.market_data.ipc.snapshot_server import SnapshotCache
from quantum_edge_core.market_data.microstructure.ofi import MicrostructureAnalyzer
from quantum_edge_core.market_data.microstructure.publisher import (
    MicrostructurePublisher,
)


@dataclass
class AggregatorStats:
    orderbook_updates_total: int = 0
    depth_publish_total: int = 0
    walls_publish_total: int = 0
    orderbook_resync_total: int = 0


class OrderBookAggregator:
    """Aggregates depth updates and publishes depth_l2 + walls."""

    def __init__(
        self,
        config: OrderbookConfig,
        publisher: ZmqPublisher,
        bus: EventBus,
        snapshot_cache: SnapshotCache,
        microstructure: MicrostructureAnalyzer | None = None,
        micro_publisher: MicrostructurePublisher | None = None,
    ) -> None:
        self._config = config
        self._publisher = publisher
        self._bus = bus
        self._snapshot_cache = snapshot_cache
        self._books: Dict[str, OrderBook] = {
            symbol: OrderBook(symbol) for symbol in config.symbols
        }
        self._running = False
        self._publish_interval = max(
            getattr(self._config, "publish_interval_ms", 100) / 1000.0, 0.05
        )
        self._stats = AggregatorStats()
        self._last_depth_ts: Dict[str, int] = {}
        self._last_walls_ts: Dict[str, int] = {}
        self._tasks: List[asyncio.Task] = []
        self._microstructure = microstructure
        self._micro_publisher = micro_publisher

    @property
    def stats(self) -> AggregatorStats:
        return self._stats

    @property
    def last_depth_ts(self) -> Dict[str, int]:
        return dict(self._last_depth_ts)

    @property
    def last_walls_ts(self) -> Dict[str, int]:
        return dict(self._last_walls_ts)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._publisher_loop()),
            asyncio.create_task(self._synthetic_feed()),
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def apply_snapshot(
        self,
        symbol: str,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
    ) -> None:
        book = self._books.get(symbol)
        if not book:
            return
        book.apply_snapshot(bids, asks)
        self._stats.orderbook_resync_total += 1
        if self._microstructure:
            self._microstructure.mark_resync()

    def apply_delta(
        self,
        symbol: str,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
    ) -> None:
        book = self._books.get(symbol)
        if not book:
            return
        book.apply_delta(bids, asks)
        self._stats.orderbook_updates_total += 1

    def process_book(
        self,
        bids: List[List[float]],
        asks: List[List[float]],
        current_price: float,
    ) -> Dict[str, Any]:
        """Provides backward compatibility with the legacy order book format."""
        return {"bids": bids, "asks": asks}

    def _next_seq(self, symbol: str, event_type: str) -> int:
        return self._bus.assign_sequence(symbol, event_type)

    async def _publisher_loop(self) -> None:
        while self._running:
            for symbol in self._books:
                await self._publish_depth(symbol)
                if self._config.walls.enabled:
                    await self._publish_walls(symbol)
            await asyncio.sleep(self._publish_interval)

    async def _publish_depth(self, symbol: str) -> None:
        book = self._books[symbol]
        bids, asks = book.top_n(self._config.top_n_levels)
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        mid = None
        spread = None
        if best_bid and best_ask:
            _, bid_qty = best_bid
            _, ask_qty = best_ask
            mid = (best_bid[0] + best_ask[0]) / 2.0
            spread = best_ask[0] - best_bid[0]
        event = DepthL2Event(
            ts_ns=time.time_ns(),
            symbol=symbol,
            event_type=DEPTH_EVENT_TYPE,
            seq=self._next_seq(symbol, DEPTH_EVENT_TYPE),
            priority=Priority.L1,
            bids=bids,
            asks=asks,
            mid=mid,
            spread=spread,
        )
        await self._publisher.publish(f"market.depth.{symbol.lower()}", event)
        self._last_depth_ts[symbol] = event.ts_ns
        self._stats.depth_publish_total += 1
        if self._config.snapshot.include_depth:
            self._snapshot_cache.update(event)
        if self._microstructure and self._micro_publisher and best_bid and best_ask:
            snapshot = self._microstructure.update_book(
                symbol=symbol,
                bid_px=best_bid[0],
                bid_qty=best_bid[1],
                ask_px=best_ask[0],
                ask_qty=best_ask[1],
                ts_event=event.ts_ns,
            )
            if snapshot:
                self._micro_publisher.publish(snapshot)

    async def _publish_walls(self, symbol: str) -> None:
        book = self._books[symbol]
        bids, asks = book.top_n(self._config.top_n_levels)
        mid = None
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid and best_ask:
            mid = (best_bid[0] + best_ask[0]) / 2.0
        threshold_qty = self._config.walls.per_symbol_threshold_qty.get(symbol)
        threshold_notional = self._config.walls.default_threshold_notional_usd
        bids_walls = self._filter_walls(
            bids, mid, threshold_qty, threshold_notional, "bid"
        )
        asks_walls = self._filter_walls(
            asks, mid, threshold_qty, threshold_notional, "ask"
        )
        summary = WallsSummary(
            count_bid_walls=len(bids_walls),
            count_ask_walls=len(asks_walls),
            max_wall_qty=max((wl.qty for wl in bids_walls + asks_walls), default=0.0),
            nearest_wall_distance_bps=self._nearest_distance(bids_walls + asks_walls),
        )
        event = WallsEvent(
            ts_ns=time.time_ns(),
            symbol=symbol,
            event_type=WALLS_EVENT_TYPE,
            seq=self._next_seq(symbol, WALLS_EVENT_TYPE),
            priority=Priority.L1,
            bids_walls=bids_walls,
            asks_walls=asks_walls,
            threshold_qty=threshold_qty,
            threshold_notional_usd=threshold_notional,
            summary=summary,
        )
        await self._publisher.publish(f"market.walls.{symbol.lower()}", event)
        self._last_walls_ts[symbol] = event.ts_ns
        self._stats.walls_publish_total += 1
        if self._config.snapshot.include_walls:
            self._snapshot_cache.update(event)

    def _filter_walls(
        self,
        levels: List[DepthLevel],
        mid: Optional[float],
        threshold_qty: Optional[float],
        threshold_notional: Optional[float],
        side: str,
    ) -> List[WallLevel]:
        walls: List[WallLevel] = []
        for level in levels[: self._config.walls.top_k]:
            meets_qty = threshold_qty is not None and level.qty >= threshold_qty
            notional = level.price * level.qty
            meets_notional = (
                threshold_notional is not None and notional >= threshold_notional
            )
            if not (meets_qty or meets_notional):
                continue
            distance = None
            if mid:
                distance = abs(level.price - mid) / mid * 10000
                if (
                    self._config.walls.max_distance_bps
                    and distance > self._config.walls.max_distance_bps
                ):
                    continue
            walls.append(
                WallLevel(
                    price=level.price,
                    qty=level.qty,
                    notional=notional,
                    distance_bps=distance,
                )
            )
        return walls

    def _nearest_distance(self, walls: List[WallLevel]) -> Optional[float]:
        distances = [
            wall.distance_bps for wall in walls if wall.distance_bps is not None
        ]
        if not distances:
            return None
        return min(distances)

    async def _synthetic_feed(self) -> None:
        """Simple generator to keep order book moving while real feed is absent."""
        base_prices = {
            symbol: 40000.0 if "BTC" in symbol else 3000.0 for symbol in self._books
        }
        step = 0.1
        while self._running:
            for symbol, book in self._books.items():
                mid = base_prices[symbol]
                bids = [
                    (mid - i * step, 5.0 + i * 0.5)
                    for i in range(self._config.top_n_levels)
                ]
                asks = [
                    (mid + i * step, 5.0 + i * 0.3)
                    for i in range(self._config.top_n_levels)
                ]
                # introduce jitter
                self.apply_delta(
                    symbol,
                    [(price, qty + random.uniform(-0.5, 0.5)) for price, qty in bids],
                    [(price, qty + random.uniform(-0.5, 0.5)) for price, qty in asks],
                )
                base_prices[symbol] *= 1 + random.uniform(-0.0002, 0.0002)
            await asyncio.sleep(self._publish_interval / 2)
