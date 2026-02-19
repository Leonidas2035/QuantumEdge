from quantum_edge_core.ai_scalper_bot.bot.core.models import (MarketState,
                                                              MarketTick)
from quantum_edge_core.ai_scalper_bot.bot.features.facade import FeatureEngine
from quantum_edge_core.ai_scalper_bot.bot.features.ofi import OfiCalculator
from quantum_edge_core.ai_scalper_bot.bot.features.vpin import VpinCalculator


def create_state(bid_p, bid_q, ask_p, ask_q):
    return MarketState(
        timestamp=1000.0,
        best_bid=bid_p,
        best_bid_qty=bid_q,
        best_ask=ask_p,
        best_ask_qty=ask_q,
        last_price=(bid_p + ask_p) / 2,
    )


def test_ofi_bid_higher():
    """Bid price increases -> OFI increases by new bid qty."""
    ofi = OfiCalculator()
    s1 = create_state(100.0, 1.0, 101.0, 1.0)
    ofi.update(s1)  # Init

    s2 = create_state(100.5, 2.0, 101.0, 1.0)  # Bid Price Up
    val = ofi.update(s2)
    # e_bid = +2.0
    # e_ask = 0 (no change)
    # result = 2.0
    assert val == 2.0


def test_ofi_ask_lower():
    """Ask price drops -> OFI increases (Ask contribution positive to OFI?).
    Wait, let's recheck Cont formula logic in implementation.
    e_n = e_B - e_A
    Ask Logic implemented:
    If curr < prev (Price improved): e_ask = curr.qty

    So if Ask Drops 101 -> 100.5 (Improvement in spread, more supply pressure?)
    Cont 2014 paper: OFI measures imbalance.
    High OFI (Positive) -> Buy Pressure > Sell Pressure.

    If Ask Price drops (Sellers are aggressive, moving down), this is Sell Pressure.
    So OFI should decrease.

    Let's check code:
    e_ask = curr.qty (positive value)
    Result = e_bid - e_ask = 0 - (+qty) = Negative.
    Correct. Sellers moving down = Sell Pressure = Negative OFI.
    """
    ofi = OfiCalculator()
    s1 = create_state(100.0, 1.0, 101.0, 1.0)
    ofi.update(s1)

    s2 = create_state(100.0, 1.0, 100.5, 5.0)  # Ask Price Down
    val = ofi.update(s2)
    # e_bid = 0
    # e_ask = +5.0 (since curr < prev)
    # result = -5.0
    assert val == -5.0


def test_vpin_bucketing():
    """Test VPIN bucket filling and calculation."""
    # Bucket size = 10, Window = 2
    vpin = VpinCalculator(bucket_vol=10.0, window=2)

    # Tick 1: 5 vol, Buyer Maker=False (Buy)
    t1 = MarketTick(100, 5.0, 1000, False)
    assert vpin.update(t1) is None  # 5/10 filled

    # Tick 2: 5 vol, Buyer Maker=True (Sell)
    t2 = MarketTick(100, 5.0, 1000, True)
    # Bucket 1 fills: Buy=5, Sell=5. Imbalance=|5-5|=0. Total=10.
    # VPIN = 0 / 10 = 0.
    val = vpin.update(t2)
    assert val == 0.0

    # Tick 3: 10 vol, Sell
    t3 = MarketTick(100, 10.0, 1000, True)  # Full bucket immediately
    # Bucket 2: Buy=0, Sell=10. Imbalance=10.
    # Window: [(5,5), (0,10)]
    # Sum Imbalances = 0 + 10 = 10
    # Total Vol = 2 * 10 = 20
    # VPIN = 10/20 = 0.5
    val = vpin.update(t3)
    assert val == 0.5


def test_facade_integration():
    """Test FeatureEngine updates both scalars."""
    fe = FeatureEngine(vpin_bucket_vol=10, vpin_window=5)

    s1 = create_state(100, 10, 101, 10)
    t1 = MarketTick(100, 5, 1000, False)

    vec = fe.update(t1, s1)

    assert vec.timestamp == 1000.0
    assert vec.ofi == 0.0  # First update
    assert vec.vpin == 0.0  # Bucket not full
