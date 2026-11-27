"""Phase 3 tests: Causal TradeDecisionV2, Trade-or-Justify P0, PreFetchCoordinator, 2-Stage Orchestrator, and QE_D2_10 replay."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import tools.qe_harness_cli as harness_cli
from quantum_edge_core.contracts import (
    HoldBlockerCode,
    RequestStatus,
    ScreeningDecisionV1,
    ThinkingLevel,
    TradeDecision,
    TradeDecisionV2,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution import (
    HarnessBridge,
    LeanExecutor,
    LeanRiskGuard,
    OrderLedger,
    PaperExchangeClient,
    RiskConfig,
)
from quantum_edge_core.orchestrator import (
    DeterministicTradingOrchestrator,
    PreFetchCoordinator,
    PreFetchedBundle,
    StatelessStagePromptAssembler,
    build_p0_static_prefix,
)
from quantum_edge_core.orchestrator.deterministic_orchestrator import (
    scope_vst_status_to_symbols,
)
from quantum_edge_ml.model_router.backends.transport import (
    ImageAttachment,
    LLMRequest,
    LLMResponse,
    LLMTransport,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger


class _ScriptedTransport(LLMTransport):
    """Deterministic scripted LLMTransport recording requests and input_chars."""

    def __init__(self, ledger: UsageLedger, replies: list[str]) -> None:
        super().__init__(ledger)
        self.replies = list(replies)
        self.requests: list[LLMRequest] = []

    async def generate(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        txt = self.replies.pop(0)
        in_chars = len(req.system_instruction) + len(req.combined_user_text)
        rec = UsageRecordV1(
            record_id=f"scr-{uuid.uuid4().hex[:12]}",
            decision_id=req.decision_id,
            stage=req.stage,
            transport=TransportChannel.AGY_CLI_PROXY,
            model_provider_id=req.model,
            thinking_level=req.thinking_level,
            request_status=RequestStatus.OK,
            provider_input_tokens=max(1, in_chars // 4),
            cache_read_tokens=0,
            output_tokens=max(1, len(txt) // 4),
            thinking_tokens=200,
            total_tokens=max(1, in_chars // 4) + max(1, len(txt) // 4) + 200,
            input_chars=in_chars,
            output_chars=len(txt),
            latency_ms=12.0,
            timeout_ms=req.timeout_sec * 1000.0,
            prefix_hash=req.prefix_hash,
            context_hash=req.context_hash,
            snapshot_id=req.snapshot_id,
        )
        self.ledger.record_usage(rec)
        return LLMResponse(text=txt, usage=rec)

    async def count_tokens(
        self, model: str, text_parts: list[str], images: list[ImageAttachment] | None = None
    ) -> dict[str, int]:
        return {"text_tokens": 0, "image_tokens_media_res": 0, "total_tokens": 0}

    async def close(self) -> None:
        return None


def _make_4pair_bundle(*, open_positions: list[dict[str, Any]] | None = None) -> PreFetchedBundle:
    real_btc_path = Path("runtime/step1_enriched_ctx_btcusdt.json")
    btc_ctx = (
        json.loads(real_btc_path.read_text(encoding="utf-8"))
        if real_btc_path.is_file()
        else None
    )
    t0 = 1_795_641_840_000
    contexts: dict[str, dict[str, Any]] = {}
    pairs = {
        "BTCUSDT": (60021.0, 0.28),
        "ETHUSDT": (2420.0, 25.4),
        "SOLUSDT": (143.0, 26.1),
        "XRPUSDT": (0.582, 26.8),
    }
    for sym, (px, spr) in pairs.items():
        if sym == "BTCUSDT" and btc_ctx is not None:
            contexts[sym] = copy.deepcopy(btc_ctx)
            continue
        bars = [
            {
                "ts": t0 + i * 300_000,
                "open": px,
                "high": round(px * 1.003, 4),
                "low": round(px * 0.997, 4),
                "close": round(px * 1.0005, 4),
                "volume": 150.0,
            }
            for i in range(60)
        ]
        contexts[sym] = {
            "market_snapshot": {
                "symbol": sym,
                "bid": px,
                "ask": round(px * (1.0 + spr / 10_000.0), 6),
                "last_price": px,
                "spread_bps": spr,
                "is_stale": False,
            },
            "ohlcv_by_tf": {"4h": bars, "1h": bars, "15m": bars, "5m": bars, "1m": bars},
            "orderbook": {"bids": [[px, 12.0]], "asks": [[round(px * 1.001, 6), 11.0]]},
            "derivatives": {"funding_rate": 0.0001, "open_interest": 42000.0},
            "microstructure_and_liquidity": {
                "liqmap": {
                    "status": "OK",
                    "symbol": sym,
                    "timeframe": "1h",
                    "regime": "flat",
                    "last_price": px,
                    "zones": [
                        {
                            "price": round(px * 1.012, 4),
                            "intensity": 0.92,
                            "side": "SHORT_LIQ",
                            "dist_pct": 1.2,
                            "confidence": 0.64,
                        }
                    ],
                },
                "orderflow": {"status": "OK", "imbalance": 0.14, "cvd_or_delta": 22.0, "metrics": {"imbalance_top10": 0.14}},
                "composite_signal": {"status": "OK", "signal": "LONG", "score": 41, "components": {}},
            },
        }
    pos_list = list(open_positions or [])
    return PreFetchedBundle(
        snapshot_id="snap-test-001",
        vst_status={
            "sandbox": True,
            "balance_vst": {"free": 9100.0, "total": 10000.0, "used": 900.0 if pos_list else 0.0},
            "open_positions_count": len(pos_list),
            "open_positions": pos_list,
            "open_orders_count": 0,
            "open_orders": [],
        },
        contexts_by_symbol=contexts,
        fetch_ms=1.2,
    )


def test_causal_schema_field_order_and_validators() -> None:
    props = list(TradeDecisionV2.model_json_schema()["properties"].keys())
    action_idx = props.index("action")
    for causal_field in ("regime_read", "primary_edge", "counter_thesis", "invalidation_price"):
        assert props.index(causal_field) < action_idx

    # P0 static prefix must be 100% deterministic across calls
    p0_a = build_p0_static_prefix()
    p0_b = build_p0_static_prefix()
    assert p0_a == p0_b
    assert "STAGE1_SCHEMA=" in p0_a
    assert "STAGE2_SCHEMA=" in p0_a

    # Unjustified HOLD rejected in Stage 1 and Stage 2
    with pytest.raises(ValidationError):
        ScreeningDecisionV1(
            market_regime_summary="chop",
            action="HOLD",
            hold_blocker_code=HoldBlockerCode.NONE,
            candidate_rationale="none",
        )
    with pytest.raises(ValidationError):
        ScreeningDecisionV1(
            market_regime_summary="breakout",
            action="DRILL_DOWN",
            candidate_symbol=None,
            candidate_rationale="drill",
        )
    with pytest.raises(ValidationError):
        TradeDecisionV2(
            regime_read="1h flat chop",
            primary_edge="No edge found",
            counter_thesis="Breakout risk",
            action="HOLD",
            symbol="BTCUSDT",
            hold_blocker_code=HoldBlockerCode.NONE,
            confidence=0.5,
        )

    # Invalid LONG SL/TP bracket rejected
    with pytest.raises(ValidationError):
        TradeDecisionV2(
            regime_read="1h bullish trend",
            primary_edge="Orderflow burst up",
            counter_thesis="Pullback risk",
            action="OPEN_LONG",
            symbol="BTCUSDT",
            confidence=0.7,
            order_type="LIMIT",
            entry_price=60000.0,
            sl_price=60100.0,
            tp_price=60500.0,
        )


def test_reduce_maps_long_and_short_to_close_actions() -> None:
    dec = TradeDecisionV2(
        regime_read="1h momentum fading near resistance",
        primary_edge="Partial profit protection after impulse",
        counter_thesis="Trend could resume higher",
        action="REDUCE",
        symbol="BTCUSDT",
        thesis_status="WEAKENED",
        confidence=0.66,
        qty=0.02,
    )
    d_long = TradeDecision.model_validate(
        dec.to_lean_decision_dict("id-1", active_position_side="LONG")
    )
    assert d_long.action == "CLOSE_LONG"
    assert d_long.margin_vst == 0.0
    assert d_long.qty == 0.02

    d_short = TradeDecision.model_validate(
        dec.to_lean_decision_dict("id-2", active_position_side="SHORT")
    )
    assert d_short.action == "CLOSE_SHORT"

    with pytest.raises(ValueError, match="active_position_side"):
        dec.to_lean_decision_dict("id-3", active_position_side=None)


def test_stage1_hold_completes_in_one_call_zero_mutations(tmp_path: Path) -> None:
    u_ledger = UsageLedger(tmp_path / "usage.sqlite3")
    o_ledger = OrderLedger(tmp_path / "orders.sqlite3")
    paper = PaperExchangeClient()
    bridge = HarnessBridge(LeanExecutor(paper, o_ledger, LeanRiskGuard()), tmp_path / "journal.jsonl")

    s1_reply = json.dumps(
        {
            "market_regime_summary": "4h/1h flat chop across majors; alts wide spreads.",
            "action": "HOLD",
            "candidate_symbol": "BTCUSDT",
            "hold_blocker_code": "NO_LIQ_ASYMMETRY",
            "candidate_rationale": "Balanced liqmap magnets and flat CVD.",
            "estimated_edge_bps": 4.0,
        }
    )
    transport = _ScriptedTransport(u_ledger, [s1_reply])
    orch = DeterministicTradingOrchestrator(
        transport=transport,
        ledger=u_ledger,
        bridge=bridge,
    )
    try:
        bundle = _make_4pair_bundle()
        audit = asyncio.run(orch.run_decision_cycle(bundle))
        assert len(transport.requests) == 1
        assert paper.submitted_orders == []
        assert audit.validator_passed is True
        assert audit.validator_reason == "STAGE1_FAST_HOLD"
        assert audit.decision_payload["action"] == "HOLD"
        assert u_ledger.summarize()["total_requests"] == 1
    finally:
        u_ledger.close()


def test_stage2_drilldown_executes_via_paper_harness_bridge(tmp_path: Path) -> None:
    u_ledger = UsageLedger(tmp_path / "usage.sqlite3")
    o_ledger = OrderLedger(tmp_path / "orders.sqlite3")
    paper = PaperExchangeClient()
    journal = tmp_path / "journal.jsonl"
    bridge = HarnessBridge(LeanExecutor(paper, o_ledger, LeanRiskGuard()), journal)

    s1_reply = json.dumps(
        {
            "market_regime_summary": "BTC 1h constructive with nearby SHORT_LIQ magnet.",
            "action": "DRILL_DOWN",
            "candidate_symbol": "BTCUSDT",
            "hold_blocker_code": "NONE",
            "candidate_rationale": "Strong composite score +47 and tight 0.28 bps spread.",
            "estimated_edge_bps": 85.0,
        }
    )
    s2_reply = json.dumps(
        {
            "regime_read": "1h/15m constructive range breakout toward upper liquidity",
            "primary_edge": "Composite +47, tight 0.28bps spread, SHORT_LIQ cluster @ 60656",
            "counter_thesis": "5m pullback to 59900 possible before sweep",
            "invalidation_price": 59550.0,
            "action": "OPEN_LONG",
            "symbol": "BTCUSDT",
            "hold_blocker_code": "NONE",
            "thesis_status": "NEW",
            "confidence": 0.75,
            "evidence_refs": ["BTCUSDT:1h", "BTCUSDT:liqmap"],
            "order_type": "MARKET",
            "entry_price": 60021.0,
            "sl_price": 59500.0,
            "tp_price": 60630.0,
            "margin_vst": 600.0,
            "leverage": 5,
            "qty": None,
        }
    )
    transport = _ScriptedTransport(u_ledger, [s1_reply, s2_reply])
    orch = DeterministicTradingOrchestrator(
        transport=transport,
        ledger=u_ledger,
        bridge=bridge,
    )
    try:
        bundle = _make_4pair_bundle()
        audit = asyncio.run(orch.run_decision_cycle(bundle))
        assert len(transport.requests) == 2
        # Prefix hash (P0 + P1) must match identically between Stage 1 and Stage 2
        assert transport.requests[0].prefix_hash == transport.requests[1].prefix_hash
        assert len(paper.submitted_orders) == 1
        ord_sub = paper.submitted_orders[0]
        assert ord_sub["symbol"] == "BTC/USDT:USDT"
        assert ord_sub["params"]["stopLoss"]["triggerPrice"] == 59500.0
        assert ord_sub["params"]["takeProfit"]["triggerPrice"] == 60630.0
        assert audit.validator_passed is True
        assert audit.execution_result is not None
        assert audit.execution_result["status"] == "SUBMITTED"
        assert journal.is_file()
    finally:
        u_ledger.close()


def test_alt_wide_spread_protocol_limit_allowed_and_market_70bps_gate() -> None:
    sol_ctx = {"market_snapshot": {"symbol": "SOLUSDT", "last_price": 143.0, "spread_bps": 26.0}}

    # 1. LIMIT on +40 bps TP is allowed despite 26 bps spread
    dec_limit = TradeDecisionV2(
        regime_read="15m demand bounce",
        primary_edge="Passive limit entry avoids 26bps spread",
        counter_thesis="Support breakdown risk",
        action="OPEN_LONG",
        symbol="SOLUSDT",
        confidence=0.68,
        order_type="LIMIT",
        entry_price=143.0,
        sl_price=142.20,
        tp_price=143.572,  # +40 bps
        margin_vst=300.0,
        leverage=3,
    )
    ok_lim, reason_lim = DeterministicTradingOrchestrator._post_validate_trade(dec_limit, sol_ctx)
    assert ok_lim is True and reason_lim == "OK"

    # 2. MARKET on +45 bps TP is rejected (< 70 bps gate)
    dec_mkt_small = TradeDecisionV2(
        regime_read="15m bounce",
        primary_edge="Small target market attempt",
        counter_thesis="Spread eats edge",
        action="OPEN_LONG",
        symbol="SOLUSDT",
        confidence=0.62,
        order_type="MARKET",
        entry_price=143.0,
        sl_price=142.20,
        tp_price=143.6435,  # +45 bps
        margin_vst=300.0,
        leverage=3,
    )
    ok_bad, reason_bad = DeterministicTradingOrchestrator._post_validate_trade(dec_mkt_small, sol_ctx)
    assert ok_bad is False and "ALT_MARKET_SPREAD_VIOLATION" in reason_bad

    # 3. MARKET on +80 bps TP passes (>= max(70, 2.5*26=65))
    dec_mkt_ok = TradeDecisionV2(
        regime_read="1h impulse breakout",
        primary_edge="Strong magnet +80bps clears 26bps spread",
        counter_thesis="Fakeout risk",
        action="OPEN_LONG",
        symbol="SOLUSDT",
        confidence=0.74,
        order_type="MARKET",
        entry_price=143.0,
        sl_price=142.00,
        tp_price=144.144,  # +80 bps
        margin_vst=300.0,
        leverage=3,
    )
    ok_good, _ = DeterministicTradingOrchestrator._post_validate_trade(dec_mkt_ok, sol_ctx)
    assert ok_good is True


def test_paired_replay_qe_d2_10_c1_and_c4_scenarios(tmp_path: Path) -> None:
    # Verify PreFetchCoordinator runs in RAM with zero SSD context dumps
    class _StubVstClient:
        def fetch_balance(self) -> dict[str, Any]:
            return {"VST": {"free": 9500.0, "total": 10000.0, "used": 500.0}}

        def fetch_positions(self) -> list[dict[str, Any]]:
            return []

        def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            return []

        def fetch_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any]:
            return {"bids": [[60000.0, 1.0]], "asks": [[60001.5, 1.0]], "timestamp": 1795641840000}

        def fetch_ticker(self, symbol: str) -> dict[str, Any]:
            return {"bid": 60000.0, "ask": 60001.5, "last": 60000.8}

        def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> list[list[float]]:
            return [
                [1795641840000 + i * 60000, 60000.0, 60010.0, 59990.0, 60005.0, 12.5]
                for i in range(60)
            ]

        def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
            return {"fundingRate": 0.0001, "markPrice": 60000.8}

        def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
            return {"openInterestAmount": 12000.0}

    coord = PreFetchCoordinator(_StubVstClient())
    ram_bundle = asyncio.run(coord.fetch_all(include_analytics=False))
    assert set(ram_bundle.contexts_by_symbol.keys()) == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}

    # Paired Replay vs QE_D2_10 c1 (843,994 input chars across 7 ReAct steps) and c4 (HOLD with open LONG)
    u_ledger = UsageLedger(tmp_path / "replay_usage.sqlite3")
    o_ledger = OrderLedger(tmp_path / "replay_orders.sqlite3")
    paper = PaperExchangeClient()
    bridge = HarnessBridge(LeanExecutor(paper, o_ledger, LeanRiskGuard()), tmp_path / "replay_journal.jsonl")

    c1_replies = [
        json.dumps(
            {
                "market_regime_summary": "BTC 1h LONG signal +47 with SHORT_LIQ @ 60656.07; alts wide spread.",
                "action": "DRILL_DOWN",
                "candidate_symbol": "BTCUSDT",
                "hold_blocker_code": "NONE",
                "candidate_rationale": "Tight BTC spread and +105bps liqmap magnet.",
                "estimated_edge_bps": 95.0,
            }
        ),
        json.dumps(
            {
                "regime_read": "1h flat-to-bullish structure targeting upper liquidity cluster",
                "primary_edge": "Composite +47, SHORT_LIQ @ 60656.07 (+105bps), tight 0.28bps spread",
                "counter_thesis": "Intraday chop could retest 59800 support",
                "invalidation_price": 59520.0,
                "action": "OPEN_LONG",
                "symbol": "BTCUSDT",
                "hold_blocker_code": "NONE",
                "thesis_status": "NEW",
                "confidence": 0.76,
                "evidence_refs": ["BTCUSDT:1h", "BTCUSDT:liqmap"],
                "order_type": "MARKET",
                "entry_price": 60021.0,
                "sl_price": 59500.0,
                "tp_price": 60640.0,
                "margin_vst": 800.0,
                "leverage": 5,
                "qty": None,
            }
        ),
    ]
    c4_replies = [
        json.dumps(
            {
                "market_regime_summary": "BTC LONG active (+13.6 VST); alt spreads 25-27bps without clean impulse.",
                "action": "HOLD",
                "candidate_symbol": "BTCUSDT",
                "hold_blocker_code": "ACTIVE_POSITION_ON_TRACK",
                "candidate_rationale": "Existing BTCUSDT LONG tracking toward 60640 TP.",
                "estimated_edge_bps": 10.0,
            }
        )
    ]

    try:
        # Replay c1
        t_c1 = _ScriptedTransport(u_ledger, c1_replies)
        orch_c1 = DeterministicTradingOrchestrator(
            transport=t_c1,
            ledger=u_ledger,
            bridge=bridge,
            assembler=StatelessStagePromptAssembler(include_images=False),
        )
        audit_c1 = asyncio.run(orch_c1.run_decision_cycle(_make_4pair_bundle(), trigger_summary="REPLAY_C1"))
        c1_chars = sum(len(r.system_instruction) + len(r.combined_user_text) for r in t_c1.requests)
        assert len(t_c1.requests) == 2
        assert audit_c1.decision_payload["action"] == "OPEN_LONG"
        assert len(paper.submitted_orders) == 1
        assert 843_994 / c1_chars >= 15.0  # >15x reduction vs QE_D2_10 c1

        # Replay c4
        t_c4 = _ScriptedTransport(u_ledger, c4_replies)
        orch_c4 = DeterministicTradingOrchestrator(
            transport=t_c4,
            ledger=u_ledger,
            bridge=bridge,
            assembler=StatelessStagePromptAssembler(include_images=False),
        )
        c4_bundle = _make_4pair_bundle(
            open_positions=[
                {
                    "symbol": "BTCUSDT",
                    "positionSide": "LONG",
                    "side": "long",
                    "contracts": 0.066,
                    "entry_price": 60021.0,
                    "unrealized_pnl": 13.6,
                }
            ]
        )
        audit_c4 = asyncio.run(orch_c4.run_decision_cycle(c4_bundle, trigger_summary="REPLAY_C4"))
        c4_chars = sum(len(r.system_instruction) + len(r.combined_user_text) for r in t_c4.requests)
        assert len(t_c4.requests) == 1
        assert audit_c4.decision_payload["action"] == "HOLD"
        assert audit_c4.decision_payload["hold_blocker_code"] == "ACTIVE_POSITION_ON_TRACK"
        assert len(paper.submitted_orders) == 1  # Still 1 (zero mutations in c4)
        assert c4_chars < c1_chars
    finally:
        u_ledger.close()


def test_markdown_fenced_json_response_parsing(tmp_path: Path) -> None:
    u_ledger = UsageLedger(tmp_path / "usage.sqlite3")
    o_ledger = OrderLedger(tmp_path / "orders.sqlite3")
    bridge = HarnessBridge(LeanExecutor(PaperExchangeClient(), o_ledger, LeanRiskGuard()), tmp_path / "j.jsonl")

    fenced = (
        "```json\n"
        + json.dumps(
            {
                "market_regime_summary": "chop",
                "action": "HOLD",
                "candidate_symbol": "BTCUSDT",
                "hold_blocker_code": "CONFLICTING_HTF_REGIME",
                "candidate_rationale": "4h bearish vs 15m bounce",
                "estimated_edge_bps": 0.0,
            },
            indent=2,
        )
        + "\n```"
    )
    transport = _ScriptedTransport(u_ledger, [fenced])
    orch = DeterministicTradingOrchestrator(transport=transport, ledger=u_ledger, bridge=bridge)
    try:
        audit = asyncio.run(orch.run_decision_cycle(_make_4pair_bundle()))
        assert audit.decision_payload["hold_blocker_code"] == "CONFLICTING_HTF_REGIME"
    finally:
        u_ledger.close()


def test_cli_run_cycle_mock_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    u_db = tmp_path / "cli_usage.sqlite3"
    o_db = tmp_path / "cli_orders.sqlite3"
    j_path = tmp_path / "cli_journal.jsonl"

    rc = harness_cli.main(
        [
            "run-cycle",
            "--transport",
            "mock",
            "--dry-run",
            "--ledger-db",
            str(u_db),
            "--order-ledger-db",
            str(o_db),
            "--journal-path",
            str(j_path),
            "--dump-ram",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["validator_passed"] is True
    assert out["ram_dump_path"].startswith("/dev/shm/quantumedge/cycles/") or "quantumedge" in out["ram_dump_path"]
    ram_path = Path(out["ram_dump_path"])
    assert ram_path.is_file()
    ram_path.unlink(missing_ok=True)

    with sqlite3.connect(u_db) as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM decision_audits").fetchone()[0]
    assert cnt == 1


def test_orchestrator_scopes_foreign_positions_and_accepts_custom_symbols(tmp_path: Path) -> None:
    # 1. Dynamic symbols accepted and normalized cleanly in ScreeningDecisionV1 & TradeDecisionV2
    s_custom = ScreeningDecisionV1(
        market_regime_summary="AVAX impulse breakout",
        action="DRILL_DOWN",
        candidate_symbol=" avaxusdt ",
        hold_blocker_code=HoldBlockerCode.NONE,
        candidate_rationale="Strong CVD on AVAXUSDT.",
        estimated_edge_bps=95.0,
    )
    assert s_custom.candidate_symbol == "AVAXUSDT"

    d_custom = TradeDecisionV2(
        regime_read="15m momentum expansion on LINKUSDT",
        primary_edge="LINKUSDT passive limit into liquidity cluster",
        counter_thesis="BTC stall could slow alt follow-through",
        action="OPEN_LONG",
        symbol=" linkusdt ",
        confidence=0.71,
        order_type="LIMIT",
        entry_price=15.20,
        sl_price=14.85,
        tp_price=15.90,
        margin_vst=250.0,
        leverage=3,
    )
    assert d_custom.symbol == "LINKUSDT"

    with pytest.raises(ValidationError):
        TradeDecisionV2(
            regime_read="bad symbol format",
            primary_edge="reject slash symbol",
            counter_thesis="invalid",
            action="HOLD",
            symbol="AVAX/USDT",
            hold_blocker_code=HoldBlockerCode.NO_LIQ_ASYMMETRY,
            confidence=0.5,
        )

    # 2. Scope helper strips foreign positions/orders (e.g. BNBUSDT) while preserving ignored counters
    raw_status = {
        "sandbox": True,
        "balance_vst": {"free": 9000.0, "total": 10000.0, "used": 1000.0},
        "open_positions_count": 2,
        "open_positions": [
            {"symbol": "BNBUSDT", "positionSide": "LONG", "contracts": 10.0},
            {"symbol": "AVAX/USDT:USDT", "positionSide": "SHORT", "contracts": 5.0},
        ],
        "open_orders_count": 2,
        "open_orders": [
            {"id": "f-1", "symbol": "BNB/USDT:USDT"},
            {"id": "a-1", "symbol": "AVAXUSDT"},
        ],
    }
    scoped = scope_vst_status_to_symbols(raw_status, ("AVAXUSDT", "LINKUSDT"))
    assert scoped["open_positions_count"] == 1
    assert scoped["open_positions_symbols"] == ["AVAXUSDT"]
    assert scoped["open_orders_count"] == 1
    assert scoped["ignored_foreign_positions_count"] == 1
    assert scoped["ignored_foreign_orders_count"] == 1

    # 3. End-to-end orchestrator ignores foreign BNBUSDT position for LLM, router, and LeanRiskGuard
    t0 = 1_795_641_840_000
    contexts: dict[str, dict[str, Any]] = {}
    for sym, px in (("AVAXUSDT", 28.0), ("LINKUSDT", 15.2)):
        bars = [
            {
                "ts": t0 + i * 300_000,
                "open": px,
                "high": round(px * 1.003, 4),
                "low": round(px * 0.997, 4),
                "close": round(px * 1.001, 4),
                "volume": 200.0,
            }
            for i in range(60)
        ]
        contexts[sym] = {
            "market_snapshot": {
                "symbol": sym,
                "bid": px,
                "ask": round(px * 1.002, 4),
                "last_price": px,
                "spread_bps": 20.0,
                "is_stale": False,
            },
            "ohlcv_by_tf": {"4h": bars, "1h": bars, "15m": bars, "5m": bars, "1m": bars},
            "orderbook": {"bids": [[px, 25.0]], "asks": [[round(px * 1.002, 4), 20.0]]},
            "derivatives": {"funding_rate": 0.0001, "open_interest": 10000.0},
            "microstructure_and_liquidity": {
                "liqmap": {"status": "OK", "symbol": sym, "timeframe": "1h", "regime": "flat", "last_price": px, "zones": []},
                "orderflow": {"status": "OK", "imbalance": 0.25, "cvd_or_delta": 10.0, "metrics": {"imbalance_top10": 0.25}},
                "composite_signal": {"status": "OK", "signal": "LONG", "score": 45, "components": {}},
            },
        }

    bundle_with_foreign = PreFetchedBundle(
        snapshot_id="snap-custom-01",
        vst_status={
            "sandbox": True,
            "balance_vst": {"free": 9200.0, "total": 10000.0, "used": 800.0},
            "open_positions_count": 2,  # Polluted by foreign positions!
            "open_positions": [
                {"symbol": "BNBUSDT", "positionSide": "LONG", "contracts": 50.0},
                {"symbol": "ETHUSDT", "positionSide": "SHORT", "contracts": 2.0},
            ],
            "open_orders_count": 1,
            "open_orders": [{"id": "bnb-sl", "symbol": "BNBUSDT"}],
        },
        contexts_by_symbol=contexts,
        fetch_ms=1.0,
    )

    u_ledger = UsageLedger(tmp_path / "custom_u.sqlite3")
    o_ledger = OrderLedger(tmp_path / "custom_o.sqlite3")
    paper = PaperExchangeClient()
    # max_open_positions=1 would BLOCK AVAXUSDT if foreign BNBUSDT/ETHUSDT were counted!
    risk_cfg = RiskConfig(allowed_symbols=("AVAXUSDT", "LINKUSDT"), max_open_positions=1)
    bridge = HarnessBridge(LeanExecutor(paper, o_ledger, LeanRiskGuard(risk_cfg)), tmp_path / "custom_j.jsonl")

    s1_reply = json.dumps(
        {
            "market_regime_summary": "AVAXUSDT breakout while LINKUSDT flat.",
            "action": "DRILL_DOWN",
            "candidate_symbol": "AVAXUSDT",
            "hold_blocker_code": "NONE",
            "candidate_rationale": "Strong composite +45 on AVAXUSDT.",
            "estimated_edge_bps": 110.0,
        }
    )
    s2_reply = json.dumps(
        {
            "regime_read": "15m constructive range breakout on AVAXUSDT",
            "primary_edge": "Orderflow +0.25 and +125bps upside target via passive LIMIT",
            "counter_thesis": "Support pullback to 27.60 possible",
            "invalidation_price": 27.45,
            "action": "OPEN_LONG",
            "symbol": "AVAXUSDT",
            "hold_blocker_code": "NONE",
            "thesis_status": "NEW",
            "confidence": 0.74,
            "evidence_refs": ["AVAXUSDT:15m"],
            "order_type": "LIMIT",
            "entry_price": 28.0,
            "sl_price": 27.40,
            "tp_price": 28.40,
            "margin_vst": 300.0,
            "leverage": 3,
            "qty": None,
        }
    )
    transport = _ScriptedTransport(u_ledger, [s1_reply, s2_reply])
    orch = DeterministicTradingOrchestrator(
        transport=transport,
        ledger=u_ledger,
        bridge=bridge,
        stage1_timeout_sec=90.0,
        stage2_timeout_sec=150.0,
    )
    try:
        audit = asyncio.run(orch.run_decision_cycle(bundle_with_foreign, trigger_summary="CUSTOM_SCOPED"))
        assert audit.validator_passed is True
        assert audit.execution_result is not None
        assert audit.execution_result["status"] == "SUBMITTED"
        assert len(transport.requests) == 2
        assert transport.requests[0].timeout_sec == 90.0
        assert transport.requests[1].timeout_sec == 150.0
        assert "BNBUSDT" not in transport.requests[0].combined_user_text
        assert "BNBUSDT" not in transport.requests[1].combined_user_text
        assert "ETHUSDT" not in transport.requests[1].combined_user_text

        # Out-of-scope candidate from Stage 1 must fail safely without KeyError
        t_oos = _ScriptedTransport(
            u_ledger,
            [
                json.dumps(
                    {
                        "market_regime_summary": "Model halllucinated symbol.",
                        "action": "DRILL_DOWN",
                        "candidate_symbol": "DOGEUSDT",
                        "hold_blocker_code": "NONE",
                        "candidate_rationale": "Out of session scope.",
                        "estimated_edge_bps": 50.0,
                    }
                )
            ],
        )
        orch_oos = DeterministicTradingOrchestrator(transport=t_oos, ledger=u_ledger, bridge=bridge)
        audit_oos = asyncio.run(orch_oos.run_decision_cycle(bundle_with_foreign, trigger_summary="OOS_CHECK"))
        assert audit_oos.validator_passed is False
        assert audit_oos.validator_reason == "SYMBOL_OUT_OF_SCOPE:DOGEUSDT"
    finally:
        u_ledger.close()
