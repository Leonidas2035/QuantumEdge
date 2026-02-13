"""Regime detector for LockBotBTC policy runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from quantum_edge_core.supervisor.supervisor.lockbot.models import MarketSnapshot, OhlcvBar, RegimeDetectorConfig, RegimeSignals


@dataclass
class RegimeDecision:
    candidate: str
    signals: RegimeSignals


class RegimeDetector:
    def __init__(self, cfg: RegimeDetectorConfig) -> None:
        self._cfg = cfg

    def evaluate(self, market: MarketSnapshot) -> RegimeDecision:
        bars = market.ohlcv_15m if len(market.ohlcv_15m) >= self._cfg.adx_period + 1 else market.ohlcv_5m
        adx = _adx(bars, self._cfg.adx_period)
        atr = _atr(bars, self._cfg.atr_period)
        atr_baseline = _atr(bars, self._cfg.atr_baseline_period) if self._cfg.atr_baseline_period > 0 else atr
        slope_bps = _slope_bps(bars, self._cfg.ema_fast, self._cfg.ema_slow)
        chaos, chaos_reasons = _detect_chaos(
            market,
            atr,
            atr_baseline,
            slope_bps,
            self._cfg,
        )
        signals = RegimeSignals(
            adx=adx,
            atr=atr,
            atr_baseline=atr_baseline,
            slope_bps=slope_bps,
            chaos=chaos,
            chaos_reasons=chaos_reasons,
        )
        if chaos:
            return RegimeDecision(candidate="CHAOS", signals=signals)
        if adx is not None and adx >= self._cfg.trend_adx_enter and slope_bps is not None:
            if slope_bps >= self._cfg.slope_bps_enter:
                return RegimeDecision(candidate="TREND_UP", signals=signals)
            if slope_bps <= -self._cfg.slope_bps_enter:
                return RegimeDecision(candidate="TREND_DOWN", signals=signals)
        return RegimeDecision(candidate="RANGE", signals=signals)


class RegimeHysteresis:
    def __init__(self, cfg: RegimeDetectorConfig) -> None:
        self._cfg = cfg
        self._current = "RANGE"
        self._last_change_ms: Optional[int] = None
        self._pending: Optional[str] = None
        self._pending_count = 0

    def current(self) -> str:
        return self._current

    def update(self, candidate: str, now_ms: int) -> Tuple[str, bool]:
        changed = False
        if self._last_change_ms is None:
            self._last_change_ms = now_ms
        hold_ms = self._cfg.min_regime_hold_s * 1000
        if candidate == self._current:
            self._pending = None
            self._pending_count = 0
            return self._current, changed
        if now_ms - self._last_change_ms < hold_ms:
            return self._current, changed
        if self._pending != candidate:
            self._pending = candidate
            self._pending_count = 1
        else:
            self._pending_count += 1
        if self._pending_count >= max(self._cfg.confirm_cycles, 1):
            self._current = candidate
            self._pending = None
            self._pending_count = 0
            self._last_change_ms = now_ms
            changed = True
        return self._current, changed


def _atr(bars: Sequence[OhlcvBar], period: int) -> Optional[float]:
    if period <= 0 or len(bars) < period + 1:
        return None
    trs: List[float] = []
    for idx in range(1, len(bars)):
        high = bars[idx].high
        low = bars[idx].low
        prev_close = bars[idx - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    window = trs[-period:]
    return sum(window) / float(period)


def _adx(bars: Sequence[OhlcvBar], period: int) -> Optional[float]:
    if period <= 0 or len(bars) < period + 1:
        return None
    trs: List[float] = []
    dm_plus: List[float] = []
    dm_minus: List[float] = []
    for idx in range(1, len(bars)):
        up_move = bars[idx].high - bars[idx - 1].high
        down_move = bars[idx - 1].low - bars[idx].low
        dm_plus.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        dm_minus.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        high = bars[idx].high
        low = bars[idx].low
        prev_close = bars[idx - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    dx_values: List[float] = []
    for idx in range(period, len(trs) + 1):
        tr_n = sum(trs[idx - period : idx])
        if tr_n == 0:
            continue
        dm_plus_n = sum(dm_plus[idx - period : idx])
        dm_minus_n = sum(dm_minus[idx - period : idx])
        di_plus = 100.0 * dm_plus_n / tr_n
        di_minus = 100.0 * dm_minus_n / tr_n
        denom = di_plus + di_minus
        if denom == 0:
            continue
        dx = 100.0 * abs(di_plus - di_minus) / denom
        dx_values.append(dx)
    if len(dx_values) < period:
        return None
    window = dx_values[-period:]
    return sum(window) / float(period)


def _ema(values: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or not values:
        return None
    alpha = 2.0 / (period + 1.0)
    ema_val = values[0]
    for value in values[1:]:
        ema_val = alpha * value + (1.0 - alpha) * ema_val
    return ema_val


def _slope_bps(bars: Sequence[OhlcvBar], fast: int, slow: int) -> Optional[float]:
    if not bars:
        return None
    closes = [bar.close for bar in bars]
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if ema_fast is None or ema_slow is None or closes[-1] == 0:
        return None
    return (ema_fast - ema_slow) / closes[-1] * 10000.0


def _detect_chaos(
    market: MarketSnapshot,
    atr: Optional[float],
    atr_baseline: Optional[float],
    slope_bps: Optional[float],
    cfg: RegimeDetectorConfig,
) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    if atr is not None and atr_baseline is not None and atr_baseline > 0:
        if atr / atr_baseline >= cfg.chaos_atr_mult:
            reasons.append("ATR_SPIKE")
    liq_intensity = market.liq.intensity_above + market.liq.intensity_below
    if liq_intensity >= cfg.chaos_liq_intensity:
        reasons.append("LIQ_INTENSITY")
    if market.mark_price and market.band_2u and market.band_2l and slope_bps is not None:
        if market.mark_price > market.band_2u:
            dist_bps = (market.mark_price - market.band_2u) / market.mark_price * 10000.0
            if dist_bps >= cfg.chaos_band_bps and slope_bps > cfg.slope_bps_enter:
                reasons.append("BAND_BREAKOUT")
        if market.mark_price < market.band_2l:
            dist_bps = (market.band_2l - market.mark_price) / market.mark_price * 10000.0
            if dist_bps >= cfg.chaos_band_bps and slope_bps < -cfg.slope_bps_enter:
                reasons.append("BAND_BREAKDOWN")
    return bool(reasons), reasons
