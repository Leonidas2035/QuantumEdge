"""
Context Builder.
Orchestrates data accumulation and feature engineering for the LLM.
"""

from __future__ import annotations

import logging
from typing import Dict, Any
from quantum_edge_core.supervisor.context.accumulator import MarketAccumulator
from quantum_edge_core.supervisor.context.features import FeatureEngine
from quantum_edge_core.supervisor.context.heatmap import LiquidationHeatmap

logger = logging.getLogger(__name__)

class ContextBuilder:
    """
    Main entry point for building AI context.
    """
    def __init__(self):
        self.accumulators: Dict[str, MarketAccumulator] = {}
        # We can support multiple symbols, but mostly focused on one active per supervisor usually.
        # Initialize default
        self.accumulators["BTCUSDT"] = MarketAccumulator()
        self.active_symbol = "BTCUSDT"
        
        # Heatmap (One per symbol ideally, but one global for now or mapped)
        self.heatmaps: Dict[str, LiquidationHeatmap] = {}
        self.heatmaps["BTCUSDT"] = LiquidationHeatmap(bin_size=10.0)

    def get_accumulator(self, symbol: str) -> MarketAccumulator:
        if symbol not in self.accumulators:
            self.accumulators[symbol] = MarketAccumulator()
        return self.accumulators[symbol]

    def on_market_data(self, topic: str, msg: Dict[str, Any]):
        """
        Route incoming ZMQ message to appropriate accumulator.
        """
        # Topic format: "market.trade.BTCUSDT" or similar
        # For now, we assumed topic was parsed outside or we parse it
        # Actually message usually contains symbol or 's'
        
        symbol = msg.get("s", self.active_symbol) # Default to active if missing
        acc = self.get_accumulator(symbol)
        
        msg_type = msg.get("e") or topic.split(".")[-1] # event type
        
        if msg_type == "trade" or "aggTrade" in msg_type or topic.endswith("trade"):
            acc.add_trade(msg)
        elif "kline" in msg_type:
            # kline data usually inside 'k' key for binance
            kline = msg.get("k", msg)
            acc.add_candle(kline)
        elif "depth" in msg_type or "book" in topic:
            acc.add_book_snapshot(msg)
        elif "liquidation" in msg_type or "liquidation" in topic:
            acc.on_liquidation(msg)
            # Update Heatmap
            hm = self.heatmaps.get(symbol)
            if hm:
                hm.on_liquidation(msg)

    def build_snapshot(self, symbol: str = None) -> Dict[str, Any]:
        """
        Build the JSON snapshot for LLM.
        """
        sym = symbol or self.active_symbol
        acc = self.get_accumulator(sym)
        
        # Calculate Features
        cvd_metrics = FeatureEngine.calc_cvd(acc)
        vwap_metrics = FeatureEngine.calc_vwap_metrics(acc)
        volatility = FeatureEngine.calc_volatility(acc)
        volatility = FeatureEngine.calc_volatility(acc)
        imbalance = FeatureEngine.calc_order_book_imbalance(acc)
        liquidation = FeatureEngine.calc_liquidation_pressure(acc)
        
        # Current Price (Tail of trades or from book)
        current_price = 0.0
        if acc.trades:
            current_price = acc.trades[-1].price
            
        # Structure matches requirements
        snapshot = {
            "market_state": {
                "symbol": sym,
                "price": current_price,
                "vwap_z_score": vwap_metrics.get("vwap_z_score", 0.0),
                "volatility_ratio": volatility, # Raw vol for now
                # "volume_change": ... 
            },
            "microstructure": {
                "cvd_absolute": cvd_metrics.get("cvd_absolute", 0.0),
                "cvd_slope": cvd_metrics.get("cvd_slope", 0.0),
                "order_book_imbalance": imbalance,
                "liquidation_pressure": liquidation,
                "liquidation_clusters": self.heatmaps.get(sym, LiquidationHeatmap()).get_top_clusters(n=5)
            },
            "risk_metrics": {
                # This part likely needs injection from Portfolio/Position State 
                # which might live in Supervisor Buffer, not MarketAccumulator.
                # We return placeholder or allow injection.
                "exposure": 0.0,
                "unrealized_pnl": 0.0
            }
        }
        
        return snapshot
    
    # helper for service integration to inject risk
    def inject_risk_state(self, snapshot: Dict[str, Any], risk_state: Dict[str, Any]):
        snapshot["risk_metrics"].update(risk_state)
        return snapshot
