"""Cost-aware threshold tuning and policy export."""

from __future__ import annotations

import argparse
import json
import math
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import xgboost as xgb

from bot.ml.features.builder import feature_names, schema_hash, schema_version


@dataclass(frozen=True)
class CostConfig:
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    spread_bps: float = 0.0
    latency_bps: float = 0.0

    def total_cost(self) -> float:
        return (
            self.fee_bps + self.slippage_bps + self.spread_bps + self.latency_bps
        ) / 10_000.0


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


def _load_calibrator(calib_root: Path, symbol: str, horizon: int):
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


def _merge_horizons(frames: List[pd.DataFrame]) -> pd.DataFrame:
    merged = frames[0]
    for other in frames[1:]:
        merged = merged.merge(
            other, on=["ts_ms", "scenario_id", "episode_id"], how="inner"
        )
    return merged


def _prepare_predictions(
    data_root: Path,
    models_root: Path,
    calib_root: Optional[Path],
    symbol: str,
    horizons: List[int],
    split: str,
) -> pd.DataFrame:
    frames = []
    features = feature_names()
    for horizon in horizons:
        horizon_dir = data_root / f"horizon_h{horizon}"
        model_path = models_root / symbol / f"h{horizon}" / "model.json"
        if not model_path.exists():
            raise FileNotFoundError(f"Missing model: {model_path}")
        model = _load_model(model_path)
        df = _load_dataset(_find_split(horizon_dir, split))
        X = df[features].to_numpy(dtype=float)
        probs = model.predict_proba(X)[:, 1]
        calibrator = (
            _load_calibrator(calib_root, symbol, horizon) if calib_root else None
        )
        probs = _apply_calibrator(calibrator, probs)
        frame = df[
            [
                "ts_ms",
                "scenario_id",
                "episode_id",
                f"fut_ret_h{horizon}",
                f"y_up_h{horizon}",
            ]
        ].copy()
        frame[f"p_up_h{horizon}"] = probs
        frames.append(frame)
    return _merge_horizons(frames)


def _grid_values(start: float, stop: float, step: float) -> List[float]:
    values = []
    v = start
    while v <= stop + 1e-9:
        values.append(round(v, 4))
        v += step
    return values


def select_thresholds(
    merged: pd.DataFrame,
    horizons: List[int],
    grid: Iterable[float],
    cost_cfg: CostConfig,
    min_coverage: float,
) -> Tuple[Dict[int, float], Dict[str, float]]:
    best_score = -math.inf
    best_thresholds: Dict[int, float] = {}
    cost = cost_cfg.total_cost()

    grid_list = list(grid)
    for t1 in grid_list:
        for t2 in grid_list:
            for t3 in grid_list:
                thresholds = {horizons[0]: t1, horizons[1]: t2, horizons[2]: t3}
                mask = np.ones(len(merged), dtype=bool)
                for h in horizons:
                    mask &= merged[f"p_up_h{h}"].to_numpy(dtype=float) >= thresholds[h]
                coverage = float(mask.mean()) if len(mask) else 0.0
                if coverage < min_coverage:
                    continue
                fut_ret_cols = [f"fut_ret_h{h}" for h in horizons]
                fut_ret_mean = merged[fut_ret_cols].mean(axis=1).to_numpy(dtype=float)
                edge = fut_ret_mean[mask] - cost
                score = float(edge.mean()) if len(edge) else -math.inf
                if score > best_score:
                    best_score = score
                    best_thresholds = thresholds
                    best_meta = {"coverage": coverage, "expected_edge": score}

    if not best_thresholds:
        best_thresholds = {h: grid_list[0] for h in horizons}
        best_meta = {"coverage": 0.0, "expected_edge": -math.inf}
    return best_thresholds, best_meta


def validate_policy_schema(policy: Dict[str, object]) -> List[str]:
    required = [
        "symbol",
        "horizons",
        "policy_type",
        "thresholds",
        "schema_hash",
        "created_at",
    ]
    missing = [key for key in required if key not in policy]
    if missing:
        return missing
    return []


