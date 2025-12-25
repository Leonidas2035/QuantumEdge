"""Runtime model loader using published manifests."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from bot.ml.signal_model.model import SignalModel

MANIFEST_VERSION = "model.v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> Dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest must be a dict")
    if raw.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("manifest_version mismatch")
    if "symbol" not in raw or "horizon" not in raw:
        raise ValueError("manifest missing symbol or horizon")
    files = raw.get("files") or {}
    model = files.get("model") or {}
    if not isinstance(model, dict):
        raise ValueError("manifest missing model file info")
    if not model.get("path") or not model.get("sha256"):
        raise ValueError("manifest missing model path or sha256")
    return raw


@dataclass
class RuntimeModelInfo:
    model: SignalModel
    threshold: float
    manifest_path: Path


def load_runtime_models(
    symbol: str,
    horizons: list[int],
    models_root: Path,
    threshold_default: float = 0.55,
) -> Tuple[Dict[int, RuntimeModelInfo], Dict[int, str]]:
    symbol = symbol.upper()
    models_root = models_root.resolve()
    models: Dict[int, RuntimeModelInfo] = {}
    errors: Dict[int, str] = {}

    for horizon in horizons:
        manifest_path = models_root / symbol / str(horizon) / "current" / "manifest.json"
        if not manifest_path.exists():
            errors[horizon] = "manifest_missing"
            continue
        try:
            manifest = _load_manifest(manifest_path)
            if manifest.get("symbol") != symbol or int(manifest.get("horizon")) != int(horizon):
                errors[horizon] = "symbol_or_horizon_mismatch"
                continue
            model_rel = Path(str(manifest["files"]["model"]["path"]))
            model_path = manifest_path.parent / model_rel
            if not model_path.exists():
                errors[horizon] = "model_missing"
                continue
            sha_expected = str(manifest["files"]["model"]["sha256"])
            sha_actual = _sha256_file(model_path)
            if sha_actual != sha_expected:
                errors[horizon] = "sha_mismatch"
                continue
            threshold = float((manifest.get("thresholds") or {}).get("p_up", threshold_default))
            model = SignalModel(symbol=symbol, horizon=int(horizon), model_path=model_path)
            models[int(horizon)] = RuntimeModelInfo(model=model, threshold=threshold, manifest_path=manifest_path)
        except Exception as exc:
            errors[horizon] = f"manifest_invalid:{exc}"

    return models, errors


def resolve_runtime_root() -> Path:
    env_runtime = os.getenv("QE_RUNTIME_DIR")
    if env_runtime:
        return Path(env_runtime).resolve()
    return Path(os.getenv("QE_ROOT") or Path(__file__).resolve().parents[4]) / "runtime"


def resolve_models_root() -> Path:
    return resolve_runtime_root() / "models"
