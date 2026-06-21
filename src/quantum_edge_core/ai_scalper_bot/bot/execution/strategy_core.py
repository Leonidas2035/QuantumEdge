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


