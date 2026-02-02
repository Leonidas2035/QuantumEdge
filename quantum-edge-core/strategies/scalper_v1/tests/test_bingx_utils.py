from bot.exchanges.bingx_swap.client import build_query_string, sign_query
from bot.exchanges.bingx_swap.mapper import (
    normalize_symbol,
    to_bingx_symbol,
    from_bingx_symbol,
    round_price_to_tick,
    round_qty_to_step,
)


def test_signature_deterministic():
    params = {
        "symbol": "BTC-USDT",
        "side": "BUY",
        "quantity": 0.001,
        "recvWindow": 5000,
        "timestamp": 1700000000000,
    }
    query = build_query_string(params)
    assert query == "quantity=0.001&recvWindow=5000&side=BUY&symbol=BTC-USDT&timestamp=1700000000000"
    signature = sign_query(query, "testsecret")
    assert signature == "f67741a255b564ab12ccaeb385fce4402e8163d688437081c1de07358cd1b26e"


def test_symbol_mapping():
    assert normalize_symbol("btc-usdt") == "BTCUSDT"
    assert to_bingx_symbol("BTCUSDT") == "BTC-USDT"
    assert from_bingx_symbol("BTC-USDT") == "BTCUSDT"


def test_rounding_helpers():
    assert round_qty_to_step(0.0049, 0.001) == 0.004
    assert round_price_to_tick(123.456, 0.1) == 123.4
