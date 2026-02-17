import asyncio
import time

from bot.market_data.data_manager import DataManager


def test_data_manager_noop_when_tsdb_disabled():
    dm = DataManager()

    async def _push():
        await dm.save_trade(
            {"p": 100.0, "q": 0.01, "s": "BTCUSDT", "T": int(time.time() * 1000)}
        )
        await dm.save_orderbook(
            {"s": "BTCUSDT", "bids": [["100.0", "1.0"]], "asks": [["101.0", "1.0"]]}
        )

    asyncio.run(_push())
    dm.close()
