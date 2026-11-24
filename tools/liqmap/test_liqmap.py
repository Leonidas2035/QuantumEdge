"""
Smoke-tests для карти ліквідацій (детерміновані, БЕЗ мережі).
Запуск: cd /home/layman/QuantumEdge && .venv/bin/python3 -m pytest tools/liqmap/test_liqmap.py -q
"""
import math
import pytest
from pathlib import Path
from tools.liqmap.model import build_heatmap, liq_price, LEVERAGE_MIX, classify_regime
from tools.liqmap.datafeed import get_cache_data, write_cache_data


def _synth_ohlcv(n=60, start=60000.0, trend=0.0):
    """Синтетичні свічки: плавний рух + коливання, стабільний обсяг."""
    rows = []
    ts = 1_700_000_000_000
    p = start
    for i in range(n):
        o = p
        # Додаємо трендову компоненту для тестів режимів
        c = p * (1 + 0.002 * math.sin(i / 5.0) + trend)
        h = max(o, c) * 1.003
        l = min(o, c) * 0.997
        v = 1000.0 + 10 * i
        rows.append([ts + i * 3_600_000, o, h, l, c, v])
        p = c
    return rows


def test_liq_price_directions():
    # лонг ліквідується нижче входу, шорт — вище
    assert liq_price(100.0, 10, "long") < 100.0
    assert liq_price(100.0, 10, "short") > 100.0
    # більше плече -> ближча ліквідація до входу
    assert liq_price(100.0, 100, "long") > liq_price(100.0, 10, "long")


def test_leverage_mix_sums_to_one():
    assert abs(sum(LEVERAGE_MIX.values()) - 1.0) < 1e-9


def test_build_heatmap_shape_and_keys():
    hm = build_heatmap(_synth_ohlcv(60), price_bins=100)
    m = hm["meta"]
    assert m["n_candles"] == 60
    assert len(hm["price_levels"]) == 100
    assert len(hm["grid"]) == 100           # price bins rows
    assert len(hm["grid"][0]) == 60         # time cols
    assert len(hm["candles"]) == 60
    assert m["price_min"] < m["last_price"] < m["price_max"]


def test_zones_have_valid_sides():
    hm = build_heatmap(_synth_ohlcv(80), price_bins=120)
    last = hm["meta"]["last_price"]
    assert len(hm["zones"]) > 0
    for z in hm["zones"]:
        assert z["side"] in ("LONG_LIQ", "SHORT_LIQ")
        # LONG_LIQ мусить бути нижче ціни, SHORT_LIQ — вище
        if z["side"] == "LONG_LIQ":
            assert z["price"] < last
        else:
            assert z["price"] > last
        assert 0.0 <= z["intensity"] <= 1.0


def test_oi_scaling_runs():
    ohlcv = _synth_ohlcv(50)
    oi = [(r[0], 5e8 + 1e6 * i) for i, r in enumerate(ohlcv)]
    hm = build_heatmap(ohlcv, price_bins=80, oi_series=oi)
    assert len(hm["zones"]) >= 0  # не падає з OI


def test_empty_flat_market_no_crash():
    # мертвий флет: нульовий обсяг -> без зон, але без винятку
    rows = [[1_700_000_000_000 + i * 3600000, 100, 100, 100, 100, 0.0] for i in range(30)]
    hm = build_heatmap(rows, price_bins=50)
    assert hm["zones"] == []


def test_backtest_runs_and_calibrates():
    from tools.liqmap.backtest import backtest, _bucket
    assert _bucket(1.0) == "1.0-1.01"
    assert _bucket(0.9) == "0.8-1.00"
    assert _bucket(0.05) is None
    res = backtest(_synth_ohlcv(220), horizon=24, warmup=120, step=12, bins=80)
    assert isinstance(res, dict)
    assert "all" in res
    for r in res["all"]:
        assert 0.0 <= r["hit_rate"] <= 1.0
        assert 0.0 <= r["calibrated_confidence"] <= 1.0
        assert "distance_bucket" in r
        assert r["n"] >= 1
        assert r["side"] in ("LONG_LIQ", "SHORT_LIQ")


