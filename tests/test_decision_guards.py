import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from decimal import Decimal
from quantum_edge_core.ai_scalper_bot.run_bot import BotEngine
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import BotState

class StopLoopException(Exception):
    pass

@pytest.fixture
def mock_bot_dependencies():
    with patch(
        "quantum_edge_core.ai_scalper_bot.run_bot.ZmqSubStream"
    ) as mock_stream, patch(
        "quantum_edge_core.ai_scalper_bot.run_bot.SupervisorReporter"
    ) as mock_reporter, patch(
        "quantum_edge_core.ai_scalper_bot.run_bot.QuestDbTelemetry"
    ) as mock_quest, patch(
        "zmq.Context"
    ) as mock_zmq_ctx:

        mock_socket = MagicMock()
        mock_zmq_ctx.return_value.socket.return_value = mock_socket

        yield {
            "stream": mock_stream,
            "reporter": mock_reporter,
            "quest": mock_quest,
            "zmq_ctx": mock_zmq_ctx,
            "socket": mock_socket,
        }

def test_decision_guards_float_price(mock_bot_dependencies):
    async def run_test():
        bot = BotEngine()
        bot.warm_up = AsyncMock()
        bot.features.update = MagicMock(return_value=MagicMock(ofi=0.1))
        bot.volatility.update = MagicMock(return_value=1.5)
        bot.strategy.decide = MagicMock(return_value=None)
        bot.reporter.send_initial_state = AsyncMock()
        bot.reporter.send_telemetry = MagicMock(side_effect=StopLoopException("stop"))
        
        with patch("quantum_edge_core.market_data.tsdb.ilp_writer.get_ilp_writer") as mock_ilp, \
             patch("quantum_edge_core.ai_scalper_bot.run_bot.logger") as mock_logger:
            
            # 1. Normal spread <= 0.5% (no warning, normal evaluating)
            tick_data = {
                "type": "trade",
                "price": 100.0,
                "quantity": 1.0,
                "timestamp": 1718569800000.0,
                "m": False,
                "bids": [[99.8, 1.0]],
                "asks": [[100.2, 1.0]],
                "whale_walls": []
            }
            bot.market_stream.get_latest_tick = MagicMock(return_value=tick_data)
            
            bot.last_eval_log = 0.0
            bot.running = True
            try:
                await bot.run()
            except StopLoopException:
                pass
            
            warning_calls = [c[0][0] for c in mock_logger.warning.call_args_list if c[0]]
            assert not any("Spread warning" in call for call in warning_calls)

            # 2. spread > 0.5% (warning logged)
            mock_logger.reset_mock()
            tick_data = {
                "type": "trade",
                "price": 100.0,
                "quantity": 1.0,
                "timestamp": 1718569800000.0,
                "m": False,
                "bids": [[99.6, 1.0]],
                "asks": [[100.3, 1.0]],
                "whale_walls": []
            }
            bot.market_stream.get_latest_tick = MagicMock(return_value=tick_data)
            
            bot.last_eval_log = 0.0
            bot.running = True
            try:
                await bot.run()
            except StopLoopException:
                pass
            
            warning_calls = [c[0][0] for c in mock_logger.warning.call_args_list if c[0]]
            print("FLOAT Warning calls:", warning_calls)
            assert any("Spread warning: spread 0.7000 exceeds 0.5% threshold" in call for call in warning_calls)

            # 3. spread < 0 (Spread inverted)
            mock_logger.reset_mock()
            tick_data = {
                "type": "trade",
                "price": 100.0,
                "quantity": 1.0,
                "timestamp": 1718569800000.0,
                "m": False,
                "bids": [[100.5, 1.0]],
                "asks": [[99.5, 1.0]],
                "whale_walls": []
            }
            bot.market_stream.get_latest_tick = MagicMock(return_value=tick_data)
            
            bot.last_eval_log = 0.0
            bot.running = True
            try:
                await bot.run()
            except StopLoopException:
                pass
            
            info_calls = [c[0][0] for c in mock_logger.info.call_args_list if c[0]]
            print("FLOAT Info calls for inverted:", info_calls)
            assert any("Reason=Spread inverted" in call for call in info_calls)

            # 4. spread > limit_1pct (Spread anomalous)
            mock_logger.reset_mock()
            tick_data = {
                "type": "trade",
                "price": 100.0,
                "quantity": 1.0,
                "timestamp": 1718569800000.0,
                "m": False,
                "bids": [[99.0, 1.0]],
                "asks": [[101.5, 1.0]],
                "whale_walls": []
            }
            bot.market_stream.get_latest_tick = MagicMock(return_value=tick_data)
            
            bot.last_eval_log = 0.0
            bot.running = True
            try:
                await bot.run()
            except StopLoopException:
                pass
            
            info_calls = [c[0][0] for c in mock_logger.info.call_args_list if c[0]]
            assert any("Reason=Spread anomalous: 2.50" in call for call in info_calls)

            # 5. last_price <= 0.0
            mock_logger.reset_mock()
            tick_data = {
                "type": "trade",
                "price": 10.0,
                "quantity": 1.0,
                "timestamp": 1718569800000.0,
                "m": False,
                "bids": [[9.9, 1.0]],
                "asks": [[10.1, 1.0]],
                "whale_walls": []
            }
            bot.market_stream.get_latest_tick = MagicMock(return_value=tick_data)
            
            original_update = bot.cache.update
            def mock_update(tick_dict):
                original_update(tick_dict)
                bot.cache._current_state.last_price = 0.0
            bot.cache.update = mock_update
            
            bot.last_eval_log = 0.0
            bot.running = True
            try:
                await bot.run()
            except StopLoopException:
                pass
            
            info_calls = [c[0][0] for c in mock_logger.info.call_args_list if c[0]]
            assert any("Reason=Waiting for Market Data (Price <= 0)" in call for call in info_calls)

    asyncio.run(run_test())

