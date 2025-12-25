"""Model validation utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from SupervisorAgent.mlops.manifest import ModelManifest


def _load_dataset(path: Path) -> Tuple[pd.DataFrame, pd.Series]:
    df = pd.read_csv(path)
    if "target" not in df.columns:
        raise ValueError("Dataset missing 'target' column")
    y = df["target"].astype(int)
    X = df.drop(columns=["target"])
    return X, y


def _split(X: pd.DataFrame, y: pd.Series, val_ratio: float = 0.2) -> Tuple[pd.DataFrame, pd.Series]:
    split_at = int(len(X) * (1 - val_ratio))
    split_at = max(split_at, 1)
    return X.iloc[split_at:], y.iloc[split_at:]


def validate_model(
    manifest_path: Path,
    dataset_path: Path,
    min_rows: int = 200,
) -> Dict[str, float]:
    manifest = ModelManifest.load(manifest_path)
    model_path = manifest_path.parent / manifest.files["model"]["path"]
    if not model_path.exists():
        raise FileNotFoundError(f"Model missing: {model_path}")

    X, y = _load_dataset(dataset_path)
    if len(X) < min_rows:
        raise ValueError(f"Not enough rows for validation: {len(X)}")

    X_val, y_val = _split(X, y)
    import xgboost as xgb

    model = xgb.XGBClassifier()
    model.load_model(str(model_path))
    preds = model.predict_proba(X_val)[:, 1]
    labels = (preds >= 0.5).astype(int)
    acc = float((labels == y_val.to_numpy()).mean())

    metrics = {"accuracy": round(acc, 4)}
    manifest.metrics.update(metrics)
    manifest.write(manifest_path)
    return metrics