def test_backtest_max_dist_filter():
    from tools.liqmap.backtest import backtest
    wide = backtest(_synth_ohlcv(220), horizon=24, warmup=120, step=12, bins=80)
    near = backtest(_synth_ohlcv(220), horizon=24, warmup=120, step=12, bins=80,
                    max_dist_pct=2.0)
    n_wide = sum(r["n"] for r in wide["all"])
    n_near = sum(r["n"] for r in near["all"])
    assert n_near <= n_wide


def test_calibrated_confidence_monotonicity():
    from tools.liqmap.backtest import pool_adjacent_violators, calibrate_pava
    # Перевіримо, що PAVA вирівнює немонотонні списки
    y = [0.8, 0.7, 0.6, 0.5, 0.9]
    w = [10, 10, 10, 10, 10]
    res = pool_adjacent_violators(y, w)
    # Має бути монотонно неспадною послідовністю
    for i in range(len(res) - 1):
        assert res[i] <= res[i+1] + 1e-9

    # Перевіримо загальну калібраційну функцію
    sample_data = [
        {"distance_bucket": "0-1%", "bucket": "0.2-0.40", "side": "LONG_LIQ", "hit_rate": 0.8, "n": 10},
        {"distance_bucket": "0-1%", "bucket": "0.4-0.60", "side": "LONG_LIQ", "hit_rate": 0.7, "n": 10},
        {"distance_bucket": "0-1%", "bucket": "0.6-0.80", "side": "LONG_LIQ", "hit_rate": 0.6, "n": 10},
        {"distance_bucket": "0-1%", "bucket": "0.8-1.00", "side": "LONG_LIQ", "hit_rate": 0.5, "n": 10},
        {"distance_bucket": "0-1%", "bucket": "1.0-1.01", "side": "LONG_LIQ", "hit_rate": 0.9, "n": 10},
    ]
    cal = calibrate_pava(sample_data)
    # Зіставимо впевненості
    confidences = [item["calibrated_confidence"] for item in sorted(cal, key=lambda x: x["bucket"])]
    for i in range(len(confidences) - 1):
        assert confidences[i] <= confidences[i+1] + 1e-9



def test_classify_regime():
    def _linear_ohlcv(n, start, change):
        rows = []
        p = start
        for i in range(n):
            o = p
            c = p * (1 + change)
            h = max(o, c) * 1.001
            l = min(o, c) * 0.999
            rows.append([1_700_000_000_000 + i * 3600000, o, h, l, c, 1000.0])
            p = c
        return rows

    # Тест висхідного тренду
    bull_candles = _linear_ohlcv(300, 60000.0, 0.001)
    assert classify_regime(bull_candles, 250) == "bull"

    # Тест низхідного тренду
    bear_candles = _linear_ohlcv(300, 60000.0, -0.001)
    assert classify_regime(bear_candles, 250) == "bear"

    # Тест флету
    flat_candles = _linear_ohlcv(300, 60000.0, 0.0)
    assert classify_regime(flat_candles, 250) == "flat"


def test_no_lookahead():
    # Переконуємось, що розрахунок карти для t заснований тільки на window[:t]
    candles = _synth_ohlcv(150)
    t = 130
    hm_t = build_heatmap(candles[:t], price_bins=80)
    hm_full = build_heatmap(candles, price_bins=80)
    
    # Стовпець t-1 в сітці grid з повної карти має збігатися з останнім стовпцем в hm_t
    grid_t_last = [row[-1] for row in hm_t["grid"]]
    grid_full_t = [row[t - 1] for row in hm_full["grid"]]
    
    for val_t, val_full in zip(grid_t_last, grid_full_t):
        assert abs(val_t - val_full) < 1e-9


def test_pagination_and_cache():
    # Тестуємо механізм локального кешування
    key = "test_cache_key_liqmap"
    test_data = {"test": 123, "list": [1, 2, 3]}
    write_cache_data(key, test_data)
    
    loaded = get_cache_data(key, max_age_seconds=10)
    assert loaded == test_data
    
    # Перевіряємо інвалідацію за часом
    loaded_old = get_cache_data(key, max_age_seconds=-1)
    assert loaded_old is None
    
    # Видаляємо тестовий файл
    cache_path = Path(f"/home/layman/QuantumEdge/runtime/cache/{key}.json")
    if cache_path.exists():
        cache_path.unlink()
