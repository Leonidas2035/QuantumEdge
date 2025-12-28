"""IO helpers for ML datasets."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Tuple

import pandas as pd


def read_episode(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        try:
            return pd.read_parquet(path)
        except Exception:
            pass
    return pd.read_csv(path)


def normalize_ticks(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    if "timestamp" not in data.columns and "ts_ms" in data.columns:
        data = data.rename(columns={"ts_ms": "timestamp"})
    if "timestamp" not in data.columns:
        raise KeyError("tick data missing timestamp/ts_ms column")
    if "price" not in data.columns or "qty" not in data.columns:
        raise KeyError("tick data missing price/qty columns")
    if "side" not in data.columns:
        data["side"] = ""
    data["timestamp"] = data["timestamp"].astype("int64", errors="ignore")
    data["price"] = data["price"].astype(float)
    data["qty"] = data["qty"].astype(float)
    return data[["timestamp", "price", "qty", "side"]]


def write_frame(path: Path, df: pd.DataFrame, fmt: str) -> Tuple[Path, str]:
    fmt = fmt.lower()
    if fmt == "parquet":
        try:
            df.to_parquet(path, index=False)
            return path, "parquet"
        except Exception:
            fmt = "csv"
    if fmt == "csv":
        df.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)
        return path, "csv"
    raise ValueError(f"Unsupported output format: {fmt}")
