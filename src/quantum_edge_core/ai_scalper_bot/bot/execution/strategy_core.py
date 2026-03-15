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

import os
import collections
import numpy as np
import xgboost as xgb

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState, TradingMode
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureVector
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager

logger: logging.Logger = logging.getLogger(__name__)

# ── Probability thresholds for XGBoost signal ────────────────────────
_BUY_THRESHOLD: float = 0.65  # P(buy) > 65% → BUY
_SELL_THRESHOLD: float = 0.35  # P(buy) < 35% → SELL
_TICK_SIZE: float = 0.10  # BTCUSDT tick size on Binance


_MIN_WALL_VOLUME: float = 5.0  # BTC


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
    LONG_ACCUMULATION = auto()
    HEDGED = auto()


# class AdaptiveGridStrategy:
#     """
#     Decides WHEN to trade based on Market State, Indicators, and Risk.
#     Adapts grid spacing using ATR (Volatility).
#     """
#
#     def __init__(self, config: Dict[str, Any]):
#         """
#         Args:
#             config: Strategy configuration dictionary.
#                     Required: 'base_order_size_q', 'safety_order_multiplier',
#                               'hedge_trigger_dd', 'grid_step_atr_mult', 'take_profit_pct', 'ofi_entry_threshold'.
#         """
#         self.state = BotState.IDLE
#         self.config = config
#         self.last_buy_price: float = 0.0
#
#         # Feature state
#         self.mid_prices = collections.deque(maxlen=100)
#         self.prev_bid_qty = 0.0
#         self.prev_ask_qty = 0.0
#
#         # Load ML model
#         self.model = None
#         model_path = "models/xgboost_alpha.json"
#         if os.path.exists(model_path):
#             try:
#                 self.model = xgb.XGBClassifier()
#                 self.model.load_model(model_path)
#                 logger.info("[StrategyCore] Loaded XGBoost model from %s", model_path)
#             except Exception as e:
#                 logger.error("[StrategyCore] Error loading model: %s", e)
#                 logger.warning(
#                     "[!] УВАГА: XGBoost модель не завантажилася (%s). "
#                     "Бот працює в Fallback режимі!",
#                     model_path,
#                 )
#         else:
#             logger.warning(
#                 "[!] УВАГА: XGBoost модель не знайдено (%s). "
#                 "Бот працює в Fallback режимі!",
#                 model_path,
#             )
#
#     def decide(
#         self,
#         market: MarketState,
#         features: FeatureVector,
#         atr: float,
#         position: PositionManager,
#     ) -> Optional[TradeAction]:
#         current_price = market.last_price
#
#         # --- ML Feature Pipeline (OFI) ---
#         self.mid_prices.append((market.best_bid + market.best_ask) / 2.0)
#         self.prev_bid_qty = market.best_bid_qty
#         self.prev_ask_qty = market.best_ask_qty
#
#         # 0. State Transition Check
#         if position.total_qty == 0 and self.state == BotState.LONG_ACCUMULATION:
#             self.state = BotState.IDLE
#         elif position.total_qty > 0 and self.state == BotState.IDLE:
#             self.state = BotState.LONG_ACCUMULATION
#
#         # 1. Hedge Trigger (Safety)
#         if position.total_qty > 0 and self.state != BotState.HEDGED:
#             dd = position.get_drawdown_pct(current_price)
#             if dd > self.config.get("hedge_trigger_dd", 0.02):
#                 self.state = BotState.HEDGED
#                 return TradeAction(
#                     "HEDGE_SHORT", market.best_bid, position.total_qty, "DD Hedge"
#                 )
#
#         # 2. MATCH TRADING MODE
#         match market.trading_mode:
#             case TradingMode.PASS:
#                 if position.total_qty <= 0:
#                     logger.info("Position is zero, skipping liquidation.")
#                     return None
#                 return TradeAction(
#                     "SELL",
#                     market.best_ask,
#                     position.total_qty,
#                     "Supervisor dictacted PASS mode",
#                 )
#
#             case TradingMode.NEUTRAL:
#                 # Two-way market making placeholder (just taking some safe spreads)
#                 if self.state == BotState.IDLE and abs(features.ofi) > 0.5:
#                     qty = (
#                         self.config.get("base_order_size_q", 0.01)
#                         * market.risk_multiplier
#                     )
#                     side = (
#                         "BUY" if features.ofi > 0 else "SELL"
#                     )  # Simplified for neutral
#                     if side == "BUY":
#                         self.last_buy_price = market.best_bid
#                         return TradeAction(
#                             "BUY", market.best_bid, qty, "Neutral MM Buy"
#                         )
#                     else:
#                         return None  # Shorting not fully implemented yet in position manager
#
#                 if self.state == BotState.LONG_ACCUMULATION:
#                     tp_price = position.avg_price * (
#                         1 + getattr(self, "take_profit_pct", 0.005)
#                     )
#                     if current_price >= tp_price:
#                         return TradeAction(
#                             "SELL", market.best_ask, position.total_qty, "Neutral MM TP"
#                         )
#                 return None
#
#             case TradingMode.SCALP:
#                 qty = (
#                     self.config.get("base_order_size_q", 0.01) * market.risk_multiplier
#                 )
#
#                 if self.state == BotState.IDLE:
#                     # SCALP: Use limit orders strictly inside buy zone
#                     if (
#                         getattr(market, "buy_zone_max", 0.0) > 0
#                         and current_price < market.buy_zone_max
#                     ):
#                         # Front-run L2 wall
#                         l2_bids = getattr(
#                             market, "whale_walls", []
#                         )  # Mocking whale walls as L2
#                         # Build a mock bid book from walls for the frontrunner if real l2_bids is missing
#                         walls = [
#                             (w.get("price", 0), w.get("vol", 0))
#                             for w in (l2_bids or [])
#                             if w.get("side") == "BID"
#                         ]
#
#                         entry_price = (
#                             find_frontrun_price(walls, market.buy_zone_max)
#                             if walls
#                             else market.best_bid
#                         )
#                         if entry_price:
#                             self.last_buy_price = entry_price
#                             return TradeAction(
#                                 "BUY", entry_price, qty, "SCALP Limit Entry"
#                             )
#
#                 elif self.state == BotState.LONG_ACCUMULATION:
#                     # Aggressive TP inside Sell Zone
#                     if (
#                         getattr(market, "sell_zone_min", 0.0) > 0
#                         and current_price >= market.sell_zone_min
#                     ):
#                         return TradeAction(
#                             "SELL", market.best_ask, position.total_qty, "SCALP Zone TP"
#                         )
#
#                     # Or fallback aggressive TP
#                     tp_price = position.avg_price * 1.002
#                     if current_price >= tp_price:
#                         return TradeAction(
#                             "SELL",
#                             market.best_ask,
#                             position.total_qty,
#                             "SCALP Quick TP",
#                         )
#
#                 return None
#
#             case TradingMode.DCA:
#                 qty = (
#                     self.config.get("base_order_size_q", 0.01) * market.risk_multiplier
#                 )
#
#                 if self.state == BotState.IDLE:
#                     # Initial entry setup
#                     if features.ofi > 0:
#                         self.last_buy_price = market.best_bid
#                         return TradeAction(
#                             "BUY", market.best_bid, qty, "DCA Initial Entry"
#                         )
#
#                 elif self.state == BotState.LONG_ACCUMULATION:
#                     # Micro-Stop based on OFI (Signs of dumping)
#                     if features.ofi < -2.0:
#                         logger.warning(
#                             "[DCA] OFI collapsed (%.2f)! Micro-stop active.",
#                             features.ofi,
#                         )
#                         return TradeAction(
#                             "CANCEL_ALL", 0.0, 0.0, "DCA Micro-Stop: OFI Dump"
#                         )
#
#                     # DCA accumulation
#                     gap = atr * self.config.get("grid_step_atr_mult", 2.0)
#                     if current_price <= (self.last_buy_price - gap):
#                         self.last_buy_price = current_price
#                         return TradeAction(
#                             "BUY",
#                             market.best_bid,
#                             qty * 1.5,
#                             f"DCA Step (Gap {gap:.2f})",
#                         )
#
#                     # Standard TP
#                     tp_price = position.avg_price * 1.01
#                     if current_price >= tp_price:
#                         return TradeAction(
#                             "SELL", market.best_ask, position.total_qty, "DCA Target TP"
#                         )
#
#                 return None
#
#         return None

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
        self.state = BotState.RUNNING  # IRON LOCK: always start active
        self.config = config
        self.grid_levels_below = config.get("grid_levels_below", 15)
        self.grid_levels_above = config.get("grid_levels_above", 15)
        self.base_order_size_q = Decimal(str(config.get("base_order_size_q", 0.001)))
        self.grid_spacing_pct = Decimal(str(config.get("grid_spacing_pct", 0.002)))

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
        from quantum_edge_core.ai_scalper_bot.bot.execution.volatility_oracle import (
            VolatilityOracle,
        )

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

        if now < self.flash_crash_pause_until:
            return None

        # Flash Crash Protection (Price Velocity)
        self.price_buffer.append((now, float(current_price)))
        if len(self.price_buffer) >= 2:
            dt = now - self.price_buffer[0][0]
            if dt > 0:
                velocity = (float(current_price) - self.price_buffer[0][1]) / dt
                velocity_pct = velocity / self.price_buffer[0][1]

                # Check threshold: > 0.5% per sec
                if abs(velocity_pct) > 0.005:
                    logger.warning(
                        "[FLASH CRASH] Velocity = %.3f%% — halting entries for 60s",
                        velocity_pct * 100,
                    )
                    self.flash_crash_pause_until = now + 60.0
                    return TradeAction(
                        action_type="CANCEL_ALL",
                        price=current_price,
                        qty=Decimal("0.0"),
                        reason="Flash Crash Protection",
                    )

        # Update Volatility Oracle
        self.vol_oracle.add_close_price(float(current_price))
        calculated_atr = Decimal(str(self.vol_oracle.calculate_atr()))
        if calculated_atr <= Decimal("0.0"):
            calculated_atr = Decimal("50.0")  # Fallback for tests if no history

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

        spacing_mult = (
            Decimal(str(getattr(market, "grid_spacing_multiplier", 1.0)))
            * self.grid_spacing_multiplier
        )

        # ── Money Management: DCA Grid Qty ──────────────────────
        exposure_pct = Decimal(str(self.config.get("exposure_pct", 0.5)))
        quote_balance = position.state.quote_balance
        if quote_balance <= 0:
            quote_balance = Decimal("10000.0")

        total_levels = Decimal(str(self.grid_levels_below + self.grid_levels_above))
        capital_per_level = (quote_balance * exposure_pct) / total_levels
        grid_qty = capital_per_level / current_price

        # Round to 4 decimals and enforce Binance Spot minimum (0.001 BTC)
        grid_qty = grid_qty.quantize(Decimal("0.0001"))
        grid_qty = max(grid_qty, Decimal("0.001"))

        self.base_order_size_q = grid_qty

        logger.info(
            "[DCA QTY CALC] quote_balance=%.2f | exposure=%.2f | levels=%d | "
            "capital/lvl=%.2f | price=%.2f | grid_qty=%.6f",
            float(quote_balance), float(exposure_pct), int(total_levels),
            float(capital_per_level), float(current_price), float(grid_qty),
        )

        # Check conditions for SYNC_GRID
        is_initial_start = self.last_sync_price == Decimal("0.0")
        macro_changed = (
            self.last_regime is not None and regime != self.last_regime
        ) or (self.last_bias is not None and bias != self.last_bias)

        # Re-sync if price moved more than the dynamic grid spacing
        price_moved_abs = Decimal("0.0")
        if not is_initial_start:
            price_moved_abs = abs(current_price - self.last_sync_price)

        out_of_bounds = price_moved_abs > (current_price * self.grid_spacing_pct)

        if is_initial_start or macro_changed or out_of_bounds:
            grid = self.calculate_grid_prices(
                current_price, calculated_atr, spacing_mult, self.gamma
            )
            bid_prices = grid["bids"]
            ask_prices = grid["asks"]
            all_prices = bid_prices + ask_prices

            # Per-level diagnostic
            for i, lvl_price in enumerate(all_prices):
                side = "BUY" if lvl_price in bid_prices else "SELL"
                logger.info(
                    "GRID ORDER #%d: %s @ %.2f | qty=%.6f BTC",
                    i, side, float(lvl_price), float(grid_qty),
                )

            logger.warning(
                "!!! DCA GRID COMPILED SUCCESSFULLY: %d orders | "
                "qty_per_level=%.6f BTC | capital/lvl=%.2f USDT | "
                "price=%.2f | regime=%s | bias=%s !!!",
                len(all_prices), float(grid_qty), float(capital_per_level),
                float(current_price), regime, bias,
            )

            self.last_grid_sync_time = now
            self.last_sync_price = current_price
            self.last_regime = regime
            self.last_bias = bias

            params = (
                f"regime={regime}|bias={bias}|atr={float(calculated_atr):.2f}"
                f"|gamma={self.gamma}|mult={float(spacing_mult):.2f}"
                f"|qty={float(grid_qty):.6f}|levels={len(all_prices)}"
            )

            return TradeAction(
                action_type="SYNC_GRID",
                price=current_price,
                qty=grid_qty,
                reason=params,
            )
        else:
            return None

    def on_order_filled(
        self, side: str, price: Decimal, qty: Decimal, spacing_pct: Decimal
    ) -> TradeAction:
        """
        Triggered directly by an ORDER_FILLED event to place the exact counter-order.
        """
        if "BUY" in side.upper():
            counter_price = price * (Decimal("1.0") + spacing_pct)
            return TradeAction("SELL", counter_price, qty, "Counter grid SELL")
        else:
            counter_price = price * (Decimal("1.0") - spacing_pct)
            return TradeAction("BUY", counter_price, qty, "Counter grid BUY")

    def adjust_to_liquidity(
        self, target_price: Decimal, liquidity_walls: list
    ) -> Decimal:
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

    def calculate_grid_prices(
        self,
        current_price: Decimal,
        calculated_atr: Decimal,
        spacing_mult: Decimal,
        effective_gamma: Decimal,
    ) -> dict:
        """
        Calculate Non-linear Grid via ATR + gamma
        """
        bids = []
        asks = []

        for k in range(1, self.grid_levels_below + 1):
            if k == 1:
                gap = calculated_atr * Decimal("0.5")
            else:
                gap = (
                    calculated_atr * spacing_mult * (effective_gamma ** Decimal(str(k)))
                )
            bids.append(current_price - gap)

        for k in range(1, self.grid_levels_above + 1):
            if k == 1:
                gap = calculated_atr * Decimal("0.5")
            else:
                gap = (
                    calculated_atr * spacing_mult * (effective_gamma ** Decimal(str(k)))
                )
            asks.append(current_price + gap)

        return {"bids": bids, "asks": asks}
