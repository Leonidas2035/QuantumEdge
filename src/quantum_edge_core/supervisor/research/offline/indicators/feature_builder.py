import json
import numpy as np
import pandas as pd
from pathlib import Path

from bot.indicators.ohlcv_indicators import ema, rsi, atr, vwap
from bot.indicators.orderflow import calc_delta, orderbook_imbalance
from bot.indicators.volatility import realized_volatility, std_vol


class FeatureBuilder:

    def __init__(self, data_path="./data"):
        self.base = Path(data_path)

    # --------------------------------------------------------
    # LOADING DATA
    # --------------------------------------------------------
    def load_latest_trades(self, symbol, limit=300):
        """Load last N trades from /data/trades."""
        path = self.base / "trades"

        files = sorted(path.glob(f"{symbol}_*.json"), reverse=True)[:limit]

        trades = []
        for f in files:
            try:
                with open(f, "r") as fp:
                    trades.append(json.load(fp))
            except:
                continue

        # Binance timestamps must be sorted ASC
        return list(reversed(trades))

    def load_latest_orderbook(self, symbol):
        """Load last orderbook snapshot."""
        path = self.base / "orderbooks"

        files = sorted(path.glob(f"{symbol}_*.json"), reverse=True)
        if not files:
            return None

        with open(files[0], "r") as fp:
            return json.load(fp)

    # --------------------------------------------------------
    # BUILD FEATURES SAFE AND CLEAN
    # --------------------------------------------------------
    def build(self, symbol: str):

        # -------- Trades ----------
        trades = self.load_latest_trades(symbol)
        if not trades or len(trades) < 10:
            return None  # insufficient data

        df = pd.DataFrame(trades)
        df["price"] = df["p"].astype(float)
        df["qty"] = df["q"].astype(float)
        df["ts"] = df["T"] // 1000  # group into 1-second buckets

        # -------- OHLCV ----------
        df_ohlcv = df.groupby("ts").agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("qty", "sum")
        )

        # Якщо даних менше ніж 2-3 секунди — не будуємо фічі
        if len(df_ohlcv) < 3:
            return None

        df_ohlcv = df_ohlcv.fillna(method="ffill").fillna(method="bfill")

        f = {}

        # ------------ SAFE INDICATORS ----------------
        # Усі фічі загортаємо в try/except, щоб НІКОЛИ не було KeyError/NaN

        try:
            f["ema_9"] = float(ema(df_ohlcv["close"], 9))
        except:
            f["ema_9"] = float(df_ohlcv["close"].iloc[-1])

        try:
            f["ema_21"] = float(ema(df_ohlcv["close"], 21))
        except:
            f["ema_21"] = float(df_ohlcv["close"].iloc[-1])

        try:
            f["rsi_14"] = float(rsi(df_ohlcv["close"], 14))
        except:
            f["rsi_14"] = 50.0

        try:
            f["atr_14"] = float(atr(df_ohlcv.copy(), 14))
        except:
            f["atr_14"] = 0.0

        try:
            vwap_val = vwap(df_ohlcv.copy())
            if np.isnan(vwap_val):
                vwap_val = df_ohlcv["close"].iloc[-1]
            f["vwap"] = float(vwap_val)
        except:
            f["vwap"] = float(df_ohlcv["close"].iloc[-1])

        # ------------ VOLATILITY ----------------
        try:
            f["vol_std_30"] = float(std_vol(df_ohlcv["close"], 30))
        except:
            f["vol_std_30"] = 0.0

        try:
            f["vol_rv_30"] = float(realized_volatility(df_ohlcv["close"], 30))
        except:
            f["vol_rv_30"] = 0.0

        # -------- ORDERFLOW ----------
        try:
            delta = calc_delta(trades)
            f["delta"] = float(delta["delta"])
            f["buy_volume"] = float(delta["buy_volume"])
            f["sell_volume"] = float(delta["sell_volume"])
            f["taker_ratio"] = float(delta["taker_ratio"])
        except:
            f["delta"] = 0.0
            f["buy_volume"] = 0.0
            f["sell_volume"] = 0.0
            f["taker_ratio"] = 0.5

        # -------- ORDERBOOK ----------
        ob = self.load_latest_orderbook(symbol)
        if ob and "bids" in ob and "asks" in ob:
            try:
                f["ob_imbalance"] = float(orderbook_imbalance(ob["bids"], ob["asks"]))
            except:
                f["ob_imbalance"] = 0.0
        else:
            f["ob_imbalance"] = 0.0

        # Гарантовано перетворюємо NaN → 0
        for k, v in f.items():
            if v is None or (isinstance(v, float) and np.isnan(v)):
                f[k] = 0.0

        return f
