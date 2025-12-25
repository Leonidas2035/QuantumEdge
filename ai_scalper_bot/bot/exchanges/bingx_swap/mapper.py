import math


QUOTE_ASSETS = ("USDT", "USDC", "USD")


def normalize_symbol(symbol: str) -> str:
    if not symbol:
        return ""
    return symbol.replace("-", "").upper()


def to_bingx_symbol(symbol: str) -> str:
    raw = symbol or ""
    if "-" in raw:
        return raw.upper()
    sym = normalize_symbol(raw)
    for quote in QUOTE_ASSETS:
        if sym.endswith(quote) and len(sym) > len(quote):
            base = sym[: -len(quote)]
            return f"{base}-{quote}"
    return sym


def from_bingx_symbol(symbol: str) -> str:
    return normalize_symbol(symbol)


def map_side(side: str) -> str:
    side_up = str(side or "").upper()
    if side_up not in {"BUY", "SELL"}:
        raise ValueError(f"Unsupported side: {side}")
    return side_up


def map_position_side(position_side: str) -> str:
    side_up = str(position_side or "").upper()
    if side_up not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported position side: {position_side}")
    return side_up


def _decimals_from_step(step: float) -> int:
    if step <= 0:
        return 0
    text = f"{step:.12f}".rstrip("0")
    if "." in text:
        return len(text.split(".")[1])
    return 0


def _format_step(value: float, step: float) -> float:
    decimals = _decimals_from_step(step)
    if decimals <= 0:
        return float(int(value))
    return float(f"{value:.{decimals}f}")


def round_qty_to_step(qty: float, step: float) -> float:
    if qty <= 0 or step <= 0:
        return 0.0
    steps = math.floor(qty / step)
    rounded = steps * step
    return _format_step(rounded, step)


def round_price_to_tick(price: float, tick: float) -> float:
    if price <= 0 or tick <= 0:
        return 0.0
    steps = math.floor(price / tick)
    rounded = steps * tick
    return _format_step(rounded, tick)
