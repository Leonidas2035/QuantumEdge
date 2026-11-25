"""Offline unit tests for AnalyticsEnricher, FullMarketContext enrichment, and cancel-open-orders."""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import ccxt
import pytest

from quantum_edge_core.contracts import FullMarketContext
from quantum_edge_core.execution import (
    RiskConfig,
    cancel_vst_open_orders,
    compute_market_analytics,
    fetch_full_vst_context,
    is_vst_sandbox_client,
)
import tools.qe_harness_cli as qe_harness_cli


def _make_realistic_ohlcv(n_bars: int = 120, base_price: float = 60000.0, step_ms: int = 3_600_000) -> list[dict]:
    start_ts = 1_760_000_000_000
    bars = []
    price = base_price
    for i in range(n_bars):
        drift = math.sin(i / 7.0) * (base_price * 0.004) + (i * base_price * 0.0002)
        open_p = round(base_price + drift, 4)
        close_p = round(open_p + math.cos(i / 5.0) * (base_price * 0.002), 4)
        high_p = round(max(open_p, close_p) + base_price * 0.003, 4)
        low_p = round(min(open_p, close_p) - base_price * 0.003, 4)
        vol = round(25.0 + (i % 13) * 4.5, 4)
        bars.append(
            {
                "ts": start_ts + i * step_ms,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": vol,
            }
        )
        price = close_p
    _ = price
    return bars


def _make_realistic_orderbook(mid_price: float = 61450.0, levels: int = 50) -> dict:
    bids = [[round(mid_price - (i + 1) * 2.5, 2), round(1.2 + (i % 7) * 0.4, 4)] for i in range(levels)]
    asks = [[round(mid_price + (i + 1) * 2.5, 2), round(0.9 + (i % 5) * 0.3, 4)] for i in range(levels)]
    # Add prominent walls for layering detection
    bids[3][1] = 18.0
    bids[8][1] = 22.0
    trades = [
        {
            "timestamp": 1_760_432_000_000 + i * 1000,
            "price": mid_price + (1.0 if i % 2 == 0 else -1.0),
            "amount": 0.75 + (i % 3) * 0.25,
            "side": "buy" if i % 3 != 0 else "sell",
        }
        for i in range(30)
    ]
    return {
        "bids": bids,
        "asks": asks,
        "timestamp": 1_760_432_000_000,
        "trades": trades,
    }


class TestAnalyticsEnricherCoreAndFailSoft:
    def test_compute_market_analytics_returns_all_three_sections_untruncated(self):
        ohlcv_by_tf = {
            "1m": _make_realistic_ohlcv(120, 61200.0, 60_000),
            "5m": _make_realistic_ohlcv(120, 61000.0, 300_000),
            "15m": _make_realistic_ohlcv(120, 60800.0, 900_000),
            "1h": _make_realistic_ohlcv(120, 60000.0, 3_600_000),
            "4h": _make_realistic_ohlcv(120, 59000.0, 14_400_000),
        }
        orderbook = _make_realistic_orderbook(61450.0, levels=50)
        derivatives = {
            "funding_rate": 0.00012,
            "open_interest": 520_000_000.0,
            "mark_price": 61451.0,
        }

        res = compute_market_analytics(
            symbol="BTCUSDT",
            ohlcv_by_tf=ohlcv_by_tf,
            orderbook=orderbook,
            derivatives=derivatives,
        )

        assert res["liqmap"]["status"] == "OK"
        assert res["liqmap"]["error"] is None
        assert len(res["liqmap"]["price_levels"]) == 140
        assert len(res["liqmap"]["current_profile"]) == 140
        assert isinstance(res["liqmap"]["zones"], list) and len(res["liqmap"]["zones"]) > 0
        assert isinstance(res["liqmap"]["summary"], str) and len(res["liqmap"]["summary"]) > 0

        assert res["orderflow"]["status"] == "OK"
        assert res["orderflow"]["error"] is None
        assert res["orderflow"]["imbalance"] is not None
        assert res["orderflow"]["cvd_or_delta"] is not None
        assert res["orderflow"]["metrics"]["bid_levels_count"] == 50
        assert res["orderflow"]["metrics"]["ask_levels_count"] == 50
        assert res["orderflow"]["metrics"]["trades_sampled"] == 30
        assert res["orderflow"]["metrics"]["delta_source"] == "trades_tape"

        assert res["composite_signal"]["status"] == "OK"
        assert res["composite_signal"]["error"] is None
        assert res["composite_signal"]["signal"] in ("LONG", "SHORT", "NEUTRAL")
        assert -100 <= res["composite_signal"]["score"] <= 100
        comps = res["composite_signal"]["components"]
        for axis in ("trend", "momentum", "volatility", "volume_flow", "liq_map", "atr"):
            assert axis in comps

    @pytest.mark.parametrize(
        ("failing_helper", "failed_key", "healthy_keys"),
        [
            (
                "quantum_edge_core.execution.analytics_enricher._compute_liqmap_section",
                "liqmap",
                ("orderflow", "composite_signal"),
            ),
            (
                "quantum_edge_core.execution.analytics_enricher._compute_orderflow_section",
                "orderflow",
                ("liqmap", "composite_signal"),
            ),
            (
                "quantum_edge_core.execution.analytics_enricher._compute_composite_section",
                "composite_signal",
                ("liqmap", "orderflow"),
            ),
        ],
    )
    def test_fail_soft_isolation_when_single_module_raises(
        self, failing_helper: str, failed_key: str, healthy_keys: tuple[str, str]
    ):
        ohlcv_by_tf = {"1h": _make_realistic_ohlcv(100, 60000.0, 3_600_000)}
        orderbook = _make_realistic_orderbook(61000.0, levels=25)
        derivatives = {"funding_rate": 0.0001}

        with patch(failing_helper, side_effect=RuntimeError(f"boom in {failed_key}")):
            res = compute_market_analytics(
                symbol="BTCUSDT",
                ohlcv_by_tf=ohlcv_by_tf,
                orderbook=orderbook,
                derivatives=derivatives,
            )

        assert res[failed_key]["status"] == "UNAVAILABLE"
        assert f"boom in {failed_key}" in res[failed_key]["error"]
        for ok_key in healthy_keys:
            assert res[ok_key]["status"] == "OK"
            assert res[ok_key]["error"] is None


