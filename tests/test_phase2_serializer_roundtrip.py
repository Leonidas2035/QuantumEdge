"""Phase 2 tests: Hypothesis round-trip, error-feedback bounds, WebP RAM rendering, specs, and CountTokens."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import ccxt
from hypothesis import given, settings, strategies as st

import tools.benchmark_count_tokens as bench_cli
from quantum_edge_core.execution import (
    MarketContextSerializer,
    extract_instrument_specs,
    serialize_for_llm,
)

INSTRUMENT_CASES = [
    ("BTCUSDT", Decimal("0.1"), Decimal("0.001")),
    ("ETHUSDT", Decimal("0.01"), Decimal("0.01")),
    ("SOLUSDT", Decimal("0.001"), Decimal("0.1")),
    ("XRPUSDT", Decimal("0.0001"), Decimal("1")),
]


@st.composite
def ohlcv_tick_series(draw: st.DrawFn) -> tuple[str, Decimal, Decimal, list[dict[str, object]]]:
    sym, tick, qstep = draw(st.sampled_from(INSTRUMENT_CASES))
    n = draw(st.integers(min_value=1, max_value=30))
    t0 = 1_795_641_840_000
    step_ms = 60_000

    # Base integer ticks in reasonable price range
    cur_c = draw(st.integers(min_value=500, max_value=800_000))
    ts = t0
    bars: list[dict[str, object]] = []
    for i in range(n):
        if i > 0 and draw(st.booleans()):
            ts += step_ms * draw(st.integers(min_value=1, max_value=4))
        elif i > 0:
            ts += step_ms

        gap = draw(st.integers(min_value=-20, max_value=20)) if i > 0 else 0
        o_t = max(1, cur_c + gap)
        # Include zero-body candles (dc=0) and extreme wicks
        dc = draw(st.integers(min_value=-200, max_value=200))
        c_t = max(1, o_t + dc)
        uw = draw(st.integers(min_value=0, max_value=400))
        lw = draw(st.integers(min_value=0, max_value=min(o_t, c_t) - 1)) if min(o_t, c_t) > 1 else 0
        h_t = max(o_t, c_t) + uw
        l_t = min(o_t, c_t) - lw
        v_q = draw(st.integers(min_value=0, max_value=500_000))
        bars.append(
            {
                "ts": ts,
                "open": o_t * tick,
                "high": h_t * tick,
                "low": l_t * tick,
                "close": c_t * tick,
                "volume": v_q * qstep,
            }
        )
        cur_c = c_t
    return sym, tick, qstep, bars


@settings(max_examples=500, deadline=None)
@given(series=ohlcv_tick_series())
def test_tick_delta_exact_roundtrip_hypothesis(
    series: tuple[str, Decimal, Decimal, list[dict[str, object]]],
) -> None:
    sym, tick, qstep, bars = series
    ser = MarketContextSerializer()
    encoded = ser.encode_ohlcv_tick_delta(sym, "1m", bars, tick_size=tick, qty_step=qstep)
    decoded = ser.decode_ohlcv_tick_delta(encoded)

    assert len(decoded["bars"]) == len(bars)
    for orig, rec in zip(bars, decoded["bars"]):
        assert rec["ts"] == orig["ts"]
        assert rec["open"] == orig["open"]
        assert rec["high"] == orig["high"]
        assert rec["low"] == orig["low"]
        assert rec["close"] == orig["close"]
        assert rec["volume"] == orig["volume"]


@st.composite
def delta_bps_series(draw: st.DrawFn) -> list[dict[str, object]]:
    n = draw(st.integers(min_value=100, max_value=500))
    t0 = 1_795_641_840_000
    step_ms = 300_000
    p = draw(st.floats(min_value=10.0, max_value=100_000.0, allow_nan=False, allow_infinity=False))
    rets = draw(
        st.lists(
            st.floats(min_value=-0.015, max_value=0.015, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    wicks_hi = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=0.01, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    wicks_lo = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=0.01, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    bars: list[dict[str, object]] = []
    cur = p
    for i in range(n):
        nxt = max(0.01, cur * (1.0 + rets[i]))
        b_hi = max(cur, nxt)
        b_lo = min(cur, nxt)
        h = b_hi * (1.0 + wicks_hi[i])
        l = max(0.001, b_lo * (1.0 - wicks_lo[i]))
        bars.append(
            {
                "ts": t0 + i * step_ms,
                "open": cur,
                "high": h,
                "low": l,
                "close": nxt,
                "volume": 10.0 + (i % 7),
            }
        )
        cur = nxt
    return bars


@settings(max_examples=500, deadline=None)
@given(bars=delta_bps_series())
def test_delta_bps_uniform_error_bound_hypothesis(bars: list[dict[str, object]]) -> None:
    ser = MarketContextSerializer()
    encoded = ser.encode_ohlcv_delta_bps("BTCUSDT", "5m", bars)
    decoded = ser.decode_ohlcv_delta_bps(encoded)["bars"]

    assert len(decoded) == len(bars)
    # Uniform worst-case error for 1-bps quantization around rec_c under +/-1.5% bar returns is 0.5 / (1 - 0.015) bps
    for orig, rec in zip(bars, decoded):
        c_err_bps = abs(rec["close"] - orig["close"]) / orig["close"] * 10_000.0
        h_err_bps = abs(rec["high"] - orig["high"]) / orig["high"] * 10_000.0
        l_err_bps = abs(rec["low"] - orig["low"]) / orig["low"] * 10_000.0
        # Error relative to anchor rec_c (open) is strictly <= 0.50 bps; relative to close <= 0.51 bps
        c_err_anchor_bps = abs(rec["close"] - orig["close"]) / rec["open"] * 10_000.0
        assert c_err_anchor_bps <= 0.50 + 1e-6
        assert c_err_bps <= 0.51 + 1e-6
        assert h_err_bps <= 1.02 + 1e-6
        assert l_err_bps <= 1.02 + 1e-6


def test_real_or_realistic_enriched_context_all_four_modes() -> None:
    syn = bench_cli.build_synthetic_enriched_context("BTCUSDT")
    assert len(syn["microstructure_and_liquidity"]["liqmap"]["zones"]) == 44
    assert len(syn["orderbook"]["bids"]) == 25

    for mode in ("tick_delta", "delta_bps", "multiscale", "hybrid_webp"):
        payload = serialize_for_llm(syn, mode=mode, ohlcv_tail=25, include_full_liqmap=True)  # type: ignore[arg-type]
        assert payload.context_hash
        assert "LIQMAP|BTCUSDT" in payload.text
        assert "L2_TOP15|" in payload.text
        if mode == "hybrid_webp":
            assert len(payload.images) == 1
            assert payload.images[0].resolution == "high"
            assert payload.images[0].token_budget == 1120
        else:
            assert payload.images == []

    real_fixture = Path("runtime/step1_enriched_ctx_btcusdt.json")
    if real_fixture.is_file():
        real_ctx = json.loads(real_fixture.read_text(encoding="utf-8"))
        for mode in ("tick_delta", "delta_bps", "multiscale", "hybrid_webp"):
            payload = serialize_for_llm(real_ctx, mode=mode, ohlcv_tail=25)  # type: ignore[arg-type]
            assert len(payload.text) > 100
            assert "BTCUSDT" in payload.text


def test_webp_renderers_in_memory_fast_and_zero_disk_writes(tmp_path: Path, monkeypatch: object) -> None:
    ser = MarketContextSerializer()
    contexts = {
        sym: bench_cli.build_synthetic_enriched_context(sym)
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    }

    # Ensure zero disk writes in cwd/tmp_path during rendering
    before_files = set(Path(".").glob("*.png")) | set(Path(".").glob("*.webp"))

    # Warm up matplotlib font/backend cache once, then measure steady-state render latency
    _ = ser.render_screening_grid_webp(contexts, tf="15m", tail=48)

    t0 = time.perf_counter()
    screen_img = ser.render_screening_grid_webp(contexts, tf="15m", tail=48)
    screen_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    cand_img = ser.render_candidate_5tf_webp("BTCUSDT", contexts["BTCUSDT"])
    cand_ms = (time.perf_counter() - t1) * 1000.0

    after_files = set(Path(".").glob("*.png")) | set(Path(".").glob("*.webp"))
    assert before_files == after_files

    assert screen_img.mime_type == "image/webp"
    assert screen_img.resolution == "medium"
    assert screen_img.token_budget == 560
    assert screen_img.data[:4] == b"RIFF" and screen_img.data[8:12] == b"WEBP"

    assert cand_img.mime_type == "image/webp"
    assert cand_img.resolution == "high"
    assert cand_img.token_budget == 1120
    assert cand_img.data[:4] == b"RIFF" and cand_img.data[8:12] == b"WEBP"

    # Sanity bound for CI/ARTLINE-PC
    assert screen_ms < 2000.0
    assert cand_ms < 2000.0


def test_extract_instrument_specs_tick_size_and_decimal_places() -> None:
    markets_tick = {
        "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "precision": {"price": 0.1, "amount": 0.0001}},
        "DOGE/USDT:USDT": {"symbol": "DOGE/USDT:USDT", "precision": {"price": 0.00001, "amount": 1}},
    }
    client_tick = SimpleNamespace(markets=markets_tick, precisionMode=ccxt.TICK_SIZE)
    specs_tick = extract_instrument_specs(client_tick)
    assert specs_tick["BTCUSDT"] == (Decimal("0.1"), Decimal("0.0001"))
    assert specs_tick["DOGEUSDT"] == (Decimal("0.00001"), Decimal("1"))

    markets_dec = {
        "ETH/USDT:USDT": {"symbol": "ETH/USDT:USDT", "precision": {"price": 2, "amount": 3}},
    }
    client_dec = SimpleNamespace(markets=markets_dec, precisionMode=ccxt.DECIMAL_PLACES)
    specs_dec = extract_instrument_specs(client_dec)
    assert specs_dec["ETHUSDT"] == (Decimal("0.01"), Decimal("0.001"))


def test_benchmark_count_tokens_local_sp_cli(tmp_path: Path) -> None:
    out_json = tmp_path / "bench.json"
    rc = bench_cli.main(["--counter", "local-sp", "--out-json", str(out_json)])
    assert rc == 0
    assert out_json.is_file()

    data = json.loads(out_json.read_text(encoding="utf-8"))
    by_fmt = {r["format"]: r for r in data["rows"]}
    assert by_fmt["json_pretty_baseline"]["counter_source"] == "local_sp_estimate"
    assert by_fmt["tick_delta"]["ratio_vs_pretty"] >= 4.0
    assert by_fmt["json_pretty_baseline"]["text_bytes"] / by_fmt["tick_delta"]["text_bytes"] >= 7.0
    assert by_fmt["delta_bps"]["ratio_vs_pretty"] >= 8.0
    assert by_fmt["multiscale"]["ratio_vs_pretty"] >= 15.0
    assert by_fmt["hybrid_webp"]["ratio_vs_pretty"] >= 12.0
    assert by_fmt["hybrid_webp"]["image_tokens"] == 1120
