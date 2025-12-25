from bot.exchanges.bingx_swap.client import BingXClient, BingXAPIError
from bot.exchanges.bingx_swap.execution import BingXExecution, ExchangeInfoCache, SymbolFilters
from bot.exchanges.bingx_swap.marketdata import BingXMarketData
from bot.exchanges.bingx_swap.models import OrderRequest, OrderResult, Position, Balance


class BingXSwapExchange:
    def __init__(self, client: BingXClient) -> None:
        self.client = client
        self.marketdata = BingXMarketData(client)
        self.execution = BingXExecution(client)

    def get_mark_price(self, symbol: str) -> float:
        return self.marketdata.get_mark_price(symbol)

    def get_last_price(self, symbol: str) -> float:
        return self.marketdata.get_last_price(symbol)

    def get_best_bid_ask(self, symbol: str):
        return self.marketdata.get_best_bid_ask(symbol)

    def place_order(self, req: OrderRequest) -> OrderResult:
        return self.execution.place_order(req)

    def cancel_order(self, symbol: str, order_id: str = None, client_order_id: str = None):
        return self.execution.cancel_order(symbol, order_id=order_id, client_order_id=client_order_id)

    def get_order(self, symbol: str, order_id: str = None, client_order_id: str = None) -> OrderResult:
        return self.execution.get_order(symbol, order_id=order_id, client_order_id=client_order_id)

    def get_open_orders(self, symbol: str):
        return self.execution.get_open_orders(symbol)

    def get_positions(self, symbol: str = None):
        return self.execution.get_positions(symbol)

    def get_balances(self):
        return self.execution.get_balances()

    def set_leverage(self, symbol: str, leverage: int, position_side: str):
        return self.execution.set_leverage(symbol, leverage, position_side)

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        return self.execution.info_cache.get(symbol)


__all__ = [
    "BingXClient",
    "BingXAPIError",
    "BingXSwapExchange",
    "BingXExecution",
    "ExchangeInfoCache",
    "SymbolFilters",
    "OrderRequest",
    "OrderResult",
    "Position",
    "Balance",
]
