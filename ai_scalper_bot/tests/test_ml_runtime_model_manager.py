import json
from pathlib import Path

from bot.ml.runtime.model_manager import ModelManager


def test_model_manager_schema_mismatch(tmp_path: Path) -> None:
    model_dir = tmp_path / "artifacts" / "models" / "BTCUSDT" / "h1"
    model_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": "model.v1",
        "symbol": "BTCUSDT",
        "horizon": 1,
        "schema_hash": "bad-hash",
        "files": {"model": {"path": "model.json", "sha256": "x"}},
    }
    (model_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    mgr = ModelManager(symbol="BTCUSDT", horizons=[1], models_root=tmp_path / "artifacts" / "models", source="artifacts")
    mgr.load()
    assert mgr.errors.get(1) == "SCHEMA_HASH_MISMATCH"
