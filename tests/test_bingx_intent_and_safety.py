"""Offline unit tests for BingX Hedge Mode intent mapping, reduceOnly safety, and VST test isolation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from quantum_edge_core.ai_scalper_bot.bot.execution.smart_executor import (
    OrderRequest,
    OrderSide,
    PositionSide,
    SmartExecutor,
)
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.bingx_gateway import (
    BingXExecutionGateway,
)
from quantum_edge_core.dyn_dca_bot.execution.order_router import OrderRouter
from quantum_edge_core.execution import OrderIntent, build_bingx_order_params
import tests.test_bingx_vst as vst_test_module
import tools.qe_trade as qe_trade


class TestBingXIntentMapping:
    def test_all_four_valid_intents(self):
        assert build_bingx_order_params("open_long") == {
            "side": "buy",
            "positionSide": "LONG",
            "reduceOnly": False,
            "hedged": True,
        }
        assert build_bingx_order_params("open_short") == {
            "side": "sell",
            "positionSide": "SHORT",
            "reduceOnly": False,
            "hedged": True,
        }
        assert build_bingx_order_params("close_long") == {
            "side": "sell",
            "positionSide": "LONG",
            "reduceOnly": True,
            "hedged": True,
        }
        assert build_bingx_order_params("close_short") == {
            "side": "buy",
            "positionSide": "SHORT",
            "reduceOnly": True,
            "hedged": True,
        }

    def test_enum_and_hedged_flag(self):
        res = build_bingx_order_params(OrderIntent.CLOSE_LONG, hedged=False)
        assert res == {
            "side": "sell",
            "positionSide": "LONG",
            "reduceOnly": True,
            "hedged": False,
        }

    @pytest.mark.parametrize("bad_intent", ["", "buy", "sell", "long", "CLOSE_LONG", None, 123])
    def test_invalid_intent_raises_value_error(self, bad_intent):
        with pytest.raises(ValueError):
            build_bingx_order_params(bad_intent)  # type: ignore[arg-type]


class TestDynDCAOrderRouterFailClosed:
    def test_reduce_only_error_does_not_retry_without_reduce_only(self, monkeypatch):
        monkeypatch.setenv("USE_TESTNET", "true")
        monkeypatch.setenv("BINGX_TESTNET_API_KEY", "dummy_key")
        monkeypatch.setenv("BINGX_TESTNET_SECRET", "dummy_secret")

        mock_ccxt_client = MagicMock()
        mock_ccxt_client.create_order.side_effect = Exception(
            "bingx {\"code\":101204,\"msg\":\"ReduceOnly Order is rejected\"}"
        )

        with patch("quantum_edge_core.dyn_dca_bot.execution.order_router.ccxt.bingx", return_value=mock_ccxt_client):
            router = OrderRouter(config=SimpleNamespace(symbol="BTCUSDT"))
            result = router.place_limit_order(
                side="sell",
                price=65000.0,
                qty=0.01,
                reduce_only=True,
                position_side="LONG",
            )

        assert mock_ccxt_client.create_order.call_count == 1
        _, kwargs = mock_ccxt_client.create_order.call_args
        assert kwargs["params"] == {"positionSide": "LONG", "reduceOnly": True}
        assert result.get("status") == "REJECTED"
        assert "order_id" not in result


class TestSmartExecutorAndGatewayReduceOnly:
    def test_smart_executor_passes_reduce_only_true(self):
        mock_exchange = AsyncMock()
        mock_exchange.create_order.return_value = {"id": "ord_close_1", "status": "closed"}

        executor = SmartExecutor(mock_exchange)
        req = OrderRequest(
            symbol="BTC/USDT:USDT",
            side=OrderSide.SELL,
            position_side=PositionSide.LONG,
            qty=0.5,
            price=62000.0,
            client_oid="tp_long_1",
            reduce_only=True,
        )

        resp = asyncio.run(executor.place_order(req))
        assert resp["id"] == "ord_close_1"
        mock_exchange.create_order.assert_awaited_once_with(
            symbol="BTC/USDT:USDT",
            type="limit",
            side="sell",
            amount=0.5,
            price=62000.0,
            params={
                "positionSide": "LONG",
                "reduceOnly": True,
                "clientOrderId": "tp_long_1",
            },
        )

    def test_bingx_gateway_passes_reduce_only_true(self):
        mock_exchange = MagicMock()
        mock_exchange.set_sandbox_mode = MagicMock()
        mock_exchange.create_order = AsyncMock(return_value={"id": "gw_close_1", "status": "open"})

        cfg = SimpleNamespace(
            symbol="XRPUSDT",
            trading_mode="futures",
            bingx_testnet_api_key="dummy_key",
            bingx_testnet_secret="dummy_secret",
            use_testnet=True,
            bingx_position_mode="hedge",
        )

        with patch(
            "quantum_edge_core.ai_scalper_bot.bot.infrastructure.bingx_gateway.ccxt.bingx",
            return_value=mock_exchange,
        ):
            gateway = BingXExecutionGateway(cfg)

        req = OrderRequest(
            symbol="XRPUSDT",
            side=OrderSide.BUY,
            position_side=PositionSide.SHORT,
            qty=100.0,
            price=None,
            client_oid="close_short_1",
            reduce_only=True,
        )

        ok = asyncio.run(gateway.execute(req))
        assert ok is True
        mock_exchange.create_order.assert_awaited_once_with(
            symbol="XRP/USDT:USDT",
            type="market",
            side="buy",
            amount=100.0,
            price=None,
            params={
                "positionSide": "SHORT",
                "reduceOnly": True,
                "clientOrderId": "close_short_1",
            },
        )


class TestQeTradeSafety:
    def test_qe_trade_market_close_enforces_reduce_only_and_blocks_wrong_side(self, monkeypatch):
        monkeypatch.setattr(qe_trade, "jlog", lambda entry: None)
        mock_ex = AsyncMock()
        mock_ex.fetch_positions.return_value = [{"side": "long", "contracts": 50, "entryPrice": 0.5}]
        mock_ex.fetch_order_book.return_value = {"bids": [[0.52, 100]], "asks": [[0.53, 100]]}
        mock_ex.create_order.return_value = {"id": "m_close_1", "side": "sell", "amount": 50.0, "status": "closed"}

        res = asyncio.run(qe_trade.market(mock_ex, "sell", "LONG", 50))
        assert res["reduceOnly"] is True
        mock_ex.create_order.assert_awaited_once_with(
            symbol=qe_trade.CCXT_SYMBOL,
            type="market",
            side="sell",
            amount=50.0,
            params={"positionSide": "LONG", "reduceOnly": True, "hedged": True},
        )

        mock_ex.create_order.reset_mock()
        with pytest.raises(ValueError):
            # Buying on LONG leg is opening, not closing — must fail before hitting exchange
            asyncio.run(qe_trade.market(mock_ex, "buy", "LONG", 50))
        mock_ex.create_order.assert_not_called()

    def test_qe_trade_place_closing_branch_sets_reduce_only_true(self, monkeypatch):
        monkeypatch.setattr(qe_trade, "jlog", lambda entry: None)
        mock_ex = AsyncMock()
        mock_ex.create_order.return_value = {
            "id": "lim_close_1",
            "side": "buy",
            "amount": 25.0,
            "price": 0.48,
            "status": "open",
        }

        res = asyncio.run(qe_trade.place(mock_ex, "buy", "SHORT", 25, 0.48))
        assert res["reduceOnly"] is True
        mock_ex.create_order.assert_awaited_once_with(
            symbol=qe_trade.CCXT_SYMBOL,
            type="limit",
            side="buy",
            amount=25.0,
            price=0.48,
            params={"positionSide": "SHORT", "reduceOnly": True, "hedged": True},
        )


class TestLiveVstIsolation:
    def test_vst_test_has_integration_marker_and_skips_by_default(self, monkeypatch):
        monkeypatch.delenv("RUN_LIVE_VST_TESTS", raising=False)

        markers = [m.name for m in getattr(vst_test_module.test_bingx_vst_live_order, "pytestmark", [])]
        assert "integration" in markers

        with pytest.raises(pytest.skip.Exception, match="RUN_LIVE_VST_TESTS=1"):
            vst_test_module.test_bingx_vst_live_order()
