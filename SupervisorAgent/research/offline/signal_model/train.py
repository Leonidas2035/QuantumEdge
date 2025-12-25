import argparse
import json
import os
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np
import xgboost as xgb

from bot.ml.feature_schema import FEATURE_NAMES
from .dataset_builder import DatasetBuilder
from bot.ml.signal_model.registry import feature_schema_hash, update_registry


def _metrics(y_true: np.ndarray, probs: np.ndarray) -> dict:
    preds = (probs >= 0.5).astype(int)
    tp = int(((preds == 1) & (y_true == 1)).sum())
    tn = int(((preds == 0) & (y_true == 0)).sum())
    fp = int(((preds == 1) & (y_true == 0)).sum())
    fn = int(((preds == 0) & (y_true == 1)).sum())
    total = len(y_true) or 1
    acc = (tp + tn) / total
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    mcc_den = ((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)) ** 0.5
    mcc = ((tp * tn) - (fp * fn)) / mcc_den if mcc_den else 0.0
    return {
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "mcc": round(mcc, 4),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
    }


def train_model(
    symbol: str,
    horizon: int,
    min_rows: int,
    model_dir: Path,
    dataset_dir: Path,
    data_dir: Optional[Path] = None,
    limit_files: Optional[int] = None,
) -> Tuple[bool, dict]:
    print(f"[INFO] Building dataset for {symbol}, horizon={horizon} ...")
    builder = DatasetBuilder(symbol=symbol, horizon=horizon, data_dir=data_dir, limit_files=limit_files)
    X, y, df = builder.build()

    if X.empty or len(X) < min_rows:
        msg = f"Not enough training data ({len(X)} rows). Need at least {min_rows}."
        print(f"[ERROR] {msg}")
        return False, {"error": msg}

    if y.nunique() < 2:
        msg = "Target contains a single class. Need both up/down examples to train."
        print(f"[ERROR] {msg}")
        return False, {"error": msg}

    params = {
        "n_estimators": 300,
        "max_depth": 5,
        "learning_rate": 0.05,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "tree_method": "hist",
    }

    model = xgb.XGBClassifier(**params)
    print("[INFO] Training XGBoost model ...")
    model.fit(X, y)

    model_path = model_dir / f"signal_xgb_{symbol}_h{horizon}.json"
    model.save_model(model_path)
    update_registry(model_dir, symbol, horizon, model_path)
    print(f"[OK] Model saved to {model_path}")

    dataset_path = dataset_dir / f"{symbol}_h{horizon}.parquet"
    try:
        df.to_parquet(dataset_path, index=False)
        print(f"[OK] Dataset saved to {dataset_path}")
    except Exception as exc:
        fallback_path = dataset_dir / f"{symbol}_h{horizon}.csv"
        df.to_csv(fallback_path, index=False)
        print(f"[WARN] Could not save parquet ({exc}). Saved CSV instead: {fallback_path}")
    probs = model.predict_proba(X)[:, 1]
    metrics = _metrics(y.to_numpy(), probs)
    balance = y.value_counts(normalize=True).to_dict()
    print(f"[DONE] Training complete. Metrics: {metrics}")
    return True, {
        "rows": len(X),
        "class_balance": balance,
        "metrics": metrics,
        "model_path": str(model_path),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Train signal model offline.")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Symbol to train on (e.g., BTCUSDT)")
    parser.add_argument("--horizons", type=str, default="1,5,30", help="Comma-separated horizons to train")
    parser.add_argument("--min-rows", type=int, default=1000, help="Minimum rows required to train")
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Path to tick CSV directory (defaults to data/ticks).",
    )
    parser.add_argument(
        "--limit-files",
        type=int,
        default=None,
        help="Limit number of tick CSV files to load (optional).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = Path(os.getenv("QE_ROOT") or Path(__file__).resolve().parents[4])
    model_dir = root / "storage" / "models"
    dataset_dir = root / "storage" / "datasets"
    model_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    data_path = Path(args.data) if args.data else None

    horizons: List[int] = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    for h in horizons:
        train_model(
            symbol=args.symbol,
            horizon=h,
            min_rows=args.min_rows,
            model_dir=model_dir,
            dataset_dir=dataset_dir,
            data_dir=data_path,
            limit_files=args.limit_files,
        )


if __name__ == "__main__":
    main()
