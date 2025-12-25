"""
Deprecated placeholder. Dataset creation now lives in DatasetBuilder.
Kept for backward compatibility; prefer using DatasetBuilder directly.
"""

from pathlib import Path
from typing import Tuple

import pandas as pd

from bot.ml.signal_model.dataset_builder import DatasetBuilder


class SignalDataset:
    def __init__(self, data_path: str = "./data", symbol: str = "BTCUSDT", horizon: int = 1):
        self.data_path = Path(data_path)
        self.symbol = symbol
        self.horizon = horizon

    def create_dataset(self, limit: int = 5000) -> Tuple[pd.DataFrame, pd.Series]:
        builder = DatasetBuilder(symbol=self.symbol, horizon=self.horizon, data_dir=self.data_path / "ticks")
        X, y, _ = builder.build()
        if limit and not X.empty:
            X = X.head(limit)
            y = y.head(limit)
        return X, y
