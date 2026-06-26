"""Evaluate multi-horizon models on ML datasets."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import xgboost as xgb

from hermes.research.offline.scalper_bot.ml.eval.metrics import (
    compute_metrics,
)
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


def _load_model(model_path: Path) -> xgb.XGBClassifier:
    model = xgb.XGBClassifier()
    model.load_model(str(model_path))
    return model


def _evaluate_split(
    df: pd.DataFrame,
    model: xgb.XGBClassifier,
    horizon: int,
    thresholds: List[float],
) -> Dict[str, object]:
    y_col = f"y_up_h{horizon}"
    if y_col not in df.columns:
        raise KeyError(f"Missing label column {y_col}")
    X = df[feature_names()].to_numpy(dtype=float)
    y_true = df[y_col].to_numpy(dtype=int)
    y_prob = model.predict_proba(X)[:, 1]
    metrics = compute_metrics(y_true, y_prob, thresholds)
    return metrics


def _per_scenario(
    df: pd.DataFrame,
    model: xgb.XGBClassifier,
    horizon: int,
    threshold: float,
) -> Dict[str, object]:
    y_col = f"y_up_h{horizon}"
    results: Dict[str, object] = {}
    for scenario_id, group in df.groupby("scenario_id"):
        if group.empty:
            continue
        X = group[feature_names()].to_numpy(dtype=float)
        y_true = group[y_col].to_numpy(dtype=int)
        y_prob = model.predict_proba(X)[:, 1]
        metrics = compute_metrics(y_true, y_prob, thresholds=[threshold])
        results[str(scenario_id)] = metrics
    return results


def evaluate(
    symbol: str,
    data_root: Path,
    models_root: Path,
    out_root: Path,
    thresholds: Optional[List[float]] = None,
) -> int:
    symbol = symbol.upper()
    data_root = data_root.resolve()
    models_root = models_root.resolve()
    out_root = out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "reports").mkdir(parents=True, exist_ok=True)

    thresholds = thresholds or [0.50, 0.55, 0.60]
    horizons = sorted(
        int(p.name.replace("horizon_h", "")) for p in data_root.glob("horizon_h*")
    )
    eval_manifest = {
        "symbol": symbol,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_hash": schema_hash(),
        "horizons": horizons,
        "thresholds": thresholds,
        "data_root": str(data_root),
        "models_root": str(models_root),
    }
    (out_root / "eval_manifest.json").write_text(
        json.dumps(eval_manifest, indent=2), encoding="utf-8"
    )

    for horizon in horizons:
        horizon_dir = data_root / f"horizon_h{horizon}"
        model_path = models_root / symbol / f"h{horizon}" / "model.json"
        if not model_path.exists():
            raise FileNotFoundError(f"Missing model: {model_path}")
        model = _load_model(model_path)

        val_path = _find_split(horizon_dir, "val")
        test_path = _find_split(horizon_dir, "test")
        val_df = _load_dataset(val_path)
        test_df = _load_dataset(test_path)

        val_metrics = _evaluate_split(val_df, model, horizon, thresholds)
        test_metrics = _evaluate_split(test_df, model, horizon, thresholds)

        horizon_out = out_root / f"horizon_h{horizon}"
        horizon_out.mkdir(parents=True, exist_ok=True)
        (horizon_out / "eval_val.json").write_text(
            json.dumps(val_metrics, indent=2), encoding="utf-8"
        )
        (horizon_out / "eval_test.json").write_text(
            json.dumps(test_metrics, indent=2), encoding="utf-8"
        )

        per_val = _per_scenario(val_df, model, horizon, threshold=thresholds[0])
        per_test = _per_scenario(test_df, model, horizon, threshold=thresholds[0])
        (horizon_out / "per_scenario_val.json").write_text(
            json.dumps(per_val, indent=2), encoding="utf-8"
        )
        (horizon_out / "per_scenario_test.json").write_text(
            json.dumps(per_test, indent=2), encoding="utf-8"
        )

    report = _render_report(out_root, horizons)
    (out_root / "reports" / "eval_report.md").write_text(report, encoding="utf-8")
    return 0


def _render_report(out_root: Path, horizons: List[int]) -> str:
    lines = ["# ML Evaluation Report", ""]
    for horizon in horizons:
        horizon_out = out_root / f"horizon_h{horizon}"
        val_path = horizon_out / "eval_val.json"
        test_path = horizon_out / "eval_test.json"
        if not val_path.exists() or not test_path.exists():
            continue
        val = json.loads(val_path.read_text(encoding="utf-8"))
        test = json.loads(test_path.read_text(encoding="utf-8"))
        lines.append(f"## Horizon h{horizon}")
        lines.append("")
        lines.append(f"Val AUC: {val.get('auc')} | Test AUC: {test.get('auc')}")
        lines.append(f"Val Brier: {val.get('brier')} | Test Brier: {test.get('brier')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate multi-horizon ML models.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data_root", required=True)
    parser.add_argument("--models_root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--thresholds", nargs="*", default=["0.50", "0.55", "0.60"])
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    thresholds = [float(t) for t in args.thresholds]
    return evaluate(
        symbol=args.symbol,
        data_root=Path(args.data_root),
        models_root=Path(args.models_root),
        out_root=Path(args.out),
        thresholds=thresholds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