def tune_policy(
    symbol: str,
    data_root: Path,
    models_root: Path,
    calib_root: Optional[Path],
    out_root: Path,
    policy_type: str,
    grid_step: float,
    cost_cfg: CostConfig,
    min_coverage: float,
) -> int:
    symbol = symbol.upper()
    data_root = data_root.resolve()
    models_root = models_root.resolve()
    calib_root = calib_root.resolve() if calib_root else None
    out_root = out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    horizons = sorted(
        int(p.name.replace("horizon_h", "")) for p in data_root.glob("horizon_h*")
    )
    if len(horizons) != 3:
        raise ValueError(
            "Expected exactly three horizons (h1/h5/h30) for policy tuning."
        )

    merged = _prepare_predictions(
        data_root=data_root,
        models_root=models_root,
        calib_root=calib_root,
        symbol=symbol,
        horizons=horizons,
        split="val",
    )

    grid = _grid_values(0.50, 0.70, grid_step)
    thresholds, meta = select_thresholds(
        merged, horizons, grid, cost_cfg, min_coverage=min_coverage
    )

    policy = {
        "symbol": symbol,
        "horizons": horizons,
        "policy_type": policy_type,
        "thresholds": {f"h{h}": thresholds[h] for h in horizons},
        "schema_hash": schema_hash(),
        "schema_version": schema_version(),
        "costs_bps": {
            "fee_bps": cost_cfg.fee_bps,
            "slippage_bps": cost_cfg.slippage_bps,
            "spread_bps": cost_cfg.spread_bps,
            "latency_bps": cost_cfg.latency_bps,
        },
        "coverage": meta.get("coverage", 0.0),
        "expected_edge": meta.get("expected_edge", 0.0),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    manifest = {
        "symbol": symbol,
        "horizons": horizons,
        "grid_step": grid_step,
        "min_coverage": min_coverage,
        "policy_type": policy_type,
        "created_at": policy["created_at"],
    }

    (out_root / "policy.json").write_text(
        json.dumps(policy, indent=2), encoding="utf-8"
    )
    (out_root / "policy_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (out_root / "tuning_report.json").write_text(
        json.dumps(policy, indent=2), encoding="utf-8"
    )
    (out_root / "tuning_report.md").write_text(_render_report(policy), encoding="utf-8")
    return 0


def _render_report(policy: Dict[str, object]) -> str:
    lines = ["# ML Policy Tuning Report", ""]
    lines.append(f"Symbol: {policy.get('symbol')}")
    lines.append(f"Horizons: {policy.get('horizons')}")
    lines.append(f"Policy: {policy.get('policy_type')}")
    lines.append(f"Thresholds: {policy.get('thresholds')}")
    lines.append(f"Coverage: {policy.get('coverage')}")
    lines.append(f"Expected edge: {policy.get('expected_edge')}")
    lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tune ML thresholds and export policy."
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data_root", required=True)
    parser.add_argument("--models_root", required=True)
    parser.add_argument("--calib_root", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--policy", dest="policy_type", default="and_gate", choices=["and_gate"]
    )
    parser.add_argument("--grid_step", type=float, default=0.01)
    parser.add_argument("--min_coverage", type=float, default=0.05)
    parser.add_argument("--fee_bps", type=float, default=0.0)
    parser.add_argument("--slippage_bps", type=float, default=0.0)
    parser.add_argument("--spread_bps", type=float, default=0.0)
    parser.add_argument("--latency_bps", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    cost_cfg = CostConfig(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        spread_bps=args.spread_bps,
        latency_bps=args.latency_bps,
    )
    return tune_policy(
        symbol=args.symbol,
        data_root=Path(args.data_root),
        models_root=Path(args.models_root),
        calib_root=Path(args.calib_root) if args.calib_root else None,
        out_root=Path(args.out),
        policy_type=args.policy_type,
        grid_step=args.grid_step,
        cost_cfg=cost_cfg,
        min_coverage=args.min_coverage,
    )


if __name__ == "__main__":
    raise SystemExit(main())
