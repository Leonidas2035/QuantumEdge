import numpy as np


def calc_delta(trades: list):
    """
    trades: list of trade events from Binance
    """
    buy = sum(float(t["q"]) for t in trades if t["m"] is False)
    sell = sum(float(t["q"]) for t in trades if t["m"] is True)

    return {
        "delta": buy - sell,
        "buy_volume": buy,
        "sell_volume": sell,
        "taker_ratio": buy / (buy + sell + 1e-9)
    }


def orderbook_imbalance(bids, asks):
    """
    bids, asks: list of [price, qty]
    """
    bid_vol = sum(float(b[1]) for b in bids)
    ask_vol = sum(float(a[1]) for a in asks)

    if bid_vol + ask_vol == 0:
        return 0

    return (bid_vol - ask_vol) / (bid_vol + ask_vol)
