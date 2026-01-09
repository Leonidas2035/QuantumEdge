import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
if str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))

from supervisor.lockbot.models import BotStatusSnapshot, LiqHeatmapSummary, MarketSnapshot, RangePolicyConfig
from supervisor.lockbot.strategy_range import evaluate_range


def _status() -> BotStatusSnapshot:
    return BotStatusSnapshot(
        mode="LOCKED",
        regime="RANGE",
        net_delta=0.0,
        long_qty=0.1,
        short_qty=0.1,
        market_lag_ms=0,
        account_lag_ms=0,
        ddn_verdict=None,
        ddn_reasons=(),
        last_cmd_type=None,
        last_cmd_id=None,
        last_cmd_ts=None,
    )


def test_range_no_action_inside_band1() -> None:
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
        funding_rate=0.0,
        funding_ts=1,
        liq=LiqHeatmapSummary(),
        ohlcv_5m=[],
        ohlcv_15m=[],
    )
    decision = evaluate_range(market, _status(), RangePolicyConfig())
    assert decision.intent is None


def test_range_action_outside_band2() -> None:
    market = MarketSnapshot(
        symbol="BTCUSDT",
        mark_price=103.0,
        mark_ts=1,
        vwap=100.0,
        band_1u=101.0,
        band_1l=99.0,
        band_2u=102.0,
        band_2l=98.0,
        avwap=None,
        avwap_anchor=None,
        avwap_anchors={},
        funding_rate=0.0,
        funding_ts=1,
        liq=LiqHeatmapSummary(),
        ohlcv_5m=[],
        ohlcv_15m=[],
    )
    decision = evaluate_range(market, _status(), RangePolicyConfig())
    assert decision.intent is not None
    assert decision.intent.cmd == "EXEC_STEP"


def test_range_heatmap_blocks() -> None:
    market = MarketSnapshot(
        symbol="BTCUSDT",
        mark_price=103.0,
        mark_ts=1,
        vwap=100.0,
        band_1u=101.0,
        band_1l=99.0,
        band_2u=102.0,
        band_2l=98.0,
        avwap=None,
        avwap_anchor=None,
        avwap_anchors={},
        funding_rate=0.0,
        funding_ts=1,
        liq=LiqHeatmapSummary(intensity_above=10.0, intensity_below=0.0),
        ohlcv_5m=[],
        ohlcv_15m=[],
    )
    decision = evaluate_range(market, _status(), RangePolicyConfig(heatmap_block=5.0))
    assert decision.intent is None
    assert decision.reason == "heatmap_block"
