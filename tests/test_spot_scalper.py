import pytest

from bot.spot_scalper import (
    BookTop,
    ExecutionEngine,
    FeatureComputer,
    OrderIntent,
    RegimeDetector,
    RiskManager,
    Signal,
    SignalEngine,
    SpotScalperEngine,
    TopFeatures,
    _volume_imbalance,
)


def test_booktop_mid_spread() -> None:
    book = BookTop(bid_px=100.0, bid_qty=1.0, ask_px=101.0, ask_qty=1.0, ts_ms=0)
    assert book.mid == pytest.approx(100.5)
    assert book.spread == pytest.approx(1.0)
    assert book.spread_bps == pytest.approx(1.0 / 100.5 * 10_000.0)


def test_volume_imbalance_edges() -> None:
    assert _volume_imbalance(0.0, 0.0) == 0.0
    assert _volume_imbalance(10.0, 0.0) == 1.0
    assert _volume_imbalance(10.0, 5.0) == pytest.approx(1 / 3, rel=1e-6)
    assert _volume_imbalance(2.0, 6.0) == pytest.approx(-0.5, rel=1e-6)


def test_regime_classification() -> None:
    detector = RegimeDetector(max_spread_bps=5.0, max_short_vol_bps=20.0, trend_threshold=0.001)
    wide = TopFeatures(10.0, 0.0, 0.0, 0.0, 0.0)
    assert detector.classify(wide) == "NO_TRADE"
    high_vol = TopFeatures(1.0, 0.0, 25.0, 0.0, 0.0)
    assert detector.classify(high_vol) == "HIGH_VOL"
    trend = TopFeatures(1.0, 0.0, 1.0, 0.01, 0.0)
    assert detector.classify(trend) == "TREND"
    quiet = TopFeatures(1.0, 0.0, 1.0, 0.0001, 0.0)
    assert detector.classify(quiet) == "RANGE"


def test_signal_engine_thresholds() -> None:
    signal_engine = SignalEngine(imbalance_threshold=0.2)
    features = TopFeatures(1.0, 0.1, 1.0, 0.0, 0.0)
    signal = signal_engine.generate(features, "RANGE")
    assert signal.side == 0
    features = TopFeatures(1.0, 0.3, 1.0, 0.0, 0.0)
    signal = signal_engine.generate(features, "RANGE")
    assert signal.side == 1
    assert 0.0 < signal.confidence <= 1.0


def test_execution_edge_ok_and_order() -> None:
    engine = ExecutionEngine(
        fee_bps=1.0,
        slippage_bps=1.0,
        ttl_ms=1000,
        max_requotes=1,
        tick_size=0.01,
        min_qty=0.001,
    )
    features = TopFeatures(1.0, 0.3, 1.0, 0.0, 1.0)
    assert engine.edge_ok(features) is False
    book = BookTop(bid_px=100.0, bid_qty=5.0, ask_px=100.02, ask_qty=5.0, ts_ms=0)
    features = TopFeatures(1.0, 0.3, 1.0, 0.0, 10.0)
    intent = engine.build_intent(book, Signal(side=1, confidence=0.5), features, 0, target_qty=0.002)
    assert isinstance(intent, OrderIntent)
    assert intent.action == "place"
    assert intent.price == pytest.approx(book.bid_px)
    assert intent.qty >= 0.001


def test_execution_partial_fill() -> None:
    engine = ExecutionEngine(
        fee_bps=0.0,
        slippage_bps=0.0,
        ttl_ms=1000,
        max_requotes=1,
        tick_size=0.01,
        min_qty=0.001,
    )
    book = BookTop(bid_px=100.0, bid_qty=5.0, ask_px=100.02, ask_qty=5.0, ts_ms=0)
    features = TopFeatures(1.0, 0.3, 1.0, 0.0, 10.0)
    intent = engine.build_intent(book, Signal(side=1, confidence=0.5), features, 0, target_qty=0.01)
    engine.apply_intent(intent, 0)
    engine.record_fill(0.005)
    assert engine._open is not None
    assert engine._open.remaining_qty == pytest.approx(0.005)


def test_risk_manager_blocks() -> None:
    risk = RiskManager(
        risk_per_trade=0.01,
        daily_dd_limit=0.01,
        max_consecutive_errors=2,
        spread_kill_bps=10.0,
        equity_usd=100.0,
    )
    assert risk.allow(1.0) is True
    risk.record_pnl(-5.0)
    assert risk.allow(1.0) is False
    assert risk.kill_switch is True


def test_spot_scalper_simulation_flow() -> None:
    features = FeatureComputer(vol_window=3, ema_fast=2, ema_slow=4)
    regime = RegimeDetector(max_spread_bps=10.0, max_short_vol_bps=1_000.0, trend_threshold=0.001)
    signal_engine = SignalEngine(imbalance_threshold=0.2)
    execution = ExecutionEngine(
        fee_bps=0.0,
        slippage_bps=0.0,
        ttl_ms=10_000,
        max_requotes=1,
        tick_size=0.01,
        min_qty=0.001,
    )
    risk = RiskManager(
        risk_per_trade=0.01,
        daily_dd_limit=1.0,
        max_consecutive_errors=5,
        spread_kill_bps=100.0,
        equity_usd=10_000.0,
    )
    engine = SpotScalperEngine(
        feature_computer=features,
        regime_detector=regime,
        signal_engine=signal_engine,
        execution_engine=execution,
        risk_manager=risk,
    )

    def step(bid, ask, bid_qty, ask_qty, ts):
        book = BookTop(bid_px=bid, bid_qty=bid_qty, ask_px=ask, ask_qty=ask_qty, ts_ms=ts)
        payload = engine.on_book(book, ts)
        intent = payload.get("intent")
        if isinstance(intent, OrderIntent):
            engine.apply_intent(intent, ts)
        return payload, intent

    payload, intent = step(100.0, 100.2, 5.0, 5.0, 0)
    assert payload["regime"] == "NO_TRADE"
    assert intent is None

    payload, intent = step(100.05, 100.07, 5.0, 5.0, 100)
    assert payload["regime"] == "RANGE"
    assert intent is None

    payload, intent = step(100.99, 101.01, 12.0, 4.0, 200)
    assert payload["regime"] == "TREND"
    assert isinstance(intent, OrderIntent)
    assert intent.action == "place"

    payload, intent = step(101.0, 101.02, 12.0, 4.0, 300)
    assert isinstance(intent, OrderIntent)
    assert intent.action == "replace"

    payload, intent = step(101.01, 101.03, 12.0, 4.0, 400)
    assert isinstance(intent, OrderIntent)
    assert intent.action == "cancel"
    assert intent.reason == "max_requotes"
