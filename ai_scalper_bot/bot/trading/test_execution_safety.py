import asyncio

from bot.trading.paper_trader import PaperTrader
from bot.trading.executor import BinanceDemoExecutor
from bot.trading.trade_stats import TradeStats
from bot.engine.decision_engine import DecisionEngine
from bot.engine.decision_types import Decision, DecisionAction, DecisionDirection


def test_paper_trader_bracket_close():
    trader = PaperTrader()
    decision = type("D", (), {"action": "buy", "size": 1.0, "order_type": "market", "tp_price": 110.0, "sl_price": 90.0})
    asyncio.run(trader.process(decision, 100.0, 1))
    assert trader.position == 1.0
    trader.check_brackets(111.0, 2)
    assert trader.position == 0.0
    assert trader.entry_price is None


def test_demo_partial_fill_helpers():
    ex = BinanceDemoExecutor(symbol="BTCUSDT")
    ex.initialize = lambda: True  # skip real network init
    ex._apply_fill("BUY", 0.5, 100.0, {"status": "PARTIALLY_FILLED", "avgPrice": 100.0})
    assert abs(ex.position - 0.5) < 1e-9
    assert ex.entry_price == 100.0
    ex.set_bracket("BUY", 120.0, 95.0)
    assert ex._bracket["tp"] == 120.0
    ex._reduce_position(0.3)
    assert abs(ex.position - 0.2) < 1e-9


def test_trade_stats_loss_streak_blocks_entry():
    eng = DecisionEngine()
    stats = TradeStats()
    eng.trade_stats["BTCUSDT"] = stats
    # record 3 losses
    for _ in range(3):
        stats.record(-1.0)
    dec = eng.decide(
        symbol="BTCUSDT",
        ensemble=type("E", (), {"components": {1: type("S", (), {"p_up": 0.6, "p_down": 0.4, "edge": 0.1})()}, "meta_edge": 0.1}),
        features=[0.0] * 10,
        position=0,
        approved=True,
        warmup_ready=True,
    )
    assert dec.action == DecisionAction.NO_TRADE
