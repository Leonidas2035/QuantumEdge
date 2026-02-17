import json
from pathlib import Path

import pandas as pd

from bot.ml.datasets.build_from_scenarios import build_from_scenarios
from bot.ml.signal_model.train import TrainConfig, train_models


def _write_episode(path: Path, start_ts_ms: int, rows: int = 120) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for idx in range(rows):
        ts_ms = start_ts_ms + idx * 1000
        price = 100.0 + idx * 0.01
        qty = 1.0
        side = "buy" if idx % 2 == 0 else "sell"
        data.append([ts_ms, price, qty, side, "", "", ""])
    df = pd.DataFrame(
        data, columns=["ts_ms", "price", "qty", "side", "bid", "ask", "depth_usd"]
    )
    df.to_csv(path, index=False)


def _setup_scenarios(tmp_path: Path) -> Path:
    root = tmp_path / "scenarios" / "BTCUSDT"
    scenario_dir = root / "S00" / "episodes"
    episodes = []
    for idx in range(3):
        episode_id = f"ep_{idx:05d}"
        ep_path = scenario_dir / f"{episode_id}.csv"
        _write_episode(ep_path, start_ts_ms=1_700_000_000_000 + idx * 200_000)
        episodes.append(
            {
                "scenario_id": "S00",
                "episode_id": episode_id,
                "path": f"S00/episodes/{episode_id}.csv",
                "start_ts_ms": 1_700_000_000_000 + idx * 200_000,
                "end_ts_ms": 1_700_000_000_000 + idx * 200_000 + 119_000,
            }
        )

    splits_dir = root / "splits"
    splits_dir.mkdir(parents=True, exist_ok=True)
    split_time = {"train": [episodes[0]], "val": [episodes[1]], "test": [episodes[2]]}
    (splits_dir / "split_time.json").write_text(
        json.dumps(split_time, indent=2), encoding="utf-8"
    )
    return root


def test_build_and_train_stage2(tmp_path: Path) -> None:
    scenarios_root = _setup_scenarios(tmp_path)
    out_root = tmp_path / "ml" / "BTCUSDT"

    build_from_scenarios(
        symbol="BTCUSDT",
        scenarios_root=scenarios_root,
        out_root=out_root,
        horizons=[1, 5, 30],
        label_mode="seconds",
        label_thr_bps=0.0,
        ignore_thr_bps=0.0,
        fee_bps=0.0,
        slippage_bps=0.0,
        output_format="csv",
    )

    train_path = out_root / "horizon_h1" / "train.csv"
    assert train_path.exists()
    assert len(pd.read_csv(train_path)) > 0

    artifacts_root = tmp_path / "artifacts" / "models"
    cfg = TrainConfig(horizons=[1], n_estimators=5, max_depth=2, learning_rate=0.1)
    outputs = train_models(
        symbol="BTCUSDT",
        data_root=out_root,
        artifacts_root=artifacts_root,
        horizons=[1],
        publish_runtime=False,
        runtime_root=None,
        cfg=cfg,
    )
    model_dir = outputs[1]
    assert (model_dir / "model.json").exists()
    assert (model_dir / "manifest.json").exists()
