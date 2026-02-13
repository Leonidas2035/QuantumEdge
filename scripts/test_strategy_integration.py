import asyncio
import structlog
from unittest.mock import MagicMock, patch

from quantum_edge_core.bot.service import TradingBot
from quantum_edge_core.events import MarketTrade

# Mock logger
structlog.configure(processors=[structlog.processors.JSONRenderer()])


async def test_strategy_integration():
    print("Initializing Strategy Integration Test...")

    with patch("quantum_edge_core.bot.service.GeminiClient") as MockGemini:
        bot = TradingBot()
        bot.subscriber = MagicMock()  # No ZMQ needed

        # Strategy: Mean Reversion (Window=20, Threshold=0.1%)
        # To trigger SELL: Price > MA * 1.001

        print("Feeding flat prices to establish MA...")
        base_price = 100.0
        for _ in range(20):
            await bot.on_market_data(
                MarketTrade(symbol="BTCUSDT", price=base_price, quantity=1.0, side="buy", timestamp=12345)
            )

        # MA should be 100.0

        print("Feeding spike price...")
        # Spike to 100.2 (0.2% jump > 0.1% threshold)
        spike_price = 100.2

        # Capture logs
        with patch.object(bot.logger, "info") as mock_info:
            await bot.on_market_data(
                MarketTrade(symbol="BTCUSDT", price=spike_price, quantity=1.0, side="buy", timestamp=12346)
            )

            # Assert signal
            # We expect "EXECUTING SIGNAL" log
            calls = [call for call in mock_info.call_args_list if "EXECUTING SIGNAL" in str(call)]
            if calls:
                print(f"[PASS] Signal Executed: {calls[0]}")
            else:
                print("[FAIL] No signal executed.")
                exit(1)

    print("Test Complete.")


if __name__ == "__main__":
    asyncio.run(test_strategy_integration())
