"""Dataset builder for ModelOps (ticks/bars)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd

from SupervisorAgent.research.offline.signal_model.dataset_builder import DatasetBuilder


def _timestamp_range(df: pd.DataFrame) -> Tuple[Optional[int], Optional[int]]:
    for key in ("timestamp", "ts"):
        if key in df.columns:
            try:
                series = pd.to_numeric(df[key], errors="coerce").dropna()
                if series.empty:
                    continue
                start_ts = int(series.min())
                end_ts = int(series.max())
                return start_ts, end_ts
            except Exception:
                continue
    return None, None


def build_dataset(
    symbol: str,
    source: str,
    input_dir: Optional[Path],
    out_dir: Path,
    limit_rows: Optional[int] = None,
    horizon: int = 1,
) -> Dict[str, object]:
    if source not in {"ticks", "bars"}:
        raise ValueError("source must be 'ticks' or 'bars'")

    builder = DatasetBuilder(symbol=symbol, horizon=horizon, data_dir=input_dir, limit_files=None)
    X, y, featured = builder.build()
    if featured.empty:
        raise ValueError("No data available to build dataset.")

    if limit_rows:
        featured = featured.head(int(limit_rows))

    version = time.strftime("%Y%m%d-%H%M%S")
    dataset_dir = out_dir / symbol.upper() / source / version
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = dataset_dir / f"{symbol.upper()}_h{horizon}.csv"
    featured.to_csv(dataset_path, index=False)
    start_ts, end_ts = _timestamp_range(featured)
    return {
        "dataset_path": dataset_path,
        "rows": int(len(featured)),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "version": version,
        "source": source,
    }

