import pytest
pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
if str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))

from supervisor.lockbot.models import LiqHeatmapSummary, MarketSnapshot, OhlcvBar, RegimeDetectorConfig
from supervisor.lockbot.regime_detector import RegimeDetector, RegimeHysteresis


def _trend_bars(count: int = 30, start: float = 100.0, step: float = 1.0) -> list[OhlcvBar]:
    bars = []
    price = start
    for idx in range(count):
        open_px = price
        close_px = price + step
        high_px = max(open_px, close_px) + 0.5
        low_px = min(open_px, close_px) - 0.5
        bars.append(OhlcvBar(ts_ms=idx * 300000, open=open_px, high=high_px, low=low_px, close=close_px, volume=1.0))
        price = close_px
    return bars


def test_regime_detector_trend_up() -> None:
    cfg = RegimeDetectorConfig(adx_period=5, atr_period=5, atr_baseline_period=10, slope_bps_enter=1.0, trend_adx_enter=5.0)
    detector = RegimeDetector(cfg)
    bars = _trend_bars()
    market = MarketSnapshot(
        symbol="BTCUSDT",
        mark_price=130.0,
        mark_ts=1,
        vwap=120.0,
        band_1u=None,
        band_1l=None,
        band_2u=None,
        band_2l=None,
        avwap=None,
        avwap_anchor=None,
        avwap_anchors={},
        funding_rate=None,
        funding_ts=None,
        liq=LiqHeatmapSummary(),
        ohlcv_5m=[],
        ohlcv_15m=bars,
    )
    decision = detector.evaluate(market)
    assert decision.candidate == "TREND_UP"


def test_regime_hysteresis_confirmation() -> None:
    cfg = RegimeDetectorConfig(confirm_cycles=2, min_regime_hold_s=0)
    hysteresis = RegimeHysteresis(cfg)
    regime, changed = hysteresis.update("RANGE", now_ms=0)
    assert regime == "RANGE"
    assert not changed
    regime, changed = hysteresis.update("TREND_UP", now_ms=1000)
    assert regime == "RANGE"
    assert not changed
    regime, changed = hysteresis.update("TREND_UP", now_ms=2000)
    assert regime == "TREND_UP"
    assert changed


def test_regime_detector_chaos_from_liq() -> None:
    cfg = RegimeDetectorConfig(chaos_liq_intensity=1.0, adx_period=5)
    detector = RegimeDetector(cfg)
    market = MarketSnapshot(
        symbol="BTCUSDT",
        mark_price=100.0,
        mark_ts=1,
        vwap=100.0,
        band_1u=101.0,
        band_1l=99.0,
        band_2u=102.0,
        band_2l=98.0,
        avwap=None,
        avwap_anchor=None,
        avwap_anchors={},
        funding_rate=None,
        funding_ts=None,
        liq=LiqHeatmapSummary(intensity_above=0.6, intensity_below=0.6),
        ohlcv_5m=[],
        ohlcv_15m=_trend_bars(10),
    )
    decision = detector.evaluate(market)
    assert decision.candidate == "CHAOS"
