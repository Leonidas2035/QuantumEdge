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


@pytest.mark.asyncio
async def test_position_sell_reduction():
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
