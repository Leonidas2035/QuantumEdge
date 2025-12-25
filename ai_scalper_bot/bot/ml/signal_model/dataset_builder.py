from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np
import pandas as pd

from bot.ml.feature_schema import FEATURE_NAMES, REGIME_ENUM


class DatasetBuilder:
    """
    Loads tick CSVs for a symbol, builds a feature matrix and binary targets.
    The tick schema is expected to be: timestamp,price,qty,side
    """

    def __init__(self, symbol: str = "BTCUSDT", horizon: int = 1, data_dir: Optional[Path] = None, limit_files: Optional[int] = None):
        self.root = Path(__file__).resolve().parents[3]
        self.symbol = symbol
        self.horizon = horizon
        self.limit_files = limit_files
        base_dir = Path(data_dir) if data_dir is not None else (self.root / "data" / "ticks")
        if not base_dir.is_absolute():
            base_dir = (self.root / base_dir).resolve()
        self.data_dir = base_dir
        self.fallback_dir = (self.root / "data" / "offline").resolve()

    def _collect_files(self) -> List[Path]:
        patterns = [f"{self.symbol}_*.csv"]
        files: List[Path] = []
        for base in (self.data_dir, self.fallback_dir):
            if base.exists():
                for pattern in patterns:
                    files.extend(base.glob(pattern))
        uniq = sorted({p.resolve() for p in files})
        if self.limit_files:
            return uniq[: self.limit_files]
        return uniq

    def _file_hint(self, fp: Path) -> str:
        try:
            size = fp.stat().st_size
        except Exception:
            size = 0
        first_line = "<unreadable>"
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as handle:
                first_line = handle.readline().strip()
                if not first_line:
                    first_line = "<empty>"
                else:
                    first_line = first_line[:120]
        except Exception:
            first_line = "<unreadable>"
        return f"size={size} bytes, first_line={first_line}"

    def _load_ticks(self) -> pd.DataFrame:
        pattern = f"{self.symbol}_*.csv"
        files = self._collect_files()
        if not files:
            print(
                f"[WARN] No tick files found for {self.symbol}. "
                f"Looked for {pattern} in {self.data_dir} and {self.fallback_dir}."
            )
            return pd.DataFrame()

        frames = []
        for fp in files:
            hint = self._file_hint(fp)
            if fp.exists() and fp.stat().st_size == 0:
                print(f"[WARN] Skipping {fp.name} ({hint}): file is empty.")
                continue
            try:
                frames.append(pd.read_csv(fp))
            except Exception as exc:
                print(f"[WARN] Skipping {fp.name} ({hint}): {exc}")
        if not frames:
            print(
                f"[WARN] No valid tick data for {self.symbol}. "
                f"Looked for {pattern} in {self.data_dir} and {self.fallback_dir}."
            )
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _normalize_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        required_cols = ["timestamp", "price", "qty", "side"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns in ticks: {missing}")

        df = df.copy()
        df = df[required_cols + [c for c in df.columns if c not in required_cols]]
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce").astype("Int64")
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
        df["side"] = df["side"].astype(str).str.lower()

        df = df.dropna(subset=["timestamp", "price", "qty"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        return df

    def _compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["side_sign"] = np.where(df["side"].str.contains("sell"), -1.0, 1.0)
        df["ts"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("ts")

        bars = df.resample("1S").agg(
            price=("price", "last"),
            qty=("qty", "sum"),
            side_sign=("side_sign", "sum"),
        )
        bars["price"] = bars["price"].ffill()
        bars["vwap"] = bars["price"]  # fallback if qty==0
        qty_nonzero = bars["qty"].replace(0, np.nan)
        bars["vwap"] = (bars["price"] * bars["qty"]) / qty_nonzero
        bars["vwap"] = bars["vwap"].fillna(bars["price"])
        bars["ret_1s"] = bars["price"].pct_change()

        def roll_ret(window: int):
            return bars["price"].pct_change(window)

        bars["ret_5s"] = roll_ret(5)
        bars["ret_30s"] = roll_ret(30)
        bars["ret_60s"] = roll_ret(60)
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

        # Regime tagging
        def _regime(row):
            vol = row["vol_30s"]
            slope = row["ema_slope_30s"]
            if pd.isna(vol) or pd.isna(slope):
                return REGIME_ENUM["flat"]
            if vol > 0.002:
                return REGIME_ENUM["high_vol"]
            if slope > 0:
                return REGIME_ENUM["trending_up"]
            if slope < 0:
                return REGIME_ENUM["trending_down"]
            return REGIME_ENUM["flat"]

        bars["regime_tag"] = bars.apply(_regime, axis=1)

        bars = bars.dropna(subset=FEATURE_NAMES)

        bars["future_price"] = bars["price"].shift(-self.horizon)
        bars["target"] = (bars["future_price"] > bars["price"]).astype(int)
        bars = bars.dropna(subset=FEATURE_NAMES + ["target"]).reset_index(drop=True)
        return bars

    def build(self) -> Tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
        raw = self._load_ticks()
        if raw.empty:
            pattern = f"{self.symbol}_*.csv"
            print(
                f"[ERROR] No data available for {self.symbol}. "
                f"Searched for {pattern} in {self.data_dir} and {self.fallback_dir}."
            )
            return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame()

        try:
            normalized = self._normalize_schema(raw)
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            return pd.DataFrame(), pd.Series(dtype=int), pd.DataFrame()

        featured = self._compute_features(normalized)
        if featured.empty:
            print(f"[ERROR] Not enough data to compute features/targets for {self.symbol}.")
            return pd.DataFrame(), pd.Series(dtype=int), normalized

        X = featured[FEATURE_NAMES].copy()
        y = featured["target"].astype(int).copy()
        return X, y, featured
