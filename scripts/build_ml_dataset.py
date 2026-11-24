#!/usr/bin/env python3
import pandas as pd
import numpy as np
import requests
import io
import os

QUESTDB_URL = "http://127.0.0.1:9000/exp"
SYMBOL = "XRPUSDT"
LOOKAHEAD_TICKS = 50  # Скільки тіків вперед дивимося для лейблінгу
PROFIT_THRESHOLD = 0.0002  # 2 базисних пункти (0.02%)


def fetch_questdb_data(query: str) -> pd.DataFrame:
    print(f"[*] Виконую запит до QuestDB: {query}")
    params = {"query": query}
    try:
        response = requests.get(QUESTDB_URL, params=params)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        return df
    except Exception as e:
        print(f"Помилка при запиті: {e}")
        return pd.DataFrame()


def build_features():
    print("[*] Етап 1: Екстракція L2 LOB і угод...")
    # Зміна стовпця з ts на timestamp для відповідності схемі QuestDB
    query_lob = f"SELECT timestamp, side, price, qty FROM orderbook_snapshots WHERE symbol='{SYMBOL}' AND depth_level=0 ORDER BY timestamp DESC LIMIT 200000"
    df = fetch_questdb_data(query_lob)

    if df.empty:
        print("[-] QuestDB повернула порожню відповідь для LOB.")
        return pd.DataFrame()

    query_trades = f"SELECT timestamp, qty FROM trades WHERE symbol='{SYMBOL}' ORDER BY timestamp DESC LIMIT 200000"
    df_trades = fetch_questdb_data(query_trades)

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if not df_trades.empty:
        df_trades["timestamp"] = pd.to_datetime(df_trades["timestamp"])

    print("[*] Етап 1.5: Трансформація L2 Orderbook у плоский датасет...")
    df_pivoted = df.pivot_table(
        index="timestamp", columns="side", values=["price", "qty"], aggfunc="first"
    ).dropna()

    df_pivoted.columns = [
        f"{col[0]}_{str(col[1]).upper()}" for col in df_pivoted.columns
    ]

    # КЛЮЧОВИЙ ФІКС: BUY/SELL замість BID/ASK
    df = df_pivoted.rename(
        columns={
            "price_BUY": "best_bid",
            "price_SELL": "best_ask",
            "qty_BUY": "best_bid_qty",
            "qty_SELL": "best_ask_qty",
        }
    ).reset_index()

    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"[*] Отримано {len(df)} унікальних зведених тіків стакану.")

    # Обчислення інтенсивності угод та об'єму за останню секунду
    if not df_trades.empty:
        print("[*] Обчислення trade_rate_1s та volume_1s...")
        df_trades = df_trades.sort_values("timestamp").reset_index(drop=True)
        df_trades_idx = df_trades.set_index("timestamp")
        
        trade_rate = df_trades_idx["qty"].rolling("1s").count()
        trade_volume = df_trades_idx["qty"].rolling("1s").sum()
        
        df_trades_features = pd.DataFrame({
            "trade_rate_1s": trade_rate,
            "volume_1s": trade_volume
        }).reset_index()
        
        df = pd.merge_asof(
            df,
            df_trades_features,
            on="timestamp",
            direction="backward"
        )
    else:
        df["trade_rate_1s"] = 0.0
        df["volume_1s"] = 0.0

    df["trade_rate_1s"] = df["trade_rate_1s"].fillna(0)
    df["volume_1s"] = df["volume_1s"].fillna(0)

    print("[*] Етап 2: Feature Engineering...")
    df["mid_price"] = (df["best_bid"] + df["best_ask"]) / 2.0
    df["spread"] = df["best_ask"] - df["best_bid"]
    df["micro_imbalance"] = (df["best_bid_qty"] - df["best_ask_qty"]) / (
        df["best_bid_qty"] + df["best_ask_qty"] + 1e-8
    )

    df["bid_qty_change"] = df["best_bid_qty"].diff().fillna(0)
    df["ask_qty_change"] = df["best_ask_qty"].diff().fillna(0)
    df["ofi_proxy"] = df["bid_qty_change"] - df["ask_qty_change"]

    df["volatility_100t"] = df["mid_price"].rolling(window=100).std().fillna(0)

    # --- Додаткові мікроструктурні фічі (Крок 1) ---
    df["ofi_ma5"] = df["ofi_proxy"].rolling(5).mean().fillna(0)
    df["top_qty_sum"] = df["best_bid_qty"] + df["best_ask_qty"]

    ofi_mean = df["ofi_proxy"].rolling(100).mean()
    ofi_std = df["ofi_proxy"].rolling(100).std().fillna(1e-8)
    df["ofi_z"] = (df["ofi_proxy"] - ofi_mean) / (ofi_std + 1e-8)
    df["ofi_z"] = df["ofi_z"].fillna(0)

    print("[*] Етап 3: Labeling (Розмітка Y)...")
    df["future_mid"] = df["mid_price"].shift(-LOOKAHEAD_TICKS)
    df["future_return"] = (df["future_mid"] - df["mid_price"]) / df["mid_price"]

    conditions = [
        (df["future_return"] >= PROFIT_THRESHOLD),
        (df["future_return"] <= -PROFIT_THRESHOLD),
    ]
    choices = [1, -1]
    df["target"] = np.select(conditions, choices, default=0)

    df = df.dropna(subset=["future_mid"]).copy()

    features_columns = [
        "timestamp",
        "mid_price",
        "spread",
        "micro_imbalance",
        "ofi_proxy",
        "volatility_100t",
        "ofi_z",
        "ofi_ma5",
        "top_qty_sum",
        "trade_rate_1s",
        "volume_1s",
        "target",
    ]
    final_df = df[features_columns]

    print(f"[*] Готово. Розподіл класів:\n{final_df['target'].value_counts()}")
    return final_df


def main():
    os.makedirs("data", exist_ok=True)
    df = build_features()

    if df.empty:
        print("[-] Пропущено збереження. Немає даних.")
        return

    out_file = "data/ml_dataset.parquet"
    df.to_parquet(out_file, engine="pyarrow")
    print(f"✅ Датасет успішно збережено: {out_file} (Розмір: {len(df)} рядків)")


if __name__ == "__main__":
    main()
