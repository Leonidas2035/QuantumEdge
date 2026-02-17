"""
Context Builder for SupervisorAgent.
Transforms raw data into an 'AI Context Contract'.
"""

from __future__ import annotations

from typing import Dict, Any
from quantum_edge_core.supervisor.supervisor.data_ingest import DataStore


class ContextBuilder:
    """
    Builds the semantic context for the LLM.
    """

    def __init__(self, data_store: DataStore):
        self.store = data_store

        # Configuration for "Risk" context
        self.risk_settings = {"max_drawdown": 1000.0, "target_notional": 50000.0}

    def build_snapshot(self) -> Dict[str, Any]:
        """
        Assemble the full context snapshot.
        """
        snapshot = {
            "price": self._build_price_context(),
            "microstructure": self._build_microstructure_context(),
            "sentiment": self._build_sentiment_context(),
            "risk_state": self._build_risk_context(),
        }
        return snapshot

    def _build_price_context(self) -> Dict[str, Any]:
        # Using a default or primary symbol if available, hardcoded for now or derived
        # For this prototype, we look at BTCUSDT metrics
        # Ideally, Supervisor monitors a specific specific symbol or portfolio
        symbol = "BTCUSDT"
        metrics = self.store.market_metrics.get(symbol, {})

        current_price = metrics.get("price", 0.0)
        vwap = metrics.get("vwap", 0.0)
        std = metrics.get("std_dev", 0.0)

        vwap_dev = self.calc_vwap_deviation(current_price, vwap, std)

        return {
            "symbol": symbol,
            "current": current_price,
            "vwap_dev": vwap_dev,
            "change_1h": metrics.get("change_1h", 0.0),
        }

    def _build_microstructure_context(self) -> Dict[str, Any]:
        symbol = "BTCUSDT"
        metrics = self.store.market_metrics.get(symbol, {})

        # Orderbook imbalance would come from raw orderbook or pre-calc metrics
        # Assuming metrics contain pre-calc fields

        return {
            "ofi": metrics.get("ofi", 0.0),
            "vpin": metrics.get("vpin", 0.0),
            "cvd": metrics.get("cvd", 0.0),
            "liquidation_proximity": metrics.get("liq_prox", 0.0),  # Placeholder
        }

    def _build_sentiment_context(self) -> Dict[str, Any]:
        symbol = "BTCUSDT"
        metrics = self.store.market_metrics.get(symbol, {})

        funding = metrics.get("funding_rate", 0.0)
        oi = metrics.get("open_interest", 0.0)

        pressure = self.calc_funding_pressure(funding, oi)

        return {
            "funding": funding,
            "oi_change": metrics.get("oi_change", 0.0),
            "funding_pressure": pressure,
        }

    def _build_risk_context(self) -> Dict[str, Any]:
        # Aggregated Portfolio Risk
        total_unrealized_pnl = 0.0
        total_exposure = 0.0

        for sym, pos in self.store.futures_positions.items():
            amt = float(pos.get("positionAmt", 0.0))
            price = float(pos.get("entryPrice", 0.0))  # Or current mark price ideally
            # Rough exposure
            total_exposure += abs(amt * price)
            total_unrealized_pnl += float(pos.get("unrealizedProfit", 0.0))

        # Drawdown calculation requires equity tracking (historical max).
        # We rely on what the data store might have or inject it.
        # For now, we report PnL as simplified state.

        return {
            "total_unrealized_pnl": total_unrealized_pnl,
            "total_exposure": total_exposure,
            "leverage": (
                total_exposure / 10000.0 if total_exposure > 0 else 0.0
            ),  # Mock Equity
        }

    # --- Math Helpers ---

    @staticmethod
    def calc_imbalance_ratio(bids: list, asks: list, depth: int = 5) -> float:
        """
        Calculates (BidVol - AskVol) / (BidVol + AskVol) for top N levels.
        bids/asks: list of [price, vol]
        """
        bid_vol = sum(float(x[1]) for x in bids[:depth])
        ask_vol = sum(float(x[1]) for x in asks[:depth])

        total = bid_vol + ask_vol
        if total == 0:
            return 0.0

        return (bid_vol - ask_vol) / total

    @staticmethod
    def calc_vwap_deviation(current_price: float, vwap: float, std_dev: float) -> float:
        """Z-score of price relative to VWAP."""
        if std_dev <= 0:
            return 0.0
        return (current_price - vwap) / std_dev

    @staticmethod
    def calc_funding_pressure(funding_rate: float, open_interest: float) -> float:
        """
        Heuristic: High funding + High OI = High Pressure.
        Returns a normalized score -1 to 1? Or unbounded?
        Here we just return strict product * scalar for observability.
        """
        # Example: 0.01% funding * 1M OI = 100 pressure units
        return funding_rate * open_interest
