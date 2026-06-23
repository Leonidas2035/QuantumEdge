from decimal import Decimal
from dataclasses import dataclass
from typing import Optional, Any
from quantum_edge_core.ai_scalper_bot.bot.execution.smart_executor import PositionSide

"""
Position Manager.
Tracks the virtual state of the bot's position (Inventory).
Responsible for PnL tracking and local state updates before Exchange confirmation.
"""


@dataclass
class PositionState:
    avg_price: Decimal = Decimal("0.0")
    total_qty: Decimal = Decimal("0.0")  # Base asset (e.g. BTC)
    quote_balance: Decimal = Decimal(
        "10000.0"
    )  # Simulated quote asset (e.g. USDT) for SPOT
    unrealized_pnl: Decimal = Decimal("0.0")


class PositionManager:
    """
    Manages the internal position state (Inventory).
    Adapted for SPOT trading (tracks base/quote balances).
    Tracks Long and Short states separately to support Hedge Mode execution.
    """

    def __init__(
        self,
        mode: str = "scalper_v1",
        initial_quote_balance: float | None = None,
    ) -> None:
        self.mode = mode
        self.long_state = PositionState()
        self.short_state = PositionState()

        # Override default quote_balance if caller provides one
        if initial_quote_balance is not None and initial_quote_balance > 0:
            self.long_state.quote_balance = Decimal(str(initial_quote_balance))
            self.short_state.quote_balance = Decimal(str(initial_quote_balance))

        # Initialize telemetry explicitly (lazy) to prevent cyclic imports
        from quantum_edge_core.ai_scalper_bot.bot.infrastructure.questdb_telemetry import (
            QuestDbTelemetry,
        )

        self.telemetry = QuestDbTelemetry()

    @property
    def state(self) -> PositionState:
        # Default/legacy compatibility property pointing to long_state
        return self.long_state

    @state.setter
    def state(self, value: PositionState) -> None:
        self.long_state = value

    def simulate_fill(
        self,
        price: Decimal,
        qty: Decimal,
        side: str,
        symbol: str = "BTCUSDT",
        position_side: Optional[PositionSide | str] = None,
    ) -> None:
        """
        Updates the position state assuming a fill occurred.

        Args:
            price: Fill price.
            qty: Fill quantity (always positive).
            side: 'BUY', 'SELL', 'HEDGE_SHORT', etc.
            symbol: Target symbol.
            position_side: Targeted positionSide for Hedge Mode.
        """
        side_str = side.upper()

        # Resolve targeted position side
        if position_side is None:
            if "SHORT" in side_str:
                target_pos = PositionSide.SHORT
            else:
                target_pos = PositionSide.LONG
        elif isinstance(position_side, str):
            target_pos = PositionSide(position_side.upper())
        else:
            target_pos = position_side

        if target_pos == PositionSide.LONG:
            state = self.long_state
            if "BUY" in side_str:
                # Weighted Average Price logic
                total_cost = (state.avg_price * state.total_qty) + (price * qty)
                new_qty = state.total_qty + qty

                state.avg_price = (
                    total_cost / new_qty if new_qty > 0 else Decimal("0.0")
                )
                state.total_qty = new_qty

                # Reduce quote balance for SPOT
                if self.mode == "spot_grid":
                    state.quote_balance -= price * qty

            elif "SELL" in side_str:
                # Pnl realization
                if state.total_qty > 0 and state.avg_price > 0:
                    realized_pnl = (price - state.avg_price) * qty
                    fees = (state.avg_price * qty * Decimal("0.0005")) + (price * qty * Decimal("0.0005"))
                    self.telemetry.log_realized_trade(
                        symbol, side, float(state.avg_price), float(price), float(qty), float(realized_pnl), float(fees)
                    )

                new_qty = max(Decimal("0.0"), state.total_qty - qty)
                if new_qty == Decimal("0.0"):
                    state.avg_price = Decimal("0.0")

                state.total_qty = new_qty

                # Increase quote balance for SPOT
                if self.mode == "spot_grid":
                    state.quote_balance += price * qty

        elif target_pos == PositionSide.SHORT:
            state = self.short_state
            if "SELL" in side_str:
                # Open/increase Short position (Selling to open)
                total_cost = (state.avg_price * state.total_qty) + (price * qty)
                new_qty = state.total_qty + qty

                state.avg_price = (
                    total_cost / new_qty if new_qty > 0 else Decimal("0.0")
                )
                state.total_qty = new_qty

                if self.mode == "spot_grid":
                    state.quote_balance += price * qty

            elif "BUY" in side_str:
                # Close/reduce Short position (Buying to cover)
                if state.total_qty > 0 and state.avg_price > 0:
                    realized_pnl = (state.avg_price - price) * qty
                    fees = (state.avg_price * qty * Decimal("0.0005")) + (price * qty * Decimal("0.0005"))
                    self.telemetry.log_realized_trade(
                        symbol, side, float(state.avg_price), float(price), float(qty), float(realized_pnl), float(fees)
                    )

                new_qty = max(Decimal("0.0"), state.total_qty - qty)
                if new_qty == Decimal("0.0"):
                    state.avg_price = Decimal("0.0")

                state.total_qty = new_qty

                if self.mode == "spot_grid":
                    state.quote_balance -= price * qty

    def calculate_order_qty(self, price: float) -> float:
        """Calculate BTC order quantity for a single grid level.

        Returns base-asset qty rounded to 4 decimals with a floor of
        0.0002 BTC (Binance Spot LOT_SIZE minimum for BTCUSDT doubled).
        """
        import logging

        logger = logging.getLogger("PositionManager")
        if self.long_state.quote_balance <= 0:
            logger.warning("ZERO BALANCE — using fallback")
            self.long_state.quote_balance = Decimal("10000.0")

        if price <= 0:
            logger.error("ZERO PRICE passed to calculate_order_qty!")
            return 0.0002

        exposure_pct = Decimal(str(getattr(self, "exposure_pct", 0.5)))
        total_levels = Decimal(str(getattr(self, "total_levels", 30)))
        price_d = Decimal(str(price))

        capital_per_level = (self.long_state.quote_balance * exposure_pct) / total_levels
        # Double the order quantity
        raw_qty = (capital_per_level / price_d) * Decimal("2.0")

        # Round to 4 decimals (BTC precision) and enforce minimum
        qty = float(raw_qty.quantize(Decimal("0.0001")))
        qty = max(qty, 0.0002)

        logger.info(
            "QTY_CALC (DOUBLED): bal=%.2f | exposure=%.2f | levels=%d | capital/lvl=%.2f | "
            "price=%.2f | raw=%.6f → final_qty=%.6f",
            float(self.long_state.quote_balance),
            float(exposure_pct),
            int(total_levels),
            float(capital_per_level),
            price,
            float(raw_qty),
            qty,
        )
        return qty

    def get_drawdown_pct(self, current_price) -> float:
        """
        Calculates negative drift percentage from average entry.
        Returns positive float for drawdown (e.g. 0.02 for -2%).
        """
        if self.long_state.total_qty == 0 or self.long_state.avg_price == 0:
            return 0.0

        price_dec = Decimal(str(current_price))
        raw_diff = (self.long_state.avg_price - price_dec) / self.long_state.avg_price
        return float(max(Decimal("0.0"), raw_diff))

    @property
    def total_qty(self) -> Decimal:
        return self.long_state.total_qty

    @property
    def avg_price(self) -> Decimal:
        return self.long_state.avg_price

    def update_from_exchange(self, positions: list, balance: dict) -> None:
        """Update internal state with authoritative data from exchange via CCXT."""
        import logging
        logger = logging.getLogger("PortfolioSynchronizer")
        
        # Parse balances
        usdt_balance = balance.get("USDT", {}).get("free", 0.0)
        if usdt_balance > 0:
            self.long_state.quote_balance = Decimal(str(usdt_balance))
            self.short_state.quote_balance = Decimal(str(usdt_balance))

        # Reset states
        self.long_state.total_qty = Decimal("0.0")
        self.long_state.avg_price = Decimal("0.0")
        self.long_state.unrealized_pnl = Decimal("0.0")
        self.short_state.total_qty = Decimal("0.0")
        self.short_state.avg_price = Decimal("0.0")
        self.short_state.unrealized_pnl = Decimal("0.0")

        # Parse positions
        for pos in positions:
            if float(pos.get("contracts", 0.0)) == 0.0 and float(pos.get("size", 0.0)) == 0.0:
                continue

            size = Decimal(str(pos.get("contracts", pos.get("size", 0.0))))
            entry_price = Decimal(str(pos.get("entryPrice", 0.0)))
            pnl = Decimal(str(pos.get("unrealizedPnl", 0.0)))

            side = pos.get("side", "").upper()
            if side == "LONG" or (side == "" and float(pos.get("size", 0)) > 0):
                self.long_state.total_qty = size
                self.long_state.avg_price = entry_price
                self.long_state.unrealized_pnl = pnl
            elif side == "SHORT" or (side == "" and float(pos.get("size", 0)) < 0):
                self.short_state.total_qty = size
                self.short_state.avg_price = entry_price
                self.short_state.unrealized_pnl = pnl


import asyncio
import ccxt.async_support as ccxt_async
import logging

class PortfolioSynchronizer:
    def __init__(self, api_key: str, api_secret: str, state_store: PositionManager):
        self.exchange = ccxt_async.bingx({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {'defaultType': 'swap'}
        })
        self.state_store = state_store
        self.logger = logging.getLogger("PortfolioSynchronizer")
        
    async def sync_loop(self):
        self.logger.info("Starting authoritative REST API portfolio synchronization loop...")
        while True:
            try:
                # Fetch undeniable truth from Exchange
                positions = await self.exchange.fetch_positions()
                balance = await self.exchange.fetch_balance()
                
                # Update local Portfolio State
                self.state_store.update_from_exchange(positions, balance)
                self.logger.info(f"Portfolio successfully synchronized with BingX REST API. Equity: {balance.get('USDT', {}).get('free', 0.0)}")
                
            except Exception as e:
                self.logger.error(f"Failed to sync portfolio via REST: {e}")
            
            # Sync every 30 seconds
            await asyncio.sleep(30)

