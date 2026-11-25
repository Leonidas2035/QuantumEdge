"""Canonical BingX Hedge Mode order intent mapping with strict reduceOnly safety."""

from enum import StrEnum


class OrderIntent(StrEnum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


def build_bingx_order_params(intent: str, hedged: bool = True) -> dict:
    """Map a canonical OrderIntent to BingX CCXT order side and Hedge Mode params.

    Closing intents ('close_long', 'close_short') strictly enforce reduceOnly=True
    to prevent accidentally opening an opposite or oversized position.
    """
    if not isinstance(intent, str):
        raise ValueError(f"Unknown order intent: {intent!r}")

    if intent == OrderIntent.OPEN_LONG:
        return {
            "side": "buy",
            "positionSide": "LONG",
            "reduceOnly": False,
            "hedged": hedged,
        }
    if intent == OrderIntent.OPEN_SHORT:
        return {
            "side": "sell",
            "positionSide": "SHORT",
            "reduceOnly": False,
            "hedged": hedged,
        }
    if intent == OrderIntent.CLOSE_LONG:
        return {
            "side": "sell",
            "positionSide": "LONG",
            "reduceOnly": True,
            "hedged": hedged,
        }
    if intent == OrderIntent.CLOSE_SHORT:
        return {
            "side": "buy",
            "positionSide": "SHORT",
            "reduceOnly": True,
            "hedged": hedged,
        }

    raise ValueError(f"Unknown order intent: {intent!r}")
