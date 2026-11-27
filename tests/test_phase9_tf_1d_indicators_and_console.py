"""Phase 9 tests: 1d/4h/1h/15m/5m hierarchy (drop 1m), causal RSI/MACD/BB/ADX/divergences, fail-soft agy --resume, and live console runner."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import tools.run_vst_session as session_runner
from quantum_edge_core.contracts import ThinkingLevel
from quantum_edge_core.execution.market_context_serializer import (
    TF_STEP_MS,
    MarketContextSerializer,
    _compute_rsi_wilder,
    _detect_causal_divergence,
)
from quantum_edge_core.orchestrator import (
    PreFetchCoordinator,
    PreFetchedBundle,
    PrefixContextAssembler,
    ThesisMemoryStore,
    build_p0_static_prefix,
)
from quantum_edge_ml.model_router.backends.transport import (
    AgyDirectCliTransport,
    LLMRequest,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger
from tools.benchmark_count_tokens import estimate_gemini_sp_tokens


def _make_bars_from_closes(closes: list[float], *, step_ms: int = 300_000) -> list[dict[str, float]]:
    t0 = 1_795_641_840_000
    bars: list[dict[str, float]] = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        hi = max(o, c) * 1.003
        lo = min(o, c) * 0.997
        bars.append(
            {
                "ts": float(t0 + i * step_ms),
                "open": round(o, 4),
                "high": round(hi, 4),
                "low": round(lo, 4),
                "close": round(c, 4),
                "volume": 120.0 + (i % 7) * 15.0,
            }
        )
    return bars


def test_structural_profile_indicators_ranges_and_zero_leakage() -> None:
    assert TF_STEP_MS["1d"] == 86_400_000
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0003, 0.006, size=100)
    closes = (600.0 * np.exp(np.cumsum(rets))).tolist()
    bars = _make_bars_from_closes(closes)

    ser = MarketContextSerializer()
    prof_60 = ser.build_structural_profile("15m", bars[:60])

    # Mutate all future bars t >= 60 wildly and verify t < 60 profile is 100% invariant
    bars_shock = list(bars[:60]) + _make_bars_from_closes([c * 1.8 for c in closes[60:]])
    prof_60_after = ser.build_structural_profile("15m", bars_shock[:60])
    assert prof_60 == prof_60_after

    for key in (
        "rsi14",
        "macd_hist_bps",
        "macd_cross",
        "bb_pos",
        "bb_bw_bps",
        "adx14",
        "di_diff",
        "rsi_div",
        "macd_div",
    ):
        assert key in prof_60

    assert 0.0 <= prof_60["rsi14"] <= 100.0
    assert 0.0 <= prof_60["adx14"] <= 100.0
    assert prof_60["bb_bw_bps"] >= 0.0
    assert math.isfinite(prof_60["bb_pos"])
    assert math.isfinite(prof_60["macd_hist_bps"])
    assert math.isfinite(prof_60["di_diff"])
    assert prof_60["macd_cross"] in ("BULL", "BEAR", "FLAT")
    assert prof_60["rsi_div"] in ("BULL", "BEAR", "NONE")
    assert prof_60["macd_div"] in ("BULL", "BEAR", "NONE")


def test_causal_divergence_detects_bullish_and_bearish_setups() -> None:
    # 1. Bullish divergence: sharp first drop (trough1=90, low RSI), bounce, shallower-slope lower low (trough2=88, higher RSI)
    bull_closes = (
        [100.0] * 15
        + [98.0, 95.0, 92.0, 90.0, 93.0, 96.0, 99.0, 101.0, 100.0, 99.0]
        + [97.5, 96.0, 94.5, 93.0, 91.5, 90.2, 89.2, 88.5, 88.0, 91.0, 94.0, 96.0]
    )
    bull_bars = _make_bars_from_closes(bull_closes)
    # Give exact distinct trough lows at the two swing bottoms
    bull_bars[18]["low"] = 89.0
    bull_bars[33]["low"] = 87.0
    highs_b = np.array([b["high"] for b in bull_bars], dtype=np.float64)
    lows_b = np.array([b["low"] for b in bull_bars], dtype=np.float64)
    rsi_b = _compute_rsi_wilder(np.array(bull_closes, dtype=np.float64), period=14)
    assert _detect_causal_divergence(highs_b, lows_b, rsi_b, lookback=40, pivot_window=2) == "BULL"

    # 2. Bearish divergence: sharp rally (peak1=112, high RSI), pullback, grinding higher high (peak2=115, lower RSI)
    bear_closes = (
        [100.0] * 15
        + [103.0, 106.0, 109.0, 112.0, 108.0, 105.0, 103.0, 102.0, 103.5, 105.0]
        + [106.5, 108.0, 109.5, 111.0, 112.2, 113.3, 114.2, 115.0, 112.0, 109.0, 107.0]
    )
    bear_bars = _make_bars_from_closes(bear_closes)
    bear_bars[18]["high"] = 113.0
    bear_bars[32]["high"] = 116.0
    highs_s = np.array([b["high"] for b in bear_bars], dtype=np.float64)
    lows_s = np.array([b["low"] for b in bear_bars], dtype=np.float64)
    rsi_s = _compute_rsi_wilder(np.array(bear_closes, dtype=np.float64), period=14)
    assert _detect_causal_divergence(highs_s, lows_s, rsi_s, lookback=40, pivot_window=2) == "BEAR"


def _make_5tf_bundle() -> tuple[PreFetchedBundle, list[str]]:
    requested_tfs: list[str] = []

    class _Stub5TFClient:
        def fetch_balance(self) -> dict[str, Any]:
            return {"VST": {"free": 9500.0, "total": 10000.0, "used": 500.0}}

        def fetch_positions(self) -> list[dict[str, Any]]:
            return []

        def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            return []

        def fetch_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any]:
            return {
                "bids": [[600.0 - i * 0.1, 5.0 + i] for i in range(15)],
                "asks": [[600.2 + i * 0.1, 5.0 + i] for i in range(15)],
                "timestamp": 1795641840000,
            }

        def fetch_ticker(self, symbol: str) -> dict[str, Any]:
            return {"bid": 600.0, "ask": 600.2, "last": 600.1}

        def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 100) -> list[list[float]]:
            requested_tfs.append(timeframe)
            rng = np.random.default_rng(100 + len(timeframe))
            closes = 600.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.003, size=limit)))
            step = TF_STEP_MS.get(timeframe, 300_000)
            out_rows: list[list[float]] = []
            for i in range(limit):
                c = float(closes[i])
                o = float(closes[i - 1] if i > 0 else closes[0])
                hi = max(o, c) * 1.003
                lo = min(o, c) * 0.997
                out_rows.append(
                    [
                        1795641840000 + i * step,
                        round(o, 2),
                        round(hi, 2),
                        round(lo, 2),
                        round(c, 2),
                        100.0 + i,
                    ]
                )
            return out_rows

        def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
            return {"fundingRate": 0.0001, "markPrice": 600.1}

        def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
            return {"openInterestAmount": 25000.0}

    coord = PreFetchCoordinator(_Stub5TFClient(), symbols=("BNBUSDT", "LINKUSDT", "SUIUSDT"))
    bundle = asyncio.run(coord.fetch_all(include_analytics=True))
    return bundle, requested_tfs


def test_new_5tf_hierarchy_1d_4h_in_p1_and_1h_15m_5m_in_stage2_no_1m(
    tmp_path: Path,
) -> None:
    bundle, requested_tfs = _make_5tf_bundle()
    assert "1m" not in requested_tfs
    assert set(requested_tfs) == {"5m", "15m", "1h", "4h", "1d"}

    store = ThesisMemoryStore(tmp_path / "t_p9.sqlite3")
    try:
        asm = PrefixContextAssembler(thesis_store=store, include_images=False)
        s1 = asm.assemble_stage1(bundle=bundle, trigger_summary="P9_5TF")
        s2 = asm.assemble_stage2(
            bundle=bundle,
            candidate_symbol="BNBUSDT",
            stage1_summary={"action": "DRILL_DOWN", "candidate_symbol": "BNBUSDT"},
        )

        # P1 caches strategic 1d and 4h (not 1h, not 1m)
        assert "MACRO|BNBUSDT|1d|" in s1.p1_slow
        assert "MACRO|BNBUSDT|4h|" in s1.p1_slow
        assert "MACRO|BNBUSDT|1h|" not in s1.p1_slow

        # Stage 1 P2 carries compact tactical indicators for 1h/15m/5m
        assert "TAC_IND|BNBUSDT|1h:rsi=" in s1.p2_dynamic
        assert "|15m:rsi=" in s1.p2_dynamic
        assert "|5m:rsi=" in s1.p2_dynamic

        # Stage 2 P2 carries OHLCV_TICK_V1 for 1h, 15m, 5m (NO 1m) + causal indicators in PROF_*
        for tf in ("1h", "15m", "5m"):
            assert f"OHLCV_TICK_V1|BNBUSDT|{tf}|" in s2.p2_dynamic
        assert "OHLCV_TICK_V1|BNBUSDT|1m|" not in s2.p2_dynamic
        for ind_tok in ("|ind=rsi=", ",adx=", ",mc=", ",bb=", ",div="):
            assert ind_tok in s2.p2_dynamic
    finally:
        store.close()


def test_agy_direct_resume_fail_soft_retry(tmp_path: Path) -> None:
    seen_cmds: list[list[str]] = []

    def _resume_flaky_runner(
        *,
        prompt: str,
        agy_model: str,
        cmd: list[str],
        timeout_sec: float,
        workdir: Path,
    ) -> dict[str, Any]:
        _ = (prompt, agy_model, timeout_sec, workdir)
        seen_cmds.append(list(cmd))
        if "--resume" in cmd:
            raise RuntimeError("agy error: conversation conv-stale-99 not found")
        return {
            "status": "SUCCESS",
            "response": '{"action":"HOLD"}',
            "conversation_id": "conv-fresh-100",
            "num_turns": 1,
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 50,
                "thinking_tokens": 80,
                "cache_read_tokens": 0,
                "total_tokens": 5130,
            },
        }

    ledger = UsageLedger(tmp_path / "failsoft_resume.sqlite3")
    transport = AgyDirectCliTransport(
        ledger,
        workdir=tmp_path / "ram_ws",
        runner_override=_resume_flaky_runner,
        reuse_session=True,
    )

    async def _run() -> None:
        r1 = await transport.generate(
            LLMRequest(
                decision_id="dec-fs-1",
                stage="stage1_screen",
                model="gemini-4-carbon",
                system_instruction="P0",
                p2_dynamic_tail="P2_S1",
                thinking_level=ThinkingLevel.LOW,
            )
        )
        assert r1.interaction_id == "conv-fresh-100"

        # Inject stale session id manually and confirm Stage 2 retries without --resume on failure
        transport._session_ids_by_decision["dec-fs-1"] = "conv-stale-99"
        r2 = await transport.generate(
            LLMRequest(
                decision_id="dec-fs-1",
                stage="stage2_drill",
                model="gemini-4-carbon",
                system_instruction="P0",
                p2_dynamic_tail="P2_S2",
                thinking_level=ThinkingLevel.MEDIUM,
            )
        )
        assert r2.usage.request_status.value == "ok"
        await transport.close()

    try:
        asyncio.run(_run())
        assert len(seen_cmds) == 3
        assert "--resume" not in seen_cmds[0]
        assert "--resume" in seen_cmds[1] and "conv-stale-99" in seen_cmds[1]
        assert "--resume" not in seen_cmds[2]
    finally:
        ledger.close()


def test_p0_and_p2_stage2_token_thresholds_with_new_indicators(tmp_path: Path) -> None:
    p0_tok = estimate_gemini_sp_tokens(build_p0_static_prefix())
    assert p0_tok >= 4_096

    bundle, _ = _make_5tf_bundle()
    store = ThesisMemoryStore(tmp_path / "tok_p9.sqlite3")
    try:
        asm = PrefixContextAssembler(thesis_store=store, include_images=False)
        asm.assemble_stage1(bundle=bundle, trigger_summary="TOK_CHECK")
        s2 = asm.assemble_stage2(
            bundle=bundle,
            candidate_symbol="BNBUSDT",
            stage1_summary={"action": "DRILL_DOWN", "candidate_symbol": "BNBUSDT"},
        )
        p2_s2_tok = estimate_gemini_sp_tokens(s2.p2_dynamic)
        assert p2_s2_tok >= 2_050
    finally:
        store.close()


def test_run_vst_session_foreground_output_and_report_export_mock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ram_summary = tmp_path / "ram_sess_p9.json"
    report_md = tmp_path / "session_report_p9.md"
    rc = session_runner.main(
        [
            "--transport",
            "mock",
            "--dry-run",
            "--duration-minutes",
            "0.03",
            "--cycle-interval-sec",
            "0.25",
            "--protection-interval-sec",
            "1.0",
            "--ledger-db",
            str(tmp_path / "u.sqlite3"),
            "--order-ledger-db",
            str(tmp_path / "o.sqlite3"),
            "--thesis-db",
            str(tmp_path / "t.sqlite3"),
            "--journal-path",
            str(tmp_path / "j.jsonl"),
            "--session-summary-ram",
            str(ram_summary),
            "--report-out",
            str(report_md),
        ]
    )
    assert rc == 0
    stdout_text = capsys.readouterr().out
    assert "[START] QuantumEdge VST Session" in stdout_text
    assert "Cycle #1" in stdout_text
    assert "Cycle #2" in stdout_text
    assert "UsageLedger" in stdout_text
    assert "[END] Session finished" in stdout_text

    assert ram_summary.is_file()
    assert report_md.is_file()
    md_text = report_md.read_text(encoding="utf-8")
    assert "# QuantumEdge Persistent VST Session Report" in md_text
    assert "## 2. UsageLedger Telemetry" in md_text
    assert "| 1 |" in md_text and "| 2 |" in md_text
