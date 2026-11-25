"""Offline unit tests for Lean Execution Core (contracts, 4-rule risk guard, SQLite ledger, LeanExecutor)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from quantum_edge_core.contracts import MarketSnapshot, OrderResult, TradeDecision
from quantum_edge_core.execution import (
    LeanExecutor,
    LeanRiskGuard,
    OrderLedger,
    RiskConfig,
)


class TestMarketSnapshotContract:
    def test_market_snapshot_ignores_extra_fields(self):
        snap = MarketSnapshot(
            symbol="BTCUSDT",
            bid=64990.0,
            ask=65010.0,
            last_price=65000.0,
            spread_bps=3.08,
            ts_utc=datetime.now(timezone.utc),
            indicators={"rsi_14": 52.4, "atr_14": None},
            unexpected_feed_field="new_exchange_metadata",
            nested_extra={"foo": 123},
        )
        assert snap.symbol == "BTCUSDT"
        assert snap.source == "bingx_ws"
        assert snap.is_stale is False
        assert snap.indicators["rsi_14"] == 52.4
        assert not hasattr(snap, "unexpected_feed_field")


class TestLeanExecutorEntriesAndExits:
    def test_open_long_and_open_short_pass_guard_and_route_properly(self, tmp_path: Path):
        mock_ex = MagicMock()
        mock_ex.create_order.side_effect = [
            {"id": "ex_long_1", "status": "open", "price": 65000.0},
            {"id": "ex_short_1", "status": "closed", "filled": 0.5, "average": 3500.0},
        ]

        ledger = OrderLedger(tmp_path / "orders.sqlite")
        guard = LeanRiskGuard(RiskConfig())
        executor = LeanExecutor(mock_ex, ledger, guard)

        dec_long = TradeDecision(
            decision_id="dec-open-long-001",
            symbol="BTCUSDT",
            action="OPEN_LONG",
            order_type="LIMIT",
            entry_price=65000.0,
            sl_price=64000.0,
            tp_price=67000.0,
            margin_vst=500.0,
            leverage=10,
            reason="15m support bounce",
        )
        res_long = executor.execute(dec_long, current_price=65050.0, open_positions_count=0)

        assert isinstance(res_long, OrderResult)
        assert res_long.status == "SUBMITTED"
        assert res_long.exchange_order_id == "ex_long_1"
        assert res_long.client_order_id.startswith("qe-")
        assert len(res_long.client_order_id) <= 36

        _, kwargs_long = mock_ex.create_order.call_args_list[0]
        assert kwargs_long["symbol"] == "BTC/USDT:USDT"
        assert kwargs_long["type"] == "limit"
        assert kwargs_long["side"] == "buy"
        assert kwargs_long["params"]["positionSide"] == "LONG"
        assert kwargs_long["params"]["reduceOnly"] is False
        assert kwargs_long["params"]["clientOrderId"] == res_long.client_order_id

        dec_short = TradeDecision(
            decision_id="dec-open-short-001",
            symbol="ETHUSDT",
            action="OPEN_SHORT",
            order_type="MARKET",
            sl_price=3600.0,
            tp_price=3350.0,
            margin_vst=175.0,
            leverage=10,
            reason="1h resistance rejection",
        )
        res_short = executor.execute(dec_short, current_price=3500.0, open_positions_count=1)

        assert res_short.status == "FILLED"
        assert res_short.exchange_order_id == "ex_short_1"
        assert res_short.filled_qty == 0.5

        _, kwargs_short = mock_ex.create_order.call_args_list[1]
        assert kwargs_short["symbol"] == "ETH/USDT:USDT"
        assert kwargs_short["type"] == "market"
        assert kwargs_short["side"] == "sell"
        assert kwargs_short["params"]["positionSide"] == "SHORT"
        assert kwargs_short["params"]["reduceOnly"] is False
        assert kwargs_short["params"]["clientOrderId"] == res_short.client_order_id

    def test_close_long_and_close_short_bypass_entry_caps_and_enforce_reduce_only(self, tmp_path: Path):
        mock_ex = MagicMock()
        mock_ex.create_order.return_value = {"id": "ex_close_1", "status": "closed", "filled": 0.1}

        cfg = RiskConfig(max_open_positions=3, entry_enabled=False)
        executor = LeanExecutor(
            mock_ex,
            OrderLedger(tmp_path / "orders.sqlite"),
            LeanRiskGuard(cfg),
        )

        # Even with open_positions_count >= max_open_positions and entry_enabled=False, closes MUST pass
        close_long = TradeDecision(
            decision_id="dec-close-long-001",
            symbol="BTCUSDT",
            action="CLOSE_LONG",
            order_type="MARKET",
            qty=0.1,
            reason="take profit target reached",
        )
        res_cl = executor.execute(close_long, current_price=66500.0, open_positions_count=5)
        assert res_cl.status == "FILLED"
        _, cl_kwargs = mock_ex.create_order.call_args_list[0]
        assert cl_kwargs["side"] == "sell"
        assert cl_kwargs["params"]["positionSide"] == "LONG"
        assert cl_kwargs["params"]["reduceOnly"] is True

        close_short = TradeDecision(
            decision_id="dec-close-short-001",
            symbol="SOLUSDT",
            action="CLOSE_SHORT",
            order_type="MARKET",
            qty=5.0,
            reason="structure flip",
        )
        res_cs = executor.execute(close_short, current_price=145.0, open_positions_count=3)
        assert res_cs.status == "FILLED"
        _, cs_kwargs = mock_ex.create_order.call_args_list[1]
        assert cs_kwargs["side"] == "buy"
        assert cs_kwargs["params"]["positionSide"] == "SHORT"
        assert cs_kwargs["params"]["reduceOnly"] is True

    def test_hold_decision_skips_exchange_and_ledger(self, tmp_path: Path):
        mock_ex = MagicMock()
        ledger = OrderLedger(tmp_path / "orders.sqlite")
        executor = LeanExecutor(mock_ex, ledger, LeanRiskGuard())

        hold_dec = TradeDecision(
            decision_id="dec-hold-001",
            symbol="BTCUSDT",
            action="HOLD",
            reason="mid-range chop between 15m levels",
        )
        res = executor.execute(hold_dec, current_price=65000.0)
        assert res.status == "SKIPPED_HOLD"
        mock_ex.create_order.assert_not_called()
        assert ledger.get(res.client_order_id) is None


class TestLedgerIdempotencyAndTimeoutHandling:
    def test_duplicate_decision_id_blocked_by_ledger(self, tmp_path: Path):
        mock_ex = MagicMock()
        mock_ex.create_order.return_value = {"id": "ex_100", "status": "open"}

        ledger = OrderLedger(tmp_path / "orders.sqlite")
        executor = LeanExecutor(mock_ex, ledger, LeanRiskGuard())

        decision = TradeDecision(
            decision_id="dec-dup-check-42",
            symbol="SOLUSDT",
            action="OPEN_LONG",
            order_type="LIMIT",
            entry_price=150.0,
            sl_price=145.0,
            margin_vst=300.0,
            leverage=5,
            reason="breakout retest",
        )

        first = executor.execute(decision, current_price=150.5)
        second = executor.execute(decision, current_price=150.5)

        assert first.status == "SUBMITTED"
        assert second.status == "REJECTED"
        assert second.error == "DUPLICATE_CLIENT_ORDER_ID"
        assert mock_ex.create_order.call_count == 1

    def test_exchange_timeout_marks_ledger_unknown_without_retry(self, tmp_path: Path):
        mock_ex = MagicMock()
        mock_ex.create_order.side_effect = TimeoutError("BingX request timed out after 30000ms")

        ledger = OrderLedger(tmp_path / "orders.sqlite")
        executor = LeanExecutor(mock_ex, ledger, LeanRiskGuard())

        decision = TradeDecision(
            decision_id="dec-timeout-001",
            symbol="BTCUSDT",
            action="OPEN_SHORT",
            order_type="LIMIT",
            entry_price=66000.0,
            sl_price=67000.0,
            margin_vst=400.0,
            leverage=10,
            reason="liquidity sweep",
        )

        res = executor.execute(decision, current_price=65950.0)

        assert res.status == "UNKNOWN"
        assert "timed out" in (res.error or "")
        assert mock_ex.create_order.call_count == 1

        stored = ledger.get(res.client_order_id)
        assert stored is not None
        assert stored["status"] == "UNKNOWN"
        assert "timed out" in stored["error"]


class TestRiskGuardRejections:
    def test_invalid_stop_loss_side_rejected_with_clear_reason(self, tmp_path: Path):
        mock_ex = MagicMock()
        executor = LeanExecutor(mock_ex, OrderLedger(tmp_path / "orders.sqlite"), LeanRiskGuard())

        bad_sl_long = TradeDecision(
            decision_id="dec-bad-sl-1",
            symbol="BTCUSDT",
            action="OPEN_LONG",
            order_type="LIMIT",
            entry_price=65000.0,
            sl_price=65500.0,  # Wrong side: SL above entry for LONG
            margin_vst=200.0,
            leverage=10,
            reason="invalid sl test",
        )
        res = executor.execute(bad_sl_long, current_price=65000.0)

        assert res.status == "REJECTED"
        assert res.error is not None and "INVALID_STOP_LOSS" in res.error
        mock_ex.create_order.assert_not_called()

    def test_excessive_margin_or_disallowed_symbol_rejected(self, tmp_path: Path):
        mock_ex = MagicMock()
        executor = LeanExecutor(mock_ex, OrderLedger(tmp_path / "orders.sqlite"), LeanRiskGuard())

        over_margin = TradeDecision(
            decision_id="dec-over-margin-1",
            symbol="BTCUSDT",
            action="OPEN_LONG",
            order_type="LIMIT",
            entry_price=65000.0,
            sl_price=64000.0,
            margin_vst=1500.0,  # > default max_margin_vst (1000.0)
            leverage=10,
            reason="oversized margin test",
        )
        res_margin = executor.execute(over_margin, current_price=65000.0)
        assert res_margin.status == "REJECTED"
        assert res_margin.error is not None and "INVALID_MARGIN" in res_margin.error

        bad_sym = TradeDecision(
            decision_id="dec-bad-sym-1",
            symbol="DOGEUSDT",
            action="OPEN_LONG",
            order_type="LIMIT",
            entry_price=0.15,
            sl_price=0.14,
            margin_vst=100.0,
            leverage=5,
            reason="unlisted symbol test",
        )
        res_sym = executor.execute(bad_sym, current_price=0.15)
        assert res_sym.status == "REJECTED"
        assert res_sym.error is not None and "SYMBOL_NOT_ALLOWED" in res_sym.error
        mock_ex.create_order.assert_not_called()
