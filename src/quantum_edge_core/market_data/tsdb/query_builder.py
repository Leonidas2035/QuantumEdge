import aiohttp
import urllib.parse
import logging

logger = logging.getLogger(__name__)

class QuestDBQueryBuilder:
    def __init__(self, host="127.0.0.1", port=9000):
        self.base_url = f"http://{host}:{port}/exec"

    async def _execute(self, query: str) -> list[dict]:
        try:
            async with aiohttp.ClientSession() as session:
                encoded_query = urllib.parse.quote(query.strip())
                url = f"{self.base_url}?query={encoded_query}"
                async with session.get(url) as response:
                    if response.status != 200:
                        raise Exception(f"QuestDB Error: {await response.text()}")
                    data = await response.json()
                    # QuestDB returns {'columns': [...], 'dataset': [[...], ...]}
                    columns_data = data.get('columns', [])
                    dataset_data = data.get('dataset', [])
                    cols = [c['name'] for c in columns_data]
                    return [dict(zip(cols, row)) for row in dataset_data]
        except Exception as e:
            logger.error(f"Failed to execute QuestDB query: {e}")
            return []

    async def get_microstructure(self, symbol: str, minutes: int = 15):
        query = f"""
        SELECT ts, mid_price, spread, ofi_raw, volume_delta 
        FROM market_features 
        WHERE symbol = '{symbol}' AND ts > dateadd('m', -{minutes}, now()) 
        ORDER BY ts ASC;
        """
        return await self._execute(query)

    async def get_volatility_profile(self, symbol: str, hours: int = 4):
        query = f"""
        SELECT ts, atr_14, mid_price 
        FROM market_features 
        WHERE symbol = '{symbol}' AND ts > dateadd('h', -{hours}, now()) 
        ORDER BY ts ASC;
        """
        return await self._execute(query)

    async def get_vwap_bands(self, symbol: str, days: int = 1):
        query = f"""
        SELECT ts, close, volume 
        FROM klines_1m 
        WHERE symbol = '{symbol}' AND ts > dateadd('d', -{days}, now()) 
        ORDER BY ts ASC;
        """
        return await self._execute(query)
