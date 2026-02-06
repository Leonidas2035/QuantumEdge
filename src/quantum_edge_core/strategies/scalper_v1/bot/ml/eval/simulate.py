"""Lightweight signal quality simulator using tuned policy."""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import xgboost as xgb

from bot.ml.features.builder import feature_names


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


def _load_calibrator(calib_root: Optional[Path], symbol: str, horizon: int):
    if calib_root is None:
        return None
    path = calib_root / symbol / f"h{horizon}" / "calibrator.pkl"
    if not path.exists():
        return None
    with path.open("rb") as handle:
        return pickle.load(handle)


def _apply_calibrator(calibrator, probs: np.ndarray) -> np.ndarray:
    if calibrator is None:
        return probs
    try:
        return calibrator.predict_proba(probs.reshape(-1, 1))[:, 1]
    except Exception:
        return calibrator.predict(probs)


def _merge(frames: List[pd.DataFrame]) -> pd.DataFrame:
    merged = frames[0]
    for other in frames[1:]:
        merged = merged.merge(other, on=["ts_ms", "scenario_id", "episode_id"], how="inner")
    return merged


def simulate(
    symbol: str,
    data_root: Path,
    models_root: Path,
    policy_path: Path,
    out_root: Path,
    calib_root: Optional[Path],
    split: str,
) -> int:
    symbol = symbol.upper()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    horizons = [int(h) for h in policy.get("horizons", [])]
    thresholds = {int(str(k).replace("h", "")): float(v) for k, v in (policy.get("thresholds") or {}).items()}
    costs = policy.get("costs_bps") or {}
    total_cost = (float(costs.get("fee_bps", 0)) + float(costs.get("slippage_bps", 0)) + float(costs.get("spread_bps", 0)) + float(costs.get("latency_bps", 0))) / 10_000.0

    frames = []
    for horizon in horizons:
        horizon_dir = data_root / f"horizon_h{horizon}"
        model_path = models_root / symbol / f"h{horizon}" / "model.json"
        if not model_path.exists():
            raise FileNotFoundError(f"Missing model: {model_path}")
        model = _load_model(model_path)
        df = _load_dataset(_find_split(horizon_dir, split))
        X = df[feature_names()].to_numpy(dtype=float)
        probs = model.predict_proba(X)[:, 1]
        calibrator = _load_calibrator(calib_root, symbol, horizon)
        probs = _apply_calibrator(calibrator, probs)
        frame = df[["ts_ms", "scenario_id", "episode_id", f"fut_ret_h{horizon}", f"y_up_h{horizon}"]].copy()
        frame[f"p_up_h{horizon}"] = probs
        frames.append(frame)

    merged = _merge(frames)
    mask = np.ones(len(merged), dtype=bool)
    for h in horizons:
        mask &= merged[f"p_up_h{h}"].to_numpy(dtype=float) >= thresholds.get(h, 0.0)

    fut_ret_cols = [f"fut_ret_h{h}" for h in horizons]
    fut_ret_mean = merged[fut_ret_cols].mean(axis=1).to_numpy(dtype=float)
    pnl = fut_ret_mean[mask] - total_cost
    cumulative = np.cumsum(pnl)
    drawdown = np.maximum.accumulate(cumulative) - cumulative
    win_rate = float((pnl > 0).mean()) if len(pnl) else 0.0

    duration_s = 1.0
    if len(merged):
        duration_s = (merged["ts_ms"].max() - merged["ts_ms"].min()) / 1000.0
        if duration_s <= 0:
            duration_s = 1.0
    trades_per_day = float(len(pnl) / (duration_s / 86400.0))

    summary = {
        "symbol": symbol,
        "split": split,
        "trades": len(pnl),
        "coverage": float(mask.mean()) if len(mask) else 0.0,
        "win_rate": win_rate,
        "expected_edge": float(pnl.mean()) if len(pnl) else 0.0,
        "max_drawdown": float(drawdown.max()) if len(drawdown) else 0.0,
        "trades_per_day": trades_per_day,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "sim_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_root / "sim_report.md").write_text(_render_report(summary), encoding="utf-8")
    return 0


def _render_report(summary: Dict[str, object]) -> str:
    lines = ["# ML Policy Simulation", ""]
    for key, value in summary.items():
        lines.append(f"{key}: {value}")
    lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate policy performance on ML datasets.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data_root", required=True)
    parser.add_argument("--models_root", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--calib_root", default=None)
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return simulate(
        symbol=args.symbol,
        data_root=Path(args.data_root),
        models_root=Path(args.models_root),
        policy_path=Path(args.policy),
        out_root=Path(args.out),
        calib_root=Path(args.calib_root) if args.calib_root else None,
        split=args.split,
    )


if __name__ == "__main__":
    raise SystemExit(main())
