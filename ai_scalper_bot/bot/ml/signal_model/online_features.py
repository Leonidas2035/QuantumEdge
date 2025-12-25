from collections import deque
from typing import Optional

import numpy as np
import pandas as pd

from bot.core.config_loader import config
from bot.ml.feature_schema import FEATURE_NAMES, REGIME_ENUM


class OnlineFeatureBuilder:
    """
    Incrementally builds multi-timeframe features aligned with DatasetBuilder.
    """

    def __init__(self, warmup_seconds: int = 600, max_ticks: int = 1200):
        self.warmup_seconds = warmup_seconds
        self.prices = deque(maxlen=max_ticks)
        self.qty = deque(maxlen=max_ticks)
        self.side = deque(maxlen=max_ticks)
        self.ts = deque(maxlen=max_ticks)

    def _regime(self, vol_30s: float, ema_slope_30s: float) -> int:
        if np.isnan(vol_30s) or np.isnan(ema_slope_30s):
            return REGIME_ENUM["flat"]
        if vol_30s > 0.002:
            return REGIME_ENUM["high_vol"]
        if ema_slope_30s > 0:
            return REGIME_ENUM["trending_up"]
        if ema_slope_30s < 0:
            return REGIME_ENUM["trending_down"]
        return REGIME_ENUM["flat"]

    def _compute(self) -> Optional[np.ndarray]:
        if len(self.ts) < 2:
            return None
        df = pd.DataFrame(
            {
                "timestamp": list(self.ts),
                "price": list(self.prices),
                "qty": list(self.qty),
                "side_sign": list(self.side),
            }
        )
        df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
        if (df["ts"].max() - df["ts"].min()).total_seconds() < self.warmup_seconds:
            return None
        df = df.set_index("ts")
        bars = df.resample("1S").agg(
            price=("price", "last"),
            qty=("qty", "sum"),
            side_sign=("side_sign", "sum"),
        )
        bars["price"] = bars["price"].ffill()
        bars["vwap"] = bars["price"]
        qty_nonzero = bars["qty"].replace(0, np.nan)
        bars["vwap"] = (bars["price"] * bars["qty"]) / qty_nonzero
        bars["vwap"] = bars["vwap"].fillna(bars["price"])
        bars["ret_1s"] = bars["price"].pct_change()
        bars["ret_5s"] = bars["price"].pct_change(5)
        bars["ret_30s"] = bars["price"].pct_change(30)
        bars["ret_60s"] = bars["price"].pct_change(60)
        bars["vol_5s"] = bars["ret_1s"].rolling(5).std()
        bars["vol_30s"] = bars["ret_1s"].rolling(30).std()
        bars["vol_60s"] = bars["ret_1s"].rolling(60).std()

        def roll_vwap(window: int):
            vol = bars["qty"].rolling(window).sum()
            pv = (bars["price"] * bars["qty"]).rolling(window).sum()
            return pv / vol.replace(0, np.nan)

        bars["vwap_1s"] = bars["vwap"]
        bars["vwap_5s"] = roll_vwap(5).fillna(bars["price"])
        bars["vwap_30s"] = roll_vwap(30).fillna(bars["price"])
        bars["vwap_60s"] = roll_vwap(60).fillna(bars["price"])

        bars["ema_short"] = bars["price"].ewm(span=5, adjust=False).mean()
        bars["ema_long"] = bars["price"].ewm(span=30, adjust=False).mean()
        bars["ema_slope_5s"] = bars["ema_short"].diff()
        bars["ema_slope_30s"] = bars["ema_long"].diff()

        def roll_imb(window: int):
            vol = bars["qty"].rolling(window).sum()
            signed = (bars["side_sign"]).rolling(window).sum()
            return signed / (vol.replace(0, np.nan))

        bars["imb_5s"] = roll_imb(5)
        bars["imb_30s"] = roll_imb(30)

        bars["vol_mean_30s"] = bars["qty"].rolling(30).mean()
        bars["vol_spike_5s"] = bars["qty"].rolling(5).mean() / bars["vol_mean_30s"]
        bars["vol_spike_30s"] = bars["qty"].rolling(30).mean() / bars["vol_mean_30s"]

        latest = bars.iloc[-1]
        regime_tag = self._regime(latest["vol_30s"], latest["ema_slope_30s"])

        feature_vector = [
            latest["ret_1s"],
            latest["ret_5s"],
            latest["ret_30s"],
            latest["ret_60s"],
            latest["vol_5s"],
            latest["vol_30s"],
            latest["vol_60s"],
            latest["vwap_1s"],
            latest["vwap_5s"],
            latest["vwap_30s"],
            latest["vwap_60s"],
            latest["ema_slope_5s"],
            latest["ema_slope_30s"],
            latest["imb_5s"],
            latest["imb_30s"],
            latest["vol_spike_5s"],
            latest["vol_spike_30s"],
            regime_tag,
        ]

        if any(pd.isna(feature_vector)):
            return None
        return np.array(feature_vector, dtype=float)

    def add_tick(self, timestamp: int, price: float, qty: float, side: str = "buy") -> Optional[np.ndarray]:
        self.ts.append(int(timestamp))
        self.prices.append(float(price))
        self.qty.append(float(qty))
        side_sign = -1.0 if str(side).lower().startswith("sell") else 1.0
        self.side.append(side_sign)
        return self._compute()
