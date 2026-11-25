"""Offline tests for uncompressed FullMarketContext, SL/TP passthrough, HarnessBridge, and JSONL journal."""

from datetime import datetime, timezone
import json
from pathlib import Path

from quantum_edge_core.contracts import (
    FullMarketContext,
    MarketSnapshot,
    TradeDecision,
)
from quantum_edge_core.execution import (
    HarnessBridge,
    LeanExecutor,
    LeanRiskGuard,
    OrderLedger,
    PaperExchangeClient,
    RiskConfig,
    build_full_market_context,
)


def _generate_candles(count: int, base_price: float, step_sec: int) -> list[dict]:
    base_ts = 1_760_000_000
    bars = []
    for i in range(count):
        open_p = round(base_price + (i % 17) * 12.5, 2)
        bars.append(
            {
                "ts": base_ts + i * step_sec,
                "open": open_p,
                "high": round(open_p + 35.0, 2),
                "low": round(open_p - 28.0, 2),
                "close": round(open_p + 7.5, 2),
                "volume": round(14.25 + i * 0.31, 4),
                "quote_volume": round((14.25 + i * 0.31) * open_p, 2),
            }
        )
    return bars


def _make_bridge(tmp_path: Path, risk_config: RiskConfig | None = None) -> tuple[HarnessBridge, PaperExchangeClient, Path]:
    paper = PaperExchangeClient()
    ledger = OrderLedger(tmp_path / "ledger.sqlite")
    guard = LeanRiskGuard(risk_config or RiskConfig())
    executor = LeanExecutor(paper, ledger, guard)
    journal_path = tmp_path / "lean_trade_journal.jsonl"
    bridge = HarnessBridge(executor=executor, journal_path=journal_path)
    return bridge, paper, journal_path


