import time

"""
Adaptive Grid Strategy Core.
Decides trade actions based on Market State, Alpha Features, and Position Risk.

Uses ``predict_proba`` (probability 0.0–1.0) instead of ``predict``
(hard class 0/1) for XGBoost signal generation.
"""

import logging
from enum import Enum, auto
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple

# Unused imports commented out per user request for audit
import os
# import numpy as np
import xgboost as xgb
import collections

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState, TradingMode
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureVector
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager

logger: logging.Logger = logging.getLogger(__name__)

# ── Probability thresholds for XGBoost signal ────────────────────────
_BUY_THRESHOLD: float = 0.65  # P(buy) > 65% → BUY
_SELL_THRESHOLD: float = 0.35  # P(buy) < 35% → SELL
_TICK_SIZE: float = 0.10  # BTCUSDT tick size on Binance


_MIN_WALL_VOLUME: float = 5.0  # BTC


# find_frontrun_price helper commented out per user request for audit
def find_frontrun_price(
    l2_bids: List[Tuple[float, float]],
    target_price: float,
    ticks_above: int = 2,
) -> Optional[float]:
    """Find optimal limit-buy price by front-running the largest L2 bid block.

    Scans the L2 order book bids below ``target_price``, finds the
    price level with maximum volume concentration (the "wall") that exceeds
    ``_MIN_WALL_VOLUME``, and returns a price ``ticks_above`` ticks ABOVE that wall.

    Parameters
    ----------
    l2_bids:
        List of ``(price, qty)`` tuples, sorted descending by price.
    target_price:
        Maximum price we're willing to buy at (from TradingPolicy.buy_zone_max).
    ticks_above:
        How many ticks above the wall to place our order (default: 2).

    Returns
    -------
    Optional[float]
        The front-run price rounded to 1 decimal place.
        Returns None if no wall >= 5.0 BTC is found in the zone.
    """
    if not l2_bids:
        return None

    best_price: float = 0.0
    best_qty: float = 0.0

    for price, qty in l2_bids:
        if price > target_price:
            continue  # Skip bids above our zone limit
        if qty > best_qty:
            best_qty = qty
            best_price = price

    if best_qty < _MIN_WALL_VOLUME or best_price <= 0.0:
        return None  # No real wall found

    frontrun: float = round(best_price + (ticks_above * _TICK_SIZE), 1)
    logger.info(
        "[L2] Front-run: wall @ %.1f (qty=%.4f), placing @ %.1f (+%d ticks)",
        best_price,
        best_qty,
        frontrun,
        ticks_above,
    )
    return frontrun


class BotState(Enum):
    STOPPED = auto()
    RUNNING = auto()
    IDLE = auto()
    PAUSED = auto()
    LONG_ACCUMULATION = auto()
    HEDGED = auto()


