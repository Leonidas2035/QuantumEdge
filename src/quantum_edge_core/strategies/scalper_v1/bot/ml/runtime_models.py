"""Runtime model loader using published manifests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Dict, Optional, Tuple

from quantum_edge_core.strategies.scalper_v1.bot.ml.features.builder import \
    feature_names as _feature_names
from quantum_edge_core.strategies.scalper_v1.bot.ml.features.builder import \
    schema_version as _schema_version
from quantum_edge_core.strategies.scalper_v1.bot.ml.signal_model.model import \
    SignalModel

MANIFEST_VERSION = "model.v1"
_LOG = logging.getLogger("runtime_models")
_SUPPORTED_MODEL_FORMATS = {"xgboost_json"}


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


def _version_tuple(raw: str) -> Optional[Tuple[int, int]]:
    try:
        parts = str(raw).split(".")
        return int(parts[0]), int(parts[1])
    except Exception:
        return None


def _compat_check(manifest: Dict[str, object], strict: bool) -> Optional[str]:
    errors = []
    warnings = []

    model_format = manifest.get("model_format")
    if model_format:
        fmt = str(model_format).lower()
        if fmt not in _SUPPORTED_MODEL_FORMATS:
            msg = f"unsupported model_format={model_format}"
            if strict:
                errors.append(msg)
            else:
                warnings.append(msg)

    model_api = manifest.get("model_api")
    if model_api:
        api_name = str(model_api).lower()
        if api_name != "predict_proba":
            msg = f"unsupported model_api={model_api}"
            if strict:
                errors.append(msg)
            else:
                warnings.append(msg)

    artifact = manifest.get("artifact") or {}
    if isinstance(artifact, dict):
        py_version = artifact.get("python")
        if py_version:
            want = _version_tuple(str(py_version))
            have = (sys.version_info.major, sys.version_info.minor)
            if want and want != have:
                msg = (
                    f"python_mismatch artifact={py_version} runtime={have[0]}.{have[1]}"
                )
                if strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)
        platform_tag = artifact.get("platform")
        if platform_tag:
            current = sys.platform
            if str(platform_tag).lower() != current.lower():
                msg = f"platform_mismatch artifact={platform_tag} runtime={current}"
                if strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)
        serializer = artifact.get("serializer")
        if serializer:
            fmt = str(serializer).lower()
            if fmt not in _SUPPORTED_MODEL_FORMATS:
                msg = f"serializer_unsupported={serializer}"
                if strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)
        lib_versions = artifact.get("lib_versions") or {}
        if isinstance(lib_versions, dict):
            for name, expected in lib_versions.items():
                try:
                    current = importlib_metadata.version(str(name))
                except importlib_metadata.PackageNotFoundError:
                    continue
                want = _version_tuple(str(expected))
                have = _version_tuple(str(current))
                if want and have and want[0] != have[0]:
                    msg = f"lib_major_mismatch {name} expected={expected} runtime={current}"
                    if strict:
                        errors.append(msg)
                    else:
                        warnings.append(msg)

    for warning in warnings:
        _LOG.warning("Model compatibility warning: %s", warning)

    if errors:
        return ";".join(errors)
    return None


def _schema_check(manifest: Dict[str, object]) -> Optional[str]:
    expected_version = _schema_version()
    expected_names = _feature_names()
    version = manifest.get("feature_schema_version")
    if version and str(version) != expected_version:
        return f"schema_version_mismatch manifest={version} runtime={expected_version}"
    names = manifest.get("feature_names")
    if names:
        try:
            names_list = list(names)
        except Exception:
            return "feature_names_invalid"
        if names_list != expected_names:
            return "feature_names_mismatch"
    return None


@dataclass
class RuntimeModelInfo:
    model: SignalModel
    threshold: float
    manifest_path: Path
    manifest_hash: str
    feature_schema_version: Optional[str]
    feature_names: Optional[list[str]]
    feature_stats: Dict[str, object]
    calibration: Dict[str, object]


def load_runtime_models(
    symbol: str,
    horizons: list[int],
    models_root: Path,
    threshold_default: float = 0.55,
    compat_strict: bool = False,
    backend: Optional[str] = None,
) -> Tuple[Dict[int, RuntimeModelInfo], Dict[int, str]]:
    symbol = symbol.upper()
    models_root = models_root.resolve()
    models: Dict[int, RuntimeModelInfo] = {}
    errors: Dict[int, str] = {}

    for horizon in horizons:
        manifest_path = (
            models_root / symbol / str(horizon) / "current" / "manifest.json"
        )
        if not manifest_path.exists():
            errors[horizon] = "manifest_missing"
            continue
        try:
            manifest = _load_manifest(manifest_path)
            if manifest.get("symbol") != symbol or int(manifest.get("horizon")) != int(
                horizon
            ):
                errors[horizon] = "symbol_or_horizon_mismatch"
                continue
            compat_error = _compat_check(manifest, compat_strict)
            if compat_error:
                errors[horizon] = f"compat_mismatch:{compat_error}"
                continue
            schema_error = _schema_check(manifest)
            if schema_error:
                errors[horizon] = f"schema_mismatch:{schema_error}"
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
            threshold = float(
                (manifest.get("thresholds") or {}).get("p_up", threshold_default)
            )
            calibration = manifest.get("calibration") or {}
            feature_stats = manifest.get("feature_stats") or {}
            model = SignalModel(
                symbol=symbol,
                horizon=int(horizon),
                model_path=model_path,
                calibration=calibration if isinstance(calibration, dict) else None,
                backend=backend,
            )
            manifest_hash = _sha256_file(manifest_path)
            models[int(horizon)] = RuntimeModelInfo(
                model=model,
                threshold=threshold,
                manifest_path=manifest_path,
                manifest_hash=manifest_hash,
                feature_schema_version=manifest.get("feature_schema_version"),
                feature_names=(
                    list(manifest.get("feature_names") or [])
                    if manifest.get("feature_names")
                    else None
                ),
                feature_stats=feature_stats if isinstance(feature_stats, dict) else {},
                calibration=calibration if isinstance(calibration, dict) else {},
            )
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
