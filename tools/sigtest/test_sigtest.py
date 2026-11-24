"""Smoke-tests для sigtest (детерміновані, БЕЗ мережі/кешу)."""
import numpy as np
import pandas as pd
from tools.sigtest.indicators import enrich
from tools.sigtest.engine import backtest_signals, _rules


def _synth(n=400, seed=3):
    rng = np.random.default_rng(seed)
    close = np.cumsum(rng.standard_normal(n)) + 1000
    return pd.DataFrame({
        "ts": range(n),
        "open": close, "high": close + 2, "low": close - 2,
        "close": close, "volume": rng.random(n) * 1000 + 100,
    })


def test_enrich_adds_columns():
    d = enrich(_synth())
    for col in ["ema9", "ema21", "ema50", "adx", "macd_hist", "rsi",
                "bb_pos", "bb_width", "atr_pct", "obv_slope"]:
        assert col in d.columns


def test_rules_output_range():
    d = enrich(_synth())
    row = list(d.itertuples(index=False))[-1]
    r = _rules(row)
    for v in r.values():
        assert v in (-1, 0, 1)


def test_backtest_signals_structure():
    d = enrich(_synth())
    res = backtest_signals(d, horizon=12, warmup=60)
    assert "per_rule" in res and "composite" in res
    for r in res["per_rule"]:
        assert 0.0 <= r["win_rate"] <= 1.0
        assert r["n"] >= 1
        assert r["long_n"] + r["short_n"] <= r["n"] + 1  # sanity
