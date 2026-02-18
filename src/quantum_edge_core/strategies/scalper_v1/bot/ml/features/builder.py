"""Unified feature builder for offline and online pipelines."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from quantum_edge_core.strategies.scalper_v1.bot.ml.feature_schema import FEATURE_NAMES, MICROSTRUCTURE_FEATURES, REGIME_ENUM

FEATURE_SCHEMA_VERSION = "v2"


def feature_names() -> List[str]:
    return list(FEATURE_NAMES)


def schema_version() -> str:
    return FEATURE_SCHEMA_VERSION


def schema_hash() -> str:
    import hashlib
    import json

    payload = json.dumps(feature_names(), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _regime_tag(vol_30s: float, ema_slope_30s: float) -> int:
    if np.isnan(vol_30s) or np.isnan(ema_slope_30s):
        return REGIME_ENUM["flat"]
    if vol_30s > 0.002:
        return REGIME_ENUM["high_vol"]
    if ema_slope_30s > 0:
        return REGIME_ENUM["trending_up"]
    if ema_slope_30s < 0:
        return REGIME_ENUM["trending_down"]
    return REGIME_ENUM["flat"]


def build_feature_frame(
    ticks: pd.DataFrame, microstructure: Optional[Dict[str, float]] = None
) -> pd.DataFrame:
    """
    Build 1s bars + features from raw tick data.

    Required columns: timestamp (ms), price, qty
    Optional: side (buy/sell), side_sign (float)
    """
    df = ticks.copy()
    if "side_sign" not in df.columns:
        side = df.get("side")
        if side is not None:
            df["side_sign"] = np.where(
                df["side"].astype(str).str.contains("sell"), -1.0, 1.0
            )
        else:
            df["side_sign"] = 1.0

    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("ts")

    bars = df.resample("1s").agg(
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

    def roll_vwap(window: int) -> pd.Series:
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

    def roll_imb(window: int) -> pd.Series:
        vol = bars["qty"].rolling(window).sum()
        signed = (bars["side_sign"]).rolling(window).sum()
        return signed / (vol.replace(0, np.nan))

    bars["imb_5s"] = roll_imb(5)
    bars["imb_30s"] = roll_imb(30)

    bars["vol_mean_30s"] = bars["qty"].rolling(30).mean()
    bars["vol_spike_5s"] = bars["qty"].rolling(5).mean() / bars["vol_mean_30s"]
    bars["vol_spike_30s"] = bars["qty"].rolling(30).mean() / bars["vol_mean_30s"]

    bars["regime_tag"] = bars.apply(
        lambda row: _regime_tag(row["vol_30s"], row["ema_slope_30s"]), axis=1
    )

    micro = microstructure or {}
    for name in MICROSTRUCTURE_FEATURES:
        value = micro.get(name, 0.0)
        try:
            value = float(value)
        except Exception:
            value = 0.0
        if value != value:
            value = 0.0
        bars[name] = value

    return bars


def build_feature_vector(bars: pd.DataFrame) -> Optional[np.ndarray]:
    if bars.empty:
        return None
    latest = bars.iloc[-1]
    feature_vector = [latest[name] for name in FEATURE_NAMES]
    if any(pd.isna(feature_vector)):
        return None
    return np.array(feature_vector, dtype=float)


@dataclass
class FeatureBuilder:
    warmup_seconds: int = 600
    max_ticks: int = 1200

    def __post_init__(self) -> None:
        self.prices = deque(maxlen=self.max_ticks)
        self.qty = deque(maxlen=self.max_ticks)
        self.side = deque(maxlen=self.max_ticks)
        self.ts = deque(maxlen=self.max_ticks)
        self._microstructure: Dict[str, float] = {}

    def add_tick(
        self, timestamp: int, price: float, qty: float, side: str = "buy"
    ) -> Optional[np.ndarray]:
        self.ts.append(int(timestamp))
        self.prices.append(float(price))
        self.qty.append(float(qty))
        side_sign = -1.0 if str(side).lower().startswith("sell") else 1.0
        self.side.append(side_sign)
        return self._compute()

    def update_microstructure(self, microstructure: Dict[str, float]) -> None:
        self._microstructure.update(microstructure or {})

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
        bars = build_feature_frame(df, microstructure=self._microstructure)
        return build_feature_vector(bars)
