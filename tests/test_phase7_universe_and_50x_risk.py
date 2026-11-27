"""Phase 7 tests: BNBUSDT/LINKUSDT/SUIUSDT universe, 1000 VST x 50x ISOLATED risk, exchange leverage setup, and SL-before-liquidation."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import tools.qe_harness_cli as harness_cli
from quantum_edge_core.contracts.lean import TradeDecision
from quantum_edge_core.contracts.optimization import HoldBlockerCode, TradeDecisionV2
from quantum_edge_core.execution.bingx_vst_adapter import (
    ensure_isolated_margin_and_leverage,
)
from quantum_edge_core.execution.harness_bridge import PaperExchangeClient
from quantum_edge_core.execution.lean_executor import LeanExecutor
from quantum_edge_core.execution.market_context_serializer import (
    DEFAULT_INSTRUMENT_SPECS,
    MarketContextSerializer,
)
from quantum_edge_core.execution.order_ledger import OrderLedger
from quantum_edge_core.execution.risk_guard import (
    QUANTUMEDGE_DEFAULT_UNIVERSE,
    LeanRiskGuard,
    RiskConfig,
)
from quantum_edge_core.orchestrator.prompts import build_p0_static_prefix
from tools.benchmark_count_tokens import estimate_gemini_sp_tokens


def test_qe_isolated_50x_risk_config_and_notional_cap() -> None:
    cfg = RiskConfig.for_qe_isolated_50x()
    assert cfg.allowed_symbols == ("BNBUSDT", "LINKUSDT", "SUIUSDT")
    assert QUANTUMEDGE_DEFAULT_UNIVERSE == ("BNBUSDT", "LINKUSDT", "SUIUSDT")
    assert cfg.max_margin_vst == 1000.0
    assert cfg.max_leverage == 50
    assert cfg.max_open_positions == 3
    assert cfg.max_notional_per_pair_vst == 50_000.0
    assert cfg.require_isolated_margin is True
    assert cfg.enforce_sl_before_liquidation is True

    # TradeDecisionV2 allows up to 50x and rejects 51x
    dec_v2 = TradeDecisionV2(
        regime_read="15m BNB impulse breakout above range",
        primary_edge="Tight spread + short liquidation magnet",
        counter_thesis="Quick pullback before continuation",
        action="OPEN_LONG",
        symbol="BNBUSDT",
        hold_blocker_code=HoldBlockerCode.NONE,
        confidence=0.80,
        order_type="MARKET",
        entry_price=600.0,
        sl_price=594.0,
        tp_price=612.0,
        margin_vst=1000.0,
        leverage=50,
    )
    assert dec_v2.leverage == 50

    with pytest.raises(ValidationError):
        TradeDecisionV2(
            regime_read="15m BNB impulse breakout above range",
            primary_edge="Over-leveraged attempt",
            counter_thesis="Liquidation risk",
            action="OPEN_LONG",
            symbol="BNBUSDT",
            confidence=0.70,
            order_type="MARKET",
            entry_price=600.0,
            sl_price=595.0,
            tp_price=610.0,
            margin_vst=500.0,
            leverage=51,
        )

    guard = LeanRiskGuard(cfg)

    # 1000 VST x 50x = 50,000 VST passes with tight 1% SL
    ok, reason = guard.validate(
        TradeDecision(
            decision_id="p7-50x-ok",
            symbol="BNBUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            entry_price=600.0,
            sl_price=594.0,
            tp_price=612.0,
            margin_vst=1000.0,
            leverage=50,
            reason="50x isolated cap test",
        ),
        open_positions_count=0,
        current_price=600.0,
    )
    assert ok is True and reason == "OK"

    # 1001 VST rejected by margin cap
    bad_m, reason_m = guard.validate(
        TradeDecision(
            decision_id="p7-over-margin",
            symbol="LINKUSDT",
            action="OPEN_LONG",
            order_type="LIMIT",
            entry_price=15.0,
            sl_price=14.85,
            margin_vst=1001.0,
            leverage=20,
            reason="over margin",
        ),
        open_positions_count=0,
        current_price=15.0,
    )
    assert bad_m is False and "INVALID_MARGIN" in reason_m

    # 51x rejected by leverage cap
    bad_l, reason_l = guard.validate(
        TradeDecision(
            decision_id="p7-over-lev",
            symbol="SUIUSDT",
            action="OPEN_SHORT",
            order_type="LIMIT",
            entry_price=3.0,
            sl_price=3.03,
            margin_vst=500.0,
            leverage=51,
            reason="over leverage",
        ),
        open_positions_count=0,
        current_price=3.0,
    )
    assert bad_l is False and "INVALID_LEVERAGE" in reason_l

    # Max notional cap rejects even when margin_vst <= raised max_margin_vst
    custom_notional_guard = LeanRiskGuard(
        RiskConfig.for_qe_isolated_50x(max_margin_vst=2000.0, max_notional_per_pair_vst=50_000.0)
    )
    bad_n, reason_n = custom_notional_guard.validate(
        TradeDecision(
            decision_id="p7-over-notional",
            symbol="BNBUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            entry_price=600.0,
            sl_price=594.0,
            margin_vst=1500.0,
            leverage=40,  # 60,000 > 50,000
            reason="over notional",
        ),
        open_positions_count=0,
        current_price=600.0,
    )
    assert bad_n is False and "MAX_NOTIONAL_EXCEEDED" in reason_n

    # CLOSE always bypasses entry caps
    ok_close, _ = guard.validate(
        TradeDecision(
            decision_id="p7-close-always",
            symbol="BNBUSDT",
            action="CLOSE_LONG",
            order_type="MARKET",
            qty=1.0,
            reason="exit",
        ),
        open_positions_count=5,
        current_price=605.0,
    )
    assert ok_close is True


def test_sl_before_liquidation_guard_at_50x() -> None:
    guard = LeanRiskGuard(RiskConfig.for_qe_isolated_50x())

    # 50x: 1.0% SL passes (< 1.60% max safe SL buffer)
    ok_50, _ = guard.validate(
        TradeDecision(
            decision_id="sl-50x-safe",
            symbol="BNBUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            entry_price=600.0,
            sl_price=594.0,  # 1.0%
            tp_price=612.0,
            margin_vst=800.0,
            leverage=50,
            reason="tight sl",
        ),
        open_positions_count=0,
        current_price=600.0,
    )
    assert ok_50 is True

    # 50x: 2.5% SL (> 0.80 / 50 = 1.60%) rejected before liquidation
    bad_50, reason_50 = guard.validate(
        TradeDecision(
            decision_id="sl-50x-unsafe",
            symbol="BNBUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            entry_price=600.0,
            sl_price=585.0,  # 2.5%
            tp_price=618.0,
            margin_vst=800.0,
            leverage=50,
            reason="too wide sl for 50x",
        ),
        open_positions_count=0,
        current_price=600.0,
    )
    assert bad_50 is False
    assert "SL_BEYOND_LIQUIDATION_BUFFER" in reason_50

    # 5x: 2.5% SL is well inside 16.0% buffer and passes
    ok_5, _ = guard.validate(
        TradeDecision(
            decision_id="sl-5x-safe",
            symbol="BNBUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            entry_price=600.0,
            sl_price=585.0,  # 2.5%
            tp_price=618.0,
            margin_vst=800.0,
            leverage=5,
            reason="5x wider sl ok",
        ),
        open_positions_count=0,
        current_price=600.0,
    )
    assert ok_5 is True


def test_lean_executor_calls_isolated_and_set_leverage_before_open(tmp_path: Path) -> None:
    class _SpyBingXClient:
        _qe_sandbox_verified = True

        def __init__(self, *, fail_leverage: bool = False, already_isolated: bool = False) -> None:
            self.calls: list[tuple[str, Any]] = []
            self.fail_leverage = fail_leverage
            self.already_isolated = already_isolated

        def set_margin_mode(self, margin_mode: str, symbol: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
            self.calls.append(("set_margin_mode", (margin_mode, symbol)))
            if self.already_isolated:
                raise RuntimeError("bingx: No need to change margin type.")
            return {"code": 0}

        def set_leverage(self, leverage: int, symbol: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
            self.calls.append(("set_leverage", (leverage, symbol, params)))
            if self.fail_leverage:
                raise RuntimeError("bingx: Leverage 50x exceeds tier limit")
            return {"code": 0}

        def create_order(
            self,
            symbol: str,
            type: str,
            side: str,
            amount: float,
            price: float | None = None,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.calls.append(("create_order", (symbol, type, side, amount, params)))
            return {"id": "vst-bnb-1", "status": "open"}

    guard = LeanRiskGuard(RiskConfig.for_qe_isolated_50x())

    # 1. Happy path: set_margin_mode('ISOLATED') -> set_leverage(50, {'side': 'LONG'}) -> create_order
    client_ok = _SpyBingXClient()
    ex_ok = LeanExecutor(client_ok, OrderLedger(tmp_path / "ok.sqlite"), guard)
    dec_ok = TradeDecision(
        decision_id="p7-exec-ok",
        symbol="BNBUSDT",
        action="OPEN_LONG",
        order_type="MARKET",
        entry_price=600.0,
        sl_price=594.0,
        tp_price=612.0,
        margin_vst=1000.0,
        leverage=50,
        reason="isolated 50x test",
    )
    res_ok = ex_ok.execute(dec_ok, current_price=600.0)
    assert res_ok.status == "SUBMITTED"
    assert [c[0] for c in client_ok.calls] == ["set_margin_mode", "set_leverage", "create_order"]
    assert client_ok.calls[0][1] == ("ISOLATED", "BNB/USDT:USDT")
    assert client_ok.calls[1][1] == (50, "BNB/USDT:USDT", {"side": "LONG"})

    # 2. Harmless 'No need to change' from set_margin_mode succeeds cleanly
    client_already = _SpyBingXClient(already_isolated=True)
    ex_already = LeanExecutor(client_already, OrderLedger(tmp_path / "alr.sqlite"), guard)
    res_alr = ex_already.execute(dec_ok, current_price=600.0)
    assert res_alr.status == "SUBMITTED"
    assert [c[0] for c in client_already.calls] == ["set_margin_mode", "set_leverage", "create_order"]

    # 3. Fatal set_leverage error -> REJECTED and create_order is NOT called!
    client_fail = _SpyBingXClient(fail_leverage=True)
    ex_fail = LeanExecutor(client_fail, OrderLedger(tmp_path / "fail.sqlite"), guard)
    res_fail = ex_fail.execute(dec_ok, current_price=600.0)
    assert res_fail.status == "REJECTED"
    assert res_fail.error is not None and "ISOLATED_LEVERAGE_SETUP_FAILED" in res_fail.error
    assert [c[0] for c in client_fail.calls] == ["set_margin_mode", "set_leverage"]

    # 4. PaperExchangeClient without set_margin_mode/set_leverage stays fail-safe
    paper = PaperExchangeClient()
    setup_res = ensure_isolated_margin_and_leverage(
        paper, symbol="LINKUSDT", leverage=20, position_side="LONG"
    )
    assert setup_res["applied"] is False
    assert setup_res["reason"] == "client_unsupported"


def test_bnb_link_sui_specs_and_serializer_roundtrip() -> None:
    assert DEFAULT_INSTRUMENT_SPECS["BNBUSDT"] == (Decimal("0.01"), Decimal("0.01"))
    assert DEFAULT_INSTRUMENT_SPECS["LINKUSDT"] == (Decimal("0.001"), Decimal("0.1"))
    assert DEFAULT_INSTRUMENT_SPECS["SUIUSDT"] == (Decimal("0.0001"), Decimal("1"))

    ser = MarketContextSerializer()
    sample_bars_by_sym = {
        "BNBUSDT": [
            {"ts": 1_795_641_840_000 + i * 300_000, "open": 620.00 + i * 0.15, "high": 621.25 + i * 0.15, "low": 619.50 + i * 0.15, "close": 620.50 + i * 0.15, "volume": 12.34}
            for i in range(6)
        ],
        "LINKUSDT": [
            {"ts": 1_795_641_840_000 + i * 300_000, "open": 15.100 + i * 0.012, "high": 15.250 + i * 0.012, "low": 15.050 + i * 0.012, "close": 15.180 + i * 0.012, "volume": 140.5}
            for i in range(6)
        ],
        "SUIUSDT": [
            {"ts": 1_795_641_840_000 + i * 300_000, "open": 3.1200 + i * 0.005, "high": 3.1650 + i * 0.005, "low": 3.1100 + i * 0.005, "close": 3.1500 + i * 0.005, "volume": 2500}
            for i in range(6)
        ],
    }

    for sym, bars in sample_bars_by_sym.items():
        wire = ser.encode_ohlcv_tick_delta(sym, "5m", bars)
        dec = ser.decode_ohlcv_tick_delta(wire)
        assert dec["symbol"] == sym
        assert len(dec["bars"]) == len(bars)
        for orig, rec in zip(bars, dec["bars"]):
            assert int(rec["ts"]) == int(orig["ts"])
            assert float(rec["open"]) == pytest.approx(float(orig["open"]), abs=1e-9)
            assert float(rec["high"]) == pytest.approx(float(orig["high"]), abs=1e-9)
            assert float(rec["low"]) == pytest.approx(float(orig["low"]), abs=1e-9)
            assert float(rec["close"]) == pytest.approx(float(orig["close"]), abs=1e-9)
            assert float(rec["volume"]) == pytest.approx(float(orig["volume"]), abs=1e-9)


def test_p0_prefix_threshold_preserved_after_prompt_update() -> None:
    p0 = build_p0_static_prefix()
    tok = estimate_gemini_sp_tokens(p0)
    assert tok >= 4_096
    assert "Tight-spread" in p0
    assert "Wide-spread" in p0
    assert "0.80 / leverage" in p0


def test_cli_run_cycle_default_bnb_link_sui_50x_mock_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = harness_cli.build_parser().parse_args(["run-cycle"])
    assert args.symbols == "BNBUSDT,LINKUSDT,SUIUSDT"
    assert args.max_margin_vst == 1000.0
    assert args.max_leverage == 50
    assert args.max_open_positions == 3
    assert args.max_notional_vst == 50_000.0

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
    assert out["validator_passed"] is True
    assert out["decision_payload"]["symbol"] == "BNBUSDT"
