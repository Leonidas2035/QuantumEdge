"""
src/quantum_edge_core/market_data/feeds/mock_feed.py

Mock Live Feed for bypassing geo-blocking (HTTP 451).
Simulates high-frequency market data.
"""

import asyncio
import time
import random
import logging
from typing import List

from quantum_edge_core.market_data.feeds.base import BaseFeed
from quantum_edge_core.market_data.bus.event_bus import EventBus
from quantum_edge_core.market_data.config import HubConfig
from quantum_edge_core.events import MarketTrade

class MockLiveFeed(BaseFeed):
    """
    Simulates a live exchange feed using random walk.
    """

    def __init__(self, config: HubConfig, bus: EventBus):
        super().__init__(config, bus)
        self.symbols = ["BTCUSDT"]  # specific mock symbol
        self.price = 50000.0
        self.tick_count = 0

    def connect(self):
        """Mock connection."""
        logging.info("MockFeed: Connecting to internal generator...")
        return True

    def subscribe(self, symbols: List[str]):
        """Mock subscription."""
        logging.info(f"MockFeed: Subscribed to {symbols}")

    async def _run(self) -> None:
        """
        Main loop generating synthetic data.
        """
        self.connect()
        self.subscribe(self.symbols)
        
        logging.info("MockFeed: Generator started. Speed=100TPS")
        
        while not self._stop_event.is_set():
            # 1. Random Walk
            change = (random.random() - 0.5) * 5 # Smaller, more frequent changes
            self.price += change
            self.tick_count += 1
            
            # Simulate Whale (1% chance)
            is_whale = random.random() < 0.01
            qty = round(random.uniform(0.001, 2.0), 4)
            if is_whale:
                 qty = round(random.uniform(20.0, 50.0), 2) # Large Block
                 
            # 2. Create Event
            event = MarketTrade(
                symbol="BTCUSDT",
                price=round(self.price, 2),
                quantity=qty,
                side="buy" if random.random() > 0.5 else "sell",
                timestamp=time.time()
            )
            
            # 3. Publish to Bus (The "callback")
            # Hub expects events on the bus to dispatch to ZMQ/Orderbook etc.
            await self.bus.publish(event)
            
            # 4. Status Log
            if self.tick_count % 500 == 0:
                logging.info(f"Mock Feed Running: {self.tick_count} ticks. Price={self.price:.2f}")
            
            # 5. Sleep (100 Hz)
            await asyncio.sleep(0.01)
