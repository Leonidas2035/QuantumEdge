"""
Feature Engine.
Calculates real-time derivative features using Numpy.
"""

from __future__ import annotations

import numpy as np
import logging
from typing import Dict
from quantum_edge_core.supervisor.context.accumulator import MarketAccumulator

logger = logging.getLogger(__name__)


class FeatureEngine:
    """
    Stateless feature calculator.
    """

    @staticmethod
    def calc_cvd(acc: MarketAccumulator) -> Dict[str, float]:
        """
        Calculate Cumulative Volume Delta properties.
        """
        if len(acc.trades) < 2:
            return {"cvd_absolute": 0.0, "cvd_slope": 0.0}

        try:
            # Vectorize
            trades = list(acc.trades)
            # Create struct array or just separate lists
            sides = np.array([t.side for t in trades])
            quantities = np.array([t.quantity for t in trades])

            # Buy = +1, Sell = -1
            # Assuming 'buy' / 'sell' strings
            dirs = np.where(sides == "buy", 1.0, -1.0)

            volume_delta = dirs * quantities
            cumulative_cvd = np.cumsum(volume_delta)

            last_cvd = float(cumulative_cvd[-1])

            # Slope: Linear regression on last N points of CVD?
            # Or simplified: (Last - First) / N
            slope = (cumulative_cvd[-1] - cumulative_cvd[0]) / len(trades)

            return {"cvd_absolute": last_cvd, "cvd_slope": float(slope)}
        except Exception as e:
            logger.debug(f"CVD Calc Error: {e}")
            return {"cvd_absolute": 0.0, "cvd_slope": 0.0}

    @staticmethod
    def calc_liquidation_pressure(acc: MarketAccumulator) -> Dict[str, float]:
        """
        Aggregate liquidation volume from rolling buffer.
        """
        liq_buy_vol = 0.0
        liq_sell_vol = 0.0

        try:
            for event in list(acc.liquidations):
                side = event.get("side", "")
                usd = float(event.get("usd_size", 0.0))

                if side == "BUY":
                    liq_buy_vol += usd
                elif side == "SELL":
                    liq_sell_vol += usd

            liq_net = liq_buy_vol - liq_sell_vol

            return {"liq_buy_vol_1m": liq_buy_vol, "liq_sell_vol_1m": liq_sell_vol, "liq_net_1m": liq_net}
        except Exception:
            return {"liq_buy_vol_1m": 0.0, "liq_sell_vol_1m": 0.0, "liq_net_1m": 0.0}

    @staticmethod
    def calc_vwap_metrics(acc: MarketAccumulator) -> Dict[str, float]:
        """
        Calculate VWAP Z-Score over the trade buffer.
        """
        if len(acc.trades) < 10:
            return {"vwap_z_score": 0.0}

        try:
            trades = list(acc.trades)
            prices = np.array([t.price for t in trades])
            quantities = np.array([t.quantity for t in trades])

            # VWAP = Sum(P*Q) / Sum(Q)
            total_vol = np.sum(quantities)
            if total_vol == 0:
                return {"vwap_z_score": 0.0}

            vwap = np.sum(prices * quantities) / total_vol

            # StdDev of Prices (Simple or Weighted? Prompt implies simple StdDev for Z-Score denominator usually, or VWAP Bands)
            # "Calculate StdDev. Output: (CurrentPrice - VWAP) / StdDev"
            std_dev = np.std(prices)

            current_price = prices[-1]

            z_score = 0.0
            if std_dev > 0:
                z_score = (current_price - vwap) / std_dev

            return {"vwap_z_score": float(z_score), "vwap": float(vwap)}
        except Exception:
            return {"vwap_z_score": 0.0}

    @staticmethod
    def calc_volatility(acc: MarketAccumulator) -> float:
        """
        Calculate volatility (StdDev of returns) from candles.
        """
        if len(acc.candles) < 2:
            return 0.0

        try:
            candles = list(acc.candles)
            closes = np.array([c.close for c in candles])

            # Log Returns: ln(Pt / Pt-1)
            # log_returns = np.diff(np.log(closes))

            # Or percentage returns
            returns = np.diff(closes) / closes[:-1]

            # StdDev
            vol = np.std(returns)

            # Annualized? Or raw. "Standard Deviation of returns over last 60 candles"
            # Returning raw std dev of that period
            return float(vol)

        except Exception:
            return 0.0

    @staticmethod
    def calc_atr(acc: MarketAccumulator, window: int = 14) -> float:
        """
        Calculate Average True Range (ATR).
        TR = max(High-Low, abs(High-ClosePrev), abs(Low-ClosePrev))
        ATR = Rolling Mean of TR.
        """
        if len(acc.candles) < window + 1:
            return 0.0

        try:
            # Convert to numpy for vectorized speed
            candles = list(acc.candles)[-(window + 1) :]  # Take enough for lookback

            highs = np.array([c.high for c in candles])
            lows = np.array([c.low for c in candles])
            closes = np.array([c.close for c in candles])

            # True Range Calculation
            # TR[i] = max(H[i]-L[i], abs(H[i]-C[i-1]), abs(L[i]-C[i-1]))
            # We need previous close, so align arrays
            # Current (i): 1 to end
            # Prev (i-1): 0 to end-1

            h = highs[1:]
            lows_sliced = lows[1:]
            prev_c = closes[:-1]

            tr1 = h - lows_sliced
            tr2 = np.abs(h - prev_c)
            tr3 = np.abs(lows_sliced - prev_c)

            tr = np.maximum(tr1, np.maximum(tr2, tr3))

            # ATR = Mean of TR over window
            # If we want Wilder's smoothing (EMA-like), we need more history.
            # For simplicity/speed here: Simple Moving Average (SMA) of TR
            atr = np.mean(tr)

            return float(atr)

        except Exception:
            return 0.0

    @staticmethod
    def calc_volatility_scalar(acc: MarketAccumulator, baseline_atr: float = 100.0) -> float:
        """
        Calculate Position Sizing Scalar based on Volatility.
        Scalar = Baseline / CurrentATR.
        Clamped between 0.2 and 2.0.
        """
        current_atr = FeatureEngine.calc_atr(acc)

        if current_atr <= 0:
            return 1.0  # Default if unknown

        # Inverse relationship: Higher Vol -> Lower Size
        scalar = baseline_atr / current_atr

        # Clamp
        scalar = max(0.2, min(scalar, 2.0))

        return float(scalar)

    @staticmethod
    def calc_order_book_imbalance(acc: MarketAccumulator) -> float:
        """
        Calculate simple imbalance from snapshot.
        """
        book = acc.order_book
        if not book:
            return 0.0

        try:
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            if not bids or not asks:
                return 0.0

            # Depth 5
            bid_vol = sum(float(x[1]) for x in bids[:5])
            ask_vol = sum(float(x[1]) for x in asks[:5])

            total = bid_vol + ask_vol
            if total == 0:
                return 0.0

            # (Bid - Ask) / (Bid + Ask) -> Range -1 to 1
            return (bid_vol - ask_vol) / total
        except Exception:
            return 0.0