class TestUncompressedFullMarketContext:
    def test_large_context_over_100kb_passes_without_truncation(self, tmp_path: Path):
        bridge, _, _ = _make_bridge(tmp_path)

        snap = MarketSnapshot(
            symbol="BTCUSDT",
            bid=64990.0,
            ask=65010.0,
            last_price=65000.0,
            spread_bps=3.08,
            ts_utc=datetime.now(timezone.utc),
            indicators={f"ema_{p}": 64800.0 + p for p in (9, 21, 50, 100, 200)},
        )

        ohlcv_by_tf = {
            "1m": _generate_candles(300, 64800.0, 60),
            "5m": _generate_candles(300, 64500.0, 300),
            "15m": _generate_candles(300, 64200.0, 900),
            "1h": _generate_candles(300, 63500.0, 3600),
            "4h": _generate_candles(250, 62000.0, 14400),
            "1d": _generate_candles(180, 58000.0, 86400),
        }
        orderbook = {
            "bids": [[round(64990.0 - i * 0.5, 2), round(0.75 + i * 0.05, 4)] for i in range(250)],
            "asks": [[round(65010.0 + i * 0.5, 2), round(0.80 + i * 0.05, 4)] for i in range(250)],
            "timestamp": 1_760_099_999_000,
        }
        microstructure = {
            "ofi_1s": 12.84,
            "cvd_10s": 88.42,
            "imbalance_top10": 0.27,
            "liqmap_zones": [
                {"price": 63800.0 - i * 50.0, "side": "LONG_LIQ", "notional_usd": 1_250_000.0 + i * 50_000.0}
                for i in range(60)
            ],
            "composite_signals": {"regime": "TREND_UP", "absorption_score": 0.81},
        }
        account_state = {
            "free_margin_vst": 98_450.0,
            "total_equity_vst": 99_120.5,
            "open_positions": [
                {
                    "symbol": "ETHUSDT",
                    "positionSide": "LONG",
                    "contracts": 2.5,
                    "entry_price": 3420.0,
                    "unrealized_pnl": 85.0,
                    "leverage": 10,
                }
            ],
            "open_orders": [
                {"id": f"ord-{i}", "symbol": "ETHUSDT", "price": 3300.0 - i * 10.0, "qty": 0.5}
                for i in range(25)
            ],
            "recent_trades": [
                {"id": f"tr-{i}", "symbol": "BTCUSDT", "pnl": 12.4, "ts": 1_760_000_000 + i}
                for i in range(40)
            ],
        }
        raw_extras = {
            "session": "EU_US_OVERLAP",
            "macro_notes": ["CPI cleared", "ETF net inflow positive"],
        }

        direct_ctx = build_full_market_context(
            snap,
            ohlcv_by_tf=ohlcv_by_tf,
            orderbook=orderbook,
            derivatives={"funding_rate": 0.0001, "open_interest": 425_000_000.0},
            microstructure_and_liquidity=microstructure,
            account_state=account_state,
            raw_extras=raw_extras,
        )
        bridge_ctx = bridge.get_full_context(
            snap,
            ohlcv_by_tf=ohlcv_by_tf,
            orderbook=orderbook,
            derivatives={"funding_rate": 0.0001, "open_interest": 425_000_000.0},
            microstructure_and_liquidity=microstructure,
            account_state=account_state,
            raw_extras=raw_extras,
        )

        assert isinstance(direct_ctx, FullMarketContext)
        assert isinstance(bridge_ctx, FullMarketContext)

        serialized_bytes = bridge_ctx.model_dump_json().encode("utf-8")
        assert len(serialized_bytes) > 100_000

        # Verify zero truncation across all structures
        for tf, expected_bars in ohlcv_by_tf.items():
            assert len(bridge_ctx.ohlcv_by_tf[tf]) == len(expected_bars)
        assert len(bridge_ctx.orderbook["bids"]) == 250
        assert len(bridge_ctx.orderbook["asks"]) == 250
        assert len(bridge_ctx.microstructure_and_liquidity["liqmap_zones"]) == 60
        assert len(bridge_ctx.account_state["open_orders"]) == 25
        assert len(bridge_ctx.account_state["recent_trades"]) == 40
        assert bridge_ctx.raw_extras == raw_extras
        assert bridge_ctx.risk_limits["entry_enabled"] is True

    def test_missing_derivatives_fields_remain_none_not_zero(self):
        snap = MarketSnapshot(
            symbol="SOLUSDT",
            bid=149.9,
            ask=150.1,
            last_price=150.0,
            spread_bps=13.33,
            ts_utc=datetime.now(timezone.utc),
        )
        ctx = build_full_market_context(
            snap,
            derivatives={"funding_rate": -0.00025},
        )
        assert ctx.derivatives["funding_rate"] == -0.00025
        assert ctx.derivatives["next_funding_time"] is None
        assert ctx.derivatives["open_interest"] is None
        assert ctx.derivatives["mark_price"] is None
        assert ctx.derivatives["index_price"] is None
        assert ctx.derivatives["long_short_ratio"] is None


