import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
if str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))

from supervisor.lockbot.models import BotStatusSnapshot, LiqHeatmapSummary, MarketSnapshot, TrendPolicyConfig
from supervisor.lockbot.strategy_trend import evaluate_trend


def _status(delta: float = 0.0) -> BotStatusSnapshot:
    long_qty = max(delta, 0.0)
    short_qty = max(-delta, 0.0)
    return BotStatusSnapshot(
        mode="LOCKED",
        regime="TREND_UP",
        net_delta=delta,
        long_qty=long_qty,
        short_qty=short_qty,
        market_lag_ms=0,
        account_lag_ms=0,
        ddn_verdict=None,
        ddn_reasons=(),
        last_cmd_type=None,
        last_cmd_id=None,
        last_cmd_ts=None,
    )


def test_trend_requires_pullback() -> None:
    market = MarketSnapshot(
        symbol="BTCUSDT",
        mark_price=110.0,
        mark_ts=1,
        vwap=100.0,
        band_1u=None,
        band_1l=None,
        band_2u=None,
        band_2l=None,
        avwap=None,
        avwap_anchor=None,
        avwap_anchors={},
        funding_rate=0.0,
        funding_ts=1,
        liq=LiqHeatmapSummary(),
        ohlcv_5m=[],
        ohlcv_15m=[],
    )
    decision = evaluate_trend(market, _status(), TrendPolicyConfig(pullback_bps=20.0), "TREND_UP")
    assert decision.intent is None
    assert decision.reason == "no_pullback"


def test_trend_action_on_pullback() -> None:
    market = MarketSnapshot(
        symbol="BTCUSDT",
        mark_price=100.5,
        mark_ts=1,
        vwap=100.0,
        band_1u=None,
        band_1l=None,
        band_2u=None,
        band_2l=None,
        avwap=None,
        avwap_anchor=None,
        avwap_anchors={},
        funding_rate=0.0,
        funding_ts=1,
        liq=LiqHeatmapSummary(),
        ohlcv_5m=[],
        ohlcv_15m=[],
    )
    cfg = TrendPolicyConfig(pullback_bps=60.0)
    decision = evaluate_trend(market, _status(delta=-0.1), cfg, "TREND_UP")
    assert decision.intent is not None
    assert decision.intent.cmd == "EXEC_STEP"