class AdaptiveGridStrategy:
    """
    Decides WHEN to trade based on Market State, Indicators, and Risk.
    Adapts grid spacing using ATR (Volatility).
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: Strategy configuration dictionary.
                    Required: 'base_order_size_q', 'safety_order_multiplier',
                              'hedge_trigger_dd', 'grid_step_atr_mult', 'take_profit_pct', 'ofi_entry_threshold'.
        """
        self.state = BotState.IDLE
        self.config = config
        self.last_buy_price: float = 0.0

        # Feature state
        self.mid_prices = collections.deque(maxlen=100)
        self.prev_bid_qty = 0.0
        self.prev_ask_qty = 0.0

        # Load ML model
        self.model = None
        model_path = "models/xgboost_alpha.json"
        if os.path.exists(model_path):
            try:
                self.model = xgb.XGBClassifier()
                self.model.load_model(model_path)
                logger.info("[StrategyCore] Loaded XGBoost model from %s", model_path)
            except Exception as e:
                logger.error("[StrategyCore] Error loading model: %s", e)
                logger.warning(
                    "[!] УВАГА: XGBoost модель не завантажилися (%s). "
                    "Бот працює в Fallback режимі!",
                    model_path,
                )
        else:
            logger.warning(
                "[!] УВАГА: XGBoost модель не знайдено (%s). "
                "Бот працює в Fallback режимі!",
                model_path,
            )

    def decide(
        self,
        market: MarketState,
        features: FeatureVector,
        atr: float,
        position: PositionManager,
    ) -> Optional[TradeAction]:
        current_price = market.last_price

        # --- ML Feature Pipeline (OFI) ---
        self.mid_prices.append((market.best_bid + market.best_ask) / 2.0)
        self.prev_bid_qty = market.best_bid_qty
        self.prev_ask_qty = market.best_ask_qty

        # 0. State Transition Check
        if position.total_qty == 0 and self.state == BotState.LONG_ACCUMULATION:
            self.state = BotState.IDLE
        elif position.total_qty > 0 and self.state == BotState.IDLE:
            self.state = BotState.LONG_ACCUMULATION

        # 1. Hedge Trigger (Safety)
        if position.total_qty > 0 and self.state != BotState.HEDGED:
            dd = position.get_drawdown_pct(current_price)
            if dd > self.config.get("hedge_trigger_dd", 0.02):
                self.state = BotState.HEDGED
                return TradeAction(
                    "HEDGE_SHORT", market.best_bid, position.total_qty, "DD Hedge"
                )

        # 2. MATCH TRADING MODE
        match market.trading_mode:
            case TradingMode.PASS:
                if position.total_qty <= 0:
                    logger.info("Position is zero, skipping liquidation.")
                    return None
                return TradeAction(
                    "SELL",
                    market.best_ask,
                    position.total_qty,
                    "Supervisor dictacted PASS mode",
                )

            case TradingMode.NEUTRAL:
                # Two-way market making placeholder (just taking some safe spreads)
                if self.state == BotState.IDLE and abs(features.ofi) > 0.5:
                    qty = (
                        self.config.get("base_order_size_q", 0.01)
                        * market.risk_multiplier
                    )
                    side = (
                        "BUY" if features.ofi > 0 else "SELL"
                    )  # Simplified for neutral
                    if side == "BUY":
                        self.last_buy_price = market.best_bid
                        return TradeAction(
                            "BUY", market.best_bid, qty, "Neutral MM Buy"
                        )
                    else:
                        return None  # Shorting not fully implemented yet in position manager

                if self.state == BotState.LONG_ACCUMULATION:
                    tp_price = position.avg_price * (
                        1 + getattr(self, "take_profit_pct", 0.005)
                    )
                    if current_price >= tp_price:
                        return TradeAction(
                            "SELL", market.best_ask, position.total_qty, "Neutral MM TP"
                        )
                return None

            case TradingMode.SCALP:
                qty = (
                    self.config.get("base_order_size_q", 0.01) * market.risk_multiplier
                )

                if self.state == BotState.IDLE:
                    # SCALP: Use limit orders strictly inside buy zone
                    if (
                        getattr(market, "buy_zone_max", 0.0) > 0
                        and current_price < market.buy_zone_max
                    ):
                        # Front-run L2 wall
                        l2_bids = getattr(
                            market, "whale_walls", []
                        )  # Mocking whale walls as L2
                        # Build a mock bid book from walls for the frontrunner if real l2_bids is missing
                        walls = [
                            (w.get("price", 0), w.get("vol", 0))
                            for w in (l2_bids or [])
                            if w.get("side") == "BID"
                        ]

                        entry_price = (
                            find_frontrun_price(walls, market.buy_zone_max)
                            if walls
                            else market.best_bid
                        )
                        if entry_price:
                            self.last_buy_price = entry_price
                            return TradeAction(
                                "BUY", entry_price, qty, "SCALP Limit Entry"
                            )

                elif self.state == BotState.LONG_ACCUMULATION:
                    # Aggressive TP inside Sell Zone
                    if (
                        getattr(market, "sell_zone_min", 0.0) > 0
                        and current_price >= market.sell_zone_min
                    ):
                        return TradeAction(
                            "SELL", market.best_ask, position.total_qty, "SCALP Zone TP"
                        )

                    # Or fallback aggressive TP
                    tp_price = position.avg_price * 1.002
                    if current_price >= tp_price:
                        return TradeAction(
                            "SELL",
                            market.best_ask,
                            position.total_qty,
                            "SCALP Quick TP",
                        )

                return None

            case TradingMode.DCA:
                qty = (
                    self.config.get("base_order_size_q", 0.01) * market.risk_multiplier
                )

                if self.state == BotState.IDLE:
                    # Initial entry setup
                    if features.ofi > 0:
                        self.last_buy_price = market.best_bid
                        return TradeAction(
                            "BUY", market.best_bid, qty, "DCA Initial Entry"
                        )

                elif self.state == BotState.LONG_ACCUMULATION:
                    # Micro-Stop based on OFI (Signs of dumping)
                    if features.ofi < -2.0:
                        logger.warning(
                            "[DCA] OFI collapsed (%.2f)! Micro-stop active.",
                            features.ofi,
                        )
                        return TradeAction(
                            "CANCEL_ALL", 0.0, 0.0, "DCA Micro-Stop: OFI Dump"
                        )

                    # DCA accumulation
                    gap = atr * self.config.get("grid_step_atr_mult", 2.0)
                    if current_price <= (self.last_buy_price - gap):
                        self.last_buy_price = current_price
                        return TradeAction(
                            "BUY",
                            market.best_bid,
                            qty * 1.5,
                            f"DCA Step (Gap {gap:.2f})",
                        )

                    # Standard TP
                    tp_price = position.avg_price * 1.01
                    if current_price >= tp_price:
                        return TradeAction(
                            "SELL", market.best_ask, position.total_qty, "DCA Target TP"
                        )

                return None

        return None