class TestContextIntegrationAndCliFlags:
    def _build_mock_vst_client(self, ohlcv_limit: int = 100) -> MagicMock:
        mock_client = MagicMock()
        mock_client._qe_sandbox_verified = True
        ob = _make_realistic_orderbook(61450.0, levels=50)
        mock_client.fetch_order_book.return_value = {
            "bids": ob["bids"],
            "asks": ob["asks"],
            "timestamp": ob["timestamp"],
            "datetime": "2026-11-26T00:00:00Z",
        }
        mock_client.fetch_ticker.return_value = {
            "bid": ob["bids"][0][0],
            "ask": ob["asks"][0][0],
            "last": 61450.0,
        }
        bars = _make_realistic_ohlcv(ohlcv_limit, 60000.0, 3_600_000)
        mock_client.fetch_ohlcv.side_effect = lambda sym, timeframe, limit: [
            [b["ts"], b["open"], b["high"], b["low"], b["close"], b["volume"]]
            for b in bars[:limit]
        ]
        mock_client.fetch_trades.return_value = ob["trades"]
        mock_client.fetch_funding_rate.return_value = {"fundingRate": 0.0001, "markPrice": 61451.0}
        mock_client.fetch_open_interest.return_value = {"openInterestValue": 480_000_000.0}
        mock_client.fetch_balance.return_value = {"VST": {"free": 250000.0, "total": 250000.0, "used": 0.0}}
        mock_client.fetch_positions.return_value = []
        mock_client.fetch_open_orders.return_value = []
        mock_client.fetch_my_trades.return_value = []
        return mock_client

    def test_fetch_full_vst_context_enriched_and_no_analytics_toggle(self, tmp_path: Path, capsys):
        mock_client = self._build_mock_vst_client(ohlcv_limit=100)

        ctx_enriched = fetch_full_vst_context(
            mock_client,
            symbol="BTCUSDT",
            risk_config=RiskConfig(),
            include_analytics=True,
        )
        assert isinstance(ctx_enriched, FullMarketContext)
        micro = ctx_enriched.microstructure_and_liquidity
        assert micro["liqmap"]["status"] == "OK"
        assert micro["orderflow"]["status"] == "OK"
        assert micro["composite_signal"]["status"] == "OK"
        assert "imbalance_top10" in micro

        ctx_plain = fetch_full_vst_context(
            mock_client,
            symbol="BTCUSDT",
            risk_config=RiskConfig(),
            include_analytics=False,
        )
        assert ctx_plain.microstructure_and_liquidity.get("analytics_enabled") is False
        assert "liqmap" not in ctx_plain.microstructure_and_liquidity

        # CLI test: default (enriched) vs --no-analytics
        out_enriched = tmp_path / "ctx_enriched.json"
        out_plain = tmp_path / "ctx_plain.json"
        with patch("tools.qe_harness_cli.create_bingx_vst_client", return_value=mock_client):
            rc1 = qe_harness_cli.main(["context", "--symbol", "BTCUSDT", "--out", str(out_enriched)])
            assert rc1 == 0
            summary1 = json.loads(capsys.readouterr().out.strip())
            assert summary1["analytics_status"] == {
                "liqmap": "OK",
                "orderflow": "OK",
                "composite_signal": "OK",
            }

            rc2 = qe_harness_cli.main(
                ["context", "--symbol", "BTCUSDT", "--no-analytics", "--out", str(out_plain)]
            )
            assert rc2 == 0
            summary2 = json.loads(capsys.readouterr().out.strip())
            assert summary2["analytics_enabled"] is False


