import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from bot.exchanges.bingx_swap.client import BingXClient
from bot.exchanges.bingx_swap.mapper import (
    from_bingx_symbol,
    map_position_side,
    map_side,
    round_price_to_tick,
    round_qty_to_step,
    to_bingx_symbol,
)
from bot.exchanges.bingx_swap.models import OrderRequest, OrderResult, Position, Balance


@dataclass
class SymbolFilters:
    step_size: float
    tick_size: float
    min_qty: float
    min_notional: float


class ExchangeInfoCache:
    def __init__(self, client: BingXClient, ttl_seconds: int = 600) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple[float, SymbolFilters]] = {}

    def get(self, symbol: str) -> SymbolFilters:
        sym = to_bingx_symbol(symbol)
        now = time.monotonic()
        cached = self._cache.get(sym)
        if cached and cached[0] > now:
            return cached[1]

        payload = self.client.request(
            "GET",
            "/openApi/swap/v2/quote/contracts",
            params={"symbol": sym},
            signed=False,
        )
        items = payload if isinstance(payload, list) else [payload]
        info = None
        for entry in items:
            if isinstance(entry, dict) and entry.get("symbol") == sym:
                info = entry
                break
        if not info:
            raise ValueError(f"Symbol {sym} not found in BingX contracts.")

        size = float(info.get("size") or 0.0)
        qty_precision = int(info.get("quantityPrecision") or 0)
        price_precision = int(info.get("pricePrecision") or 0)
        step_size = size if size > 0 else (10 ** (-qty_precision) if qty_precision > 0 else 0.0)
        tick_size = 10 ** (-price_precision) if price_precision > 0 else 0.0
        min_qty = float(info.get("tradeMinQuantity") or 0.0)
        min_notional = float(info.get("tradeMinUSDT") or 0.0)

        filters = SymbolFilters(
            step_size=step_size,
            tick_size=tick_size,
            min_qty=min_qty,
            min_notional=min_notional,
        )
        self._cache[sym] = (now + self.ttl_seconds, filters)
        return filters


