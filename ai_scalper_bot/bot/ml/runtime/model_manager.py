"""Runtime model manager for multi-horizon inference."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from bot.ml.features.builder import feature_names as schema_feature_names, schema_hash as current_schema_hash
from bot.ml.signal_model.model import SignalModel, SignalOutput


MANIFEST_VERSION = "model.v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a dict")
    return data


@dataclass
class ModelEntry:
    horizon: int
    model: SignalModel
    threshold: float
    manifest_path: Path
    manifest_hash: str
    schema_hash: Optional[str]
    feature_names: Optional[list[str]]
    feature_stats: Dict[str, object]


@dataclass
class PredictionResult:
    outputs: Dict[int, SignalOutput]
    latency_ms: float


class ModelManager:
    def __init__(
        self,
        symbol: str,
        horizons: list[int],
        models_root: Path,
        source: str = "runtime",
        reload_interval_s: float = 30.0,
    ):
        self.symbol = symbol.upper()
        self.horizons = list(horizons)
        self.models_root = models_root
        self.source = source
        self.reload_interval_s = reload_interval_s
        self.last_load_ts = 0.0
        self.entries: Dict[int, ModelEntry] = {}
        self.errors: Dict[int, str] = {}
        self.model_versions: Dict[int, str] = {}
        self.feature_stats: Dict[str, object] = {}

    def load(self) -> None:
        self.entries = {}
        self.errors = {}
        self.model_versions = {}
        self.feature_stats = {}

        for horizon in self.horizons:
            manifest_path = self._manifest_path(horizon)
            if not manifest_path.exists():
                self.errors[horizon] = "MODEL_MISSING"
                continue
            try:
                manifest = _load_json(manifest_path)
                if manifest.get("manifest_version") != MANIFEST_VERSION:
                    self.errors[horizon] = "MANIFEST_VERSION_MISMATCH"
                    continue
                if str(manifest.get("symbol")).upper() != self.symbol or int(manifest.get("horizon")) != int(horizon):
                    self.errors[horizon] = "MANIFEST_SYMBOL_MISMATCH"
                    continue
                schema_hash = manifest.get("schema_hash") or manifest.get("feature_schema_hash")
                if schema_hash and schema_hash != current_schema_hash():
                    self.errors[horizon] = "SCHEMA_HASH_MISMATCH"
                    continue
                names = manifest.get("feature_names")
                if names:
                    expected = schema_feature_names()
                    if list(names) != expected:
                        self.errors[horizon] = "FEATURE_NAMES_MISMATCH"
                        continue
                files = manifest.get("files") or {}
                model_info = files.get("model") or {}
                model_rel = model_info.get("path")
                if not model_rel:
                    self.errors[horizon] = "MODEL_PATH_MISSING"
                    continue
                model_path = manifest_path.parent / str(model_rel)
                if not model_path.exists():
                    self.errors[horizon] = "MODEL_FILE_MISSING"
                    continue
                model = SignalModel(symbol=self.symbol, horizon=int(horizon), model_path=model_path)
                threshold = float((manifest.get("thresholds") or {}).get("p_up", 0.55))
                manifest_hash = _sha256_file(manifest_path)
                entry = ModelEntry(
                    horizon=int(horizon),
                    model=model,
                    threshold=threshold,
                    manifest_path=manifest_path,
                    manifest_hash=manifest_hash,
                    schema_hash=schema_hash,
                    feature_names=list(names) if names else None,
                    feature_stats=manifest.get("feature_stats") or {},
                )
                self.entries[int(horizon)] = entry
                self.model_versions[int(horizon)] = manifest_hash
                if entry.feature_stats:
                    self.feature_stats = entry.feature_stats
            except Exception:
                self.errors[horizon] = "MANIFEST_INVALID"

        self.last_load_ts = time.time()

    def maybe_reload(self) -> None:
        if time.time() - self.last_load_ts < self.reload_interval_s:
            return
        self.load()

    def _manifest_path(self, horizon: int) -> Path:
        if self.source == "artifacts":
            return self.models_root / self.symbol / f"h{horizon}" / "manifest.json"
        return self.models_root / self.symbol / str(horizon) / "current" / "manifest.json"

    def predict(self, features) -> PredictionResult:
        outputs: Dict[int, SignalOutput] = {}
        start = time.perf_counter()
        for horizon, entry in self.entries.items():
            try:
                outputs[horizon] = entry.model.predict_proba(features)
            except Exception:
                self.errors[horizon] = "INFERENCE_ERROR"
        latency_ms = (time.perf_counter() - start) * 1000.0
        return PredictionResult(outputs=outputs, latency_ms=latency_ms)

    def ready(self) -> bool:
        return not self.errors and bool(self.entries)

    def thresholds(self) -> Dict[int, float]:
        return {h: entry.threshold for h, entry in self.entries.items()}
