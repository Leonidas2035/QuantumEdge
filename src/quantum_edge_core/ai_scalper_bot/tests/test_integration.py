import pytest
from unittest.mock import MagicMock, AsyncMock
import os

# Set Env for tests
os.environ["BINANCE_API_KEY"] = "test"
os.environ["BINANCE_SECRET_KEY"] = "test"

from quantum_edge_core.ai_scalper_bot.bot.infrastructure.exchange import (
    BinanceExecutionGateway,
)
from quantum_edge_core.ai_scalper_bot.bot.infrastructure.reporter import (
    SupervisorReporter,
)
from quantum_edge_core.ai_scalper_bot.run_bot import BotEngine
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction


@pytest.mark.asyncio
async def test_gateway_signature():
    """Test HMAC signature generation."""
    class MockConfig:
        symbol = "BTCUSDT"
        binance_api_key = "test"
        binance_secret = "test"
        use_testnet = True
    gw = BinanceExecutionGateway(MockConfig())
    gw.secret_key = "secret"
    params = {"symbol": "BTCUSDT", "param": "value"}
    sig = gw._sign(params)
    assert len(sig) == 64  # SHA256 hex digest length


@pytest.mark.asyncio
async def test_gateway_execute_dry_run():
    class MockConfig:
        symbol = "BTCUSDT"
        binance_api_key = "test"
        binance_secret = "test"
        use_testnet = True
    gw = BinanceExecutionGateway(MockConfig())
    action = TradeAction("BUY", 50000, 0.1, "Test")
    res = await gw.execute(action)
    assert res is True


@pytest.mark.asyncio
async def test_gateway_execute_network_mock():
    """Verify network call structure in non-dry-run."""
    class MockConfig:
        symbol = "BTCUSDT"
        binance_api_key = "test"
        binance_secret = "test"
        use_testnet = True
    gw = BinanceExecutionGateway(MockConfig())
    gw.api_key = "key"
    gw.secret_key = "secret"

    # Needs aiohttp patch or mock injection
    # We can inject _get_session
    mock_session = AsyncMock()  # Initialize here

    # Mocking aiohttp context manager structure
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {"orderId": 123}

    # The session.post() is a method that returns an async context manager.
    # It is NOT a coroutine itself, so we don't await session.post().
    # We await the __aenter__ of the returned CM.

    post_context = MagicMock()
    post_context.__aenter__.return_value = mock_response
    post_context.__aexit__.return_value = None

    # post() should return the context manager
    mock_session.post = MagicMock(return_value=post_context)

    gw._get_session = AsyncMock(return_value=mock_session)

    action = TradeAction("BUY", 50000, 0.1, "Test")
    res = await gw.execute(action)

    assert res is True
    # Verify post called
    mock_session.post.assert_called_once()
    args, kwargs = mock_session.post.call_args
    assert "signature" in kwargs["params"]
    assert kwargs["params"]["side"] == "BUY"


@pytest.mark.asyncio
async def test_reporter_heartbeat():
    reporter = SupervisorReporter("tcp://*:5557")

    # Mock socket
    reporter.socket = AsyncMock()

    from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import BotState

    await reporter.send_heartbeat(BotState.IDLE, 100.0, 0.5)

    reporter.socket.send_multipart.assert_called_once()
    call_args = reporter.socket.send_string.call_args[0][0]
    assert "heartbeat" in call_args
    assert "IDLE" in call_args


@pytest.mark.asyncio
async def test_bot_engine_loop_flow():
    """Test one iteration of the main loop logic."""
    engine = BotEngine()

    # Mock I/O
    engine.market_stream = MagicMock()
    # Feed one tick then None to verify logic doesn't crash on empty
    tick = {"p": "50000", "q": "1.0", "T": 1234567890, "m": False}
    engine.market_stream.get_latest_tick.return_value = tick

    engine.reporter.send_heartbeat = AsyncMock()
    engine.gateway.execute = AsyncMock()

    # Force Strategy to emit ACTION
    engine.strategy.decide = MagicMock(
        return_value=TradeAction("BUY", 49999, 0.1, "Test")
    )

    # Execute one 'tick' logic manually (avoiding infinite while loop for test)
    # We basically replicate steps inside the loop for integration check

    # Step 1: Update
    engine.cache.update(tick)

    # Manual Tick Object creation as per run_bot.py
    from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketTick

    tick_obj = MarketTick(50000.0, 1.0, 1234567890.0, False)

    state = engine.cache._current_state
    feat = engine.features.update(tick_obj, state)
    atr = engine.volatility.update(50000.0)

    action = engine.strategy.decide(state, feat, atr, engine.position)

    assert action is not None
    assert action.action_type == "BUY"

    # Step 2: Execute
    await engine.gateway.execute(action)
    engine.gateway.execute.assert_called_once()

    # Step 3: PnL / Pos Sim
    engine.position.simulate_fill(action.price, action.qty, action.action_type)
    assert engine.position.total_qty == 0.1