class BingXExecution:
    def __init__(self, client: BingXClient, info_cache: Optional[ExchangeInfoCache] = None) -> None:
        self.client = client
        self.info_cache = info_cache or ExchangeInfoCache(client)
        self.logger = logging.getLogger(__name__)

    def place_order(self, req: OrderRequest) -> OrderResult:
        symbol = to_bingx_symbol(req.symbol)
        filters = self.info_cache.get(symbol)
        qty = round_qty_to_step(req.qty, filters.step_size) if filters.step_size else req.qty
        if filters.min_qty and qty < filters.min_qty:
            raise ValueError(f"Quantity {qty} below minQty {filters.min_qty} for {symbol}")
        price = req.price
        if price is not None and filters.tick_size:
            price = round_price_to_tick(price, filters.tick_size)
        if filters.min_notional and price is not None:
            notional = qty * price
            if notional < filters.min_notional:
                raise ValueError(f"Order notional {notional} below minNotional {filters.min_notional} for {symbol}")

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": map_side(req.side),
            "type": str(req.order_type).upper(),
            "quantity": qty,
        }
        if req.position_side:
            params["positionSide"] = map_position_side(req.position_side)
        if price is not None:
            params["price"] = price
        if req.time_in_force:
            params["timeInForce"] = req.time_in_force
        if req.reduce_only:
            params["reduceOnly"] = True
        if req.client_order_id:
            params["clientOrderId"] = req.client_order_id

        data = self.client.request("POST", "/openApi/swap/v2/trade/order", params=params, signed=True)
        order_data = data.get("order") if isinstance(data, dict) else data
        if not isinstance(order_data, dict):
            order_data = {}
        order_id = str(order_data.get("orderId") or "")
        client_order_id = order_data.get("clientOrderId") or req.client_order_id
        status = str(order_data.get("status") or "UNKNOWN")
        filled_qty = float(order_data.get("executedQty") or order_data.get("filledQty") or 0.0)
        avg_price = float(order_data.get("avgPrice") or 0.0)
        if filled_qty <= 0 and req.order_type.upper() == "MARKET":
            filled_qty = float(qty)
        return OrderResult(
            order_id=order_id,
            client_order_id=client_order_id,
            status=status,
            filled_qty=filled_qty,
            avg_price=avg_price,
            raw=order_data or data,
        )

    def cancel_order(self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None) -> Any:
        if not order_id and not client_order_id:
            raise ValueError("order_id or client_order_id is required to cancel.")
        params: Dict[str, Any] = {"symbol": to_bingx_symbol(symbol)}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["clientOrderId"] = client_order_id
        return self.client.request("DELETE", "/openApi/swap/v2/trade/order", params=params, signed=True)

    def get_order(self, symbol: str, order_id: Optional[str] = None, client_order_id: Optional[str] = None) -> OrderResult:
        if not order_id and not client_order_id:
            raise ValueError("order_id or client_order_id is required to query order.")
        params: Dict[str, Any] = {"symbol": to_bingx_symbol(symbol)}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["clientOrderId"] = client_order_id
        data = self.client.request("GET", "/openApi/swap/v2/trade/order", params=params, signed=True)
        order_data = data.get("order") if isinstance(data, dict) else data
        if not isinstance(order_data, dict):
            order_data = {}
        return OrderResult(
            order_id=str(order_data.get("orderId") or ""),
            client_order_id=order_data.get("clientOrderId"),
            status=str(order_data.get("status") or "UNKNOWN"),
            filled_qty=float(order_data.get("executedQty") or 0.0),
            avg_price=float(order_data.get("avgPrice") or 0.0),
            raw=order_data or data,
        )

    def get_open_orders(self, symbol: str) -> List[OrderResult]:
        params = {"symbol": to_bingx_symbol(symbol)} if symbol else {}
        data = self.client.request("GET", "/openApi/swap/v2/trade/openOrders", params=params, signed=True)
        orders = data.get("orders") if isinstance(data, dict) else data
        if not isinstance(orders, list):
            orders = []
        results = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            results.append(
                OrderResult(
                    order_id=str(order.get("orderId") or ""),
                    client_order_id=order.get("clientOrderId"),
                    status=str(order.get("status") or "UNKNOWN"),
                    filled_qty=float(order.get("executedQty") or 0.0),
                    avg_price=float(order.get("avgPrice") or 0.0),
                    raw=order,
                )
            )
        return results

    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        params = {"symbol": to_bingx_symbol(symbol)} if symbol else {}
        data = self.client.request("GET", "/openApi/swap/v2/user/positions", params=params, signed=True)
        items = data.get("positions") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = data.get("result") if isinstance(data, dict) else []
        results: List[Position] = []
        if not isinstance(items, list):
            return results
        for entry in items:
            if not isinstance(entry, dict):
                continue
            qty = float(entry.get("positionAmt") or entry.get("positionQty") or entry.get("positionAmount") or 0.0)
            position_side = str(entry.get("positionSide") or "").upper()
            results.append(
                Position(
                    symbol=from_bingx_symbol(entry.get("symbol", "")),
                    position_side=position_side,
                    qty=qty,
                    entry_price=float(entry.get("avgPrice") or entry.get("entryPrice") or 0.0),
                    unrealized_pnl=float(entry.get("unrealizedProfit") or entry.get("unrealizedPnl") or 0.0),
                    leverage=float(entry.get("leverage") or 0.0),
                    liquidation_price=float(entry.get("liquidationPrice") or 0.0),
                    raw=entry,
                )
            )
        return results

    def get_balances(self) -> List[Balance]:
        data = self.client.request("GET", "/openApi/swap/v2/user/balance", params={}, signed=True)
        items = data.get("balances") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = data.get("result") if isinstance(data, dict) else []
        results: List[Balance] = []
        if not isinstance(items, list):
            return results
        for entry in items:
            if not isinstance(entry, dict):
                continue
            asset = str(entry.get("asset") or entry.get("currency") or "")
            available = float(entry.get("availableBalance") or entry.get("available") or 0.0)
            total = float(entry.get("balance") or entry.get("total") or entry.get("equity") or available or 0.0)
            results.append(Balance(asset=asset, available=available, total=total, raw=entry))
        return results

    def set_leverage(self, symbol: str, leverage: int, position_side: str) -> Optional[Any]:
        params = {
            "symbol": to_bingx_symbol(symbol),
            "leverage": leverage,
            "positionSide": map_position_side(position_side),
        }
        try:
            return self.client.request("POST", "/openApi/swap/v2/trade/leverage", params=params, signed=True)
        except Exception as exc:
            self.logger.info("Leverage update skipped for %s (%s).", symbol, exc)
            return None
