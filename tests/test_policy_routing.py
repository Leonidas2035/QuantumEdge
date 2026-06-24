import pytest
import asyncio
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_bot_dependencies():
    with patch("quantum_edge_core.ai_scalper_bot.run_bot.ZmqSubStream") as mock_stream, \
         patch("quantum_edge_core.ai_scalper_bot.run_bot.SupervisorReporter") as mock_reporter, \
         patch("quantum_edge_core.ai_scalper_bot.run_bot.QuestDbTelemetry") as mock_quest, \
         patch("zmq.Context") as mock_zmq_ctx:
        
        mock_socket = MagicMock()
        mock_zmq_ctx.return_value.socket.return_value = mock_socket
        yield {"socket": mock_socket}

@pytest.mark.asyncio
async def test_policy_routing(mock_bot_dependencies):
    from quantum_edge_core.ai_scalper_bot.run_bot import BotEngine
    from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState

    bot = BotEngine()
    # Ensure current_state is instantiated
    assert bot.cache._current_state is not None
    
    # 1. Send command before any tick
    await bot.handle_supervisor_command({
        "action": "policy",
        "payload": {
            "buy_zone_max": 63000.0,
            "risk_multiplier": 0.5,
            "trading_mode": "SCALP"
        }
    })
    
    # Check if applied
    ms = bot.cache._current_state
    assert ms.buy_zone_max == 63000.0, f"Expected 63000.0, got {ms.buy_zone_max}"
    assert ms.risk_multiplier == 0.5, f"Expected 0.5, got {ms.risk_multiplier}"
    
    # 2. Simulate tick
    bot.cache.update({
        "p": 62500.0,
        "q": 1.0,
        "T": 1718569800000.0,
        "m": False
    })
    
    # Check if preserved
    ms = bot.cache._current_state
    assert ms.buy_zone_max == 63000.0, f"Tick wiped buy_zone_max: {ms.buy_zone_max}"
    assert ms.risk_multiplier == 0.5, f"Tick wiped risk_multiplier: {ms.risk_multiplier}"
    
    # 3. Flat command
    await bot.handle_supervisor_command({
        "buy_zone_max": 64000.0,
        "risk_multiplier": 0.8
    })
    
    ms = bot.cache._current_state
    assert ms.buy_zone_max == 64000.0, f"Flat command failed: {ms.buy_zone_max}"
    assert ms.risk_multiplier == 0.8, f"Flat command failed: {ms.risk_multiplier}"
