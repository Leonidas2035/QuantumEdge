from typing import Tuple, Optional

from bot.exchanges.bingx_swap.client import BingXClient
from bot.exchanges.bingx_swap.mapper import to_bingx_symbol


class BingXMarketData:
    def __init__(self, client: BingXClient) -> None:
        self.client = client

    def get_mark_price(self, symbol: str) -> float:
        payload = self.client.request(
            "GET",
            "/openApi/swap/v2/quote/premiumIndex",
            params={"symbol": to_bingx_symbol(symbol)},
            signed=False,
        )
        if isinstance(payload, dict):
            return float(payload.get("markPrice") or 0.0)
        return float(payload or 0.0)

    def get_last_price(self, symbol: str) -> float:
        payload = self.client.request(
            "GET",
            "/openApi/swap/v2/quote/price",
            params={"symbol": to_bingx_symbol(symbol)},
            signed=False,
        )
        if isinstance(payload, dict):
            return float(payload.get("price") or 0.0)
        return float(payload or 0.0)

    def get_best_bid_ask(self, symbol: str) -> Optional[Tuple[float, float]]:
        payload = self.client.request(
            "GET",
            "/openApi/swap/v2/quote/bookTicker",
            params={"symbol": to_bingx_symbol(symbol)},
            signed=False,
        )
        if not isinstance(payload, dict):
            return None
        book = payload.get("book_ticker") or payload.get("bookTicker") or {}
        bid = float(book.get("bid_price") or book.get("bidPrice") or 0.0)
        ask = float(book.get("ask_price") or book.get("askPrice") or 0.0)
        if bid <= 0 or ask <= 0:
            return None
        return bid, ask
