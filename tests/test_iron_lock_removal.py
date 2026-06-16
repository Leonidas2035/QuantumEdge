import pytest
from unittest.mock import MagicMock, patch


# Mock ZMQ and external systems during module load or test setup
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

        # Configure ZMQ socket mocks so that connect/subscribe don't fail
        mock_socket = MagicMock()
        mock_zmq_ctx.return_value.socket.return_value = mock_socket

        yield {
            "stream": mock_stream,
            "reporter": mock_reporter,
            "quest": mock_quest,
            "zmq_ctx": mock_zmq_ctx,
            "socket": mock_socket,
        }


def test_system_settings():
    """Test ZmqRegistry validation and SystemSettings structure."""
    from quantum_edge_core.config.settings import (
        SystemSettings,
        ZmqRegistry,
        get_settings,
    )

    # Check default ZMQ Registry ports
    registry = ZmqRegistry()
    assert registry.hub_pub_port == 5555
    assert registry.telemetry_port == 5557
    assert registry.policy_port == 5558
    assert registry.heartbeat_port == 8765
    assert registry.questdb_port == 9009

    # Check singleton settings
    settings = get_settings()
    assert settings.execution_mode.value == "paper"
    assert "BTCUSDT" in settings.symbols

    # Test validator with uppercase/mixed-case environment variable override
    settings_live = SystemSettings(execution_mode="LIVE")  # type: ignore[arg-type]
    assert settings_live.execution_mode.value == "live"

    # Test invalid port validation
    with pytest.raises(ValueError):
        ZmqRegistry(hub_pub_port=80)  # Port < 1024 should raise ValidationError


def test_iron_lock_removal_and_state_retention(mock_bot_dependencies):
    """Test that PAUSE_ENTRIES state is retained across tick updates."""
    from quantum_edge_core.ai_scalper_bot.run_bot import BotEngine
    from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import BotState

    # Instantiate BotEngine
    bot = BotEngine()

    # Initialize the cache state by sending an initial tick
    bot.cache.update(
        {
            "p": 60000.0,
            "q": 1.0,
            "T": 1718569800000.0,
            "m": False,
            "b": 59999.0,
            "a": 60001.0,
            "B": 5.0,
            "A": 5.0,
            "W": [],
        }
    )

    # Ensure cache has a current state initialized
    assert bot.cache._current_state is not None
    market_state = bot.cache._current_state

    # Initially: entries should NOT be paused, strategy should be active
    bot.strategy.state = BotState.RUNNING
    bot.gateway.status = "RUNNING"
    bot.gateway.entries_paused = False
    market_state.entries_paused = False

    # 1. Send PAUSE_ENTRIES command from Supervisor
    bot.handle_supervisor_command({"action": "PAUSE_ENTRIES"})

    # Verify that the states are updated
    assert bot.gateway.entries_paused is True
    assert bot.strategy.state == BotState.PAUSED
    assert market_state.entries_paused is True

    # 2. Simulate a new MarketTick update
    # In the old logic, any incoming tick would trigger the "Iron Lock" block
    # resetting strategy.state = RUNNING, gateway.status = RUNNING, entries_paused = False.
    # Now, the tick update should keep the paused state persistent.

    # We will simulate a depth or kline tick coming in.
    # We can invoke the cache update to simulate a new tick.
    norm_tick = {
        "p": 65000.0,
        "q": 1.5,
        "T": 1718569800000.0,
        "m": False,
        "b": 64999.0,
        "a": 65001.0,
        "B": 10.0,
        "A": 10.0,
        "W": [],
    }

    # Let's perform cache update & state retrieval
    bot.cache.update(norm_tick)
    updated_state = bot.cache._current_state

    # Check that after the update, the entries are STILL paused!
    assert updated_state.entries_paused is True
    assert bot.strategy.state == BotState.PAUSED
    assert bot.gateway.entries_paused is True

    # 3. Send RESUME_ENTRIES command
    bot.handle_supervisor_command({"action": "RESUME_ENTRIES"})

    assert bot.gateway.entries_paused is False
    assert bot.strategy.state == BotState.RUNNING
    assert updated_state.entries_paused is False
