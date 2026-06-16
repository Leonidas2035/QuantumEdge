from decimal import Decimal

"""
Position Manager.
Tracks the virtual state of the bot's position (Inventory).
Responsible for PnL tracking and local state updates before Exchange confirmation.
"""

from dataclasses import dataclass


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
    """

    def __init__(
        self,
        mode: str = "scalper_v1",
        initial_quote_balance: float | None = None,
    ) -> None:
        self.state = PositionState()
        self.mode = mode

        # Override default quote_balance if caller provides one
        if initial_quote_balance is not None and initial_quote_balance > 0:
            self.state.quote_balance = Decimal(str(initial_quote_balance))

        # Initialize telemetry explicitly (lazy) to prevent cyclic imports
        from quantum_edge_core.ai_scalper_bot.bot.infrastructure.questdb_telemetry import (
            QuestDbTelemetry,
        )

        self.telemetry = QuestDbTelemetry()

    def simulate_fill(
        self, price: Decimal, qty: Decimal, side: str, symbol: str = "BTCUSDT"
    ):
        """
        Updates the position state assuming a fill occurred.

        Args:
            price: Fill price.
            qty: Fill quantity (always positive).
            side: 'BUY' or 'SELL'.
        """
        if side == "BUY" or "BUY" in side:
            # Weighted Average Price logic
            total_cost = (self.state.avg_price * self.state.total_qty) + (price * qty)
            new_qty = self.state.total_qty + qty

            self.state.avg_price = (
                total_cost / new_qty if new_qty > 0 else Decimal("0.0")
            )
            self.state.total_qty = new_qty

            # Reduce quote balance for SPOT
            if self.mode == "spot_grid":
                self.state.quote_balance -= price * qty

        elif side == "SELL" or "SELL" in side:
            # Pnl realization
            if self.state.total_qty > 0 and self.state.avg_price > 0:
                realized_pnl = (price - self.state.avg_price) * qty
                self.telemetry.log_realized_trade(
                    symbol, side, price, qty, realized_pnl
                )

            new_qty = max(Decimal("0.0"), self.state.total_qty - qty)
            if new_qty == Decimal("0.0"):
                self.state.avg_price = Decimal("0.0")

            self.state.total_qty = new_qty

            # Increase quote balance for SPOT
            if self.mode == "spot_grid":
                self.state.quote_balance += price * qty

        elif side == "HEDGE_SHORT":
            # In a Hedged state, we might effectively zero out delta.
            # For this simple manager, we assume separate tracking or net delta.
            # If we open a short hedge, we are effectively neutral.
            # Let's treat 'HEDGE_SHORT' as effectively reducing delta to 0 or negative.
            # Since logic says "OPEN_SHORT (Hedge)", we might be tracking net delta.
            # For now, let's just track it as a special state or ignore effect on 'Long' Avg Price.
            pass

    def calculate_order_qty(self, price: float) -> float:
        """Calculate BTC order quantity for a single grid level.

        Returns base-asset qty rounded to 4 decimals with a floor of
        0.0001 BTC (Binance Spot LOT_SIZE minimum for BTCUSDT).
        """
        import logging

        logger = logging.getLogger("PositionManager")
        if self.state.quote_balance <= 0:
            logger.warning("ZERO BALANCE — using fallback")
            self.state.quote_balance = Decimal("10000.0")

        if price <= 0:
            logger.error("ZERO PRICE passed to calculate_order_qty!")
            return 0.0001

        exposure_pct = Decimal(str(getattr(self, "exposure_pct", 0.5)))
        total_levels = Decimal(str(getattr(self, "total_levels", 30)))
        price_d = Decimal(str(price))

        capital_per_level = (self.state.quote_balance * exposure_pct) / total_levels
        raw_qty = capital_per_level / price_d

        # Round to 4 decimals (BTC precision) and enforce minimum
        qty = float(raw_qty.quantize(Decimal("0.0001")))
        qty = max(qty, 0.0001)  # Binance LOT_SIZE minimum for BTCUSDT

        logger.info(
            "QTY_CALC: bal=%.2f | exposure=%.2f | levels=%d | capital/lvl=%.2f | "
            "price=%.2f | raw=%.6f → final_qty=%.6f",
            float(self.state.quote_balance),
            float(exposure_pct),
            int(total_levels),
            float(capital_per_level),
            price,
            float(raw_qty),
            qty,
        )
        return qty

    def get_drawdown_pct(self, current_price: Decimal) -> Decimal:
        """
        Calculates negative drift percentage from average entry.
        Returns positive float for drawdown (e.g. 0.02 for -2%).
        """
        if self.state.total_qty == 0 or self.state.avg_price == 0:
            return Decimal("0.0")

        # Drawdown = (Entry - Current) / Entry
        # If Price is higher (Profit), result is negative.
        # If Price is lower (Loss), result is positive.

        raw_diff = (self.state.avg_price - current_price) / self.state.avg_price
        return max(Decimal("0.0"), raw_diff)

    @property
    def total_qty(self) -> float:
        return self.state.total_qty

    @property
    def avg_price(self) -> float:
        return self.state.avg_price
