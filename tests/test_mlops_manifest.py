from pathlib import Path

from SupervisorAgent.mlops.manifest import ModelManifest, MANIFEST_VERSION, validate_manifest
from SupervisorAgent.mlops.registry import sha256_file
from SupervisorAgent.mlops.publisher import publish_model


def test_manifest_roundtrip(tmp_path: Path):
    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(b"dummy-model")
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
    )
    manifest_path = tmp_path / "manifest.json"
    manifest.write(manifest_path)
    loaded = ModelManifest.load(manifest_path)
    assert loaded.symbol == "BTCUSDT"
    assert loaded.manifest_version == MANIFEST_VERSION
    assert loaded.files["model"]["sha256"]


def test_publish_atomic(tmp_path: Path):
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    model_path = artifact_dir / "model.pkl"
    model_path.write_bytes(b"dummy-model")
    manifest = ModelManifest.new(
        symbol="BTCUSDT",
        horizon=1,
        model_type="signal_model",
        features_version="feat.v1",
        model_path=model_path.name,
        model_sha=sha256_file(model_path),
        training_data={"source": "ticks", "rows": 10},
    )
    manifest.write(artifact_dir / "manifest.json")
    runtime_root = tmp_path / "runtime"
    current_dir = publish_model(artifact_dir, runtime_root, keep_previous=False)
    assert (current_dir / "manifest.json").exists()
    assert (current_dir / "model.pkl").exists()


def test_manifest_validation():
    good = {
        "manifest_version": MANIFEST_VERSION,
        "symbol": "BTCUSDT",
        "horizon": 1,
        "model_type": "signal_model",
        "created_at": 1,
        "features_version": "feat.v1",
        "files": {"model": {"path": "model.pkl", "sha256": "abc"}},
    }
    assert validate_manifest(good)["symbol"] == "BTCUSDT"
