import numpy as np
import pandas as pd

from hermes.research.offline.scalper_bot.ml.eval.tune_policy import (
    CostConfig,
    select_thresholds,
)


def test_tuner_determinism():
    rows = 50
    data = {
        "ts_ms": np.arange(rows) * 1000,
        "scenario_id": ["S00"] * rows,
        "episode_id": ["ep_00001"] * rows,
        "p_up_h1": np.linspace(0.4, 0.9, rows),
        "p_up_h5": np.linspace(0.45, 0.85, rows),
        "p_up_h30": np.linspace(0.5, 0.8, rows),
        "fut_ret_h1": np.linspace(-0.001, 0.002, rows),
        "fut_ret_h5": np.linspace(-0.001, 0.002, rows),
        "fut_ret_h30": np.linspace(-0.001, 0.002, rows),
    }
    df = pd.DataFrame(data)
    horizons = [1, 5, 30]
    grid = [0.5, 0.6]
    cost = CostConfig()

    thresholds_1, meta_1 = select_thresholds(df, horizons, grid, cost, min_coverage=0.1)
    thresholds_2, meta_2 = select_thresholds(df, horizons, grid, cost, min_coverage=0.1)

    assert thresholds_1 == thresholds_2
    assert meta_1 == meta_2
