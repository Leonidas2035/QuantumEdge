import asyncio
import logging
from bot.core.config import Config
from bot.core.orderbook import OrderBookCache
from bot.features.ofi import OfiCalculator
from bot.features.vpin import VpinCalculator
from bot.execution.strategy_core import AdaptiveGridStrategy
from bot.infrastructure.zmq_adapter import ZmqSubStream
from bot.infrastructure.reporter import SupervisorReporter
from bot.infrastructure.exchange import BingXExecutionGateway

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("QuantumEdgeBot")

class BotEngine:
    def __init__(self):
        self.config = Config()
        self.zmq_stream = ZmqSubStream(port=self.config.market_data_port)
        self.cache = OrderBookCache()
        self.ofi = OfiCalculator()
        self.vpin = VpinCalculator()
        self.strategy = AdaptiveGridStrategy()
        # ПІДКЛЮЧАЄМО BINGX ЗАМІСТЬ BINANCE
        self.gateway = BingXExecutionGateway(self.config)
        self.reporter = SupervisorReporter(port=self.config.supervisor_port)
        logger.info(f">>> Bot Initialized. Target: {self.config.symbol} on BingX (DEMO)")

    async def run(self):
        last_report_time = 0
        while True:
            try:
                tick = await self.zmq_stream.get_latest_tick()
                if not tick: continue

                self.cache.update(tick)
                if not self.cache.snapshot: continue
                
                ofi_val = self.ofi.update(self.cache.snapshot)
                vpin_val = self.vpin.update(tick)
                action = self.strategy.decide(self.cache.snapshot, ofi_val, vpin_val)
                
                if action:
                    logger.info(f"!!! SIGNAL: {action.action_type}")
                    await self.gateway.execute(action)
                
                import time
                if time.time() - last_report_time > 5.0:
                    print(f"[STATUS] Price: {tick['p']} | OFI: {ofi_val:.4f}")
                    last_report_time = time.time()
            except Exception as e:
                logger.error(f"Loop Error: {e}")
                await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(BotEngine().run())
