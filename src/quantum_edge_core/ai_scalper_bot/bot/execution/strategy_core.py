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

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureVector
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager

logger: logging.Logger = logging.getLogger(__name__)

# ── Probability thresholds for XGBoost signal ────────────────────────
_BUY_THRESHOLD: float = 0.65   # P(buy) > 65% → BUY
_SELL_THRESHOLD: float = 0.35  # P(buy) < 35% → SELL
_TICK_SIZE: float = 0.10       # BTCUSDT tick size on Binance


def find_frontrun_price(
    l2_bids: List[Tuple[float, float]],
    target_price: float,
    ticks_above: int = 2,
) -> float:
    """Find optimal limit-buy price by front-running the largest L2 bid block.

    Scans the L2 order book bids below ``target_price``, finds the
    price level with maximum volume concentration (the "wall"), and
    returns a price ``ticks_above`` ticks ABOVE that wall.

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
    float
        The front-run price. Falls back to ``target_price`` if no
        suitable bids are found.
    """
    if not l2_bids:
        return target_price

    best_price: float = 0.0
    best_qty: float = 0.0

    for price, qty in l2_bids:
        if price > target_price:
            continue  # Skip bids above our zone limit
        if qty > best_qty:
            best_qty = qty
            best_price = price

    if best_price <= 0.0:
        return target_price

    frontrun: float = best_price + (ticks_above * _TICK_SIZE)
    logger.info(
        "[L2] Front-run: wall @ %.2f (qty=%.4f), placing @ %.2f (+%d ticks)",
        best_price, best_qty, frontrun, ticks_above,
    )
    return frontrun


class BotState(Enum):
    IDLE = auto()
    LONG_ACCUMULATION = auto()
    HEDGED = auto()


