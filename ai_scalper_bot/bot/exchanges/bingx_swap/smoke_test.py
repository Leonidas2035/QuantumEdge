import argparse
import os
import sys
import time

from bot.exchanges.bingx_swap import BingXClient, BingXSwapExchange, OrderRequest
from bot.exchanges.bingx_swap.mapper import normalize_symbol, round_price_to_tick, round_qty_to_step


def _build_client() -> BingXClient:
    api_key = os.getenv("BINGX_DEMO_API_KEY") or os.getenv("BINGX_API_KEY")
    api_secret = os.getenv("BINGX_DEMO_API_SECRET") or os.getenv("BINGX_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("Missing BINGX_DEMO_API_KEY/BINGX_DEMO_API_SECRET env vars.")
    base_url = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com")
    recv_window = int(os.getenv("BINGX_RECV_WINDOW", "5000"))
    return BingXClient(base_url, api_key, api_secret, recv_window=recv_window, timeout=10.0)


def _print_kv(label: str, value) -> None:
    print(f"{label}: {value}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="BingX swap smoke test (demo mode).")
    parser.add_argument("--symbol", default="BTC-USDT", help="Symbol like BTC-USDT or BTCUSDT.")
    parser.add_argument("--place-test-order", action="store_true", help="Place and cancel a tiny LIMIT order.")
    args = parser.parse_args(argv)

    env = os.getenv("BINGX_ENV", "demo")
    if env and env.lower() != "demo":
        print(f"[WARN] BINGX_ENV={env}; demo mode expects demo keys.")

    client = _build_client()
    exchange = BingXSwapExchange(client)
    symbol = normalize_symbol(args.symbol)

    server_time = client.request("GET", "/openApi/swap/v2/server/time", signed=False)
    _print_kv("server_time", server_time.get("serverTime") if isinstance(server_time, dict) else server_time)

    last_price = exchange.get_last_price(symbol)
    mark_price = exchange.get_mark_price(symbol)
    _print_kv("last_price", last_price)
    _print_kv("mark_price", mark_price)

    positions = exchange.get_positions(symbol=None)
    _print_kv("positions", len(positions))

    if not args.place_test_order:
        return 0

    filters = exchange.get_symbol_filters(symbol)
    price = last_price * 0.5 if last_price else 0.0
    if filters.tick_size and price > 0:
        price = round_price_to_tick(price, filters.tick_size)
    if price <= 0:
        raise RuntimeError("Unable to compute a valid limit price.")

    qty = max(filters.min_qty, filters.step_size)
    if filters.min_notional:
        qty = max(qty, filters.min_notional / price)
    qty = round_qty_to_step(qty, filters.step_size) if filters.step_size else qty
    if qty <= 0:
        raise RuntimeError("Unable to compute a valid order quantity.")

    client_order_id = f"QE-SMOKE-{int(time.time() * 1000)}"
    req = OrderRequest(
        symbol=symbol,
        side="BUY",
        position_side="LONG",
        order_type="LIMIT",
        qty=qty,
        price=price,
        reduce_only=False,
        client_order_id=client_order_id[:32],
        time_in_force="GTC",
    )
    result = exchange.place_order(req)
    _print_kv("order_id", result.order_id or "n/a")
    _print_kv("client_order_id", result.client_order_id or "n/a")

    exchange.cancel_order(symbol, order_id=result.order_id or None, client_order_id=result.client_order_id or None)
    refreshed = exchange.get_order(symbol, order_id=result.order_id or None, client_order_id=result.client_order_id or None)
    _print_kv("cancel_status", refreshed.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
