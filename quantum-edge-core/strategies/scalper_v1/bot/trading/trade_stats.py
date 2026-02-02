import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TradeRecord:
    timestamp: float
    pnl: float
    symbol: Optional[str] = None
    side: Optional[str] = None


class TradeStats:
    def __init__(self):
        self.trades: List[TradeRecord] = []

    def record(self, pnl: float, ts: float = None, symbol: Optional[str] = None, side: Optional[str] = None):
        self.trades.append(TradeRecord(timestamp=ts or time.time(), pnl=pnl, symbol=symbol, side=side))

    def _recent(self, window_seconds: float) -> List[TradeRecord]:
        cutoff = time.time() - window_seconds
        return [t for t in self.trades if t.timestamp >= cutoff]

    def loss_streak(self, window_trades: int, max_losses: int) -> int:
        recent = self.trades[-window_trades:]
        losses = [t for t in recent if t.pnl < 0]
        return len(losses)

    def trades_last_hour(self) -> int:
        return len(self._recent(3600))

    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    def total_pnl_window(self, window_seconds: float) -> float:
        return sum(t.pnl for t in self._recent(window_seconds))

    def max_drawdown_abs(self) -> float:
        """
        Computes max drawdown of cumulative PnL series (absolute, not percent).
        """
        drawdown = 0.0
        peak = 0.0
        cumulative = 0.0
        for t in self.trades:
            cumulative += t.pnl
            peak = max(peak, cumulative)
            drawdown = min(drawdown, cumulative - peak)
        return abs(drawdown)