@dataclass
class TradeAction:
    action_type: str  # 'BUY', 'SELL', 'HEDGE_SHORT'
    price: float
    qty: float
    reason: str


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
                    "[!] УВАГА: XGBoost модель не завантажилася (%s). "
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
        """
        Main Decision Function.
        """
        current_price = market.last_price  # Or best_bid? Using last_price as reference.

        # --- ML Feature Pipeline ---
        bid_price = market.best_bid
        ask_price = market.best_ask
        bid_qty = market.best_bid_qty
        ask_qty = market.best_ask_qty
        
        mid_price = (bid_price + ask_price) / 2.0
        self.mid_prices.append(mid_price)
        
        buy_probability: Optional[float] = None
        if self.model is not None:
            spread = ask_price - bid_price
            micro_imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty + 1e-8)
            ofi_proxy = (bid_qty - self.prev_bid_qty) - (ask_qty - self.prev_ask_qty)
            volatility_100t = float(np.std(self.mid_prices)) if len(self.mid_prices) >= 100 else 0.0

            X = np.array([[spread, micro_imbalance, ofi_proxy, volatility_100t]])
            buy_probability = float(self.model.predict_proba(X)[0][1])

            logger.info(
                "[XGBoost] P(buy)=%.3f | Spread=%.4f, Imbalance=%.4f, "
                "OFI=%.4f, Vol100t=%.6f",
                buy_probability, spread, micro_imbalance, ofi_proxy, volatility_100t,
            )
            
        self.prev_bid_qty = bid_qty
        self.prev_ask_qty = ask_qty
        # ---------------------------

        # 0. State Transition Check
        if position.total_qty == 0 and self.state == BotState.LONG_ACCUMULATION:
            self.state = BotState.IDLE
        elif position.total_qty > 0 and self.state == BotState.IDLE:
            self.state = BotState.LONG_ACCUMULATION

        # 1. Check for Hedge Trigger (Critical Safety)
        if position.total_qty > 0 and self.state != BotState.HEDGED:
            dd = position.get_drawdown_pct(current_price)
            if dd > self.config.get("hedge_trigger_dd", 0.02):
                self.state = BotState.HEDGED
                # Hedge full quantity to be Delta Neutral
                return TradeAction(
                    action_type="HEDGE_SHORT",
                    price=market.best_bid,  # Sell into bid
                    qty=position.total_qty,
                    reason="Risk Limit Reached: Drawdown > Threshold",
                )

        # 2. Logic based on Whale Walls (Front-running)
        # Prioritize trading off massive limit walls if they exist close to current price
        if market.whale_walls:
            # Sort walls by distance to current price
            closest_walls = sorted(
                market.whale_walls, 
                key=lambda w: abs((w.get("price", 0.0) if isinstance(w, dict) else getattr(w, "price", 0.0)) - current_price)
            )
            nearest = closest_walls[0]
            n_price = nearest.get("price", 0.0) if isinstance(nearest, dict) else getattr(nearest, "price", 0.0)
            n_side = nearest.get("side", "") if isinstance(nearest, dict) else getattr(nearest, "side", "")
            
            # Calculate distance percentage
            if current_price > 0:
                dist_pct = abs(n_price - current_price) / current_price
                
                # If nearest wall is within 0.2%
                if dist_pct <= 0.002:
                    qty = self.config.get("base_order_size_q", 0.01) * getattr(market, "risk_multiplier", 1.0)
                    
                    # BID Wall -> Support -> Bounce up (BUY)
                    if n_side == "BID" and features.ofi > -1.0:
                        if getattr(market, "entries_paused", False):
                            pass # Supervisor halted entries
                        else:
                            # Front-run: Buy slightly above the wall
                            front_run_price = n_price * 1.0001
                            # Only buy if we are IDLE or need a DCA
                            if self.state == BotState.IDLE or (self.state == BotState.LONG_ACCUMULATION and current_price <= (self.last_buy_price - atr)):
                                self.last_buy_price = current_price
                                return TradeAction(
                                    action_type="BUY",
                                    price=front_run_price,
                                    qty=qty,
                                    reason=f"Front-run BID Wall @ {n_price} (OFI: {features.ofi:.2f})"
                                )
                            
                    # ASK Wall -> Resistance -> Reject down (SELL/TAKE PROFIT)
                    elif n_side == "ASK" and features.ofi < 1.0:
                        front_run_price = n_price * 0.9999
                        if self.state == BotState.LONG_ACCUMULATION:
                            return TradeAction(
                                action_type="SELL",
                                price=front_run_price,
                                qty=position.total_qty,
                                reason=f"Front-run ASK Wall @ {n_price} (Take Profit, OFI: {features.ofi:.2f})"
                            )

        # 3. Logic based on States (Standard or ML)
        if getattr(market, "entries_paused", False):
            # If paused, only allow HEDGE and SELLs (which are above).
            return None

        if self.model is not None and buy_probability is not None:
            # --- ML Logic (probability-based) ---
            qty = self.config.get("base_order_size_q", 0.01) * getattr(market, "risk_multiplier", 1.0)

            if buy_probability > _BUY_THRESHOLD and self.state == BotState.IDLE:
                # Front-run L2 instead of market-buying
                buy_zone_max = getattr(market, "buy_zone_max", 0.0)
                l2_bids = getattr(market, "l2_bids", [])
                if l2_bids and buy_zone_max > 0:
                    entry_price = find_frontrun_price(l2_bids, buy_zone_max)
                else:
                    entry_price = market.best_bid + _TICK_SIZE  # 1 tick above best bid

                self.last_buy_price = entry_price
                logger.info(
                    "[LIMIT] Front-run BUY @ %.2f (P=%.3f, zone_max=%.2f)",
                    entry_price, buy_probability, buy_zone_max,
                )
                return TradeAction(
                    action_type="BUY",
                    price=entry_price,
                    qty=qty,
                    reason=f"XGBoost P(buy)={buy_probability:.3f} → Limit @ {entry_price:.2f}"
                )
            elif buy_probability < _SELL_THRESHOLD and self.state == BotState.LONG_ACCUMULATION:
                return TradeAction(
                    action_type="SELL",
                    price=market.best_bid,
                    qty=position.total_qty,
                    reason=f"XGBoost P(buy)={buy_probability:.3f} → SELL"
                )
        else:
            # --- Fallback (Old Baseline Logic) ---
            if self.state == BotState.IDLE:
                # Entry Logic: High OFI (Buying Pressure)
                if features.ofi > self.config.get("ofi_entry_threshold", 0.1):
                    qty = self.config.get("base_order_size_q", 0.01) * getattr(market, "risk_multiplier", 1.0)
                    self.last_buy_price = (
                        current_price  # Temporarily set, confirmed on fill
                    )
                    return TradeAction(
                        action_type="BUY",
                        price=market.best_ask,  # Buy from ask
                        qty=qty,
                        reason=f"High OFI ({features.ofi:.2f})",
                    )

            elif self.state == BotState.LONG_ACCUMULATION:
                # A. Take Profit Check
                tp_price = position.avg_price * (
                    1 + self.config.get("take_profit_pct", 0.01)
                )
                if current_price >= tp_price:
                    return TradeAction(
                        action_type="SELL",
                        price=market.best_bid,
                        qty=position.total_qty,
                        reason="Take Profit Hit",
                    )

                # B. DCA Check (Dynamic ATR Spacing)
                # If price < last_buy - (ATR * gap_mult)
                gap = atr * self.config.get("grid_step_atr_mult", 2.0)

                # Safety: Ensure Gap is not zero or tiny
                if gap < current_price * 0.0005:  # Minimum 5 bps spacing
                    gap = current_price * 0.0005

                if current_price <= (self.last_buy_price - gap):
                    # DCA Size: Multiplier * Base (Simplified)
                    # In real bot, we'd replicate existing size or use martingale
                    dca_qty = self.config.get("base_order_size_q", 0.01) * getattr(market, "risk_multiplier", 1.0)

                    self.last_buy_price = current_price
                    return TradeAction(
                        action_type="BUY",
                        price=market.best_ask,
                        qty=dca_qty,
                        reason=f"DCA Step (ATR Gap: {gap:.2f})",
                    )

            elif self.state == BotState.HEDGED:
                # Logic to unwind hedge would go here.
                # For now, stay hedged until manual intervention or further logic.
                pass

        return None
