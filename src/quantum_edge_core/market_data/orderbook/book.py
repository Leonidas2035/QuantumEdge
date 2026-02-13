"""In-memory order book supporting incremental snapshots."""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from quantum_edge_core.market_data.models.orderbook import DepthLevel


class OrderBookSide:
    def __init__(self, descending: bool = True) -> None:
        self._levels: Dict[float, float] = {}
        self._descending = descending

    def update(self, price: float, qty: float) -> None:
        if qty <= 0:
            self._levels.pop(price, None)
            return
        self._levels[price] = qty

    def snapshot(self, levels: Iterable[Tuple[float, float]]) -> None:
        self._levels.clear()
        for price, qty in levels:
            if qty <= 0:
                continue
            self._levels[price] = qty

    def top_n(self, n: int) -> List[DepthLevel]:
        sorted_levels = sorted(self._levels.items(), key=lambda item: item[0], reverse=self._descending)
        return [DepthLevel(price=price, qty=qty) for price, qty in sorted_levels[:n]]

    def best(self) -> Tuple[float, float] | None:
        if not self._levels:
            return None
        price = max(self._levels.keys()) if self._descending else min(self._levels.keys())
        return price, self._levels[price]

    def prune(self, max_levels: int) -> None:
        if len(self._levels) <= max_levels:
            return
        sorted_keys = sorted(self._levels.keys(), reverse=self._descending)
        for price in sorted_keys[max_levels:]:
            self._levels.pop(price, None)


class OrderBook:
    def __init__(self, symbol: str, cap_levels: int = 256) -> None:
        self.symbol = symbol
        self.bids = OrderBookSide(descending=True)
        self.asks = OrderBookSide(descending=False)
        self._cap_levels = cap_levels

    def apply_snapshot(
        self,
        bids: Iterable[Tuple[float, float]],
        asks: Iterable[Tuple[float, float]],
    ) -> None:
        self.bids.snapshot(bids)
        self.asks.snapshot(asks)
        self._prune()

    def apply_delta(
        self,
        bids: Iterable[Tuple[float, float]],
        asks: Iterable[Tuple[float, float]],
    ) -> None:
        for price, qty in bids:
            self.bids.update(price, qty)
        for price, qty in asks:
            self.asks.update(price, qty)
        self._prune()

    def top_n(self, n: int) -> Tuple[List[DepthLevel], List[DepthLevel]]:
        return self.bids.top_n(n), self.asks.top_n(n)

    def best_bid(self) -> Tuple[float, float] | None:
        return self.bids.best()

    def best_ask(self) -> Tuple[float, float] | None:
        return self.asks.best()

    def _prune(self) -> None:
        self.bids.prune(self._cap_levels)
        self.asks.prune(self._cap_levels)
