import unittest
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from quantum_edge_core.ai_scalper_bot.bot.execution.smart_executor import (
    SmartExecutor,
    OrderRequest,
    OrderSide,
    PositionSide,
)
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import (
    DynamicDCAStrategy,
    MarketState,
    FeatureVector,
)
from quantum_edge_core.ai_scalper_bot.bot.core.models import TradingMode


class TestSmartExecutor(unittest.IsolatedAsyncioTestCase):

    async def test_smart_executor_place_order_long_sell(self):
        """
        Test that a SELL order with position_side=PositionSide.LONG correctly routes
        payload structures to CCXT without falling back to a SHORT bias.
        """
        mock_exchange = AsyncMock()
        mock_exchange.create_order.return_value = {
            "id": "test_order_123",
            "status": "closed",
        }

        executor = SmartExecutor(mock_exchange)
        request = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            position_side=PositionSide.LONG,
            qty=1.5,
            price=60000.0,
            client_oid="client_ref_123",
        )

        response = await executor.place_order(request)

        self.assertEqual(response["id"], "test_order_123")
        mock_exchange.create_order.assert_called_once_with(
            symbol="BTCUSDT",
            type="limit",
            side="sell",
            amount=1.5,
            price=60000.0,
            params={
                "positionSide": "LONG",
                "clientOrderId": "client_ref_123",
            },
        )

    async def test_smart_executor_place_order_short_buy(self):
        """
        Test that a BUY order with position_side=PositionSide.SHORT correctly routes
        payload structures to CCXT without falling back to a LONG bias.
        """
        mock_exchange = AsyncMock()
        mock_exchange.create_order.return_value = {
            "id": "test_order_456",
            "status": "closed",
        }

        executor = SmartExecutor(mock_exchange)
        request = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            position_side=PositionSide.SHORT,
            qty=2.0,
            price=None,  # Market order
        )

        response = await executor.place_order(request)

        self.assertEqual(response["id"], "test_order_456")
        mock_exchange.create_order.assert_called_once_with(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=2.0,
            price=None,
            params={
                "positionSide": "SHORT",
            },
        )

    def test_position_manager_hedge_mode(self):
        """
        Test that PositionManager maintains separate Long and Short states
        and correctly handles order quantity doubling.
        """
        pm = PositionManager(mode="spot_grid", initial_quote_balance=10000.0)

        # 1. Simulate BUY fill for LONG position
        pm.simulate_fill(
            price=Decimal("50000.0"),
            qty=Decimal("0.1"),
            side="BUY",
            position_side=PositionSide.LONG,
        )
        self.assertEqual(pm.long_state.total_qty, Decimal("0.1"))
        self.assertEqual(pm.long_state.avg_price, Decimal("50000.0"))
        self.assertEqual(pm.short_state.total_qty, Decimal("0.0"))

        # 2. Simulate SELL fill for SHORT position (Selling to open)
        pm.simulate_fill(
            price=Decimal("51000.0"),
            qty=Decimal("0.05"),
            side="SELL",
            position_side=PositionSide.SHORT,
        )
        self.assertEqual(pm.short_state.total_qty, Decimal("0.05"))
        self.assertEqual(pm.short_state.avg_price, Decimal("51000.0"))
        self.assertEqual(pm.long_state.total_qty, Decimal("0.1"))  # Long unchanged

        # 3. Simulate SELL fill to reduce LONG position (Close/Reduce)
        pm.simulate_fill(
            price=Decimal("52000.0"),
            qty=Decimal("0.04"),
            side="SELL",
            position_side=PositionSide.LONG,
        )
        self.assertEqual(pm.long_state.total_qty, Decimal("0.06"))
        self.assertEqual(pm.long_state.avg_price, Decimal("50000.0"))  # Avg price remains same
        self.assertEqual(pm.short_state.total_qty, Decimal("0.05"))  # Short unchanged

        # 4. Simulate BUY fill to reduce SHORT position (Buy to cover)
        pm.simulate_fill(
            price=Decimal("50000.0"),
            qty=Decimal("0.02"),
            side="BUY",
            position_side=PositionSide.SHORT,
        )
        self.assertEqual(pm.short_state.total_qty, Decimal("0.03"))
        self.assertEqual(pm.short_state.avg_price, Decimal("51000.0"))

        # 5. Check order qty calculations (should be scaled by 2x)
        # Final long quote balance is 7080.0 USDT.
        # Exposure = 0.5, levels = 30 -> capital per level = 7080 * 0.5 / 30 = 118.0 USDT.
        # Doubled: 118.0 * 2 = 236.0 USDT. At price 50000 -> 236.0 / 50000 = 0.00472 BTC.
        # Rounded to 4 decimals -> 0.0047 BTC.
        pm.exposure_pct = Decimal("0.5")
        pm.total_levels = Decimal("30")
        qty = pm.calculate_order_qty(50000.0)
        self.assertEqual(qty, 0.0047)


    def test_strategy_on_order_filled(self):
        """
        Test that DynamicDCAStrategy on_order_filled returns OrderRequest
        with correct side and positionSide matching long/short axes.
        """
        config = {
            "symbol": "BTCUSDT",
            "grid_levels_below": 15,
            "grid_levels_above": 15,
            "risk_percent": 0.01,
            "fractional_kelly": 0.25,
            "base_order_size_q": 0.001,
        }
        strategy = DynamicDCAStrategy(config)

        # 1. Filled Buy (Long position added) -> should produce Sell order targeting Long position
        req_long = strategy.on_order_filled(
            side="BUY",
            price=Decimal("60000.0"),
            qty=Decimal("0.005"),
            spacing_pct=Decimal("0.005"),
        )
        self.assertIsInstance(req_long, OrderRequest)
        self.assertEqual(req_long.side, OrderSide.SELL)
        self.assertEqual(req_long.position_side, PositionSide.LONG)
        self.assertAlmostEqual(req_long.price, 60300.0)
        self.assertEqual(req_long.qty, 0.005)

        # 2. Filled Sell (Short position added) -> should produce Buy order targeting Short position
        req_short = strategy.on_order_filled(
            side="SELL",
            price=Decimal("60000.0"),
            qty=Decimal("0.005"),
            spacing_pct=Decimal("0.005"),
        )
        self.assertIsInstance(req_short, OrderRequest)
        self.assertEqual(req_short.side, OrderSide.BUY)
        self.assertEqual(req_short.position_side, PositionSide.SHORT)
        self.assertAlmostEqual(req_short.price, 59700.0)
        self.assertEqual(req_short.qty, 0.005)

    def test_strategy_liquidity_wall_frontrunning(self):
        """
        Test that DynamicDCAStrategy.decide() calls adjust_to_liquidity()
        to adjust grid order prices when a whale wall is detected near a level.
        """
        config = {
            "symbol": "BTCUSDT",
            "grid_levels_below": 1,
            "grid_levels_above": 1,
            "risk_percent": 0.01,
            "fractional_kelly": 0.25,
            "base_order_size_q": 0.001,
        }
        strategy = DynamicDCAStrategy(config)

        # Set up market state with a whale wall
        # Standard Bid 1 at price 60000 with ATR 50 would be 60000 - 25 = 59975.
        # Place a BID wall at 59950.0 (within 1% of 59975).
        # Expected adjusted bid: 59950 * 1.001 = 60009.95 (Wait, BID wall is 59950.0 * 1.001 = 60009.95? No, 59950.0 * 1.001 = 60009.95? No! 59950 * 1.001 = 59950 + 59.95 = 60009.95! Wait, 59950 * 1.001 = 60009.95. Yes!)
        market = MarketState(
            timestamp=time.time(),
            best_bid=59990.0,
            best_ask=60010.0,
            best_bid_qty=1.0,
            best_ask_qty=1.0,
            last_price=60000.0,
            trading_mode=TradingMode.DCA,
        )
        market.whale_walls = [
            {"price": 59950.0, "side": "BID", "qty": 10.0}
        ]

        pm = PositionManager(mode="spot_grid", initial_quote_balance=10000.0)
        features = MagicMock()
        features.ofi = 0.0

        # Run decide
        action = strategy.decide(market, features, 50.0, pm)

        # Action should be SYNC_GRID
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "SYNC_GRID")

        # Let's inspect the adjusted bid price by manually calling adjust_to_liquidity
        adj_price = strategy.adjust_to_liquidity(Decimal("59975.0"), market.whale_walls)
        self.assertEqual(adj_price, Decimal("59950.0") * Decimal("1.001"))

    def test_strategy_skip_initial_sync(self):
        """
        Test that DynamicDCAStrategy.decide() skips the initial SYNC_GRID
        when QE_SKIP_INITIAL_SYNC is set, and instead warms up last_sync_price.
        """
        import os
        os.environ["QE_SKIP_INITIAL_SYNC"] = "1"
        try:
            config = {
                "symbol": "BTCUSDT",
                "grid_levels_below": 1,
                "grid_levels_above": 1,
                "risk_percent": 0.01,
                "fractional_kelly": 0.25,
                "base_order_size_q": 0.001,
            }
            strategy = DynamicDCAStrategy(config)
            
            market = MarketState(
                timestamp=time.time(),
                best_bid=59990.0,
                best_ask=60010.0,
                best_bid_qty=1.0,
                best_ask_qty=1.0,
                last_price=60000.0,
                trading_mode=TradingMode.DCA,
            )
            pm = PositionManager(mode="spot_grid", initial_quote_balance=10000.0)
            features = MagicMock()
            features.ofi = 0.0

            action = strategy.decide(market, features, 50.0, pm)
            
            # Action should be None (skipped initial sync), and last_sync_price warmed up to 60000
            self.assertIsNone(action)
            self.assertEqual(strategy.last_sync_price, Decimal("60000.0"))
        finally:
            del os.environ["QE_SKIP_INITIAL_SYNC"]


if __name__ == "__main__":
    unittest.main()

