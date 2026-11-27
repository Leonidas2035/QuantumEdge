"""Phase 8 tests: AgyDirectCliTransport, ConvictionPositionSizer, LocalForecastProvider, P2 risk_limits, and persistent RAM-first session runner."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import tools.run_vst_session as session_runner
from quantum_edge_core.contracts import (
    ForecastBundleV1,
    HoldBlockerCode,
    RequestStatus,
    ThinkingLevel,
    TradeDecision,
    TradeDecisionV2,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution import (
    ConvictionPositionSizer,
    HarnessBridge,
    LeanExecutor,
    LeanRiskGuard,
    OrderLedger,
    PaperExchangeClient,
    RiskConfig,
    SizingResult,
)
from quantum_edge_core.orchestrator import (
    DeterministicTradingOrchestrator,
    PreFetchedBundle,
    PrefixContextAssembler,
    ThesisMemoryStore,
    build_p0_static_prefix,
)
from quantum_edge_core.orchestrator.deterministic_orchestrator import LocalForecastProvider
from quantum_edge_ml.model_router.backends.transport import (
    AGY_SESSION_RESUME_FLAG,
    AgyDirectCliTransport,
    ImageAttachment,
    LLMRequest,
    LLMResponse,
    LLMTransport,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger
from tests.test_phase3_orchestrator import _ScriptedTransport, _make_4pair_bundle
from tools.benchmark_count_tokens import estimate_gemini_sp_tokens


def _make_varying_bars(px: float, n: int = 100, seed: int = 7) -> list[dict[str, float]]:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0002, 0.0035, size=n)
    rets[:30] -= 0.0015
    rets[65:] += 0.0018
    closes = px * np.exp(np.cumsum(rets))
    opens = np.concatenate([[px], closes[:-1]])
    highs = np.maximum(opens, closes) * (1.0 + np.abs(rng.normal(0.0, 0.002, size=n)))
    lows = np.minimum(opens, closes) * (1.0 - np.abs(rng.normal(0.0, 0.002, size=n)))
    vols = rng.uniform(20.0, 140.0, size=n)
    t0 = 1_795_641_840_000
    return [
        {
            "ts": float(t0 + i * 300_000),
            "open": round(float(opens[i]), 4),
            "high": round(float(highs[i]), 4),
            "low": round(float(lows[i]), 4),
            "close": round(float(closes[i]), 4),
            "volume": round(float(vols[i]), 2),
        }
        for i in range(n)
    ]


def test_agy_direct_cli_transport_parses_usage_and_logs_unknown_on_timeout(
    tmp_path: Path,
) -> None:
    assert AGY_SESSION_RESUME_FLAG == "--resume"
    seen_cmds: list[list[str]] = []
    call_idx = {"n": 0}

    def _fake_runner(
        *,
        prompt: str,
        agy_model: str,
        cmd: list[str],
        timeout_sec: float,
        workdir: Path,
    ) -> dict[str, Any]:
        _ = (prompt, agy_model, timeout_sec, workdir)
        seen_cmds.append(list(cmd))
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            return {
                "status": "SUCCESS",
                "response": '{"action":"DRILL_DOWN","candidate_symbol":"BNBUSDT"}',
                "conversation_id": "conv-stage1-abc",
                "num_turns": 1,
                "usage": {
                    "input_tokens": 11200,
                    "output_tokens": 140,
                    "thinking_tokens": 220,
                    "cache_read_tokens": 4000,
                    "total_tokens": 15560,
                },
            }
        if call_idx["n"] == 2:
            return {
                "status": "SUCCESS",
                "response": '{"action":"HOLD"}',
                "conversation_id": "conv-stage1-abc",
                "num_turns": 1,
                "usage": {
                    "input_tokens": 9800,
                    "output_tokens": 95,
                    "thinking_tokens": 310,
                    "cache_read_tokens": 7500,
                    "total_tokens": 10205,
                },
            }
        raise TimeoutError("agy CLI timed out after 90s")

    ledger = UsageLedger(tmp_path / "direct_ledger.sqlite3")
    transport = AgyDirectCliTransport(
        ledger,
        workdir=tmp_path / "ram_ws",
        runner_override=_fake_runner,
        reuse_session=True,
        max_transient_retries=0,
    )

    async def _run() -> None:
        r1 = await transport.generate(
            LLMRequest(
                decision_id="dec-dir-1",
                stage="stage1_screen",
                model="gemini-4-carbon-medium",
                system_instruction="P0",
                p1_slow_prefix="P1",
                p2_dynamic_tail="P2",
                thinking_level=ThinkingLevel.LOW,
                timeout_sec=90.0,
            )
        )
        assert r1.usage.agy_alias == "gemini-4-carbon-low"
        assert r1.usage.provider_input_tokens == 11200
        assert r1.usage.cache_read_tokens == 4000
        assert r1.usage.thinking_tokens == 220
        assert r1.usage.output_tokens == 140
        assert r1.usage.total_tokens == 15560

        r2 = await transport.generate(
            LLMRequest(
                decision_id="dec-dir-1",
                stage="stage2_drill",
                model="gemini-4-carbon",
                system_instruction="P0",
                p1_slow_prefix="P1",
                p2_dynamic_tail="P2_S2",
                thinking_level=ThinkingLevel.XHIGH,
                timeout_sec=150.0,
            )
        )
        assert r2.usage.agy_alias == "gemini-4-carbon-high"
        assert r2.usage.thinking_level == ThinkingLevel.HIGH

        with pytest.raises(TimeoutError):
            await transport.generate(
                LLMRequest(
                    decision_id="dec-dir-timeout",
                    stage="stage1_screen",
                    model="gemini-4-carbon",
                    system_instruction="P0",
                    p2_dynamic_tail="P2",
                    thinking_level=ThinkingLevel.LOW,
                    timeout_sec=90.0,
                )
            )
        await transport.close()

    try:
        asyncio.run(_run())
        assert "--sandbox" in seen_cmds[0]
        assert "--disable-slash-commands" in seen_cmds[0]
        assert "--resume" not in seen_cmds[0]
        assert "--resume" in seen_cmds[1] and "conv-stage1-abc" in seen_cmds[1]

        rows = ledger.fetch_usage_rows()
        to_rows = [r for r in rows if r["request_status"] == "timeout"]
        assert len(to_rows) == 1
        assert int(to_rows[0]["is_censored"]) == 1
        assert to_rows[0]["provider_input_tokens"] is None
        assert to_rows[0]["output_tokens"] is None
        assert to_rows[0]["thinking_tokens"] is None
        assert to_rows[0]["cache_read_tokens"] is None
        assert to_rows[0]["total_tokens"] is None
    finally:
        ledger.close()


def test_conviction_position_sizer_scales_by_risk_score_and_respects_liquidation_and_stop_caps(
    tmp_path: Path,
) -> None:
    cfg = RiskConfig.for_qe_isolated_50x()
    guard = LeanRiskGuard(cfg)
    sizer = ConvictionPositionSizer()

    # 1. Strong low-risk setup with tight 0.6% SL -> high notional, 50x leverage, passes LeanRiskGuard
    ctx_low_risk = {
        "market_snapshot": {"symbol": "BNBUSDT", "last_price": 600.0, "spread_bps": 1.5},
        "microstructure_and_liquidity": {
            "orderflow": {"status": "OK", "imbalance": 0.72},
            "composite_signal": {"status": "OK", "score": 78},
            "liqmap": {"status": "OK", "nearest_short_liq": {"price": 609.0, "dist_pct": 1.5}},
        },
    }
    fc_low_risk = ForecastBundleV1(
        symbol="BNBUSDT",
        regime_probs={"UP": 0.80, "RANGE": 0.15, "DOWN": 0.05},
        regime_maturity="EARLY",
        regime_entropy=0.35,
        cond_vol_bps=18.0,
        vol_zscore=0.2,
        return_quantiles_bps={"q05": -20.0, "q10": -12.0, "q25": 2.0, "q50": 18.0, "q75": 35.0, "q90": 52.0, "q95": 65.0},
        barrier_probs={"up_first": 0.72, "down_first": 0.14, "neither": 0.14},
        expected_mfe_bps={"q50": 45.0, "q90": 95.0},
        expected_mae_bps={"q50": -10.0, "q90": -22.0},
    )
    dec_low = TradeDecisionV2(
        regime_read="15m strong BNB breakout with early HMM confirmation",
        primary_edge="Orderflow +0.72, composite +78, SHORT_LIQ @ 609 (+150bps)",
        counter_thesis="Minor 1m pullback to 598 before sweep",
        action="OPEN_LONG",
        symbol="BNBUSDT",
        confidence=0.85,
        order_type="MARKET",
        entry_price=600.0,
        sl_price=596.4,  # 0.60% stop loss
        tp_price=609.0,  # +150 bps
        margin_vst=900.0,
        leverage=50,
    )
    res_low = sizer.size_order(
        decision=dec_low, ctx=ctx_low_risk, forecast=fc_low_risk, risk_config=cfg
    )
    assert isinstance(res_low, SizingResult)
    assert res_low.risk_score < 0.30
    assert res_low.leverage == 50
    assert 25_000.0 <= res_low.notional_vst <= 50_000.0
    assert res_low.margin_vst <= 1000.0
    assert (res_low.sl_dist_bps / 10_000.0) <= 0.75 / res_low.leverage

    ok_low, reason_low = guard.validate(
        TradeDecision.model_validate(
            {**dec_low.to_lean_decision_dict("s-low"), "margin_vst": res_low.margin_vst, "leverage": res_low.leverage}
        ),
        open_positions_count=0,
        current_price=600.0,
    )
    assert ok_low is True, reason_low

    # 2. High-risk setup + wide 2.0% SL -> leverage auto-clamped <= 37x, smaller notional, ZERO SL_BEYOND_LIQUIDATION_BUFFER!
    ctx_high_risk = {
        "market_snapshot": {"symbol": "LINKUSDT", "last_price": 10.0, "spread_bps": 26.0},
        "microstructure_and_liquidity": {
            "orderflow": {"status": "OK", "imbalance": -0.45},
            "composite_signal": {"status": "OK", "score": -20},
            "liqmap": {"status": "OK"},
        },
    }
    fc_high_risk = ForecastBundleV1(
        symbol="LINKUSDT",
        regime_probs={"UP": 0.15, "RANGE": 0.20, "DOWN": 0.65},
        regime_maturity="EXHAUSTION",
        regime_entropy=0.82,
        cond_vol_bps=65.0,
        vol_zscore=2.6,
        return_quantiles_bps={"q05": -90.0, "q10": -60.0, "q25": -20.0, "q50": 5.0, "q75": 25.0, "q90": 55.0, "q95": 80.0},
        barrier_probs={"up_first": 0.22, "down_first": 0.58, "neither": 0.20},
        expected_mfe_bps={"q50": 35.0, "q90": 75.0},
        expected_mae_bps={"q50": -80.0, "q90": -180.0},
    )
    dec_high = TradeDecisionV2(
        regime_read="Risky counter-trend long attempt on LINKUSDT",
        primary_edge="Wide limit catch at demand shelf despite adverse CVD",
        counter_thesis="Downtrend continuation blows straight through 9.80",
        action="OPEN_LONG",
        symbol="LINKUSDT",
        confidence=0.52,
        order_type="LIMIT",
        entry_price=10.0,
        sl_price=9.80,  # 2.0% wide SL
        tp_price=10.40,
        margin_vst=900.0,
        leverage=50,
    )
    res_high = sizer.size_order(
        decision=dec_high, ctx=ctx_high_risk, forecast=fc_high_risk, risk_config=cfg
    )
    assert res_high.risk_score >= 0.70
    assert res_high.leverage <= 37
    assert res_high.notional_vst < res_low.notional_vst

    ok_high, reason_high = guard.validate(
        TradeDecision.model_validate(
            {**dec_high.to_lean_decision_dict("s-high"), "margin_vst": res_high.margin_vst, "leverage": res_high.leverage}
        ),
        open_positions_count=0,
        current_price=10.0,
    )
    assert ok_high is True, reason_high

    # 3. End-to-end orchestrator clamps LLM's margin_vst=5000.0 (Canary 02 C2 bug!) so trade SUCCEEDS!
    u_ledger = UsageLedger(tmp_path / "u_clamp.sqlite3")
    o_ledger = OrderLedger(tmp_path / "o_clamp.sqlite3")
    paper = PaperExchangeClient()
    bridge = HarnessBridge(LeanExecutor(paper, o_ledger, guard), tmp_path / "j_clamp.jsonl")
    bundle_3 = PreFetchedBundle(
        snapshot_id="snap-bnb-clamp",
        vst_status={"balance_vst": {"free": 10000.0}, "open_positions": [], "open_orders": [], "open_positions_count": 0, "open_orders_count": 0},
        contexts_by_symbol={
            "BNBUSDT": {
                **ctx_low_risk,
                "ohlcv_by_tf": {"4h": _make_varying_bars(600.0, 40), "1h": _make_varying_bars(600.0, 40), "15m": _make_varying_bars(600.0, 40), "5m": _make_varying_bars(600.0, 40), "1m": _make_varying_bars(600.0, 40)},
                "orderbook": {"bids": [[600.0, 10.0]], "asks": [[600.1, 10.0]]},
                "derivatives": {"funding_rate": 0.0001, "open_interest": 15000.0},
            }
        },
        fetch_ms=1.0,
    )
    s1_reply = json.dumps(
        {
            "market_regime_summary": "BNBUSDT tight spread bullish breakout.",
            "action": "DRILL_DOWN",
            "candidate_symbol": "BNBUSDT",
            "hold_blocker_code": "NONE",
            "candidate_rationale": "Strong orderflow +78 on BNB.",
            "estimated_edge_bps": 120.0,
        }
    )
    s2_reply_5000 = json.dumps(
        {
            "regime_read": "15m strong BNB breakout above range",
            "primary_edge": "Orderflow +0.72 and SHORT_LIQ @ 609",
            "counter_thesis": "1m dip to 598 possible",
            "action": "OPEN_LONG",
            "symbol": "BNBUSDT",
            "hold_blocker_code": "NONE",
            "confidence": 0.85,
            "order_type": "MARKET",
            "entry_price": 600.0,
            "sl_price": 596.4,
            "tp_price": 609.0,
            "margin_vst": 5000.0,  # Over 1000.0 limit! Sizer must clamp this cleanly!
            "leverage": 50,
        }
    )
    t_scr = _ScriptedTransport(u_ledger, [s1_reply, s2_reply_5000])
    orch = DeterministicTradingOrchestrator(
        transport=t_scr, ledger=u_ledger, bridge=bridge, position_sizer=sizer
    )
    try:
        audit = asyncio.run(
            orch.run_decision_cycle(bundle_3, forecasts_by_symbol={"BNBUSDT": fc_low_risk})
        )
        assert audit.validator_passed is True
        assert audit.execution_result is not None
        assert audit.execution_result["status"] == "SUBMITTED"
        assert audit.decision_payload["sizing"]["clamped_from_llm_margin"] == 5000.0
        assert audit.decision_payload["sizing"]["margin_vst"] <= 1000.0
        assert len(paper.submitted_orders) == 1
    finally:
        u_ledger.close()


def test_p2_includes_risk_limits_and_compact_forecast_v1(tmp_path: Path) -> None:
    p0_tok = estimate_gemini_sp_tokens(build_p0_static_prefix())
    assert p0_tok >= 4_096

    store = ThesisMemoryStore(tmp_path / "thesis_p8.sqlite3")
    try:
        cfg = RiskConfig.for_qe_isolated_50x()
        asm = PrefixContextAssembler(thesis_store=store, include_images=False, risk_limits=cfg)
        bundle = _make_4pair_bundle()
        fc = ForecastBundleV1(
            symbol="BTCUSDT",
            horizon="15m",
            regime_probs={"UP": 0.68, "RANGE": 0.22, "DOWN": 0.10},
            regime_maturity="EARLY",
            regime_entropy=0.45,
            cond_vol_bps=19.5,
            vol_zscore=0.25,
            return_quantiles_bps={"q05": -30.0, "q10": -20.0, "q25": -6.0, "q50": 8.0, "q75": 22.0, "q90": 36.0, "q95": 48.0},
            barrier_probs={"up_first": 0.64, "down_first": 0.18, "neither": 0.18},
            expected_mfe_bps={"q50": 32.0, "q90": 68.0},
            expected_mae_bps={"q50": -12.0, "q90": -25.0},
        )
        s1 = asm.assemble_stage1(
            bundle=bundle, trigger_summary="P8_CHECK", forecasts_by_symbol={"BTCUSDT": fc}, current_seq=1
        )
        s2 = asm.assemble_stage2(
            bundle=bundle, candidate_symbol="BTCUSDT", stage1_summary={"action": "DRILL_DOWN", "candidate_symbol": "BTCUSDT"}, forecast=fc
        )
        for p2_text in (s1.p2_dynamic, s2.p2_dynamic):
            assert '"max_margin_vst":1000.0' in p2_text
            assert '"max_leverage":50' in p2_text
            assert '"max_notional_vst":50000.0' in p2_text
            assert "FORECAST_V1|BTCUSDT|15m|" in p2_text
    finally:
        store.close()


def test_local_forecast_provider_produces_valid_bundles_in_ram() -> None:
    provider = LocalForecastProvider(horizon="15m", timeframe="5m", horizon_steps=3, refit_interval_sec=900.0)
    bundle = PreFetchedBundle(
        snapshot_id="snap-fc-test",
        vst_status={"balance_vst": {"free": 10000.0}, "open_positions": [], "open_orders": []},
        contexts_by_symbol={
            "BNBUSDT": {"ohlcv_by_tf": {"5m": _make_varying_bars(620.0, n=100, seed=11)}},
            "LINKUSDT": {"ohlcv_by_tf": {"5m": _make_varying_bars(15.2, n=100, seed=22)}},
        },
        fetch_ms=0.5,
    )
    res1 = provider.build_forecasts(bundle, now_monotonic=100.0)
    assert set(res1.keys()) == {"BNBUSDT", "LINKUSDT"}
    for sym, fc in res1.items():
        assert isinstance(fc, ForecastBundleV1)
        assert fc.symbol == sym
        assert fc.to_compact_prompt_str().startswith(f"FORECAST_V1|{sym}|15m|")

    # Cached refit reuse on second cycle inside 900s
    res2 = provider.build_forecasts(bundle, now_monotonic=280.0)
    assert set(res2.keys()) == {"BNBUSDT", "LINKUSDT"}


def test_persistent_run_vst_session_mock_preserves_prefix_hash_and_runs_protection(
    tmp_path: Path,
) -> None:
    ram_summary = tmp_path / "ram_session_summary.json"
    args = session_runner.build_parser().parse_args(
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
            str(tmp_path / "sess_usage.sqlite3"),
            "--order-ledger-db",
            str(tmp_path / "sess_orders.sqlite3"),
            "--thesis-db",
            str(tmp_path / "sess_thesis.sqlite3"),
            "--journal-path",
            str(tmp_path / "sess_journal.jsonl"),
            "--session-summary-ram",
            str(ram_summary),
        ]
    )
    summary = asyncio.run(session_runner.run_persistent_session(args))
    assert ram_summary.is_file()
    assert summary["cycles_count"] >= 2
    assert summary["prefix_hash_stable"] is True
    assert len(summary["unique_prefix_hashes"]) == 1
    assert summary["cycles"][0]["resync_mode"] == "RESYNC"
    assert summary["cycles"][1]["resync_mode"] in ("LOCAL_RECONSTRUCT", "CACHE_HIT")
    assert summary["watcher_runs"] >= 1
    # Mock HOLD triggers 1 LLM call per cycle (never called by PositionProtectionWatcher)
    assert summary["usage_summary"]["total_requests"] == summary["cycles_count"]


def test_agy_direct_transient_crash_recovery_logs_retry_of_and_clears_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_cmds: list[list[str]] = []
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(float(delay))

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    call_no = {"n": 0}

    def _flaky_runner(**kwargs: Any) -> dict[str, Any]:
        call_no["n"] += 1
        seen_cmds.append(list(kwargs["cmd"]))
        if call_no["n"] == 1:
            return {
                "status": "SUCCESS",
                "response": '{"action":"HOLD"}',
                "conversation_id": "conv-prev-1",
                "usage": {"input_tokens": 1000, "output_tokens": 20, "total_tokens": 1020},
            }
        if call_no["n"] == 2:
            raise RuntimeError("agy exited with code 1: HTTP 503 service unavailable")
        return {
            "status": "SUCCESS",
            "response": '{"action":"DRILL_DOWN","candidate_symbol":"BNBUSDT"}',
            "conversation_id": "conv-fresh-2",
            "usage": {"input_tokens": 4200, "output_tokens": 60, "total_tokens": 4260},
        }

    ledger = UsageLedger(tmp_path / "retry_ledger.sqlite3")
    transport = AgyDirectCliTransport(
        ledger,
        workdir=tmp_path / "ram_retry",
        runner_override=_flaky_runner,
        reuse_session=True,
        max_transient_retries=2,
        retry_backoff_sec=(2.0, 5.0),
    )
    try:
        async def _run() -> None:
            await transport.generate(
                LLMRequest(
                    decision_id="d-init",
                    stage="stage1_screen",
                    model="gemini-4-carbon",
                    system_instruction="P0",
                    p2_dynamic_tail="P2",
                    thinking_level=ThinkingLevel.LOW,
                )
            )
            r2 = await transport.generate(
                LLMRequest(
                    decision_id="d-retry",
                    stage="stage1_screen",
                    model="gemini-4-carbon",
                    system_instruction="P0",
                    p2_dynamic_tail="P2",
                    thinking_level=ThinkingLevel.LOW,
                )
            )
            assert r2.usage.request_status == RequestStatus.OK
            await transport.close()

        asyncio.run(_run())
        assert sleep_calls == [2.0]
        assert "--resume" in seen_cmds[1]
        # After transient crash, session was reset so retry omits --resume
        assert "--resume" not in seen_cmds[2]

        rows = ledger.fetch_usage_rows()
        err_rows = [r for r in rows if r["decision_id"] == "d-retry" and r["request_status"] == "error"]
        ok_rows = [r for r in rows if r["decision_id"] == "d-retry" and r["request_status"] == "ok"]
        assert len(err_rows) == 1
        assert err_rows[0]["provider_input_tokens"] is None
        assert len(ok_rows) == 1
        assert ok_rows[0]["retry_of"] == err_rows[0]["record_id"]
    finally:
        ledger.close()


def test_agy_direct_quota_reached_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(float(delay))

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    def _quota_runner(**_kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        raise RuntimeError("API error: RESOURCE_EXHAUSTED quota exhausted for project")

    ledger = UsageLedger(tmp_path / "quota_ledger.sqlite3")
    transport = AgyDirectCliTransport(
        ledger,
        workdir=tmp_path / "ram_quota",
        runner_override=_quota_runner,
        max_transient_retries=2,
        retry_backoff_sec=(2.0, 5.0),
    )
    try:
        with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
            asyncio.run(
                transport.generate(
                    LLMRequest(
                        decision_id="d-quota",
                        stage="stage1_screen",
                        model="gemini-4-carbon",
                        system_instruction="P0",
                        p2_dynamic_tail="P2",
                        thinking_level=ThinkingLevel.LOW,
                    )
                )
            )
        assert calls["n"] == 1
        assert sleep_calls == []
        rows = ledger.fetch_usage_rows()
        assert len(rows) == 1
        assert rows[0]["request_status"] == "quota_reached"
    finally:
        ledger.close()


def test_agy_direct_auto_resets_session_after_max_resume_turns(tmp_path: Path) -> None:
    seen_cmds: list[list[str]] = []

    def _ok_runner(**kwargs: Any) -> dict[str, Any]:
        seen_cmds.append(list(kwargs["cmd"]))
        return {
            "status": "SUCCESS",
            "response": '{"action":"HOLD"}',
            "conversation_id": f"conv-turn-{len(seen_cmds)}",
            "usage": {"input_tokens": 1000, "output_tokens": 20, "total_tokens": 1020},
        }

    ledger = UsageLedger(tmp_path / "turns_ledger.sqlite3")
    transport = AgyDirectCliTransport(
        ledger,
        workdir=tmp_path / "ram_turns",
        runner_override=_ok_runner,
        reuse_session=True,
        max_resume_turns=2,
    )
    try:
        async def _run() -> None:
            for idx in range(3):
                await transport.generate(
                    LLMRequest(
                        decision_id=f"d-turn-{idx}",
                        stage="stage1_screen",
                        model="gemini-4-carbon",
                        system_instruction="P0",
                        p2_dynamic_tail="P2",
                        thinking_level=ThinkingLevel.LOW,
                    )
                )
            await transport.close()

        asyncio.run(_run())
        assert len(seen_cmds) == 3
        assert "--resume" not in seen_cmds[0]
        assert "--resume" in seen_cmds[1] and "conv-turn-1" in seen_cmds[1]
        assert "--resume" not in seen_cmds[2]
    finally:
        ledger.close()