class TestHarnessBridgeExecutionAndJournal:
    def test_submit_open_long_from_dict_and_model_passes_sl_tp_and_logs_jsonl(self, tmp_path: Path):
        bridge, paper, journal_path = _make_bridge(tmp_path)

        raw_decision = {
            "decision_id": "hermes-open-long-dict-1",
            "symbol": "BTCUSDT",
            "action": "OPEN_LONG",
            "order_type": "LIMIT",
            "entry_price": 65000.0,
            "sl_price": 64100.0,
            "tp_price": 66800.0,
            "margin_vst": 500.0,
            "leverage": 10,
            "reason": "H1 bullish orderblock + positive CVD divergence",
        }
        res1 = bridge.submit_decision(raw_decision, current_price=65020.0, open_positions_count=0)
        assert res1.status == "SUBMITTED"
        assert res1.exchange_order_id == "paper-1"

        model_decision = TradeDecision(
            decision_id="hermes-open-long-model-2",
            symbol="ETHUSDT",
            action="OPEN_LONG",
            order_type="MARKET",
            sl_price=3350.0,
            tp_price=3650.0,
            margin_vst=300.0,
            leverage=10,
            reason="15m breakout continuation",
        )
        res2 = bridge.submit_decision(model_decision, current_price=3450.0, open_positions_count=1)
        assert res2.status == "SUBMITTED"
        assert res2.exchange_order_id == "paper-2"

        assert len(paper.submitted_orders) == 2

        ord1 = paper.submitted_orders[0]
        assert ord1["symbol"] == "BTC/USDT:USDT"
        assert ord1["side"] == "buy"
        assert ord1["params"]["positionSide"] == "LONG"
        assert ord1["params"]["reduceOnly"] is False
        assert ord1["params"]["clientOrderId"] == res1.client_order_id
        assert ord1["params"]["stopLoss"] == {"triggerPrice": 64100.0}
        assert ord1["params"]["takeProfit"] == {"triggerPrice": 66800.0}

        ord2 = paper.submitted_orders[1]
        assert ord2["symbol"] == "ETH/USDT:USDT"
        assert ord2["params"]["positionSide"] == "LONG"
        assert ord2["params"]["reduceOnly"] is False
        assert ord2["params"]["clientOrderId"] == res2.client_order_id
        assert ord2["params"]["stopLoss"] == {"triggerPrice": 3350.0}
        assert ord2["params"]["takeProfit"] == {"triggerPrice": 3650.0}

        lines = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 2
        assert lines[0]["decision_id"] == "hermes-open-long-dict-1"
        assert lines[0]["sl_price"] == 64100.0
        assert lines[0]["tp_price"] == 66800.0
        assert lines[0]["order_status"] == "SUBMITTED"
        assert lines[0]["exchange_order_id"] == "paper-1"
        assert lines[0]["error"] is None

    def test_submit_close_long_at_max_positions_enforces_reduce_only_without_sl_tp(self, tmp_path: Path):
        bridge, paper, journal_path = _make_bridge(
            tmp_path,
            risk_config=RiskConfig(max_open_positions=3),
        )

        close_dec = TradeDecision(
            decision_id="hermes-close-long-1",
            symbol="BTCUSDT",
            action="CLOSE_LONG",
            order_type="MARKET",
            sl_price=63000.0,  # Must be stripped on close orders
            tp_price=67000.0,  # Must be stripped on close orders
            qty=0.08,
            reason="Target liquidity pool swept",
        )
        res = bridge.submit_decision(close_dec, current_price=66750.0, open_positions_count=3)
        assert res.status == "SUBMITTED"
        assert len(paper.submitted_orders) == 1

        submitted = paper.submitted_orders[0]
        assert submitted["side"] == "sell"
        assert submitted["params"]["positionSide"] == "LONG"
        assert submitted["params"]["reduceOnly"] is True
        assert "stopLoss" not in submitted["params"]
        assert "takeProfit" not in submitted["params"]

        entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 1
        assert entries[0]["action"] == "CLOSE_LONG"
        assert entries[0]["order_status"] == "SUBMITTED"

    def test_duplicate_decision_id_blocked_and_logged_to_journal(self, tmp_path: Path):
        bridge, paper, journal_path = _make_bridge(tmp_path)

        decision = TradeDecision(
            decision_id="hermes-idempotent-77",
            symbol="SOLUSDT",
            action="OPEN_SHORT",
            order_type="LIMIT",
            entry_price=155.0,
            sl_price=160.0,
            tp_price=144.0,
            margin_vst=250.0,
            leverage=8,
            reason="4h supply rejection",
        )
        first = bridge.submit_decision(decision, current_price=154.8)
        second = bridge.submit_decision(decision, current_price=154.8)

        assert first.status == "SUBMITTED"
        assert second.status == "REJECTED"
        assert second.error == "DUPLICATE_CLIENT_ORDER_ID"
        assert len(paper.submitted_orders) == 1

        entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 2
        assert entries[0]["order_status"] == "SUBMITTED"
        assert entries[1]["order_status"] == "REJECTED"
        assert entries[1]["error"] == "DUPLICATE_CLIENT_ORDER_ID"

    def test_hold_decision_skips_exchange_and_writes_journal_reason(self, tmp_path: Path):
        bridge, paper, journal_path = _make_bridge(tmp_path)

        hold_reason = "Awaiting 15m close outside equilibrium range 64800-65200"
        res = bridge.submit_decision(
            {
                "decision_id": "hermes-hold-99",
                "symbol": "BTCUSDT",
                "action": "HOLD",
                "reason": hold_reason,
            },
            current_price=65000.0,
        )

        assert res.status == "SKIPPED_HOLD"
        assert len(paper.submitted_orders) == 0

        entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 1
        assert entries[0]["action"] == "HOLD"
        assert entries[0]["order_status"] == "SKIPPED_HOLD"
        assert entries[0]["reason"] == hold_reason
        assert entries[0]["exchange_order_id"] is None
