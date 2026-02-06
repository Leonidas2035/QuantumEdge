"""Probability calibration for ML models."""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from bot.ml.eval.metrics import ece_score
from bot.ml.features.builder import feature_names, schema_hash


def _load_dataset(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _find_split(horizon_dir: Path, split: str) -> Path:
    matches = list(horizon_dir.glob(f"{split}.*"))
    if not matches:
        raise FileNotFoundError(f"Missing {split} split in {horizon_dir}")
    if len(matches) > 1:
        raise ValueError(f"Multiple {split} files in {horizon_dir}")
    return matches[0]


def _load_model(path: Path) -> xgb.XGBClassifier:
    model = xgb.XGBClassifier()
    model.load_model(str(path))
    return model


def _fit_calibrator(method: str, y_true: np.ndarray, y_prob: np.ndarray):
    if method == "platt":
        from sklearn.linear_model import LogisticRegression

        lr = LogisticRegression(max_iter=200)
        lr.fit(y_prob.reshape(-1, 1), y_true)
        return lr
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(y_prob, y_true)
        return iso
    raise ValueError(f"Unsupported calibration method: {method}")


def _apply_calibrator(method: str, calibrator, y_prob: np.ndarray) -> np.ndarray:
    if method == "platt":
        return calibrator.predict_proba(y_prob.reshape(-1, 1))[:, 1]
    if method == "isotonic":
        return calibrator.predict(y_prob)
    raise ValueError(f"Unsupported calibration method: {method}")


def _brier(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    from sklearn.metrics import brier_score_loss

    return float(brier_score_loss(y_true, y_prob))


def calibrate(
    symbol: str,
    data_root: Path,
    models_root: Path,
    out_root: Path,
    method: str,
) -> int:
    symbol = symbol.upper()
    data_root = data_root.resolve()
    models_root = models_root.resolve()
    out_root = out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    horizons = sorted(int(p.name.replace("horizon_h", "")) for p in data_root.glob("horizon_h*"))
    for horizon in horizons:
        horizon_dir = data_root / f"horizon_h{horizon}"
        model_path = models_root / symbol / f"h{horizon}" / "model.json"
        if not model_path.exists():
            raise FileNotFoundError(f"Missing model: {model_path}")

        val_df = _load_dataset(_find_split(horizon_dir, "val"))
        test_df = _load_dataset(_find_split(horizon_dir, "test"))
        features = feature_names()
        y_col = f"y_up_h{horizon}"

        model = _load_model(model_path)
        y_val = val_df[y_col].to_numpy(dtype=int)
        p_val = model.predict_proba(val_df[features].to_numpy(dtype=float))[:, 1]
        y_test = test_df[y_col].to_numpy(dtype=int)
        p_test = model.predict_proba(test_df[features].to_numpy(dtype=float))[:, 1]

        calibrator = _fit_calibrator(method, y_val, p_val)
        p_val_cal = _apply_calibrator(method, calibrator, p_val)
        p_test_cal = _apply_calibrator(method, calibrator, p_test)

        metrics = {
            "brier_before": _brier(y_val, p_val),
            "brier_after": _brier(y_val, p_val_cal),
            "ece_before": ece_score(y_val, p_val),
            "ece_after": ece_score(y_val, p_val_cal),
            "test_brier_before": _brier(y_test, p_test),
            "test_brier_after": _brier(y_test, p_test_cal),
        }

        horizon_out = out_root / symbol / f"h{horizon}"
        horizon_out.mkdir(parents=True, exist_ok=True)
        with (horizon_out / "calibrator.pkl").open("wb") as handle:
            pickle.dump(calibrator, handle)

        manifest = {
            "symbol": symbol,
            "horizon": int(horizon),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "schema_hash": schema_hash(),
            "model_path": str(model_path),
        }
        (horizon_out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (horizon_out / "calibration_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate ML model probabilities.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data_root", required=True)
    parser.add_argument("--models_root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--method", choices=["platt", "isotonic"], default="platt")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return calibrate(
        symbol=args.symbol,
        data_root=Path(args.data_root),
        models_root=Path(args.models_root),
        out_root=Path(args.out),
        method=args.method,
    )


if __name__ == "__main__":
    raise SystemExit(main())
