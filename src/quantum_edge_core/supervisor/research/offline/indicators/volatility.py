import numpy as np
import pandas as pd


def realized_volatility(series: pd.Series, window=30):
    returns = series.pct_change()
    return np.sqrt(np.sum(returns**2)) * np.sqrt(1e3)


def std_vol(series: pd.Series, window=30):
    return series.pct_change().rolling(window).std().iloc[-1]


def micro_price(asks, bids):
    """
    Microprice = (best_bid * ask_volume + best_ask * bid_volume) / (ask_volume + bid_volume)
    """
    if not asks or not bids:
        return None

    best_ask = float(asks[0][0])
    best_bid = float(bids[0][0])
    ask_vol = float(asks[0][1])
    bid_vol = float(bids[0][1])

    return (best_ask * bid_vol + best_bid * ask_vol) / (ask_vol + bid_vol + 1e-9)