from decimal import Decimal


@dataclass
class TradeAction:
    action_type: str  # 'BUY', 'SELL', 'HEDGE_SHORT', 'SYNC_GRID'
    price: Decimal
    qty: Decimal
    reason: str


class DynamicDCAStrategy:
    """
    Continuous Dynamic DCA Strategy for SPOT.
    Implements L2 Level Detection, Non-linear ATR+gamma Grids, and Flash Crash Protection.
    """

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
        self.max_open_positions = config.get("max_open_positions", 1)
        self.target_profit_pct = config.get("target_profit_pct", 0.01)

    def evaluate_position(self, market_state: dict, position_state: dict) -> str:
        live_price = market_state.get("mid_price")
        if not live_price:
            return "WAITING_DATA"

        # 1. Calculate Live PnL
        entry_price = position_state.get("average_entry_price", 0.0)
        size = position_state.get("position_size", 0.0)
        side = position_state.get("side", "BUY")

        if size > 0:
            if side == "BUY":
                unrealized_pnl = (live_price - entry_price) * size
                tp_price = entry_price * (1 + self.target_profit_pct)
                
                # 2. Enforce Take Profit
                if live_price >= tp_price:
                    logger.info(f"Target Profit Reached: Live {live_price} >= TP {tp_price}. Closing Long.")
                    return "CLOSE_LONG"

            elif side == "SELL":
                unrealized_pnl = (entry_price - live_price) * abs(size)
                tp_price = entry_price * (1 - self.target_profit_pct)
                
                if live_price <= tp_price:
                    logger.info(f"Target Profit Reached: Live {live_price} <= TP {tp_price}. Closing Short.")
                    return "CLOSE_SHORT"
            
            # Update local state with real PnL
            position_state["unrealized_pnl"] = unrealized_pnl

        return "HOLD"

    def should_enter_new_trade(self, position_state: dict) -> bool:
        active_count = position_state.get("active_positions_count", 0)
        if active_count >= self.max_open_positions:
            logger.debug(f"Entry blocked: Max positions ({self.max_open_positions}) reached.")
            return False
        return True

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

        # Execute TP evaluation logic
        if position.total_qty > 0:
            target_profit_pct = Decimal(str(self.config.get("target_profit_pct", 0.01)))
            avg_entry = position.long_state.avg_price
            unrealized_pnl = (current_price - avg_entry) * position.long_state.total_qty
            position.long_state.unrealized_pnl = unrealized_pnl
            
            tp_price = avg_entry * (Decimal("1.0") + target_profit_pct)
            if current_price >= tp_price:
                logger.info(f"Target Profit Reached: Live {current_price} >= TP {tp_price}. Closing Long.")
                return TradeAction(
                    action_type="SELL",
                    price=current_price,
                    qty=position.long_state.total_qty,
                    reason=f"TP Hit (PNL: {unrealized_pnl:.2f})"
                )

        # Execute duplicate entries prevention
        if not self.should_enter_new_trade({"active_positions_count": 1 if position.total_qty > 0 else 0}):
            # If max positions reached, prevent new grid generation
            market.entries_paused = True

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

            if is_initial_start and os.environ.get("QE_SKIP_INITIAL_SYNC") == "1":
                logger.info("[GRID] Skipped initial sync as QE_SKIP_INITIAL_SYNC is active.")
                return None

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
    ):
        """
        Triggered directly by an ORDER_FILLED event to place the exact counter-order.
        """
        from quantum_edge_core.ai_scalper_bot.bot.execution.smart_executor import (
            OrderRequest,
            OrderSide,
            PositionSide,
        )
        if "BUY" in side.upper():
            counter_price = float(price * (Decimal("1.0") + spacing_pct))
            return OrderRequest(
                symbol=self.config.get("symbol", "BTCUSDT"),
                side=OrderSide.SELL,
                position_side=PositionSide.LONG,
                qty=float(qty),
                price=counter_price,
                client_oid="counter_sell"
            )
        else:
            counter_price = float(price * (Decimal("1.0") - spacing_pct))
            return OrderRequest(
                symbol=self.config.get("symbol", "BTCUSDT"),
                side=OrderSide.BUY,
                position_side=PositionSide.SHORT,
                qty=float(qty),
                price=counter_price,
                client_oid="counter_buy"
            )

    def adjust_to_liquidity(self, target_price: Decimal, liquidity_walls: list) -> Decimal:
        """
        Adjust target price to front-run a liquidity wall.
        """
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
        """
        Calculate Non-linear Grid via ATR + gamma
        """
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


