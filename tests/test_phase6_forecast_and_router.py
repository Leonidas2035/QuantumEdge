"""Phase 6 tests: CausalHMM zero leakage, Purged Walk-Forward, EGARCH simulation, ACI-CQR vol shift, MultiHead LightGBM, ShadowTSFM, ReasoningEffortRouter hysteresis, and 12-gate Acceptance Matrix."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import tools.qe_harness_cli as harness_cli
import tools.verify_acceptance_matrix as matrix_cli
from quantum_edge_core.contracts import ForecastBundleV1, ThinkingLevel
from quantum_edge_ml.forecasting import (
    AdaptiveConformalCalibrator,
    CausalHMMFilter,
    EGARCHVolatilityModel,
    ForecastPipeline,
    MultiHeadLGBMForecaster,
    ShadowTSFMChallenger,
    compute_calibration_metrics,
    compute_triple_barrier_labels,
    purged_walk_forward_splits,
)
from quantum_edge_ml.model_router.reasoning_router import ReasoningEffortRouter
from tools.benchmark_count_tokens import estimate_gemini_sp_tokens


def _make_ohlcv_df(n: int = 180, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0001, 0.0035, size=n)
    # Inject distinct down/flat/up regimes so all 3 Triple-Barrier classes occur
    rets[:60] -= 0.0018
    rets[120:] += 0.0020
    closes = 60_000.0 * np.exp(np.cumsum(rets))
    opens = np.concatenate([[60_000.0], closes[:-1]])
    wicks_u = np.abs(rng.normal(0.0, 0.002, size=n))
    wicks_d = np.abs(rng.normal(0.0, 0.002, size=n))
    highs = np.maximum(opens, closes) * (1.0 + wicks_u)
    lows = np.minimum(opens, closes) * (1.0 - wicks_d)
    vols = rng.uniform(8.0, 95.0, size=n)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}
    )


def test_causal_hmm_zero_future_leakage() -> None:
    rng = np.random.default_rng(101)
    X_train = np.vstack(
        [
            rng.normal([-0.01, 0.015, 0.02], 0.003, size=(60, 3)),
            rng.normal([0.0, 0.005, 0.008], 0.002, size=(60, 3)),
            rng.normal([0.01, 0.012, 0.018], 0.003, size=(60, 3)),
        ]
    )
    hmm = CausalHMMFilter(n_states=3, random_state=42).fit(X_train)

    X_base = rng.normal(0.0, 0.008, size=(100, 3))
    X_shock = X_base.copy()
    # Massive future shock strictly at t >= 50
    X_shock[50:] = rng.normal(0.25, 0.10, size=(50, 3))

    post_base = hmm.filter_causal(X_base)
    post_shock = hmm.filter_causal(X_shock)

    max_diff_before_50 = float(np.max(np.abs(post_base[:50] - post_shock[:50])))
    assert max_diff_before_50 == 0.0
    assert np.allclose(np.sum(post_base, axis=1), 1.0)


def test_purged_walk_forward_strict_gap() -> None:
    splits = purged_walk_forward_splits(
        n_samples=360, n_splits=5, label_horizon=12, embargo_bars=6
    )
    assert len(splits) == 5
    for train_idx, test_idx in splits:
        assert len(train_idx) > 0 and len(test_idx) > 0
        assert int(np.max(train_idx)) + 12 < int(np.min(test_idx))

    with pytest.raises(ValueError, match="Insufficient"):
        purged_walk_forward_splits(n_samples=30, n_splits=5, label_horizon=12, embargo_bars=6)


def test_egarch_multistep_simulation_and_short_series_fallback() -> None:
    rng = np.random.default_rng(77)
    log_ret = rng.normal(0.0, 0.004, size=220)
    eg = EGARCHVolatilityModel(seed=42)

    vol_bps, vol_z = eg.forecast_vol_bps(log_ret, horizon_steps=3, simulations=300)
    assert vol_bps > 0.0
    assert math.isfinite(vol_z)

    # Short series & constant series fallback without raising
    short_vol, short_z = eg.forecast_vol_bps(log_ret[:20], horizon_steps=3)
    assert short_vol >= 5.0 and short_z == 0.0
    const_vol, const_z = eg.forecast_vol_bps(np.zeros(120), horizon_steps=3)
    assert const_vol >= 5.0 and const_z == 0.0


def test_aci_cqr_recovers_coverage_under_2x_vol_shift() -> None:
    rng = np.random.default_rng(2026)
    # 80% normal quantile interval for sigma=1.0 is [-1.2816, +1.2816]
    q_lo_raw, q_hi_raw = -1.28155, 1.28155

    y_pre = rng.normal(0.0, 1.0, size=200)
    y_shift = rng.normal(0.0, 2.2, size=600)  # 2.2x volatility jump!

    aci = AdaptiveConformalCalibrator(alpha_target=0.20, gamma=0.02, window=100)
    for y in y_pre:
        aci.update(float(y), q_lo_raw, q_hi_raw)

    raw_hits = 0
    aci_hits = 0
    eval_start = 80  # Measure post-burn-in online coverage under 2.2x vol shift
    for idx, y in enumerate(y_shift):
        yv = float(y)
        lo_adj, hi_adj = aci.adjust_interval(q_lo_raw, q_hi_raw)
        if idx >= eval_start:
            if q_lo_raw <= yv <= q_hi_raw:
                raw_hits += 1
            if lo_adj <= yv <= hi_adj:
                aci_hits += 1
        aci.update(yv, q_lo_raw, q_hi_raw)

    n_eval = len(y_shift) - eval_start
    raw_cov = raw_hits / n_eval
    aci_cov = aci_hits / n_eval

    assert raw_cov < 0.60
    assert 0.74 <= aci_cov <= 0.86


def test_multihead_lgbm_pipeline_anti_crossing_and_calibration_metrics() -> None:
    df = _make_ohlcv_df(n=200, seed=42)
    pipe = ForecastPipeline.fit_from_ohlcv(df, horizon_steps=3, random_state=42)
    bundle = pipe.predict_bundle(symbol="BTCUSDT", ohlcv_df=df, horizon="15m", horizon_steps=3)

    assert isinstance(bundle, ForecastBundleV1)
    q_keys = ("q05", "q10", "q25", "q50", "q75", "q90", "q95")
    q_vals = [bundle.return_quantiles_bps[k] for k in q_keys]
    assert q_vals == sorted(q_vals)  # Strict anti-crossing
    assert abs(sum(bundle.barrier_probs.values()) - 1.0) < 1e-3
    assert bundle.expected_mfe_bps["q50"] <= bundle.expected_mfe_bps["q90"]
    assert bundle.expected_mae_bps["q90"] <= bundle.expected_mae_bps["q50"] <= 0.0

    # Compact FORECAST_V1 prompt wire format token budget check (<= 300 local SP tokens)
    compact_wire = bundle.to_compact_prompt_str()
    assert compact_wire.startswith("FORECAST_V1|BTCUSDT|15m|")
    tok_count = estimate_gemini_sp_tokens(compact_wire)
    assert tok_count <= 300

    # Also test 2-class edge case in MultiHeadLGBMForecaster
    X_small = np.random.default_rng(1).normal(0, 1, size=(40, 4))
    y_z = np.random.default_rng(2).normal(0, 1, size=40)
    y_tb_two = np.array([-1, 1] * 20, dtype=np.int64)  # Class 0 ('neither') absent!
    mh2 = MultiHeadLGBMForecaster(random_state=42, n_estimators=20).fit_causal(
        X_small, y_z, y_tb_two, np.abs(y_z) * 10.0, -np.abs(y_z) * 10.0
    )
    row_out = mh2.predict_row(X_small[0], 25.0)
    assert row_out["barrier_probs"]["neither"] == 0.0
    assert abs(sum(row_out["barrier_probs"].values()) - 1.0) < 1e-3

    # Calibration metrics check (well-calibrated synthetic sample -> ECE <= 0.05)
    rng = np.random.default_rng(123)
    p_true = rng.uniform(0.1, 0.9, size=2000)
    y_bin = (rng.uniform(0.0, 1.0, size=2000) < p_true).astype(np.int64)
    y_cont = rng.normal(0.0, 1.0, size=2000)
    q_preds = {
        "q10": np.full(2000, -1.28),
        "q50": np.zeros(2000),
        "q90": np.full(2000, 1.28),
    }
    cal = compute_calibration_metrics(y_bin, p_true, y_cont, q_preds)
    assert cal["ece"] <= 0.05
    assert cal["brier_score"] > 0.0
    assert cal["crps_approx"] > 0.0


def test_shadow_tsfm_challenger_fail_soft_and_mock_disagreement() -> None:
    df = _make_ohlcv_df(n=60, seed=9)
    # Missing chronos/kronos packages fail-soft to 0.0 without raising
    s_none = ShadowTSFMChallenger(backend="none")
    assert s_none.compute_disagreement(lgbm_q50_bps=12.0, ohlcv_df=df) == 0.0

    s_chronos = ShadowTSFMChallenger(backend="chronos2", device="cpu")
    assert s_chronos.compute_disagreement(lgbm_q50_bps=12.0, ohlcv_df=df) == 0.0

    # Injected mock predictor produces bounded disagreement in [0, 1]
    last_c = float(df["close"].iloc[-1])
    s_mock = ShadowTSFMChallenger(
        backend="none",
        mock_predictor_fn=lambda d, h: last_c * 1.0040,  # +40 bps vs lgbm -10 bps -> diff=50 bps
    )
    dis = s_mock.compute_disagreement(lgbm_q50_bps=-10.0, ohlcv_df=df, cond_vol_bps=25.0)
    assert abs(dis - 1.0) < 1e-6


def test_reasoning_router_hysteresis_no_flapping() -> None:
    scores = [0.64, 0.67, 0.63, 0.72, 0.62, 0.58, 0.92]

    r_carbon = ReasoningEffortRouter(model_id="gemini-4-carbon", hysteresis=0.05)
    assert r_carbon.select_level_from_score("BTCUSDT", 0.95, stage=1)[1] == ThinkingLevel.LOW
    seq_carbon = [r_carbon.select_level_from_score("BTCUSDT", d, stage=2)[1] for d in scores]
    assert seq_carbon == [
        ThinkingLevel.MEDIUM,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.HIGH,
        ThinkingLevel.HIGH,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.HIGH,
    ]

    r_neon = ReasoningEffortRouter(model_id="gemini-4-neon", hysteresis=0.05)
    seq_neon = [r_neon.select_level_from_score("BTCUSDT", d, stage=2)[1] for d in scores]
    assert seq_neon == [
        ThinkingLevel.MEDIUM,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.HIGH,
        ThinkingLevel.HIGH,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.XHIGH,
    ]


def test_verify_acceptance_matrix_bootstrap_hold_quality_and_dry_run_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    good_diffs = np.full(50, 1.5, dtype=np.float64)
    res_good = matrix_cli.moving_block_bootstrap_noninferiority(good_diffs, margin_bps=-2.0)
    assert res_good["passed"] is True

    bad_diffs = np.full(50, -5.0, dtype=np.float64)
    res_bad = matrix_cli.moving_block_bootstrap_noninferiority(bad_diffs, margin_bps=-2.0)
    assert res_bad["passed"] is False

    hq = matrix_cli.compute_hold_quality(
        [
            {"has_open_on_track": True, "realized_best_net_opp_bps": 55.0},
            {"has_open_on_track": False, "realized_best_net_opp_bps": 12.0},
            {"has_open_on_track": False, "realized_best_net_opp_bps": 48.0},
            {"has_open_on_track": False, "realized_best_net_opp_bps": 19.0},
        ]
    )
    assert hq == 0.75

    rc = matrix_cli.main(["--dry-run"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out.strip())
    assert report["overall_pass"] is True
    assert report["gates_passed"] == 12
    assert report["gates_total"] == 12
    assert all(report["gates"].values())
    assert "/dev/shm/quantumedge" in report["ram_dir"] or "quantumedge" in report["ram_dir"]


def test_cli_run_cycle_with_reasoning_router_mock_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = harness_cli.main(
        [
            "run-cycle",
            "--transport",
            "mock",
            "--dry-run",
            "--ledger-db",
            str(tmp_path / "u.sqlite3"),
            "--order-ledger-db",
            str(tmp_path / "o.sqlite3"),
            "--journal-path",
            str(tmp_path / "j.jsonl"),
            "--thesis-db",
            str(tmp_path / "t.sqlite3"),
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["effort_router_enabled"] is True
    assert out["validator_passed"] is True


def test_reasoning_router_normal_vst_25bps_spread_does_not_inflate_complexity_to_high() -> None:
    router = ReasoningEffortRouter(model_id="gemini-4-carbon", hysteresis=0.05)
    calm_ctx = {
        "market_snapshot": {"symbol": "LINKUSDT", "spread_bps": 25.0},
        "microstructure_and_liquidity": {
            "composite_signal": {"score": 45.0},
            "orderflow": {"imbalance": 0.35},
        },
    }
    calm_fc = {"regime_entropy": 0.45, "vol_zscore": 0.4, "tsfm_disagreement": 0.0}
    calm_status = {"open_positions_count": 3}

    d_calm = router.compute_complexity(ctx=calm_ctx, forecast=calm_fc, vst_status=calm_status)
    assert d_calm < 0.65
    score, level = router.select_level(
        symbol="LINKUSDT",
        ctx=calm_ctx,
        forecast=calm_fc,
        vst_status=calm_status,
        stage=2,
    )
    assert score < 0.65
    assert level in (ThinkingLevel.LOW, ThinkingLevel.MEDIUM)

    # Genuine stress (wide 60bps spread + signal conflict + high entropy & disagreement) escalates cleanly
    stress_ctx = {
        "market_snapshot": {"symbol": "LINKUSDT", "spread_bps": 60.0},
        "microstructure_and_liquidity": {
            "composite_signal": {"score": 40.0},
            "orderflow": {"imbalance": -0.5},
        },
    }
    stress_fc = {"regime_entropy": 0.90, "vol_zscore": 2.4, "tsfm_disagreement": 0.80}
    d_stress = router.compute_complexity(ctx=stress_ctx, forecast=stress_fc, vst_status=calm_status)
    assert d_stress >= 0.70
