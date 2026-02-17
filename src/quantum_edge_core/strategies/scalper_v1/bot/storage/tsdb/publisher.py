from __future__ import annotations

import time
from typing import Optional

from bot.storage.event_bus import EventPriority
from bot.storage.tsdb.sink import get_tsdb_sink


def _ts_ms(value: Optional[int | float]) -> int:
    if value is None:
        return int(time.time() * 1000)
    return int(value)


class TsdbPublisher:
    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self._sink = get_tsdb_sink()

    async def publish_signal(
        self, symbol: str, signal: str, score: float, model: Optional[str], ts_ms: int
    ) -> None:
        await self._sink.publish(
            {
                "table": "signals",
                "bot_id": self.bot_id,
                "symbol": symbol,
                "signal": signal,
                "score": float(score),
                "model": model,
                "ts": _ts_ms(ts_ms),
            },
            priority=EventPriority.NORMAL,
        )

    async def publish_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: float,
        status: str,
        client_order_id: Optional[str],
        exchange_order_id: Optional[str],
        ts_ms: int,
    ) -> None:
        await self._sink.publish(
            {
                "table": "orders",
                "bot_id": self.bot_id,
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "qty": float(qty),
                "price": float(price),
                "status": status,
                "client_order_id": client_order_id,
                "exchange_order_id": exchange_order_id,
                "ts": _ts_ms(ts_ms),
            },
            priority=EventPriority.HIGH,
        )

    async def publish_fill(
        self,
        symbol: str,
        price: float,
        qty: float,
        fee: Optional[float],
        fee_asset: Optional[str],
        client_order_id: Optional[str],
        ts_ms: int,
    ) -> None:
        await self._sink.publish(
            {
                "table": "fills",
                "bot_id": self.bot_id,
                "symbol": symbol,
                "client_order_id": client_order_id,
                "price": float(price),
                "qty": float(qty),
                "fee": float(fee) if fee is not None else None,
                "fee_asset": fee_asset,
                "ts": _ts_ms(ts_ms),
            },
            priority=EventPriority.HIGH,
        )

    async def publish_position(
        self,
        symbol: str,
        position: float,
        entry_price: Optional[float],
        unrealized_pnl: Optional[float],
        leverage: Optional[float],
        ts_ms: int,
    ) -> None:
        await self._sink.publish(
            {
                "table": "positions",
                "bot_id": self.bot_id,
                "symbol": symbol,
                "position": float(position),
                "entry_price": float(entry_price) if entry_price is not None else None,
                "unrealized_pnl": (
                    float(unrealized_pnl) if unrealized_pnl is not None else None
                ),
                "leverage": float(leverage) if leverage is not None else None,
                "ts": _ts_ms(ts_ms),
            },
            priority=EventPriority.NORMAL,
        )

    async def publish_equity(
        self,
        equity: Optional[float],
        balance: Optional[float],
        drawdown: Optional[float],
        ts_ms: int,
    ) -> None:
        await self._sink.publish(
            {
                "table": "equity",
                "bot_id": self.bot_id,
                "equity": float(equity) if equity is not None else None,
                "balance": float(balance) if balance is not None else None,
                "drawdown": float(drawdown) if drawdown is not None else None,
                "ts": _ts_ms(ts_ms),
            },
            priority=EventPriority.NORMAL,
        )
