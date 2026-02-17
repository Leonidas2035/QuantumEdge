"""
Mock Exchange for Backtesting.
Simulates order fills, fees, and positions.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    timestamp: float
    symbol: str
    side: str
    price: float
    qty: float
    fee: float


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: Optional[float]
    type: str  # LIMIT or MARKET
    status: str
    timestamp: float


class MockExchange:
    def __init__(
        self,
        initial_balance: float = 10000.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0005,
        latency_ms: int = 50,
    ):
        self.balance = initial_balance
        self.equity = initial_balance
        self.positions: Dict[str, float] = {}  # Symbol -> Quantity (+/-)
        self.avg_entry_price: Dict[str, float] = {}

        self.open_orders: Dict[str, Order] = {}
        self.trades: List[Trade] = []

        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.latency_ms = (
            latency_ms  # Simulated delay (not used in simple event loop yet)
        )

        self.current_time = 0.0

    def on_tick(self, event: Dict[str, Any]):
        """
        Processes market data event to fill orders.
        updates current time.
        """
        # Event: 'timestamp' (datetime or iso), 'price'
        # Let's assume timestamp is float or we convert
        # For simulation, just store it
        # event['timestamp'] needs handling if it's datetime
        # For fills: check if Limit orders are crossed

        price = event.get("price")
        if not price:
            return

        # Check simulated fills against OHLC or tick
        # Since we use tick data:
        # Buy Limit fills if Price <= Limit
        # Sell Limit fills if Price >= Limit

        filled_ids = []
        for oid, order in self.open_orders.items():
            if order.status != "NEW":
                continue

            if order.type == "LIMIT":
                if (order.side == "BUY" and price <= order.price) or (
                    order.side == "SELL" and price >= order.price
                ):
                    self._fill_order(order, price, is_maker=True)
                    filled_ids.append(oid)

        for oid in filled_ids:
            del self.open_orders[oid]

    def execute_order(
        self, symbol: str, side: str, qty: float, type: str, price: float = None
    ) -> str:
        """
        Place new order. Returns simulated Order ID.
        """
        order_id = str(uuid.uuid4())

        if type == "MARKET":
            # Instant fill at current price (passed in? No, we need current market price).
            # Limitation: MockExchange needs access to 'current price' state if not passed.
            # Usually backtest engine calls 'execute' then exchange fills immediately using last known price.
            # Or we pass price as 'market price' for simulation.

            # For simplicity: Market orders fill at `price` argument (slippage can be added).
            # If price is None, we can't fill.
            if price is None:
                logger.error("Market order requires current price estimation")
                return None

            # Slippage simulation?
            # price = price * (1 + 0.0001) if buy

            self._fill_immediate(symbol, side, qty, price, order_id)
            return order_id

        elif type == "LIMIT":
            order = Order(
                order_id=order_id,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                type="LIMIT",
                status="NEW",
                timestamp=self.current_time,
            )
            self.open_orders[order_id] = order
            return order_id

    def _fill_immediate(self, symbol, side, qty, price, order_id):
        # Taker trade
        fee = qty * price * self.taker_fee
        self._update_position(symbol, side, qty, price, fee)

        self.trades.append(
            Trade(
                timestamp=self.current_time,
                symbol=symbol,
                side=side,
                price=price,
                qty=qty,
                fee=fee,
            )
        )

    def _fill_order(self, order: Order, fill_price: float, is_maker: bool):
        # Update Order
        order.status = "FILLED"

        # Calculate Fee
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        fee = order.qty * fill_price * fee_rate

        self._update_position(order.symbol, order.side, order.qty, fill_price, fee)

        self.trades.append(
            Trade(
                timestamp=self.current_time,
                symbol=order.symbol,
                side=order.side,
                price=fill_price,
                qty=order.qty,
                fee=fee,
            )
        )

    def _update_position(self, symbol, side, qty, price, fee):
        # Update Balance (deduct fee)
        self.balance -= fee

        current_pos = self.positions.get(symbol, 0.0)
        current_avg = self.avg_entry_price.get(symbol, 0.0)

        # PnL realization logic if reducing position
        # For simplicity: Average Entry Price logic

        if side == "BUY":
            if current_pos >= 0:
                # Adding to Long
                total_cost = (current_pos * current_avg) + (qty * price)
                new_pos = current_pos + qty
                self.avg_entry_price[symbol] = total_cost / new_pos
                self.positions[symbol] = new_pos
            else:
                # Reducing Short
                # Realized PnL = (Entry - Exit) * Qty
                # Entry = current_avg. Exit = price.
                # Short profit if Price < Entry
                covered_qty = min(abs(current_pos), qty)
                pnl = (current_avg - price) * covered_qty
                self.balance += pnl

                new_pos = current_pos + qty
                self.positions[symbol] = new_pos
                if new_pos == 0:
                    self.avg_entry_price[symbol] = 0.0
                elif new_pos > 0:
                    # Flipped to Long
                    remaining = qty - covered_qty
                    self.avg_entry_price[symbol] = price  # New entry for remainder

        elif side == "SELL":
            if current_pos <= 0:
                # Adding to Short
                total_cost = (abs(current_pos) * current_avg) + (qty * price)
                new_pos = current_pos - qty
                self.avg_entry_price[symbol] = total_cost / abs(new_pos)
                self.positions[symbol] = new_pos
            else:
                # Reducing Long
                # Realized PnL = (Exit - Entry) * Qty
                closed_qty = min(current_pos, qty)
                pnl = (price - current_avg) * closed_qty
                self.balance += pnl

                new_pos = current_pos - qty
                self.positions[symbol] = new_pos
                if new_pos == 0:
                    self.avg_entry_price[symbol] = 0.0
                elif new_pos < 0:
                    # Flipped to Short
                    self.avg_entry_price[symbol] = price

    def get_equity(self, current_price: float) -> float:
        """Estimate total equity (Balance + Unrealized PnL)."""
        upnl = 0.0
        for sym, pos in self.positions.items():
            if pos == 0:
                continue
            entry = self.avg_entry_price.get(sym, 0.0)
            if pos > 0:
                upnl += (current_price - entry) * pos
            else:
                upnl += (entry - current_price) * abs(pos)

        return self.balance + upnl
