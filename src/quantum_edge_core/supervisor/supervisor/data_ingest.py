"""
ZMQ Data Ingestion Layer for SupervisorAgent.
Handles subscriptions, message parsing, and state management (Partial Patches).
"""

from __future__ import annotations

import json
import logging
import zmq
import zmq.asyncio
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class DataStore:
    """
    In-memory state for the Supervisor.
    Maintains the latest known state of accounts, positions, and market metrics.
    """
    # Spot Balances: Asset -> Data
    # e.g., "BTC": {"free": 0.1, "locked": 0.0}
    spot_balances: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    # Futures Positions: Symbol -> Info
    # e.g., "BTCUSDT": {"positionAmt": 0.5, "unrealizedProfit": 100.0, ...}
    futures_positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Market Metrics: Symbol -> Info
    # e.g., "BTCUSDT": {"ofi": 5.2, "vpin": 0.3}
    market_metrics: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Metadata for versioning/staleness
    _versions: Dict[str, int] = field(default_factory=dict)  # topic -> version

    def update_spot_balance(self, asset: str, free: Optional[float] = None, locked: Optional[float] = None):
        """Update spot balance for an asset."""
        if asset not in self.spot_balances:
            self.spot_balances[asset] = {"free": 0.0, "locked": 0.0}
        
        if free is not None:
            self.spot_balances[asset]["free"] = float(free)
        if locked is not None:
            self.spot_balances[asset]["locked"] = float(locked)

    def update_futures_position(self, symbol: str, data: Dict[str, Any]):
        """Update futures position data (Partial Patch)."""
        if symbol not in self.futures_positions:
            self.futures_positions[symbol] = {}
        
        # Merge dict
        self.futures_positions[symbol].update(data)
        
        # Type coercion for critical fields if present
        if "positionAmt" in data:
            self.futures_positions[symbol]["positionAmt"] = float(data["positionAmt"])
        if "entryPrice" in data:
            self.futures_positions[symbol]["entryPrice"] = float(data["entryPrice"])
        if "unrealizedProfit" in data:
            self.futures_positions[symbol]["unrealizedProfit"] = float(data["unrealizedProfit"])


class ZmqListener:
    """
    Async ZMQ Listener.
    Subscribes to market and account updates.
    """
    def __init__(self, zmq_context: Optional[zmq.asyncio.Context] = None, sub_address: str = "tcp://127.0.0.1:5555"):
        self.ctx = zmq_context or zmq.asyncio.Context()
        self.sub_address = sub_address
        self.socket: Optional[zmq.asyncio.Socket] = None
        self.running = False
        self.store = DataStore()
        
        # State tracking
        self.connected = False
        
    async def start(self):
        """Start listening."""
        self.socket = self.ctx.socket(zmq.SUB)
        self.socket.connect(self.sub_address)
        
        # Topics to subscribe to
        topics = ["market.enriched", "hub.account_snapshot", "hub.account_delta", "market.liquidation"]
        for t in topics:
            self.socket.setsockopt_string(zmq.SUBSCRIBE, t)
            
        self.running = True
        self.connected = True
        logger.info(f"ZmqListener started on {self.sub_address}")

    async def stop(self):
        """Stop listening."""
        self.running = False
        if self.socket:
            self.socket.close()
            self.connected = False

    async def get_message_nowait(self) -> Optional[Dict[str, Any]]:
        """
        Non-blocking read.
        Use this in your fast monitor loop.
        Returns parsed JSON or None.
        """
        if not self.socket:
            return None
            
        try:
            # Poll with 0 timeout
            if await self.socket.poll(timeout=0):
                topic, message = await self.socket.recv_multipart()
                topic_str = topic.decode("utf-8")
                payload = json.loads(message.decode("utf-8"))
                
                self._process_message(topic_str, payload)
                return {"topic": topic_str, "payload": payload}
        except zmq.ZMQError as e:
            logger.error(f"ZMQ Error: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON Parse Error: {e}")
            
        return None

    def _process_message(self, topic: str, payload: Dict[str, Any]):
        """Internal message processor/dispatcher."""
        current_ver = self.store._versions.get(topic, -1)
        msg_ver = payload.get("version", 0)
        
        # Simple version check - strict ordering
        # if msg_ver <= current_ver:
        #    return # Ignore old
        
        # For this prototype, we update version map but process anyway 
        # as reset might occur. In prod, handle resets carefully.
        self.store._versions[topic] = msg_ver

        data = payload.get("data", {})
        
        if topic.startswith("market.enriched"):
            symbol = data.get("symbol")
            if symbol:
                 if symbol not in self.store.market_metrics:
                     self.store.market_metrics[symbol] = {}
                 self.store.market_metrics[symbol].update(data)

        elif topic == "hub.account_snapshot":
            # Replace logic
            s_type = data.get("type")
            if s_type == "spot":
                for asset, info in data.get("balances", {}).items():
                    self.store.update_spot_balance(asset, info.get("free"), info.get("locked"))
            elif s_type == "futures":
                for pos in data.get("positions", []):
                    sym = pos.get("symbol")
                    if sym:
                        self.store.update_futures_position(sym, pos)

        elif topic == "hub.account_delta":
            # Patch logic
            s_type = data.get("type")
            if s_type == "spot":
                updates = data.get("updates", [])
                for u in updates:
                    self.store.update_spot_balance(u.get("asset"), u.get("free"), u.get("locked"))
            elif s_type == "futures":
                updates = data.get("updates", [])
                for u in updates:
                    sym = u.get("symbol")
                    if sym:
                        self.store.update_futures_position(sym, u)
