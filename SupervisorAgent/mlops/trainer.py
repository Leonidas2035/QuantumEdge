"""Model training for ModelOps."""

from __future__ import annotations

import platform
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from SupervisorAgent.mlops.manifest import ModelManifest
from SupervisorAgent.mlops.registry import sha256_file
from SupervisorAgent.research.offline.signal_model.train import train_model
from importlib import metadata as importlib_metadata


def _version_tag() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _collect_lib_versions(names: List[str]) -> Dict[str, str]:
    versions: Dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            continue
    return versions


def train_horizons(
    symbol: str,
    horizons: List[int],
    source: str,
    input_dir: Optional[Path],
    artifacts_root: Path,
    min_rows: int,
    thresholds: Optional[Dict[str, float]] = None,
) -> List[Path]:
    symbol = symbol.upper()
    artifacts_root = artifacts_root.resolve()
    thresholds = thresholds or {"p_up": 0.55}
    manifests: List[Path] = []
    version = _version_tag()

    for horizon in horizons:
        artifact_dir = artifacts_root / "models" / symbol / str(horizon) / version
        model_dir = artifact_dir
        dataset_dir = artifacts_root / "datasets" / symbol / source / version
        model_dir.mkdir(parents=True, exist_ok=True)
        dataset_dir.mkdir(parents=True, exist_ok=True)

        ok, info = train_model(
            symbol=symbol,
            horizon=int(horizon),
            min_rows=min_rows,
            model_dir=model_dir,
            dataset_dir=dataset_dir,
            data_dir=input_dir,
            limit_files=None,
        )
        if not ok:
            raise RuntimeError(f"Training failed for {symbol} horizon {horizon}: {info.get('error')}")

        model_path = Path(info["model_path"])
        model_sha = sha256_file(model_path)
        artifact_meta = {
            "python": platform.python_version(),
            "platform": sys.platform,
            "serializer": "xgboost_json",
            "lib_versions": _collect_lib_versions(["numpy", "pandas", "xgboost"]),
        }
        manifest = ModelManifest.new(
            symbol=symbol,
            horizon=int(horizon),
            model_type="signal_model",
            features_version="feat.v1",
            model_path=model_path.name,
            model_sha=model_sha,
            training_data={
                "source": source,
                "rows": info.get("rows"),
                "start_ts": None,
                "end_ts": None,
            },
            metrics=info.get("metrics") or {},
            thresholds=thresholds,
            created_at=int(time.time()),
            model_format="xgboost_json",
            model_api="predict_proba",
            artifact=artifact_meta,
        )
        manifest_path = artifact_dir / "manifest.json"
        manifest.write(manifest_path)
        manifests.append(manifest_path)

    return manifests