def test_decision_guards_decimal_price(mock_bot_dependencies):
    async def run_test():
        bot = BotEngine()
        bot.warm_up = AsyncMock()
        bot.features.update = MagicMock(return_value=MagicMock(ofi=0.1))
        bot.volatility.update = MagicMock(return_value=1.5)
        bot.strategy.decide = MagicMock(return_value=None)
        bot.reporter.send_initial_state = AsyncMock()
        bot.reporter.send_telemetry = AsyncMock(side_effect=StopLoopException("stop"))
        
        with patch("quantum_edge_core.market_data.tsdb.ilp_writer.get_ilp_writer") as mock_ilp, \
             patch("quantum_edge_core.ai_scalper_bot.run_bot.logger") as mock_logger:
            
            original_update = bot.cache.update
            def mock_update(tick_dict):
                original_update(tick_dict)
                bot.cache._current_state.last_price = Decimal(str(tick_dict["p"]))
                bot.cache._current_state.best_bid = Decimal(str(tick_dict["b"]))
                bot.cache._current_state.best_ask = Decimal(str(tick_dict["a"]))
            bot.cache.update = mock_update

            # 1. Normal spread <= 0.5% (no warning, normal evaluating)
            tick_data = {
                "type": "trade",
                "price": 100.0,
                "quantity": 1.0,
                "timestamp": 1718569800000.0,
                "m": False,
                "bids": [[99.8, 1.0]],
                "asks": [[100.2, 1.0]],
                "whale_walls": []
            }
            bot.market_stream.get_latest_tick = MagicMock(return_value=tick_data)
            
            bot.last_eval_log = 0.0
            bot.running = True
            try:
                await bot.run()
            except StopLoopException:
                pass
            
            warning_calls = [c[0][0] for c in mock_logger.warning.call_args_list if c[0]]
            assert not any("Spread warning" in call for call in warning_calls)

            # 2. spread > 0.5% (warning logged)
            mock_logger.reset_mock()
            tick_data = {
                "type": "trade",
                "price": 100.0,
                "quantity": 1.0,
                "timestamp": 1718569800000.0,
                "m": False,
                "bids": [[99.6, 1.0]],
                "asks": [[100.3, 1.0]],
                "whale_walls": []
            }
            bot.market_stream.get_latest_tick = MagicMock(return_value=tick_data)
            
            bot.last_eval_log = 0.0
            bot.running = True
            try:
                await bot.run()
            except StopLoopException:
                pass
            
            warning_calls = [c[0][0] for c in mock_logger.warning.call_args_list if c[0]]
            print("DECIMAL Warning calls:", warning_calls)
            assert any("Spread warning: spread 0.7000 exceeds 0.5% threshold" in call for call in warning_calls)

    asyncio.run(run_test())
