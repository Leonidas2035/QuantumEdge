import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import List, Optional

from bot.engine.decision_engine import Decision


@dataclass
class PaperTrade:
    timestamp: int
    action: str
    price: float
    size: float
    fee: float
    pnl: float


class PaperTrader:
    """
    Minimal paper trading executor: applies decisions, simulates small latency and fees,
    and tracks positions/PnL.
    """

    def __init__(self, fee_bps: float = 2.0, latency_ms_range=(2, 5)):
        self.fee_rate = fee_bps / 10_000  # bps to fraction
        self.latency_ms_range = latency_ms_range
        self.position: float = 0.0
        self.entry_price: Optional[float] = None
        self.realized_pnl: float = 0.0
        self.trades: List[PaperTrade] = []
        self.tp_price: Optional[float] = None
        self.sl_price: Optional[float] = None
        self.bracket_side: Optional[str] = None
        self.trade_stats = None

    async def _latency(self):
        delay = random.uniform(*self.latency_ms_range) / 1000.0
        if delay > 0:
            await asyncio.sleep(delay)

    def _fee(self, notional: float) -> float:
        return abs(notional) * self.fee_rate

    def set_bracket(self, side: str, tp_price: Optional[float], sl_price: Optional[float]) -> bool:
        if not tp_price and not sl_price:
            return False
        self.bracket_side = side.lower()
        self.tp_price = tp_price
        self.sl_price = sl_price
        return True

    def _clear_bracket(self) -> None:
        self.tp_price = None
        self.sl_price = None
        self.bracket_side = None

    def check_brackets(self, price: float, timestamp: int) -> bool:
        if self.position == 0 or (self.tp_price is None and self.sl_price is None):
            return False
        hit = None
        if self.tp_price is not None:
            if (self.position > 0 and price >= self.tp_price) or (self.position < 0 and price <= self.tp_price):
                hit = "tp"
        if self.sl_price is not None:
            if (self.position > 0 and price <= self.sl_price) or (self.position < 0 and price >= self.sl_price):
                hit = hit or "sl"
        if not hit:
            return False
        action = "close_long" if self.position > 0 else "close_short"
        fee = self._fee(price * abs(self.position))
        pnl = (price - self.entry_price) * self.position - fee if self.entry_price else 0.0
        self.realized_pnl += pnl
        self.trades.append(PaperTrade(timestamp, action, price, self.position, fee, pnl))
        self.position = 0.0
        self.entry_price = None
        self._clear_bracket()
        self._record_trade(pnl, "SELL" if self.bracket_side == "buy" else "BUY")
        return True

    async def process(self, decision: Decision, price: float, timestamp: int, symbol: Optional[str] = None):
        await self._latency()

        if decision.action == "hold":
            return

        self.check_brackets(price, timestamp)

        size = decision.size if decision.size else 1.0
        fee = self._fee(price * size)
        pnl = 0.0

        # Close existing position if opposite signal arrives
        if decision.action in ("buy", "close") and self.position < 0:
            pnl = (self.entry_price - price) * abs(self.position) - fee
            self.realized_pnl += pnl
            self.trades.append(PaperTrade(timestamp, "close_short", price, self.position, fee, pnl))
            self.position = 0.0
            self.entry_price = None
            self._clear_bracket()
            self._record_trade(pnl, "BUY")

        if decision.action in ("sell", "close") and self.position > 0:
            pnl = (price - self.entry_price) * abs(self.position) - fee
            self.realized_pnl += pnl
            self.trades.append(PaperTrade(timestamp, "close_long", price, self.position, fee, pnl))
            self.position = 0.0
            self.entry_price = None
            self._clear_bracket()
            self._record_trade(pnl, "SELL")

        # Open new position if flat and actionable
        if decision.action == "buy" and self.position == 0:
            self.position = size
            self.entry_price = price
            self.trades.append(PaperTrade(timestamp, "open_long", price, size, fee, pnl))
            tp_price = getattr(decision, "tp_price", None)
            sl_price = getattr(decision, "sl_price", None)
            if tp_price or sl_price:
                self.set_bracket("buy", tp_price, sl_price)
        elif decision.action == "sell" and self.position == 0:
            self.position = -size
            self.entry_price = price
            self.trades.append(PaperTrade(timestamp, "open_short", price, size, fee, pnl))
            tp_price = getattr(decision, "tp_price", None)
            sl_price = getattr(decision, "sl_price", None)
            if tp_price or sl_price:
                self.set_bracket("sell", tp_price, sl_price)

    def _record_trade(self, pnl: float, side: str) -> None:
        if self.trade_stats:
            try:
                self.trade_stats.record(pnl, time.time(), side=side)
            except Exception:
                pass

    def summary(self):
        open_pnl = 0.0
        if self.position and self.entry_price:
            # mark-to-market with last known trade price from order log
            last_price = self.trades[-1].price if self.trades else self.entry_price
            open_pnl = (last_price - self.entry_price) * self.position
        return {
            "position": self.position,
            "entry_price": self.entry_price,
            "realized_pnl": self.realized_pnl,
            "open_pnl": open_pnl,
            "trades": len(self.trades),
        }

    def process_sync(self, decision: Decision, price: float, timestamp: int, symbol: Optional[str] = None):
        """
        Convenience wrapper for non-async contexts.
        """
        import asyncio
        asyncio.run(self.process(decision, price, timestamp, symbol=symbol))
