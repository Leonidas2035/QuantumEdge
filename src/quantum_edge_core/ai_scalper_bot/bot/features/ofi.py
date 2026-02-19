"""
Order Flow Imbalance (OFI) Calculator.
Calculates microstructure imbalance incrementally based on best bid/ask changes.
Ref: Cont, Kukanov, Stoikov (2014).
"""

from typing import Optional

from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketState


class OfiCalculator:
    """
    Calculates Order Flow Imbalance (OFI) incrementally.
    """

    __slots__ = ("_prev",)

    def __init__(self):
        self._prev: Optional[MarketState] = None

    def update(self, curr: MarketState) -> float:
        """
        Updates OFI based on the change between previous and current MarketState.
        Complexity: O(1)

        Formula:
        e_n = e_n^B - e_n^A

        Where bid contribution e_n^B:
        - If P_bid > P_bid_prev: +Q_bid
        - If P_bid < P_bid_prev: -Q_bid_prev
        - If P_bid == P_bid_prev: Q_bid - Q_bid_prev

        Ask contribution is symmetric (remember Ask represents supply).
        """
        if self._prev is None:
            self._prev = MarketState(
                timestamp=curr.timestamp,
                best_bid=curr.best_bid,
                best_ask=curr.best_ask,
                best_bid_qty=curr.best_bid_qty,
                best_ask_qty=curr.best_ask_qty,
                last_price=curr.last_price,
            )
            return 0.0

        # --- Bid Side Calculation ---
        e_bid = 0.0
        if curr.best_bid > self._prev.best_bid:
            e_bid = curr.best_bid_qty
        elif curr.best_bid < self._prev.best_bid:
            e_bid = -self._prev.best_bid_qty
        else:
            e_bid = curr.best_bid_qty - self._prev.best_bid_qty

        # --- Ask Side Calculation ---
        # Ask represents supply.
        # If P_ask < P_ask_prev (Price improved/dropped): +Q_ask (New supply at better price? No wait)
        # Standard Cont logic:
        # e_n^A:
        # - If P_ask < P_ask_prev: +Q_ask
        # - If P_ask > P_ask_prev: -Q_ask_prev
        # - If P_ask == P_ask_prev: Q_ask - Q_ask_prev
        e_ask = 0.0
        if curr.best_ask < self._prev.best_ask:
            e_ask = curr.best_ask_qty
        elif curr.best_ask > self._prev.best_ask:
            e_ask = -self._prev.best_ask_qty
        else:
            e_ask = curr.best_ask_qty - self._prev.best_ask_qty

        # Update previous state
        # Note: We must store a COPY of struct if we weren't using immutable dataclasses
        # or if the upstream object is reused.
        # Since MarketState is slots=True but mutable, and OrderBookCache reuses/overwrites,
        # we strictly need to COPY the values we care about or Copy the object?
        # User prompt says "State: Stores previous MarketState".
        # Issue: If OrderBookCache.update modifies self._current_state in place, storing definition
        # 'self._prev = curr' will point to the SAME object.
        # Checking OrderBookCache logic:
        # "if self._current_state is None: ... else: s = self._current_state; s.timestamp = ..."
        # YES, it mutates in place.
        # Therefore, we MUST copy the state values we need.
        # OR we modify OfiCalculator to store just limits.

        # Correction: Store values, not the reference.
        # However, to match the prompt signature (stores MarketState),
        # I will create a new MarketState or just store tuple.
        # Prompt says: "State: Needs to store the previous MarketState (best_bid_p...)"

        # Creating a snapshot copy
        self._prev = MarketState(
            timestamp=curr.timestamp,
            best_bid=curr.best_bid,
            best_ask=curr.best_ask,
            best_bid_qty=curr.best_bid_qty,
            best_ask_qty=curr.best_ask_qty,
            last_price=curr.last_price,
        )

        return e_bid - e_ask
