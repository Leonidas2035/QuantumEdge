import hashlib
import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_ROOT = REPO_ROOT / "ai_scalper_bot"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if BOT_ROOT.exists() and str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))
if "xgboost" not in sys.modules:
    stub = types.ModuleType("xgboost")
    stub.XGBClassifier = object
    sys.modules["xgboost"] = stub

import bot.ml.runtime_models as rm  # noqa: E402


class DummySignalModel:
    def __init__(self, *args, **kwargs):
        self.model_path = kwargs.get("model_path")


def _sha256(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def test_runtime_models_valid(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(rm, "SignalModel", DummySignalModel)
    models_root = tmp_path / "models"
    manifest_dir = models_root / "BTCUSDT" / "1" / "current"
    manifest_dir.mkdir(parents=True)
    model_path = manifest_dir / "model.pkl"
    model_path.write_bytes(b"dummy")
    manifest = {
        "manifest_version": rm.MANIFEST_VERSION,
        "symbol": "BTCUSDT",
        "horizon": 1,
        "model_type": "signal_model",
        "created_at": 1,
        "features_version": "feat.v1",
        "files": {"model": {"path": "model.pkl", "sha256": _sha256(b"dummy")}},
        "thresholds": {"p_up": 0.55},
    }
    (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    models, errors = rm.load_runtime_models("BTCUSDT", [1], models_root=models_root)
    assert 1 in models
    assert errors == {}


def test_runtime_models_sha_mismatch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(rm, "SignalModel", DummySignalModel)
    models_root = tmp_path / "models"
    manifest_dir = models_root / "BTCUSDT" / "1" / "current"
    manifest_dir.mkdir(parents=True)
    model_path = manifest_dir / "model.pkl"
    model_path.write_bytes(b"dummy")
    manifest = {
        "manifest_version": rm.MANIFEST_VERSION,
        "symbol": "BTCUSDT",
        "horizon": 1,
        "model_type": "signal_model",
        "created_at": 1,
        "features_version": "feat.v1",
        "files": {"model": {"path": "model.pkl", "sha256": "bad"}},
    }
    (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, errors = rm.load_runtime_models("BTCUSDT", [1], models_root=models_root)
    assert errors.get(1) == "sha_mismatch"


def test_runtime_models_compat_strict_blocks(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(rm, "SignalModel", DummySignalModel)
    models_root = tmp_path / "models"
    manifest_dir = models_root / "BTCUSDT" / "1" / "current"
    manifest_dir.mkdir(parents=True)
    model_path = manifest_dir / "model.pkl"
    model_path.write_bytes(b"dummy")
    manifest = {
        "manifest_version": rm.MANIFEST_VERSION,
        "symbol": "BTCUSDT",
        "horizon": 1,
        "model_type": "signal_model",
        "created_at": 1,
        "features_version": "feat.v1",
        "files": {"model": {"path": "model.pkl", "sha256": _sha256(b"dummy")}},
        "model_format": "xgboost_json",
        "model_api": "predict_proba",
        "artifact": {"python": "0.0.1", "platform": "linux", "serializer": "xgboost_json"},
    }
    (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _, errors = rm.load_runtime_models("BTCUSDT", [1], models_root=models_root, compat_strict=True)
    assert errors.get(1, "").startswith("compat_mismatch")
