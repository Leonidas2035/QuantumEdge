"""Offline unit tests for BingX VST sandbox adapter and tools/qe_harness_cli.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from quantum_edge_core.contracts import FullMarketContext
from quantum_edge_core.execution import (
    OrderLedger,
    RiskConfig,
    create_bingx_vst_client,
    fetch_full_vst_context,
)
import tools.qe_harness_cli as qe_harness_cli


class TestBingXVstSandboxClientAndContext:
    def test_create_bingx_vst_client_enforces_sandbox_and_blocks_real_trading(self, monkeypatch):
        monkeypatch.setenv("REAL_TRADING", "false")
        monkeypatch.setenv("BINGX_TESTNET_API_KEY", "vst_key_placeholder")
        monkeypatch.setenv("BINGX_TESTNET_SECRET", "vst_secret_placeholder")

        mock_instance = MagicMock()
        with patch(
            "quantum_edge_core.execution.bingx_vst_adapter.ccxt.bingx",
            return_value=mock_instance,
        ) as mock_bingx_cls:
            client = create_bingx_vst_client(load_env=False)

        assert client is mock_instance
        mock_bingx_cls.assert_called_once()
        mock_instance.set_sandbox_mode.assert_called_once_with(True)

        # Verify REAL_TRADING=true raises immediately
        monkeypatch.setenv("REAL_TRADING", "true")
        with pytest.raises(RuntimeError, match="REAL_TRADING is prohibited"):
            create_bingx_vst_client(load_env=False)

    def test_fetch_full_vst_context_aggregates_all_tfs_orderbook_positions_and_none_derivatives(self):
        mock_client = MagicMock()
        mock_client.fetch_order_book.return_value = {
            "bids": [[64995.0, 1.2], [64990.0, 2.5]],
            "asks": [[65005.0, 1.1], [65010.0, 3.0]],
            "timestamp": 1_760_100_000_000,
            "datetime": "2026-11-25T12:00:00Z",
        }
        mock_client.fetch_ticker.return_value = {
            "bid": 64995.0,
            "ask": 65005.0,
            "last": 65000.0,
        }
        mock_client.fetch_ohlcv.side_effect = lambda sym, timeframe, limit: [
            [1_760_000_000_000 + i * 60_000, 64900.0, 65100.0, 64850.0, 65000.0, 12.5]
            for i in range(limit)
        ]
        # Simulate funding endpoint available, but open_interest endpoint unavailable
        mock_client.fetch_funding_rate.return_value = {
            "fundingRate": 0.0001,
            "markPrice": 65001.2,
            # indexPrice intentionally missing -> must stay None
        }
        mock_client.fetch_open_interest.side_effect = NotImplementedError("OI endpoint unavailable in mock")
        mock_client.fetch_balance.return_value = {
            "VST": {"free": 95000.0, "total": 96200.0, "used": 1200.0}
        }
        mock_client.fetch_positions.return_value = [
            {
                "symbol": "XRP/USDT:USDT",
                "side": "short",
                "contracts": 150.0,
                "entryPrice": 0.62,
                "markPrice": 0.61,
                "unrealizedPnl": 1.5,
                "leverage": 10,
                "info": {"positionSide": "SHORT"},
            },
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 0.0,  # Closed/empty leg -> filtered out
            },
        ]
        mock_client.fetch_open_orders.return_value = [
            {
                "id": "vst-ord-1",
                "symbol": "XRP/USDT:USDT",
                "type": "limit",
                "side": "buy",
                "price": 0.58,
                "amount": 150.0,
                "filled": 0.0,
                "status": "open",
                "reduceOnly": True,
                "info": {"positionSide": "SHORT"},
            }
        ]
        mock_client.fetch_my_trades.return_value = []

        tfs = ("1m", "5m", "15m", "1h", "4h")
        ctx = fetch_full_vst_context(
            mock_client,
            symbol="BTCUSDT",
            risk_config=RiskConfig(),
            timeframes=tfs,
            ohlcv_limit=40,
            orderbook_limit=20,
        )

        assert isinstance(ctx, FullMarketContext)
        assert ctx.market_snapshot.symbol == "BTCUSDT"
        assert ctx.market_snapshot.last_price == 65000.0
        for tf in tfs:
            assert len(ctx.ohlcv_by_tf[tf]) == 40
        assert len(ctx.orderbook["bids"]) == 2
        assert len(ctx.orderbook["asks"]) == 2

        # Present derivatives kept, missing/failed ones preserved as None (not 0.0)
        assert ctx.derivatives["funding_rate"] == 0.0001
        assert ctx.derivatives["mark_price"] == 65001.2
        assert ctx.derivatives["index_price"] is None
        assert ctx.derivatives["open_interest"] is None
        assert ctx.derivatives["long_short_ratio"] is None

        # Account state reflects active XRP position and order
        assert ctx.account_state["free_margin_vst"] == 95000.0
        assert len(ctx.account_state["open_positions"]) == 1
        assert ctx.account_state["open_positions"][0]["symbol"] == "XRPUSDT"
        assert len(ctx.account_state["open_orders"]) == 1
        assert ctx.account_state["open_orders"][0]["symbol"] == "XRPUSDT"


class TestQeHarnessCliDryRun:
    def test_cli_execute_dry_run_open_close_ledger_journal_and_duplicate_block(
        self, tmp_path: Path, capsys
    ):
        ledger_db = tmp_path / "cli_orders.sqlite3"
        journal_path = tmp_path / "cli_journal.jsonl"

        open_decision = {
            "decision_id": "cli-test-open-long-1",
            "symbol": "BTCUSDT",
            "action": "OPEN_LONG",
            "order_type": "LIMIT",
            "entry_price": 65000.0,
            "sl_price": 64000.0,
            "tp_price": 67000.0,
            "margin_vst": 500.0,
            "leverage": 10,
            "reason": "15m demand zone bounce",
        }

        rc1 = qe_harness_cli.main(
            [
                "execute",
                "--dry-run",
                "--decision-json",
                json.dumps(open_decision),
                "--current-price",
                "65010.0",
                "--ledger-db",
                str(ledger_db),
                "--journal-path",
                str(journal_path),
            ]
        )
        assert rc1 == 0
        out1 = json.loads(capsys.readouterr().out.strip())
        assert out1["status"] == "SUBMITTED"
        assert out1["exchange_order_id"] == "paper-1"

        # Duplicate decision_id must be rejected by SQLite OrderLedger
        rc_dup = qe_harness_cli.main(
            [
                "execute",
                "--decision-json",
                json.dumps(open_decision),
                "--ledger-db",
                str(ledger_db),
                "--journal-path",
                str(journal_path),
            ]
        )
        assert rc_dup == 0
        out_dup = json.loads(capsys.readouterr().out.strip())
        assert out_dup["status"] == "REJECTED"
        assert out_dup["error"] == "DUPLICATE_CLIENT_ORDER_ID"

        # CLOSE_LONG via CLI --dry-run
        close_decision = {
            "decision_id": "cli-test-close-long-1",
            "symbol": "BTCUSDT",
            "action": "CLOSE_LONG",
            "order_type": "MARKET",
            "qty": 0.075,
            "reason": "TP hit",
        }
        rc_close = qe_harness_cli.main(
            [
                "execute",
                "--dry-run",
                "--decision-json",
                json.dumps(close_decision),
                "--current-price",
                "66950.0",
                "--open-positions-count",
                "3",
                "--ledger-db",
                str(ledger_db),
                "--journal-path",
                str(journal_path),
            ]
        )
        assert rc_close == 0
        out_close = json.loads(capsys.readouterr().out.strip())
        assert out_close["status"] == "SUBMITTED"
        assert out_close["action"] == "CLOSE_LONG"

        # Verify SQLite ledger and JSONL journal entries
        ledger = OrderLedger(ledger_db)
        assert ledger.get(out1["client_order_id"])["status"] == "SUBMITTED"
        assert ledger.get(out_close["client_order_id"])["status"] == "SUBMITTED"

        journal_lines = [
            json.loads(line)
            for line in journal_path.read_text(encoding="utf-8").splitlines()
        ]
        assert len(journal_lines) == 3
        assert [row["order_status"] for row in journal_lines] == [
            "SUBMITTED",
            "REJECTED",
            "SUBMITTED",
        ]
