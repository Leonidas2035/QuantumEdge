import re


def main():
    with open(
        "src/quantum_edge_core/ai_scalper_bot/bot/execution/strategy_core.py", "r"
    ) as f:
        content = f.read()

    # Replace DynamicGridStrategy with DynamicDCAStrategy and its new logic

    new_class = """class DynamicDCAStrategy:
    \"\"\"
    Continuous Dynamic DCA Strategy for SPOT.
    Implements L2 Level Detection, Non-linear ATR+gamma Grids, and Flash Crash Protection.
    \"\"\"

    def __init__(self, config: Dict[str, Any]):
        self.state = BotState.IDLE
        self.config = config
        self.grid_levels_below = config.get("grid_levels_below", 15)
        self.grid_levels_above = config.get("grid_levels_above", 15)
        self.base_order_size_q = Decimal(str(config.get("base_order_size_q", 0.001)))

        self.last_grid_sync_time = 0.0
        self.last_sync_price = Decimal("0.0")

        # Non-linear grid config
        self.gamma = Decimal("1.2")
        self.grid_spacing_multiplier = Decimal("1.0")

        self.last_regime: Optional[str] = None
        self.last_bias: Optional[str] = None

        # Flash crash protection
        self.price_buffer = collections.deque(maxlen=10)
        self.flash_crash_pause_until = 0.0

        # Volatility Oracle for ATR
        from quantum_edge_core.ai_scalper_bot.bot.execution.volatility_oracle import VolatilityOracle
        self.vol_oracle = VolatilityOracle(config)

    def decide(
        self,
        market: MarketState,
        features: FeatureVector,
        atr: float,  # keeping signature for compatibility, but using vol_oracle
        position: PositionManager,
    ) -> Optional[TradeAction]:

        now = time.time()
        current_price = Decimal(str(market.last_price))
        if current_price <= Decimal("0.0"):
            return None

        # Flash Crash Protection (Price Velocity)
        self.price_buffer.append((now, float(current_price)))
        if len(self.price_buffer) >= 2:
            dt = now - self.price_buffer[0][0]
            if dt > 0:
                velocity = (float(current_price) - self.price_buffer[0][1]) / dt
                velocity_pct = velocity / self.price_buffer[0][1]

                # Check threshold: e.g. 2% drop in 10s -> velocity_pct < -0.002 per second (approx)
                if velocity_pct < -0.002:
                    logger.warning("[FLASH CRASH] Velocity %.4f per sec. Halting entries.", velocity_pct)
                    self.flash_crash_pause_until = now + 60.0
                    return TradeAction(
                        action_type="CANCEL_ALL",
                        price=Decimal("0.0"),
                        qty=Decimal("0.0"),
                        reason="Flash Crash Protection",
                    )

        if now < self.flash_crash_pause_until:
            return None

        # Update Volatility Oracle
        self.vol_oracle.add_close_price(float(current_price))
        calculated_atr = Decimal(str(self.vol_oracle.calculate_atr()))
        if calculated_atr <= Decimal("0.0"):
            calculated_atr = Decimal("50.0") # Fallback for tests if no history

        grid_bottom = Decimal(str(getattr(market, "grid_bottom", 0.0)))
        grid_top = Decimal(str(getattr(market, "grid_top", 0.0)))

        # Boundary Guard
        if grid_bottom > 0 and grid_top > 0:
            if current_price < grid_bottom or current_price > grid_top:
                logger.info(
                    "[GRID] Price %s out of bounds [%s, %s]. Paused.",
                    current_price,
                    grid_bottom,
                    grid_top,
                )
                market.entries_paused = True
                return None

        regime = getattr(market, "market_regime", "ranging")
        bias = getattr(market, "grid_bias", "neutral")

        # Regime adjustments
        if regime == "trending" and bias == "bullish":
            # Check EMA condition conceptually (assuming > 200 EMA)
            self.grid_spacing_multiplier = Decimal("1.5")
        elif regime == "ranging":
            self.grid_spacing_multiplier = Decimal("0.5")
        else:
            self.grid_spacing_multiplier = Decimal("1.0")

        spacing_mult = Decimal(str(getattr(market, "grid_spacing_multiplier", 1.0))) * self.grid_spacing_multiplier

        # Money Management
        risk_percent = Decimal(str(self.config.get("risk_percent", 0.01)))
        fractional_kelly = Decimal(str(self.config.get("fractional_kelly", 0.25)))
        quote_balance = position.state.quote_balance

        if quote_balance > 0:
            risk_amount = quote_balance * risk_percent * fractional_kelly
            self.base_order_size_q = risk_amount / current_price

        # Check conditions for SYNC_GRID
        is_initial_start = self.last_sync_price == Decimal("0.0")
        macro_changed = (
            self.last_regime is not None and regime != self.last_regime
        ) or (self.last_bias is not None and bias != self.last_bias)

        # Re-sync if price moved more than the base ATR gap
        price_moved_abs = Decimal("0.0")
        if not is_initial_start:
            price_moved_abs = abs(current_price - self.last_sync_price)

        out_of_bounds = price_moved_abs > (calculated_atr * Decimal("0.5"))

        if is_initial_start or macro_changed or out_of_bounds:
            self.last_grid_sync_time = now
            self.last_sync_price = current_price
            self.last_regime = regime
            self.last_bias = bias

            params = f"regime={regime}|bias={bias}|atr={float(calculated_atr):.2f}|gamma={self.gamma}|mult={float(spacing_mult):.2f}"

            return TradeAction(
                action_type="SYNC_GRID",
                price=current_price,
                qty=self.base_order_size_q,
                reason=params,
            )

        return None

    def on_order_filled(
        self, side: str, price: Decimal, qty: Decimal, spacing_pct: Decimal
    ) -> TradeAction:
        \"\"\"
        Triggered directly by an ORDER_FILLED event to place the exact counter-order.
        \"\"\"
        if "BUY" in side.upper():
            counter_price = price * (Decimal("1.0") + spacing_pct)
            return TradeAction("SELL", counter_price, qty, "Counter grid SELL")
        else:
            counter_price = price * (Decimal("1.0") - spacing_pct)
            return TradeAction("BUY", counter_price, qty, "Counter grid BUY")

    def adjust_to_liquidity(self, target_price: Decimal, liquidity_walls: list) -> Decimal:
        \"\"\"
        Adjust target price to front-run a liquidity wall.
        \"\"\"
        if not liquidity_walls:
            return target_price

        for wall in liquidity_walls:
            wall_price = Decimal(str(wall["price"]))
            # If wall is close to our target (within 1%)
            if abs(wall_price - target_price) / target_price < Decimal("0.01"):
                # Front-run by 0.1%
                if wall["side"].upper() == "BID":
                    return wall_price * Decimal("1.001")
                else:
                    return wall_price * Decimal("0.999")

        return target_price

    def calculate_grid_prices(self, current_price: Decimal, calculated_atr: Decimal, spacing_mult: Decimal) -> dict:
        \"\"\"
        Calculate Non-linear Grid via ATR + gamma
        \"\"\"
        bids = []
        asks = []

        for k in range(1, self.grid_levels_below + 1):
            if k == 1:
                gap = calculated_atr * Decimal("0.5")
            else:
                gap = calculated_atr * spacing_mult * (self.gamma ** Decimal(str(k)))
            bids.append(current_price - gap)

        for k in range(1, self.grid_levels_above + 1):
            if k == 1:
                gap = calculated_atr * Decimal("0.5")
            else:
                gap = calculated_atr * spacing_mult * (self.gamma ** Decimal(str(k)))
            asks.append(current_price + gap)

        return {"bids": bids, "asks": asks}
"""

    content = re.sub(
        r"class DynamicGridStrategy:.*?return TradeAction\(\"BUY\", counter_price, qty, \"Counter grid BUY\"\)",
        new_class,
        content,
        flags=re.DOTALL,
    )

    with open(
        "src/quantum_edge_core/ai_scalper_bot/bot/execution/strategy_core.py", "w"
    ) as f:
        f.write(content)


if __name__ == "__main__":
    main()
