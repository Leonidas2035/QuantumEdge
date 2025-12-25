"""Model artifact manifest helpers (model.v1)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

MANIFEST_VERSION = "model.v1"


def _require(value: Any, name: str, expected: tuple[type, ...]) -> Any:
    if not isinstance(value, expected):
        raise ValueError(f"{name} must be {expected}, got {type(value).__name__}")
    return value


def _require_str(value: Any, name: str) -> str:
    value = _require(value, name, (str,))
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_int(value: Any, name: str, minimum: int = 0) -> int:
    value = _require(value, name, (int,))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def validate_manifest(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("manifest payload must be a dict")

    manifest_version = _require_str(data.get("manifest_version"), "manifest_version")
    symbol = _require_str(data.get("symbol"), "symbol")
    horizon = _require_int(data.get("horizon"), "horizon", minimum=1)
    model_type = _require_str(data.get("model_type"), "model_type")
    created_at = _require_int(data.get("created_at"), "created_at", minimum=0)
    features_version = _require_str(data.get("features_version"), "features_version")

    files = data.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError("files must be an object")
    model = files.get("model") or {}
    if not isinstance(model, dict):
        raise ValueError("files.model must be an object")
    model_path = _require_str(model.get("path"), "files.model.path")
    model_sha = _require_str(model.get("sha256"), "files.model.sha256")

    model_format = data.get("model_format")
    if model_format is not None:
        model_format = _require_str(model_format, "model_format")
    model_api = data.get("model_api")
    if model_api is not None:
        model_api = _require_str(model_api, "model_api")

    artifact = data.get("artifact") or {}
    if artifact and not isinstance(artifact, dict):
        raise ValueError("artifact must be an object")
    artifact_out: Dict[str, Any] = {}
    if "python" in artifact:
        artifact_out["python"] = _require_str(artifact.get("python"), "artifact.python")
    if "platform" in artifact:
        artifact_out["platform"] = _require_str(artifact.get("platform"), "artifact.platform")
    if "serializer" in artifact:
        artifact_out["serializer"] = _require_str(artifact.get("serializer"), "artifact.serializer")
    if "lib_versions" in artifact:
        lib_versions = artifact.get("lib_versions")
        if not isinstance(lib_versions, dict):
            raise ValueError("artifact.lib_versions must be an object")
        cleaned = {}
        for key, value in lib_versions.items():
            cleaned[str(key)] = _require_str(value, f"artifact.lib_versions.{key}")
        artifact_out["lib_versions"] = cleaned

    return {
        "manifest_version": manifest_version,
        "symbol": symbol,
        "horizon": horizon,
        "model_type": model_type,
        "created_at": created_at,
        "features_version": features_version,
        "training_data": data.get("training_data") or {},
        "metrics": data.get("metrics") or {},
        "thresholds": data.get("thresholds") or {},
        "files": {"model": {"path": model_path, "sha256": model_sha}},
        "model_format": model_format,
        "model_api": model_api,
        "artifact": artifact_out,
    }


@dataclass
class ModelManifest:
    manifest_version: str
    symbol: str
    horizon: int
    model_type: str
    created_at: int
    features_version: str
    training_data: Dict[str, Any]
    metrics: Dict[str, Any]
    thresholds: Dict[str, Any]
    files: Dict[str, Dict[str, str]]
    model_format: Optional[str] = None
    model_api: Optional[str] = None
    artifact: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        symbol: str,
        horizon: int,
        model_type: str,
        features_version: str,
        model_path: str,
        model_sha: str,
        training_data: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        thresholds: Optional[Dict[str, Any]] = None,
        created_at: Optional[int] = None,
        model_format: Optional[str] = None,
        model_api: Optional[str] = None,
        artifact: Optional[Dict[str, Any]] = None,
    ) -> "ModelManifest":
        payload = {
            "manifest_version": MANIFEST_VERSION,
            "symbol": symbol,
            "horizon": int(horizon),
            "model_type": model_type,
            "created_at": int(created_at or time.time()),
            "features_version": features_version,
            "training_data": training_data or {},
            "metrics": metrics or {},
            "thresholds": thresholds or {},
            "files": {"model": {"path": model_path, "sha256": model_sha}},
            "model_format": model_format,
            "model_api": model_api,
            "artifact": artifact or {},
        }
        validated = validate_manifest(payload)
        return cls(**validated)

    @classmethod
    def load(cls, path: Path) -> "ModelManifest":
        raw = json.loads(path.read_text(encoding="utf-8"))
        validated = validate_manifest(raw)
        return cls(**validated)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "manifest_version": self.manifest_version,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "model_type": self.model_type,
            "created_at": self.created_at,
            "features_version": self.features_version,
            "training_data": self.training_data,
            "metrics": self.metrics,
            "thresholds": self.thresholds,
            "files": self.files,
        }
        if self.model_format:
            payload["model_format"] = self.model_format
        if self.model_api:
            payload["model_api"] = self.model_api
        if self.artifact is not None:
            payload["artifact"] = self.artifact
        return payload

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
