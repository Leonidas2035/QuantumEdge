"""
Shared feature schema for offline and online pipelines.

This keeps feature names consistent between dataset_builder and online_features.
"""

FEATURE_NAMES = [
    # Returns / volatility
    "ret_1s",
    "ret_5s",
    "ret_30s",
    "ret_60s",
    "vol_5s",
    "vol_30s",
    "vol_60s",
    # VWAP / trend
    "vwap_1s",
    "vwap_5s",
    "vwap_30s",
    "vwap_60s",
    "ema_slope_5s",
    "ema_slope_30s",
    # Order-flow / imbalance
    "imb_5s",
    "imb_30s",
    # Volume / liquidity
    "vol_spike_5s",
    "vol_spike_30s",
    # Regime tag (numeric)
    "regime_tag",
]


REGIME_ENUM = {
    "flat": 0,
    "trending_up": 1,
    "trending_down": -1,
    "high_vol": 2,
}
