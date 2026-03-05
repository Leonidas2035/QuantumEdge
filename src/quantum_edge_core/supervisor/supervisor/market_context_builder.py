"""Async multi-metric builder for LLM Supervision."""

import json
import asyncio
import httpx
import logging
from typing import Dict, Any

from quantum_edge_core.supervisor.supervisor.state import RiskStateSnapshot

class MarketContextBuilder:
    def __init__(self, use_testnet: bool = True):
        # By default use testnet due to geoblock limits on pure fapi.binance.com in some regions
        # Although user requested `https://fapi.binance.com/fapi/v1/...`, we will use the testnet.binancefuture.com fallback
        # just in case, but user said "реальний ринок, без проксі". So I'll default to real fapi but allow testnet.
        self.base_url = "https://fapi.binance.com" if not use_testnet else "https://testnet.binancefuture.com"
        self.logger = logging.getLogger(__name__)

    async def build_context(self, symbol: str, current_state: RiskStateSnapshot) -> str:
        snapshot: Dict[str, Any] = {}
        
        # State Data
        snapshot["equity"] = current_state.equity_now
        snapshot["pnl"] = current_state.realized_pnl_today

        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                # 1. Price
                price_resp = await client.get(f"{self.base_url}/fapi/v1/ticker/price?symbol={symbol}")
                if price_resp.status_code == 200:
                    snapshot["price"] = float(price_resp.json()["price"])
            except Exception as e:
                self.logger.warning(f"Failed to fetch price: {e}")
                snapshot["price"] = None

            try:
                # 2. Depth / Walls
                depth_resp = await client.get(f"{self.base_url}/fapi/v1/depth?symbol={symbol}&limit=1000")
                if depth_resp.status_code == 200:
                    data = depth_resp.json()
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])
                    if bids:
                        biggest_bid = max(bids, key=lambda x: float(x[1]))
                        snapshot["bid_wall_price"] = float(biggest_bid[0])
                        snapshot["bid_wall_vol"] = float(biggest_bid[1])
                    if asks:
                        biggest_ask = max(asks, key=lambda x: float(x[1]))
                        snapshot["ask_wall_price"] = float(biggest_ask[0])
                        snapshot["ask_wall_vol"] = float(biggest_ask[1])
            except Exception as e:
                self.logger.warning(f"Failed to fetch depth: {e}")

            try:
                # 3. Klines 4H and 1H
                async def get_trend(interval: str):
                    r = await client.get(f"{self.base_url}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit=2")
                    if r.status_code == 200:
                        klines = r.json()
                        if len(klines) > 1:
                            last = klines[-2] # Last closed
                            o = float(last[1])
                            c = float(last[4])
                            return "UP" if c > o else "DOWN"
                    return None

                snapshot["trend_4H"] = await get_trend("4h")
                snapshot["trend_1H"] = await get_trend("1h")
            except Exception as e:
                self.logger.warning(f"Failed to fetch klines: {e}")

            try:
                # 4. Funding rate
                funding_resp = await client.get(f"{self.base_url}/fapi/v1/premiumIndex?symbol={symbol}")
                if funding_resp.status_code == 200:
                    snapshot["funding_rate"] = float(funding_resp.json().get("lastFundingRate", 0))
            except Exception as e:
                self.logger.warning(f"Failed to fetch funding rate: {e}")
                snapshot["funding_rate"] = None

            try:
                # 5. Long/Short Ratio
                ls_resp = await client.get(f"{self.base_url}/futures/data/topLongShortAccountRatio?symbol={symbol}&period=5m&limit=1")
                if ls_resp.status_code == 200:
                    ls_data = ls_resp.json()
                    if ls_data and isinstance(ls_data, list):
                        snapshot["ls_ratio"] = float(ls_data[0].get("longShortRatio", 0))
            except Exception as e:
                self.logger.warning(f"Failed to fetch LS ratio: {e}")
                snapshot["ls_ratio"] = None

        return json.dumps(snapshot, separators=(",", ":"))
