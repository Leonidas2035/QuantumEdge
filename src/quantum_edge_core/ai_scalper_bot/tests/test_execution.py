import time

import pytest
from decimal import Decimal

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState, TradingMode
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureVector
from quantum_edge_core.ai_scalper_bot.bot.execution.volatility import OnlineVolatility
from quantum_edge_core.ai_scalper_bot.bot.execution.position import PositionManager
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import (
    DynamicDCAStrategy,
    BotState,
    TradeAction,
)


# --- 1. Volatility Tests ---
def test_volatility_calculation():
    vol = OnlineVolatility(alpha=0.5)

    val = vol.update(100.0)
    assert val == 0.0

    val = vol.update(102.0)
    assert val == 2.0

    val = vol.update(101.0)
    assert val == 1.5

    val = vol.update(111.0)
    assert val == 5.75


# --- 2. Position Manager Tests ---
def test_position_weighted_avg():
    pm = PositionManager()

    pm.simulate_fill(Decimal("100.0"), Decimal("1.0"), "BUY")
    assert pm.avg_price == Decimal("100.0")
    assert pm.total_qty == Decimal("1.0")

    pm.simulate_fill(Decimal("90.0"), Decimal("1.0"), "BUY")
    assert pm.avg_price == Decimal("95.0")
    assert pm.total_qty == Decimal("2.0")

    dd = pm.get_drawdown_pct(Decimal("90.0"))
    assert float(dd) == pytest.approx(0.0526, abs=0.0001)


def test_position_sell_reduction():
    pm = PositionManager()
    pm.simulate_fill(Decimal("100.0"), Decimal("2.0"), "BUY")

    pm.simulate_fill(Decimal("110.0"), Decimal("1.0"), "SELL")
    assert pm.avg_price == Decimal("100.0")
    assert pm.total_qty == Decimal("1.0")


# --- 3. Strategy Core Tests ---
def create_state(price):
    return MarketState(
        timestamp=1000,
        last_price=price,
        best_bid=price,
        best_ask=price,
        best_bid_qty=1,
        best_ask_qty=1,
        trading_mode=TradingMode.DCA,
        market_regime="ranging",
        grid_bias="neutral",
    )


def create_features(ofi=0, vpin=0):
    return FeatureVector(timestamp=1000, ofi=ofi, vpin=vpin)


def test_strategy_dynamic_dca_initial_sync():
    config = {
        "risk_percent": 0.01,
        "fractional_kelly": 0.25,
    }
    strat = DynamicDCAStrategy(config)
    pm = PositionManager()

    action = strat.decide(create_state(100.0), create_features(), 1.0, pm)
    assert action is not None
    assert action.action_type == "SYNC_GRID"


def test_strategy_dynamic_dca_flash_crash():
    config = {
        "risk_percent": 0.01,
        "fractional_kelly": 0.25,
    }
    strat = DynamicDCAStrategy(config)
    pm = PositionManager()

    # Add a price 10 seconds ago
    strat.price_buffer.append((time.time() - 10, 106.0))

    # Sudden drop to 100.0 (> 0.5% per sec threshold)
    action = strat.decide(create_state(100.0), create_features(), 1.0, pm)
    assert action is not None
    assert action.action_type == "CANCEL_ALL"


def test_adjust_to_liquidity():
    strat = DynamicDCAStrategy({})

    # Mock walls from microstructure
    walls = [
        {"price": 69000.0, "qty": 50.0, "side": "BID"},
        {"price": 71000.0, "qty": 40.0, "side": "ASK"},
    ]

    # Target price near a bid wall
    target_buy = Decimal("69050.0")
    adjusted_buy = strat.adjust_to_liquidity(target_buy, walls)
    # Front-run bid by 0.1% = 69000 * 1.001 = 69069
    assert adjusted_buy == Decimal("69069.0")

    # Target price near an ask wall
    target_sell = Decimal("70950.0")
    adjusted_sell = strat.adjust_to_liquidity(target_sell, walls)
    # Front-run ask by 0.1% = 71000 * 0.999 = 70929
    assert adjusted_sell == Decimal("70929.0")


