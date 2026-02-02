"""Train baseline multi-horizon signal models from ML datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import xgboost as xgb

from bot.ml.features.builder import feature_names, schema_hash, schema_version


@dataclass
class TrainConfig:
    horizons: List[int]
    n_estimators: int = 200
    max_depth: int = 5
    learning_rate: float = 0.05
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    random_state: int = 42


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _load_dataset(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _find_split_file(horizon_dir: Path, split: str) -> Path:
    matches = list(horizon_dir.glob(f"{split}.*"))
    if not matches:
        raise FileNotFoundError(f"Missing {split} dataset in {horizon_dir}")
    if len(matches) > 1:
        raise ValueError(f"Multiple {split} datasets found in {horizon_dir}")
    return matches[0]


def _load_splits(horizon_dir: Path, horizon: int) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train_path = _find_split_file(horizon_dir, "train")
    val_path = _find_split_file(horizon_dir, "val")
    test_path = _find_split_file(horizon_dir, "test")
    train = _load_dataset(train_path)
    val = _load_dataset(val_path)
    test = _load_dataset(test_path)
    return train, val, test


def _build_manifest(
    symbol: str,
    horizon: int,
    model_path: Path,
    metrics: Dict[str, object],
    thresholds: Dict[str, float],
    dataset_hashes: Dict[str, str],
    train_stats: Dict[str, object],
) -> Dict[str, object]:
    created_ts = int(datetime.now(timezone.utc).timestamp())
    manifest = {
        "manifest_version": "model.v1",
        "symbol": symbol,
        "horizon": int(horizon),
        "model_type": "signal_model",
        "created_at": created_ts,
        "feature_schema_version": schema_version(),
        "feature_names": feature_names(),
        "schema_hash": schema_hash(),
        "training_data": train_stats,
        "metrics": metrics,
        "thresholds": thresholds,
        "dataset_fingerprint": dataset_hashes,
        "files": {
            "model": {
                "path": model_path.name,
                "sha256": _sha256_file(model_path),
            }
        },
        "model_format": "xgboost_json",
        "model_api": "predict_proba",
        "artifact": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": sys.platform,
            "serializer": "xgboost_json",
            "lib_versions": _lib_versions(),
        },
        "git_commit": _git_commit(Path(__file__).resolve().parents[3]),
    }
    return manifest


def _lib_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    try:
        import importlib.metadata as metadata

        for name in ("numpy", "pandas", "xgboost", "scikit-learn"):
            try:
                versions[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                continue
    except Exception:
        return versions
    return versions


def _training_stats(df: pd.DataFrame, horizon: int) -> Dict[str, object]:
    stats: Dict[str, object] = {"rows": int(len(df))}
    if "ts_ms" in df.columns and len(df):
        stats["start_ts_ms"] = int(df["ts_ms"].min())
        stats["end_ts_ms"] = int(df["ts_ms"].max())
    y_col = f"y_up_h{horizon}"
    if y_col in df.columns and len(df):
        counts = df[y_col].value_counts(dropna=True).to_dict()
        stats["class_balance"] = {str(k): int(v) for k, v in counts.items()}
    return stats


def _evaluate_thresholds(y_true: np.ndarray, y_prob: np.ndarray) -> Dict[str, float]:
    return {"p_up": 0.55}


def _metrics(y_true: np.ndarray, y_prob: np.ndarray) -> Dict[str, object]:
    metrics: Dict[str, object] = {}
    try:
        from sklearn.metrics import roc_auc_score, confusion_matrix, precision_recall_fscore_support

        if len(np.unique(y_true)) > 1:
            metrics["auc"] = float(roc_auc_score(y_true, y_prob))
        for thr in (0.50, 0.55, 0.60):
            preds = (y_prob >= thr).astype(int)
            precision, recall, fscore, _ = precision_recall_fscore_support(y_true, preds, average="binary", zero_division=0)
            metrics[f"precision@{thr}"] = float(precision)
            metrics[f"recall@{thr}"] = float(recall)
            metrics[f"f1@{thr}"] = float(fscore)
            metrics[f"confusion@{thr}"] = confusion_matrix(y_true, preds).tolist()
    except Exception:
        pass
    return metrics


def _train_horizon(
    symbol: str,
    horizon: int,
    data_root: Path,
    out_root: Path,
    cfg: TrainConfig,
) -> Path:
    horizon_dir = data_root / f"horizon_h{horizon}"
    train_df, val_df, test_df = _load_splits(horizon_dir, horizon)
    features = feature_names()
    y_col = f"y_up_h{horizon}"

    if train_df.empty or val_df.empty or test_df.empty:
        raise ValueError(f"Empty split detected for h{horizon}: train={len(train_df)} val={len(val_df)} test={len(test_df)}")
    missing = [name for name in features + [y_col] if name not in train_df.columns]
    if missing:
        raise KeyError(f"Missing columns in training data for h{horizon}: {missing}")

    X_train = train_df[features].to_numpy(dtype=float)
    y_train = train_df[y_col].to_numpy(dtype=int)
    X_val = val_df[features].to_numpy(dtype=float)
    y_val = val_df[y_col].to_numpy(dtype=int)
    X_test = test_df[features].to_numpy(dtype=float)
    y_test = test_df[y_col].to_numpy(dtype=int)

    model = xgb.XGBClassifier(
        n_estimators=cfg.n_estimators,
        max_depth=cfg.max_depth,
        learning_rate=cfg.learning_rate,
        subsample=cfg.subsample,
        colsample_bytree=cfg.colsample_bytree,
        random_state=cfg.random_state,
        eval_metric="logloss",
        n_jobs=1,
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    y_prob = model.predict_proba(X_test)[:, 1]
    metrics = _metrics(y_test, y_prob)
    thresholds = _evaluate_thresholds(y_test, y_prob)

    out_dir = out_root / symbol / f"h{horizon}"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.json"
    model.save_model(str(model_path))

    dataset_hashes = {
        "train": _sha256_file(_find_split_file(horizon_dir, "train")),
        "val": _sha256_file(_find_split_file(horizon_dir, "val")),
        "test": _sha256_file(_find_split_file(horizon_dir, "test")),
    }
    train_stats = _training_stats(train_df, horizon)
    manifest = _build_manifest(symbol, horizon, model_path, metrics, thresholds, dataset_hashes, train_stats)

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (out_dir / "thresholds.json").write_text(json.dumps(thresholds, indent=2), encoding="utf-8")
    (out_dir / "schema_hash.txt").write_text(schema_hash() + "\n", encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir


def _find_qe_root() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "QuantumEdge.py").exists():
            return parent
    return None


def _publish_runtime(model_dir: Path, symbol: str, horizon: int, runtime_root: Path) -> Path:
    models_root = runtime_root / "models" / symbol / str(horizon)
    tmp_dir = models_root / f"current.tmp.{os.getpid()}"
    dest_dir = models_root / "current"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for item in model_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, tmp_dir / item.name)

    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    os.replace(tmp_dir, dest_dir)
    return dest_dir


def train_models(
    symbol: str,
    data_root: Path,
    artifacts_root: Path,
    horizons: List[int],
    publish_runtime: bool,
    runtime_root: Optional[Path],
    cfg: TrainConfig,
) -> Dict[int, Path]:
    outputs: Dict[int, Path] = {}
    for horizon in horizons:
        out_dir = _train_horizon(symbol, horizon, data_root, artifacts_root, cfg)
        outputs[int(horizon)] = out_dir
        if publish_runtime and runtime_root is not None:
            _publish_runtime(out_dir, symbol, horizon, runtime_root)
    return outputs


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train multi-horizon signal models.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data-root", required=True, help="data/ml/<SYMBOL>")
    parser.add_argument("--horizons", nargs="+", default=["1", "5", "30"])
    parser.add_argument("--artifacts-root", default="artifacts/models")
    parser.add_argument("--publish-runtime", action="store_true", help="Publish to runtime/models if QE_ROOT is found.")
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--subsample", type=float, default=0.8)
    parser.add_argument("--colsample-bytree", type=float, default=0.8)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    horizons = [int(h) for h in args.horizons]
    data_root = Path(args.data_root)
    artifacts_root = Path(args.artifacts_root)
    runtime_root = None
    qe_root = _find_qe_root()
    if qe_root:
        runtime_root = qe_root / "runtime"
    publish_runtime = bool(args.publish_runtime) and runtime_root is not None

    cfg = TrainConfig(
        horizons=horizons,
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        learning_rate=args.learning_rate,
        subsample=args.subsample,
        colsample_bytree=args.colsample_bytree,
        random_state=args.seed,
    )
    train_models(
        symbol=args.symbol,
        data_root=data_root,
        artifacts_root=artifacts_root,
        horizons=horizons,
        publish_runtime=publish_runtime,
        runtime_root=runtime_root,
        cfg=cfg,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
