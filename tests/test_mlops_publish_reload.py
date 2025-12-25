import json
import sys
import types
from pathlib import Path

from SupervisorAgent.mlops.manifest import ModelManifest
from SupervisorAgent.mlops.publisher import publish_model
from SupervisorAgent.mlops.registry import sha256_file

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def _write_artifact(path: Path, created_at: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    model_path = path / "model.pkl"
    model_path.write_bytes(f"model-{created_at}".encode("utf-8"))
    manifest = ModelManifest.new(
        symbol="BTCUSDT",
        horizon=1,
        model_type="signal_model",
        features_version="feat.v1",
        model_path=model_path.name,
        model_sha=sha256_file(model_path),
        training_data={"source": "ticks", "rows": 10},
        metrics={"accuracy": 0.5},
        thresholds={"p_up": 0.55},
        created_at=created_at,
        model_format="xgboost_json",
        model_api="predict_proba",
        artifact={"python": "3.11.0", "platform": "win32", "serializer": "xgboost_json"},
    )
    manifest.write(path / "manifest.json")


def test_publish_then_reload(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rm, "SignalModel", DummySignalModel)
    runtime_root = tmp_path / "runtime"
    artifacts_root = tmp_path / "artifacts"

    v1_dir = artifacts_root / "v1"
    _write_artifact(v1_dir, created_at=100)
    publish_model(v1_dir, runtime_root, keep_previous=False)
    manifest_path = runtime_root / "models" / "BTCUSDT" / "1" / "current" / "manifest.json"
    m1 = json.loads(manifest_path.read_text(encoding="utf-8"))
    models, errors = rm.load_runtime_models("BTCUSDT", [1], models_root=runtime_root / "models")
    assert 1 in models
    assert errors == {}

    v2_dir = artifacts_root / "v2"
    _write_artifact(v2_dir, created_at=200)
    publish_model(v2_dir, runtime_root, keep_previous=False)
    m2 = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert m2["created_at"] != m1["created_at"]
    models, errors = rm.load_runtime_models("BTCUSDT", [1], models_root=runtime_root / "models")
    assert 1 in models
    assert errors == {}