def test_adjust_to_liquidity_round_number():
    config = {"front_run_pct": 0.001}
    strat = DynamicDCAStrategy(config)

    # Mock wall at a round number
    walls = [
        {"price": 70000.0, "qty": 100.0, "side": "BID"},
    ]

    # Target price near the bid wall (within 1%)
    target_buy = Decimal("70050.0")
    adjusted_buy = strat.adjust_to_liquidity(target_buy, walls)

    # Front-run bid by 0.1% = 70000 * 1.001 = 70070.0
    assert adjusted_buy == Decimal("70070.0")


def test_regime_change_sync_grid():
    config = {
        "risk_percent": 0.01,
        "fractional_kelly": 0.25,
    }
    strat = DynamicDCAStrategy(config)
    pm = PositionManager()

    state1 = create_state(100.0)
    state1.market_regime = "ranging"
    state1.grid_bias = "neutral"

    # Initial sync
    action1 = strat.decide(state1, create_features(), 1.0, pm)
    assert action1 is not None
    assert action1.action_type == "SYNC_GRID"

    # Same state -> None
    action2 = strat.decide(state1, create_features(), 1.0, pm)
    assert action2 is None

    # Change regime
    state2 = create_state(100.0)
    state2.market_regime = "trending"
    state2.grid_bias = "bullish"

    action3 = strat.decide(state2, create_features(), 1.0, pm)
    assert action3 is not None
    assert action3.action_type == "SYNC_GRID"


def test_paper_trader_order_matching_and_counter():
    from quantum_edge_core.ai_scalper_bot.bot.infrastructure.paper_trader import PaperTrader
    from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction

    # 1. Initialize PaperTrader
    pt = PaperTrader()
    assert len(pt.open_orders) == 0

    # 2. Place a BUY limit order at 95.0
    buy_action = TradeAction(action_type="BUY", price=Decimal("95.0"), qty=Decimal("0.1"), reason="GRID_BUY_L1")
    res = pt._place_limit_order(buy_action)
    assert res is True
    assert len(pt.open_orders) == 1
    assert pt.open_orders[0]["price"] == 95.0
    assert pt.open_orders[0]["status"] == "OPEN"

    # 3. Simulate tick at 96.0 (should NOT match BUY at 95.0)
    fills = pt.on_tick(Decimal("96.0"))
    assert len(fills) == 0
    assert len(pt.open_orders) == 1

    # 4. Simulate tick at 94.5 (should match BUY at 95.0)
    fills = pt.on_tick(Decimal("94.5"))
    assert len(fills) == 1
    assert fills[0]["status"] == "FILLED"
    assert len(pt.open_orders) == 0

    # 5. Execute ORDER_FILLED event for the filled BUY (should place a counter SELL order)
    # Target profit is 1.2% (1% profit + 0.2% commission buffer) -> 95.0 * 1.012 = 96.14
    fill_action = TradeAction(
        action_type="ORDER_FILLED",
        price=Decimal("95.0"),
        qty=Decimal("0.1"),
        reason="side=BUY|spacing_pct=0.012",
    )
    import asyncio
    res_counter = asyncio.run(pt.execute(fill_action))
    assert res_counter is True

    # Check that the counter order is in open_orders
    assert len(pt.open_orders) == 1
    counter_order = pt.open_orders[0]
    assert counter_order["side"] == "SELL"
    assert counter_order["price"] == pytest.approx(96.14, abs=0.01)
    assert counter_order["qty"] == 0.1
    assert counter_order["status"] == "OPEN"

    # 6. Simulate tick at 96.5 (should match counter SELL at 96.14)
    fills_sell = pt.on_tick(Decimal("96.5"))
    assert len(fills_sell) == 1
    assert fills_sell[0]["status"] == "FILLED"
    assert len(pt.open_orders) == 0

