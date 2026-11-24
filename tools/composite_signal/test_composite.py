"""
Unit tests для композитного двигуна сигналів та його бектестера.
Запуск: cd /home/layman/QuantumEdge && .venv/bin/python3 -m pytest tools/composite_signal/test_composite.py -q
"""
import math
import pandas as pd
import pytest
from tools.composite_signal.engine import calculate_composite_score
from tools.composite_signal.backtest import backtest_composite, pool_adjacent_violators, calibrate_pava_composite


def _synth_ohlcv(n=100, start=60000.0, trend=0.0):
    rows = []
    ts = 1_700_000_000_000
    p = start
    for i in range(n):
        o = p
        c = p * (1 + 0.002 * math.sin(i / 5.0) + trend)
        h = max(o, c) * 1.003
        l = min(o, c) * 0.997
        v = 1000.0 + 10 * i
        rows.append([ts + i * 3_600_000, o, h, l, c, v])
        p = c
    return rows


def test_calculate_composite_score_range():
    candles = _synth_ohlcv(120)
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    
    res = calculate_composite_score(df)
    assert "score" in res
    assert -100 <= res["score"] <= 100
    assert "trend" in res
    assert "momentum" in res
    assert "volatility" in res
    assert "volume_flow" in res
    assert "liq_map" in res
    assert res["atr"] >= 0


def test_pava_composite_monotonicity_logic():
    # Робота PAVA для LONG (позитивні бакети)
    y = [0.4, 0.6, 0.5]
    w = [10, 10, 10]
    res = pool_adjacent_violators(y, w)
    assert res[0] <= res[1] <= res[2]

    # Робота калібратора
    sample_data = [
        {"bucket": "10 to 40", "side": "LONG", "hit_rate": 0.4, "n": 10},
        {"bucket": "40 to 80", "side": "LONG", "hit_rate": 0.6, "n": 10},
        {"bucket": "80 to 100", "side": "LONG", "hit_rate": 0.5, "n": 10},
    ]
    cal = calibrate_pava_composite(sample_data)
    confidences = [item["calibrated_confidence"] for item in sorted(cal, key=lambda x: x["bucket"])]
    for i in range(len(confidences) - 1):
        assert confidences[i] <= confidences[i+1] + 1e-9


def test_backtest_composite_runs():
    candles = _synth_ohlcv(200)
    res = backtest_composite(candles, horizon=12, warmup=100, step=12, bins=50)
    assert isinstance(res, dict)
    assert "all" in res
    for r in res["all"]:
        assert 0.0 <= r["hit_rate"] <= 1.0
        assert 0.0 <= r["calibrated_confidence"] <= 1.0
        assert r["n"] >= 0
        assert r["side"] in ("LONG", "SHORT")
