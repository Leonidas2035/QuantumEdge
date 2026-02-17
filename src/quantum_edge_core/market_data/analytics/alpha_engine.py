"""
src/quantum_edge_core/market_data/analytics/alpha_engine.py

Alpha Engine: Comprehensive Market Analytics & Regime Switching.
"""

import time
import collections
import statistics
import structlog
from typing import Optional, Deque

from quantum_edge_core.events import MarketTrade, LargeBlockEvent, MarketMetrics

logger = structlog.get_logger()


class AlphaEngine:
    def __init__(self, symbol: str = "BTCUSDT", rsi_period: int = 14):
        self.symbol = symbol
        self.logger = logger.bind(component="AlphaEngine", symbol=symbol)

        # Microstructure State
        self.buy_vol_1s = 0.0
        self.sell_vol_1s = 0.0
        self.last_ts_1s = time.time()

        # Technical State
        self.prices: Deque[float] = collections.deque(maxlen=100)  # Short term buffer
        self.vwap_cum_vol = 0.0
        self.vwap_cum_pv = 0.0

        # RSI State
        self.rsi_period = rsi_period
        self.gains: Deque[float] = collections.deque(maxlen=rsi_period)
        self.losses: Deque[float] = collections.deque(maxlen=rsi_period)
        self.last_price = 0.0

        # Output State
        self.current_metrics: Optional[MarketMetrics] = None
        self.whale_activity_score = 0.0

    def update_trade(self, trade: MarketTrade) -> Optional[LargeBlockEvent]:
        # 1. Update VWAP
        self.vwap_cum_vol += trade.quantity
        self.vwap_cum_pv += trade.price * trade.quantity

        # 2. Update Flow (Simple Imbalance)
        if trade.side == "buy":
            self.buy_vol_1s += trade.quantity
        else:
            self.sell_vol_1s += trade.quantity

        # Reset Flow window if > 1s
        now = time.time()
        if now - self.last_ts_1s > 1.0:
            self.buy_vol_1s = 0.0
            self.sell_vol_1s = 0.0
            self.last_ts_1s = now

        # 3. RSI Data Point
        if self.last_price > 0:
            change = trade.price - self.last_price
            if change > 0:
                self.gains.append(change)
                self.losses.append(0)
            else:
                self.gains.append(0)
                self.losses.append(abs(change))
        self.last_price = trade.price
        self.prices.append(trade.price)

        # 4. Whale Detection (Integrated)
        if trade.quantity >= 20.0:
            self.whale_activity_score += trade.quantity
            self.logger.info("Whale Detected via AlphaEngine", qty=trade.quantity)
            return LargeBlockEvent(
                symbol=trade.symbol,
                price=trade.price,
                quantity=trade.quantity,
                side=trade.side,
                timestamp=trade.timestamp,
            )

        # Decay Whale Score
        self.whale_activity_score *= 0.999

        return None

    def compute_metrics(self) -> MarketMetrics:
        # Calculate VWAP
        vwap = self.vwap_cum_pv / self.vwap_cum_vol if self.vwap_cum_vol > 0 else 0.0

        # Calculate Imbalance
        total_flow = self.buy_vol_1s + self.sell_vol_1s
        imbalance = (
            (self.buy_vol_1s - self.sell_vol_1s) / total_flow if total_flow > 0 else 0.0
        )

        # Calculate RSI
        avg_gain = statistics.mean(self.gains) if self.gains else 0.0
        avg_loss = statistics.mean(self.losses) if self.losses else 0.0
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        # Determine Regime
        # Simple Logic:
        # Volatility > Threshold -> VOLATILE
        # RSI > 70 or < 30 -> TREND
        # Else -> RANGE

        regime = "RANGE"
        if len(self.prices) >= 10:
            std_dev = statistics.stdev(self.prices)
            if std_dev > (vwap * 0.005):  # 0.5% volatility
                regime = "VOLATILE"
            elif rsi > 70:
                regime = "TREND_UP"
            elif rsi < 30:
                regime = "TREND_DOWN"

        metrics = MarketMetrics(
            symbol=self.symbol,
            regime=regime,
            ofi_1s=self.buy_vol_1s - self.sell_vol_1s,  # Simplified OFI (Volume Delta)
            vwap=vwap,
            imbalance=imbalance,
            whale_activity=self.whale_activity_score,
            timestamp=time.time(),
        )
        self.current_metrics = metrics
        return metrics
