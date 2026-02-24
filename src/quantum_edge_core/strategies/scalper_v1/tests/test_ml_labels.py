import numpy as np
import pandas as pd

from quantum_edge_core.supervisor.research.offline.scalper_bot.ml.labels.builder import (
    LabelConfig,
    build_labels,
)


def test_label_alignment_future_return():
    index = pd.date_range("2024-01-01", periods=4, freq="S")
    bars = pd.DataFrame({"price": [100.0, 101.0, 102.0, 103.0]}, index=index)
    config = LabelConfig(horizons=(1,), label_thr_bps=0.0)
    labels = build_labels(bars, config, price_col="price")

    assert np.isclose(labels["fut_ret_h1"].iloc[0], 0.01)
    assert labels["y_up_h1"].iloc[0] == 1.0
    assert np.isnan(labels["y_up_h1"].iloc[-1])
