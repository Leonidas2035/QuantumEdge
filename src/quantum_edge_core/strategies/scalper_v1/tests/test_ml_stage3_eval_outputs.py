import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from hermes.research.offline.scalper_bot.ml.eval.run import (
    evaluate,
)
from bot.ml.features.builder import feature_names


def _make_dataset(path: Path, rows: int) -> None:
    features = feature_names()
    data = {name: np.random.random(rows) for name in features}
    data["ts_ms"] = np.arange(rows) * 1000
    data["scenario_id"] = ["S00"] * rows
    data["episode_id"] = ["ep_00001"] * rows
    data["fut_ret_h1"] = np.random.uniform(-0.001, 0.001, rows)
    data["y_up_h1"] = (data["fut_ret_h1"] > 0).astype(int)
    df = pd.DataFrame(data)
    df.to_csv(path, index=False)


def test_eval_outputs(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "ml" / "BTCUSDT" / "horizon_h1"
    data_root.mkdir(parents=True, exist_ok=True)
    _make_dataset(data_root / "train.csv", 50)
    _make_dataset(data_root / "val.csv", 30)
    _make_dataset(data_root / "test.csv", 30)

    model_dir = tmp_path / "artifacts" / "models" / "BTCUSDT" / "h1"
    model_dir.mkdir(parents=True, exist_ok=True)

    train = pd.read_csv(data_root / "train.csv")
    xgboost_model = xgb.XGBClassifier(
        n_estimators=5, max_depth=2, eval_metric="logloss"
    )
    xgboost_model.fit(train[feature_names()], train["y_up_h1"])
    xgboost_model.save_model(str(model_dir / "model.json"))

    out_root = tmp_path / "artifacts" / "eval" / "BTCUSDT"
    evaluate(
        symbol="BTCUSDT",
        data_root=data_root.parent,
        models_root=model_dir.parent.parent,
        out_root=out_root,
        thresholds=[0.5],
    )

    manifest = out_root / "eval_manifest.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["symbol"] == "BTCUSDT"