class TestCancelOpenOrdersSafety:
    def test_cancel_open_orders_rejects_non_sandbox_client_and_real_trading(self, monkeypatch):
        live_client = ccxt.bingx({"options": {"defaultType": "swap"}})
        # Without set_sandbox_mode(True), urls point to production open-api
        assert is_vst_sandbox_client(live_client) is False
        with pytest.raises(RuntimeError, match="sandbox"):
            cancel_vst_open_orders(live_client, symbol="ETHUSDT")

        sandbox_client = ccxt.bingx({"options": {"defaultType": "swap"}})
        sandbox_client.set_sandbox_mode(True)
        assert is_vst_sandbox_client(sandbox_client) is True

        monkeypatch.setenv("REAL_TRADING", "true")
        assert is_vst_sandbox_client(sandbox_client) is False
        with pytest.raises(RuntimeError, match="sandbox|REAL_TRADING"):
            cancel_vst_open_orders(sandbox_client, symbol="ETHUSDT")

    def test_cli_cancel_open_orders_live_vst_and_dry_run_journal(self, tmp_path: Path, capsys, monkeypatch):
        monkeypatch.setenv("REAL_TRADING", "false")
        mock_client = MagicMock()
        mock_client.urls = {"api": {"swap": "https://open-api-vst.{hostname}/openApi"}}
        mock_client.fetch_open_orders.return_value = [
            {
                "id": "lim-101",
                "symbol": "ETH/USDT:USDT",
                "type": "limit",
                "side": "buy",
                "price": 1620.0,
                "amount": 0.5,
                "status": "open",
                "reduceOnly": False,
                "info": {"positionSide": "LONG", "type": "LIMIT"},
            },
            {
                "id": "lim-102",
                "symbol": "ETH/USDT:USDT",
                "type": "limit",
                "side": "sell",
                "price": 1680.0,
                "amount": 0.5,
                "status": "open",
                "reduceOnly": False,
                "info": {"positionSide": "SHORT", "type": "LIMIT"},
            },
        ]
        mock_client.cancel_order.side_effect = lambda oid, sym: {"id": oid, "status": "canceled"}

        journal_path = tmp_path / "cancel_journal.jsonl"

        # 1. Dry-run performs 0 cancel_order calls
        rc_dry = qe_harness_cli.main(
            [
                "cancel-open-orders",
                "--symbol",
                "ETHUSDT",
                "--dry-run",
                "--journal-path",
                str(journal_path),
            ]
        )
        assert rc_dry == 0
        dry_res = json.loads(capsys.readouterr().out.strip())
        assert dry_res["mode"] == "dry_run"
        assert dry_res["cancelled_count"] == 0
        mock_client.cancel_order.assert_not_called()

        # 2. --live-vst on verified sandbox client cancels open orders and logs to journal
        with patch("tools.qe_harness_cli.create_bingx_vst_client", return_value=mock_client):
            rc_live = qe_harness_cli.main(
                [
                    "cancel-open-orders",
                    "--symbol",
                    "ETHUSDT",
                    "--live-vst",
                    "--journal-path",
                    str(journal_path),
                ]
            )
        assert rc_live == 0
        live_res = json.loads(capsys.readouterr().out.strip())
        assert live_res["sandbox"] is True
        assert live_res["cancelled_count"] == 2
        assert live_res["cancelled_order_ids"] == ["lim-101", "lim-102"]
        assert mock_client.cancel_order.call_count == 2

        rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert rows[1]["action"] == "CANCEL_OPEN_ORDERS"
        assert rows[1]["cancelled_count"] == 2
