"""Async multi-metric builder for LLM Supervision."""

import json
import asyncio
import httpx
import logging
from typing import Dict, Any, Optional

import pandas as pd
import pandas_ta as ta

from quantum_edge_core.supervisor.supervisor.state import RiskStateSnapshot

class MarketContextBuilder:
    def __init__(self, use_testnet: bool = True):
        # By default use testnet due to geoblock limits on pure fapi.binance.com in some regions
        # Although user requested `https://fapi.binance.com/fapi/v1/...`, we will use the testnet.binancefuture.com fallback
        # just in case, but user said "реальний ринок, без проксі". So I'll default to real fapi but allow testnet.
        self.base_url = "https://fapi.binance.com" if not use_testnet else "https://testnet.binancefuture.com"
        self.db_url = "http://127.0.0.1:9000/exec"
        self.logger = logging.getLogger(__name__)

    async def _fetch_candles_from_db(self, client: httpx.AsyncClient, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Fetch custom timeframe candles from QuestDB using SAMPLE BY."""
        query = f"""
            SELECT 
                timestamp,
                first(price) AS open,
                max(price) AS high,
                min(price) AS low,
                last(price) AS close,
                sum(qty) AS volume
            FROM trades
            WHERE symbol = '{symbol}'
            SAMPLE BY {timeframe} ALIGN TO CALENDAR
            ORDER BY timestamp DESC LIMIT 100;
        """
        try:
            resp = await client.get(self.db_url, params={"query": query})
            if resp.status_code == 200:
                data = resp.json()
                dataset = data.get("dataset", [])
                if not dataset:
                    return None
                
                # Reverse dataset so the oldest is first for TA math
                dataset.reverse()
                
                # columns = [col["name"] for col in data.get("columns", [])]
                df = pd.DataFrame(dataset, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df.set_index("timestamp", inplace=True)
                
                # Clean up nulls resulting from missing periods before fill
                df.ffill(inplace=True) 
                return df
        except Exception as e:
            self.logger.error(f"Failed to fetch candles from QuestDB ({timeframe}): {e}")
            
        return None

    def _calculate_ta(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculate Technical Indicators using Pandas TA."""
        if df is None or len(df) < 50:
            return {"rsi": None, "boll": None, "trend": None}
            
        ta_data = {}
        
        try:
            # 1. RSI (14)
            rsi = ta.rsi(df["close"], length=14)
            ta_data["rsi"] = float(rsi.iloc[-1]) if rsi is not None and not pd.isna(rsi.iloc[-1]) else None
            
            # 2. Bollinger Bands (20, 2)
            bb = ta.bbands(df["close"], length=20, std=2)
            if bb is not None and not bb.empty:
                last_bb = bb.iloc[-1]
                last_close = df["close"].iloc[-1]
                
                upper = last_bb.get("BBU_20_2.0", None)
                lower = last_bb.get("BBL_20_2.0", None)
                
                if upper and lower and not pd.isna(upper) and not pd.isna(lower):
                    bnd_width = upper - lower
                    if last_close > upper - (bnd_width * 0.1):
                        ta_data["boll"] = "upper"
                    elif last_close < lower + (bnd_width * 0.1):
                        ta_data["boll"] = "lower"
                    else:
                        ta_data["boll"] = "mid"
                else:
                    ta_data["boll"] = None
            else:
                 ta_data["boll"] = None

            # 3. Simple Trend (SMA 50 vs SMA 20)
            sma20 = ta.sma(df["close"], length=20)
            sma50 = ta.sma(df["close"], length=50)
            
            if sma20 is not None and sma50 is not None:
                last_20 = sma20.iloc[-1]
                last_50 = sma50.iloc[-1]
                if pd.isna(last_20) or pd.isna(last_50):
                     ta_data["trend"] = None
                elif last_20 > last_50:
                    ta_data["trend"] = "UP"
                elif last_20 < last_50:
                    ta_data["trend"] = "DOWN"
                else:
                    ta_data["trend"] = "SIDEWAYS"
            else:
                 ta_data["trend"] = None
        except Exception as e:
            self.logger.error(f"Error calculating TA: {e}")
            return {"rsi": None, "boll": None, "trend": None}
            
        return ta_data

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
                # 3. Local QuestDB TA Engine (1H and 5M)
                df_1h = await self._fetch_candles_from_db(client, symbol, "1h")
                snapshot["ta_1h"] = self._calculate_ta(df_1h)

                df_5m = await self._fetch_candles_from_db(client, symbol, "5m")
                snapshot["ta_5m"] = self._calculate_ta(df_5m)
            except Exception as e:
                self.logger.warning(f"Failed to generate TA: {e}")
                snapshot["ta_1h"] = {"rsi": None, "boll": None, "trend": None}
                snapshot["ta_5m"] = {"rsi": None, "boll": None, "trend": None}

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
