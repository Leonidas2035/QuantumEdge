import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import structlog

from quantum_edge_core.bot.service import TradingBot
from quantum_edge_core.events import MarketTrade

# Mock logger to avoid clutter
structlog.configure(processors=[structlog.processors.JSONRenderer()])


async def test_bot_integration():
    print("Initializing TradingBot Integration Test...")

    # Mock GeminiClient
    with patch("quantum_edge_core.bot.service.GeminiClient") as MockGemini:
        mock_ai = MockGemini.return_value
        # Mock safe_analyze_risk to return a dummy string
        mock_ai.safe_analyze_risk = AsyncMock(return_value="AI says HOLD")

        bot = TradingBot()
        # Override subscriber connect to be no-op (we will inject events manually or mock socket)
        # Actually proper integration needs ZMQ.
        # Let's mock ZMQ to avoid needing a running Hub for this isolated test
        bot.subscriber = MagicMock()
        bot.subscriber.connect = MagicMock()

        print("Injecting Mock Market Data...")
        # Simulate 105 events to trigger one AI check (at 100) and some logs
        for i in range(105):
            event = MarketTrade(
                symbol="BTCUSDT",
                price=50000.0 + i,
                quantity=0.1,
                side="buy",
                timestamp=1234567890,
            )
            await bot.on_market_data(event)

        print("Verifying Logic...")
        assert bot.event_count == 105
        # AI Check happens at % 100 == 0. So at 100.
        mock_ai.safe_analyze_risk.assert_called_once()
        print("[PASS] AI Supervisor was consulted exactly once.")

        # Check logs/logic (implicit in assert_called)

    print("Test Complete.")


if __name__ == "__main__":
    asyncio.run(test_bot_integration())
