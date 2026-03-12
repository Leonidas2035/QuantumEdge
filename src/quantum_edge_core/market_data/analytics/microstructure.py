"""
src/quantum_edge_core/market_data/analytics/microstructure.py

Microstructure Analysis Engine.
"""

import structlog
from typing import Optional
from quantum_edge_core.events import (
    MarketTrade,
    OrderBookUpdate,
    LargeBlockEvent,
    MicrostructureMetrics,
)

logger = structlog.get_logger()


class MicrostructureAnalyzer:
    def __init__(self, whale_threshold: float = 20.0):
        self.whale_threshold = whale_threshold
        self.logger = logger.bind(component="MicrostructureAnalyzer")
        # State
        self.last_trade_ts = 0.0
        self.current_spread_bps = 0.0

    def process_trade(self, trade: MarketTrade) -> Optional[LargeBlockEvent]:
        """
        Detect large trades.
        """
        # Simple threshold check
        if trade.quantity >= self.whale_threshold:
            self.logger.info("Whale Detected", quantity=trade.quantity, side=trade.side)
            return LargeBlockEvent(
                symbol=trade.symbol,
                price=trade.price,
                quantity=trade.quantity,
                side=trade.side,
                timestamp=trade.timestamp,
            )
        return None

    def process_book(self, update: OrderBookUpdate) -> Optional[MicrostructureMetrics]:
        """
        Calculate book metrics.
        """
        if not update.bids or not update.asks:
            return None

        # Top of Book
        best_bid = update.bids[0][0]
        best_ask = update.asks[0][0]

        # Calculate Spread
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_bps = (spread / mid_price) * 10000

        # Calculate Imbalance (Top 5 levels)
        bid_vol = sum(level[1] for level in update.bids[:5])
        ask_vol = sum(level[1] for level in update.asks[:5])

        total_vol = bid_vol + ask_vol
        if total_vol == 0:
            imbalance = 0.0
        else:
            imbalance = (bid_vol - ask_vol) / total_vol

        return MicrostructureMetrics(
            symbol=update.symbol,
            imbalance=imbalance,
            spread_bps=spread_bps,
            timestamp=update.timestamp,
        )

    def detect_liquidity_walls(
        self, order_book: dict, avg_volume_multiplier: float = 5.0
    ) -> list[dict]:
        """
        Detect Level-2 Liquidity Walls (Blocks).
        Identifies price levels where the volume exceeds average_volume * avg_volume_multiplier.
        Psychological round numbers (modulo 100 or 1000) have increased priority/weight.
        """
        walls = []
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        all_levels = bids + asks
        if not all_levels:
            return walls

        total_volume = sum(level[1] for level in all_levels)
        avg_volume = total_volume / len(all_levels)

        threshold = avg_volume * avg_volume_multiplier

        def process_side(levels, side):
            for price, qty in levels:
                is_round = price % 100 == 0
                # If round number, lower the threshold requirement by 20%
                adjusted_threshold = threshold * 0.8 if is_round else threshold

                if qty >= adjusted_threshold:
                    walls.append(
                        {
                            "price": float(price),
                            "qty": float(qty),
                            "side": side,
                            "is_round": is_round,
                        }
                    )
                    self.logger.info(
                        "LIQUIDITY_WALL_DETECTED at %s (vol=%s)", price, qty
                    )

        process_side(bids, "BID")
        process_side(asks, "ASK")

        return walls
