"""Unified parameterized MarketContextSerializer for QuantumEdge Hybrid (Phase 2).

Consolidates all 4 serialization modes (tick_delta, delta_bps, multiscale,
hybrid_webp) and RAM-only WebP chart rendering (io.BytesIO, zero disk writes).
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, Sequence

import ccxt
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
from PIL import Image

from quantum_edge_ml.model_router.backends.transport import ImageAttachment

SerializationMode = Literal["tick_delta", "delta_bps", "multiscale", "hybrid_webp"]

TF_STEP_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

STRATEGIC_TFS: tuple[str, ...] = ("1d", "4h")
TACTICAL_TFS: tuple[str, ...] = ("1h", "15m", "5m")
DEFAULT_ALL_TFS: tuple[str, ...] = ("1d", "4h", "1h", "15m", "5m")
LEGACY_STRATEGIC_TFS: tuple[str, ...] = ("4h", "1h")
LEGACY_TACTICAL_TFS: tuple[str, ...] = ("15m", "5m", "1m")
LEGACY_ALL_TFS: tuple[str, ...] = ("4h", "1h", "15m", "5m", "1m")


def resolve_tf_hierarchy(
    ohlcv_by_tf: Mapping[str, Any] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (strategic_tfs, tactical_tfs) favoring 1d/4h + 1h/15m/5m with 1m legacy fallback."""
    if isinstance(ohlcv_by_tf, Mapping):
        has_1d = bool(ohlcv_by_tf.get("1d"))
        has_1m = bool(ohlcv_by_tf.get("1m"))
        if not has_1d and has_1m:
            return LEGACY_STRATEGIC_TFS, LEGACY_TACTICAL_TFS
    return STRATEGIC_TFS, TACTICAL_TFS


def _compute_rsi_wilder(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Strictly causal Wilder RSI(14) in [0, 100]."""
    n = len(closes)
    if n < 2:
        return np.full(n, 50.0, dtype=np.float64)
    deltas = np.diff(closes)
    gains = np.maximum(deltas, 0.0)
    losses = np.maximum(-deltas, 0.0)
    alpha = 1.0 / float(max(1, period))
    avg_gain = pd.Series(gains).ewm(alpha=alpha, adjust=False).mean().values
    avg_loss = pd.Series(losses).ewm(alpha=alpha, adjust=False).mean().values
    rsi_tail = np.empty(len(deltas), dtype=np.float64)
    for i in range(len(deltas)):
        ag, al = float(avg_gain[i]), float(avg_loss[i])
        if al <= 1e-12 and ag <= 1e-12:
            rsi_tail[i] = 50.0
        elif al <= 1e-12:
            rsi_tail[i] = 100.0
        else:
            rs = ag / al
            rsi_tail[i] = 100.0 - 100.0 / (1.0 + rs)
    return np.clip(np.concatenate([[50.0], rsi_tail]), 0.0, 100.0)


def _compute_macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Strictly causal MACD(12, 26, 9) returning (macd_line, signal_line, histogram)."""
    s = pd.Series(closes, dtype=np.float64)
    ema_fast = s.ewm(span=fast, adjust=False).mean().values
    ema_slow = s.ewm(span=slow, adjust=False).mean().values
    macd_line = ema_fast - ema_slow
    sig_line = pd.Series(macd_line).ewm(span=signal, adjust=False).mean().values
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def _compute_bollinger(
    closes: np.ndarray,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float]:
    """Strictly causal Bollinger Bands(20, 2.0) returning (bb_pos %B, bb_bw_bps)."""
    s = pd.Series(closes, dtype=np.float64)
    mid = float(s.rolling(period, min_periods=1).mean().iloc[-1])
    std = float(s.rolling(period, min_periods=1).std(ddof=0).fillna(0.0).iloc[-1])
    upper = mid + num_std * std
    lower = mid - num_std * std
    bw = upper - lower
    bb_pos = (float(closes[-1]) - lower) / bw if bw > 1e-12 else 0.5
    bb_bw_bps = bw / max(abs(mid), 1e-12) * 10_000.0
    return float(bb_pos), float(max(0.0, bb_bw_bps))


def _compute_adx_wilder(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> tuple[float, float]:
    """Strictly causal Wilder ADX(14) returning (adx14, di_diff = +DI14 - -DI14)."""
    n = len(closes)
    if n < 3:
        return 0.0, 0.0
    prev_c = np.concatenate([[closes[0]], closes[:-1]])
    prev_h = np.concatenate([[highs[0]], highs[:-1]])
    prev_l = np.concatenate([[lows[0]], lows[:-1]])
    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)),
    )
    up_move = highs - prev_h
    dn_move = prev_l - lows
    plus_dm = np.where((up_move > dn_move) & (up_move > 0.0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0.0), dn_move, 0.0)

    alpha = 1.0 / float(max(1, period))
    tr_rma = pd.Series(tr).ewm(alpha=alpha, adjust=False).mean().values
    p_rma = pd.Series(plus_dm).ewm(alpha=alpha, adjust=False).mean().values
    m_rma = pd.Series(minus_dm).ewm(alpha=alpha, adjust=False).mean().values

    plus_di = np.divide(100.0 * p_rma, tr_rma, out=np.zeros_like(tr_rma), where=tr_rma > 1e-12)
    minus_di = np.divide(100.0 * m_rma, tr_rma, out=np.zeros_like(tr_rma), where=tr_rma > 1e-12)
    di_sum = plus_di + minus_di
    dx = np.divide(100.0 * np.abs(plus_di - minus_di), di_sum, out=np.zeros_like(di_sum), where=di_sum > 1e-12)
    adx_series = pd.Series(dx).ewm(alpha=alpha, adjust=False).mean().values
    return float(np.clip(adx_series[-1], 0.0, 100.0)), float(plus_di[-1] - minus_di[-1])


def _detect_causal_divergence(
    highs: np.ndarray,
    lows: np.ndarray,
    indicator: np.ndarray,
    lookback: int = 40,
    pivot_window: int = 2,
) -> Literal["BULL", "BEAR", "NONE"]:
    """Detect BULL/BEAR divergence strictly from confirmed past pivots (zero look-ahead)."""
    n_total = min(len(highs), len(lows), len(indicator))
    if n_total < 2 * pivot_window + 3:
        return "NONE"
    h = highs[-lookback:]
    l = lows[-lookback:]
    ind = indicator[-lookback:]
    n = len(h)
    if n < 2 * pivot_window + 3:
        return "NONE"

    pivot_lows: list[int] = []
    pivot_highs: list[int] = []
    for i in range(pivot_window, n - pivot_window):
        win_l = l[i - pivot_window : i + pivot_window + 1]
        if l[i] <= float(np.min(win_l)) and (l[i] < l[i - 1] or l[i] < l[i + 1]):
            pivot_lows.append(i)
        win_h = h[i - pivot_window : i + pivot_window + 1]
        if h[i] >= float(np.max(win_h)) and (h[i] > h[i - 1] or h[i] > h[i + 1]):
            pivot_highs.append(i)

    bull_idx = -1
    if len(pivot_lows) >= 2:
        p1, p2 = pivot_lows[-2], pivot_lows[-1]
        if l[p2] < l[p1] and ind[p2] > ind[p1]:
            bull_idx = p2

    bear_idx = -1
    if len(pivot_highs) >= 2:
        q1, q2 = pivot_highs[-2], pivot_highs[-1]
        if h[q2] > h[q1] and ind[q2] < ind[q1]:
            bear_idx = q2

    if bull_idx == -1 and bear_idx == -1:
        return "NONE"
    if bull_idx > bear_idx:
        return "BULL"
    if bear_idx > bull_idx:
        return "BEAR"
    return "NONE"

_CACHED_MPF_STYLE = mpf.make_mpf_style(base_mpf_style="yahoo")

# Baseline BingX VST steps (refined dynamically via from_ccxt_markets or bar precision)
DEFAULT_INSTRUMENT_SPECS: dict[str, tuple[Decimal, Decimal]] = {
    "BTCUSDT": (Decimal("0.1"), Decimal("0.001")),
    "ETHUSDT": (Decimal("0.01"), Decimal("0.01")),
    "SOLUSDT": (Decimal("0.001"), Decimal("0.1")),
    "XRPUSDT": (Decimal("0.0001"), Decimal("1")),
    "BNBUSDT": (Decimal("0.01"), Decimal("0.01")),
    "LINKUSDT": (Decimal("0.001"), Decimal("0.1")),
    "SUIUSDT": (Decimal("0.0001"), Decimal("1")),
}


@dataclass(slots=True)
class SerializedMarketPayload:
    """Serialized asset payload paired with optional RAM-rendered WebP attachments."""

    mode: SerializationMode
    text: str
    images: list[ImageAttachment] = field(default_factory=list)
    context_hash: str = ""
    raw_refs: dict[str, str] = field(default_factory=dict)


def _extract_bar_fields(bar: Any) -> dict[str, Any]:
    """Normalize dict ({ts|timestamp, open, high, low, close, volume}) or 6-item list."""
    if isinstance(bar, Mapping):
        ts_val = bar.get("ts") if bar.get("ts") is not None else bar.get("timestamp")
        if ts_val is None:
            raise ValueError(f"OHLCV bar missing 'ts'/'timestamp': {bar!r}")
        return {
            "ts": int(ts_val),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar.get("volume", 0),
        }
    if isinstance(bar, (list, tuple)) and len(bar) >= 6:
        return {
            "ts": int(bar[0]),
            "open": bar[1],
            "high": bar[2],
            "low": bar[3],
            "close": bar[4],
            "volume": bar[5],
        }
    raise ValueError(f"Unsupported OHLCV bar format: {type(bar).__name__}")


def _normalize_bars(bars: Sequence[Any]) -> list[dict[str, Any]]:
    normalized = [_extract_bar_fields(b) for b in (bars or ())]
    normalized.sort(key=lambda b: int(b["ts"]))
    return normalized


class MarketContextSerializer:
    """Single parameterized market context serializer for QuantumEdge Hybrid."""

    def __init__(
        self,
        instrument_specs: Mapping[str, tuple[Decimal, Decimal]] | None = None,
    ) -> None:
        self.specs: dict[str, tuple[Decimal, Decimal]] = dict(DEFAULT_INSTRUMENT_SPECS)
        if instrument_specs:
            self.specs.update({k.upper(): v for k, v in instrument_specs.items()})

    @classmethod
    def from_ccxt_markets(
        cls,
        markets: Mapping[str, Mapping[str, Any]],
        *,
        is_tick_size_mode: bool = True,
    ) -> MarketContextSerializer:
        """Extract exact (tick_size, qty_step) from CCXT markets mapping."""
        specs: dict[str, tuple[Decimal, Decimal]] = dict(DEFAULT_INSTRUMENT_SPECS)
        for m in markets.values():
            if not isinstance(m, Mapping):
                continue
            raw_sym = (
                str(m.get("symbol") or m.get("id") or "")
                .split(":")[0]
                .replace("/", "")
                .replace("-", "")
                .upper()
            )
            prec = m.get("precision") or {}
            p_raw, a_raw = prec.get("price"), prec.get("amount")
            if raw_sym and p_raw is not None and a_raw is not None:
                if is_tick_size_mode:
                    specs[raw_sym] = (Decimal(str(p_raw)), Decimal(str(a_raw)))
                else:
                    specs[raw_sym] = (
                        Decimal("10") ** (-int(p_raw)),
                        Decimal("10") ** (-int(a_raw)),
                    )
        return cls(instrument_specs=specs)

    def _get_spec(self, symbol: str) -> tuple[Decimal, Decimal]:
        return self.specs.get(symbol.upper(), (Decimal("0.0001"), Decimal("0.001")))

    # ------------------------------------------------------------------
    # 1. Exact Decimal Integer Tick-Delta (OHLCV_TICK_V1) Encode / Decode
    # ------------------------------------------------------------------
    @staticmethod
    def _to_exact_int_units(value: Any, step: Decimal) -> int:
        try:
            d_val = value if isinstance(value, Decimal) else Decimal(str(value))
            return int((d_val / step).to_integral_exact())
        except (InvalidOperation, ArithmeticError) as exc:
            raise ValueError(
                f"Value {value!r} is not an exact multiple of step {step} — exact tick_delta impossible."
            ) from exc

    @staticmethod
    def _refine_decimal_step(
        values: Sequence[Any],
        base_step: Decimal,
    ) -> Decimal:
        """Refine step to finest decimal scale in values when auto-selecting steps."""
        min_exp = base_step.normalize().as_tuple().exponent
        needs_refine = False
        for v in values:
            d = v if isinstance(v, Decimal) else Decimal(str(v))
            try:
                (d / base_step).to_integral_exact()
            except (InvalidOperation, ArithmeticError):
                needs_refine = True
                exp = d.normalize().as_tuple().exponent
                if isinstance(exp, int) and exp < min_exp:
                    min_exp = exp
        if not needs_refine:
            return base_step
        refined = Decimal("10") ** int(min_exp)
        return refined if refined < base_step else base_step

    def encode_ohlcv_tick_delta(
        self,
        symbol: str,
        tf: str,
        bars: Sequence[Any],
        *,
        tick_size: Decimal | None = None,
        qty_step: Decimal | None = None,
    ) -> str:
        if not bars:
            return f"OHLCV_TICK_V1|{symbol.upper()}|{tf}|EMPTY"
        sorted_bars = _normalize_bars(bars)
        default_t, default_q = self._get_spec(symbol)

        if tick_size is not None:
            t_sz = tick_size
        else:
            price_vals = [b[k] for b in sorted_bars for k in ("open", "high", "low", "close")]
            t_sz = self._refine_decimal_step(price_vals, default_t)

        if qty_step is not None:
            q_st = qty_step
        else:
            vol_vals = [b["volume"] for b in sorted_bars]
            q_st = self._refine_decimal_step(vol_vals, default_q)

        step_ms = TF_STEP_MS.get(tf, 60_000)
        t0 = int(sorted_bars[0]["ts"])
        lines = [
            f"OHLCV_TICK_V1|{symbol.upper()}|{tf}|{t_sz}|{q_st}|{t0}|{step_ms}",
            "dt?,do,dc,uw,lw,vq",
        ]
        prev_c: int | None = None
        for idx, b in enumerate(sorted_bars):
            ts = int(b["ts"])
            o_t = self._to_exact_int_units(b["open"], t_sz)
            h_t = self._to_exact_int_units(b["high"], t_sz)
            l_t = self._to_exact_int_units(b["low"], t_sz)
            c_t = self._to_exact_int_units(b["close"], t_sz)
            v_q = self._to_exact_int_units(b["volume"], q_st)
            dt_off = ts - (t0 + idx * step_ms)
            do = o_t if prev_c is None else (o_t - prev_c)
            dc = c_t - o_t
            uw = h_t - max(o_t, c_t)
            lw = min(o_t, c_t) - l_t
            if uw < 0 or lw < 0:
                raise ValueError(f"Invalid OHLC bar (uw={uw}, lw={lw}) at ts={ts}")
            lines.append(
                f"{do},{dc},{uw},{lw},{v_q}"
                if dt_off == 0
                else f"@{dt_off},{do},{dc},{uw},{lw},{v_q}"
            )
            prev_c = c_t
        return "\n".join(lines)

    @staticmethod
    def decode_ohlcv_tick_delta(payload: str) -> dict[str, Any]:
        lines = [ln.strip() for ln in payload.strip().splitlines() if ln.strip()]
        header = lines[0].split("|")
        if header[0] != "OHLCV_TICK_V1":
            raise ValueError(f"Unknown schema: {header[0]}")
        if len(header) == 4 and header[3] == "EMPTY":
            return {"symbol": header[1], "tf": header[2], "bars": []}
        symbol, tf = header[1], header[2]
        t_sz, q_st = Decimal(header[3]), Decimal(header[4])
        t0, step_ms = int(header[5]), int(header[6])
        bars: list[dict[str, Any]] = []
        prev_c: int | None = None
        for idx, row in enumerate(lines[2:]):
            parts = row.split(",")
            if parts[0].startswith("@"):
                dt_off = int(parts[0][1:])
                do, dc, uw, lw, v_q = (int(x) for x in parts[1:6])
            else:
                dt_off = 0
                do, dc, uw, lw, v_q = (int(x) for x in parts[0:5])
            ts = t0 + idx * step_ms + dt_off
            o_t = do if prev_c is None else (prev_c + do)
            c_t = o_t + dc
            h_t = max(o_t, c_t) + uw
            l_t = min(o_t, c_t) - lw
            prev_c = c_t
            bars.append(
                {
                    "ts": ts,
                    "open": o_t * t_sz,
                    "high": h_t * t_sz,
                    "low": l_t * t_sz,
                    "close": c_t * t_sz,
                    "volume": v_q * q_st,
                }
            )
        return {"symbol": symbol, "tf": tf, "tick_size": t_sz, "qty_step": q_st, "bars": bars}

    # ------------------------------------------------------------------
    # 2. Error-Feedback Delta-BPS (DELTA_BPS_V1) Encode / Decode
    # ------------------------------------------------------------------
    @staticmethod
    def encode_ohlcv_delta_bps(
        symbol: str,
        tf: str,
        bars: Sequence[Any],
        *,
        tail: int | None = None,
    ) -> str:
        if not bars:
            return f"DELTA_BPS_V1|{symbol.upper()}|{tf}|EMPTY"
        sorted_bars = _normalize_bars(bars)
        vols = [float(b.get("volume", 0.0)) for b in sorted_bars]
        v_med = float(np.median(vols)) if vols else 1.0
        v_med = v_med if v_med > 1e-12 else 1.0
        sliced = sorted_bars[-tail:] if (tail is not None and len(sorted_bars) > tail) else sorted_bars
        t0 = int(sliced[0]["ts"])
        step_ms = TF_STEP_MS.get(tf, 60_000)
        p0 = float(sliced[0]["open"])
        if p0 <= 0:
            raise ValueError(f"Non-positive anchor price p0={p0} for {symbol} {tf}")
        lines = [
            f"DELTA_BPS_V1|{symbol.upper()}|{tf}|p0={p0:.10g}|vmed={v_med:.8g}|t0={t0}|step={step_ms}",
            "dc,uw,lw,vr",
        ]
        rec_c = p0
        for b in sliced:
            c_f, h_f, l_f, v_f = (
                float(b["close"]),
                float(b["high"]),
                float(b["low"]),
                float(b["volume"]),
            )
            dc = int(round((c_f - rec_c) / rec_c * 10_000.0))
            new_c = rec_c * (1.0 + dc / 10_000.0)
            b_hi, b_lo = max(rec_c, new_c), min(rec_c, new_c)
            uw = max(0, int(round((h_f - b_hi) / b_hi * 10_000.0)))
            lw = max(0, int(round((b_lo - l_f) / b_lo * 10_000.0)))
            vr = max(0, int(round(10.0 * v_f / v_med)))
            lines.append(f"{dc},{uw},{lw},{vr}")
            rec_c = new_c
        return "\n".join(lines)

    @staticmethod
    def decode_ohlcv_delta_bps(payload: str) -> dict[str, Any]:
        lines = [ln.strip() for ln in payload.strip().splitlines() if ln.strip()]
        header = lines[0].split("|")
        if header[0] != "DELTA_BPS_V1":
            raise ValueError(f"Unknown schema: {header[0]}")
        if len(header) == 4 and header[3] == "EMPTY":
            return {"symbol": header[1], "tf": header[2], "bars": []}
        meta = dict(kv.split("=", 1) for kv in header[3:])
        p0, v_med = float(meta["p0"]), float(meta["vmed"])
        t0, step_ms = int(meta["t0"]), int(meta["step"])
        bars: list[dict[str, Any]] = []
        rec_c = p0
        for idx, row in enumerate(lines[2:]):
            dc, uw, lw, vr = (int(x) for x in row.split(","))
            o_f = rec_c
            c_f = o_f * (1.0 + dc / 10_000.0)
            b_hi, b_lo = max(o_f, c_f), min(o_f, c_f)
            h_f = b_hi * (1.0 + uw / 10_000.0)
            l_f = b_lo * (1.0 - lw / 10_000.0)
            v_f = (vr / 10.0) * v_med
            bars.append(
                {
                    "ts": t0 + idx * step_ms,
                    "open": o_f,
                    "high": h_f,
                    "low": l_f,
                    "close": c_f,
                    "volume": v_f,
                }
            )
            rec_c = c_f
        return {"symbol": header[1], "tf": header[2], "bars": bars}

    # ------------------------------------------------------------------
    # 3. Structural 100-Bar Profile + Lossless LiqMap & L2 CSV
    # ------------------------------------------------------------------
    @staticmethod
    def build_structural_profile(tf: str, bars: Sequence[Any]) -> dict[str, Any]:
        if not bars:
            return {"tf": tf, "n": 0}
        s = _normalize_bars(bars)
        closes = np.array([float(b["close"]) for b in s], dtype=np.float64)
        highs = np.array([float(b["high"]) for b in s], dtype=np.float64)
        lows = np.array([float(b["low"]) for b in s], dtype=np.float64)
        vols = np.array([float(b["volume"]) for b in s], dtype=np.float64)
        typ = (highs + lows + closes) / 3.0
        vol_sum = float(np.sum(vols))
        vwap = float(np.sum(typ * vols) / vol_sum) if vol_sum > 1e-12 else float(np.mean(typ))
        prev_c = np.concatenate([[closes[0]], closes[:-1]])
        tr = np.maximum(
            highs - lows,
            np.maximum(np.abs(highs - prev_c), np.abs(lows - prev_c)),
        )
        atr14 = float(np.mean(tr[-14:]))
        ema20 = float(pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(pd.Series(closes).ewm(span=50, adjust=False).mean().iloc[-1])
        last_c = float(closes[-1])

        rsi_series = _compute_rsi_wilder(closes, period=14)
        macd_line, _, macd_hist = _compute_macd(closes, fast=12, slow=26, signal=9)
        bb_pos, bb_bw_bps = _compute_bollinger(closes, period=20, num_std=2.0)
        adx14, di_diff = _compute_adx_wilder(highs, lows, closes, period=14)
        rsi_div = _detect_causal_divergence(highs, lows, rsi_series, lookback=40, pivot_window=2)
        macd_div = _detect_causal_divergence(highs, lows, macd_line, lookback=40, pivot_window=2)

        h_curr = float(macd_hist[-1])
        h_prev = float(macd_hist[-2]) if len(macd_hist) >= 2 else h_curr
        if h_curr > 0.0 and (h_curr >= h_prev or h_prev <= 0.0):
            macd_cross = "BULL"
        elif h_curr < 0.0 and (h_curr <= h_prev or h_prev >= 0.0):
            macd_cross = "BEAR"
        else:
            macd_cross = "FLAT"

        return {
            "tf": tf,
            "n": len(s),
            "hi": round(float(np.max(highs)), 6),
            "lo": round(float(np.min(lows)), 6),
            "last": round(last_c, 6),
            "vwap": round(vwap, 6),
            "ema20_bps": round((last_c - ema20) / max(ema20, 1e-12) * 10_000.0, 1),
            "ema50_bps": round((last_c - ema50) / max(ema50, 1e-12) * 10_000.0, 1),
            "atr14_bps": round(atr14 / max(last_c, 1e-12) * 10_000.0, 1),
            "ret_full_bps": round(
                (last_c - float(closes[0])) / max(float(closes[0]), 1e-12) * 10_000.0, 1
            ),
            "rsi14": round(float(rsi_series[-1]), 1),
            "macd_hist_bps": round(h_curr / max(abs(last_c), 1e-12) * 10_000.0, 2),
            "macd_cross": macd_cross,
            "bb_pos": round(bb_pos, 3),
            "bb_bw_bps": round(bb_bw_bps, 1),
            "adx14": round(adx14, 1),
            "di_diff": round(di_diff, 1),
            "rsi_div": rsi_div,
            "macd_div": macd_div,
        }

    @staticmethod
    def encode_structural_profile_wire(prof: Mapping[str, Any]) -> str:
        """Render structural profile + causal indicators compactly to save ~40% prompt tokens."""
        base_keys = (
            "tf",
            "n",
            "hi",
            "lo",
            "last",
            "vwap",
            "ema20_bps",
            "ema50_bps",
            "atr14_bps",
            "ret_full_bps",
        )
        base_obj = {k: prof[k] for k in base_keys if k in prof}
        base_json = json.dumps(base_obj, separators=(",", ":"), sort_keys=True)
        if "rsi14" not in prof:
            return base_json
        return (
            f"{base_json}|ind="
            f"rsi={prof['rsi14']},mh={prof['macd_hist_bps']},mc={prof['macd_cross']},"
            f"bb={prof['bb_pos']},bw={prof['bb_bw_bps']},"
            f"adx={prof['adx14']},di={prof['di_diff']},"
            f"div={prof['rsi_div']}/{prof['macd_div']}"
        )

    @staticmethod
    def encode_liqmap_csv(liqmap: Mapping[str, Any]) -> str:
        if liqmap.get("status") != "OK":
            return f"LIQMAP|STATUS={liqmap.get('status', 'UNAVAILABLE')}"
        lines = [
            f"LIQMAP|{liqmap.get('symbol')}|tf={liqmap.get('timeframe')}|regime={liqmap.get('regime')}|last={liqmap.get('last_price')}",
            "side,price,dist_pct,intensity,conf",
        ]
        for z in liqmap.get("zones") or []:
            side_code = "L" if z.get("side") == "LONG_LIQ" else "S"
            conf = "" if z.get("confidence") is None else f"{float(z['confidence']):.3f}"
            lines.append(
                f"{side_code},{z.get('price')},{z.get('dist_pct')},{round(float(z.get('intensity', 0.0)), 3)},{conf}"
            )
        return "\n".join(lines)

    @staticmethod
    def encode_orderbook_csv(orderbook: Mapping[str, Any], top_n: int = 15) -> str:
        bids_raw = (orderbook.get("bids") or [])[:top_n]
        asks_raw = (orderbook.get("asks") or [])[:top_n]

        def _fmt_lvl(lvl: Any) -> str:
            if isinstance(lvl, Mapping):
                return f"{lvl.get('price')},{lvl.get('qty', lvl.get('amount', 0))}"
            return f"{lvl[0]},{lvl[1]}"

        lines = [f"L2_TOP{top_n}|bid_p,bid_q|ask_p,ask_q"]
        for i in range(max(len(bids_raw), len(asks_raw))):
            b_str = _fmt_lvl(bids_raw[i]) if i < len(bids_raw) else ","
            a_str = _fmt_lvl(asks_raw[i]) if i < len(asks_raw) else ","
            lines.append(f"{b_str}|{a_str}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 4. In-Memory mplfinance -> WebP Renderer (2x2 medium + 5-TF high)
    # ------------------------------------------------------------------
    @staticmethod
    def _bars_to_df(bars: Sequence[Any], tail: int = 60) -> pd.DataFrame:
        s = _normalize_bars(bars)[-tail:]
        df = pd.DataFrame(s)
        df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.set_index("dt")[["open", "high", "low", "close", "volume"]].astype(float)

    def render_screening_grid_webp(
        self,
        contexts_by_symbol: Mapping[str, Mapping[str, Any]],
        tf: str = "15m",
        tail: int = 48,
    ) -> ImageAttachment:
        fig, axes = plt.subplots(2, 2, figsize=(8.8, 5.0), dpi=90, facecolor="white")
        try:
            for ax, sym in zip(axes.flat, sorted(contexts_by_symbol.keys())[:4]):
                ctx = contexts_by_symbol[sym]
                bars = (ctx.get("ohlcv_by_tf") or {}).get(tf) or []
                if len(bars) >= 5:
                    mpf.plot(
                        self._bars_to_df(bars, tail=tail),
                        type="candle",
                        ax=ax,
                        style=_CACHED_MPF_STYLE,
                        volume=False,
                        show_nontrading=False,
                    )
                    liq = (ctx.get("microstructure_and_liquidity") or {}).get("liqmap") or {}
                    for key, color in (
                        ("nearest_short_liq", "#D93025"),
                        ("nearest_long_liq", "#1A73E8"),
                    ):
                        z = liq.get(key)
                        if isinstance(z, Mapping) and z.get("price"):
                            ax.axhline(
                                float(z["price"]),
                                color=color,
                                linestyle="--",
                                linewidth=1.0,
                                alpha=0.85,
                            )
                snap = ctx.get("market_snapshot") or {}
                ax.set_title(
                    f"{sym} ({tf}) | p={snap.get('last_price')} | spr={snap.get('spread_bps')}bps",
                    fontsize=9,
                    pad=4,
                )
                ax.tick_params(labelsize=7)
            fig.tight_layout(pad=0.6)
            webp_bytes = self._fig_to_webp_bytes(fig, quality=80)
        finally:
            plt.close(fig)
        return ImageAttachment(data=webp_bytes, mime_type="image/webp", resolution="medium")

    def render_candidate_5tf_webp(
        self,
        symbol: str,
        ctx: Mapping[str, Any],
        tfs: Sequence[str] | None = None,
    ) -> ImageAttachment:
        if tfs is None:
            strat_tfs, tact_tfs = resolve_tf_hierarchy(ctx.get("ohlcv_by_tf"))
            tfs = (*strat_tfs, *tact_tfs)
        fig, axes = plt.subplots(3, 2, figsize=(9.2, 6.4), dpi=90, facecolor="white")
        try:
            flat = list(axes.flat)
            liq = (ctx.get("microstructure_and_liquidity") or {}).get("liqmap") or {}
            top_zones = sorted(
                liq.get("zones") or [],
                key=lambda z: float(z.get("intensity", 0.0)),
                reverse=True,
            )[:6]
            for idx, tf in enumerate(tfs):
                ax = flat[idx]
                bars = (ctx.get("ohlcv_by_tf") or {}).get(tf) or []
                if len(bars) >= 5:
                    mpf.plot(
                        self._bars_to_df(bars, tail=48),
                        type="candle",
                        ax=ax,
                        style=_CACHED_MPF_STYLE,
                        volume=False,
                        show_nontrading=False,
                    )
                    for z in top_zones:
                        col = "#D93025" if z.get("side") == "SHORT_LIQ" else "#1A73E8"
                        ax.axhline(
                            float(z["price"]),
                            color=col,
                            linestyle=":",
                            linewidth=0.9,
                            alpha=0.75,
                        )
                ax.set_title(f"{symbol} — {tf}", fontsize=9, pad=3)
                ax.tick_params(labelsize=7)
            ax_liq = flat[5]
            if top_zones:
                prices = [float(z["price"]) for z in top_zones]
                intens = [float(z["intensity"]) for z in top_zones]
                cols = [
                    "#D93025" if z.get("side") == "SHORT_LIQ" else "#1A73E8"
                    for z in top_zones
                ]
                ax_liq.barh([f"{p:.2f}" for p in prices], intens, color=cols)
            ax_liq.set_title(f"{symbol} Top LiqMap Clusters", fontsize=9)
            ax_liq.tick_params(labelsize=7)
            fig.tight_layout(pad=0.6)
            webp_bytes = self._fig_to_webp_bytes(fig, quality=82)
        finally:
            plt.close(fig)
        return ImageAttachment(data=webp_bytes, mime_type="image/webp", resolution="high")

    @staticmethod
    def _fig_to_webp_bytes(fig: plt.Figure, quality: int = 82) -> bytes:
        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba())
        img = Image.fromarray(rgba, mode="RGBA").convert("RGB")
        out_buf = io.BytesIO()
        img.save(out_buf, format="WEBP", quality=quality, method=1)
        return out_buf.getvalue()

    # ------------------------------------------------------------------
    # 5. Single Entry Point for Asset Serialization
    # ------------------------------------------------------------------
    def serialize_single_asset(
        self,
        ctx: Mapping[str, Any],
        *,
        mode: SerializationMode = "hybrid_webp",
        ohlcv_tail: int = 25,
        include_full_liqmap: bool = True,
    ) -> SerializedMarketPayload:
        snap = ctx.get("market_snapshot") or {}
        symbol = str(snap.get("symbol") or "UNKNOWN").upper()
        micro = ctx.get("microstructure_and_liquidity") or {}
        ohlcv = ctx.get("ohlcv_by_tf") or {}
        sections: list[str] = []
        raw_refs: dict[str, str] = {}
        images: list[ImageAttachment] = []

        header_obj = {
            "sym": symbol,
            "bid": snap.get("bid"),
            "ask": snap.get("ask"),
            "last": snap.get("last_price"),
            "spr_bps": snap.get("spread_bps"),
            "stale": snap.get("is_stale"),
            "deriv": ctx.get("derivatives"),
            "of": (micro.get("orderflow") or {}).get("metrics", {}),
            "comp": {
                "sig": (micro.get("composite_signal") or {}).get("signal"),
                "score": (micro.get("composite_signal") or {}).get("score"),
                "c": (micro.get("composite_signal") or {}).get("components"),
            },
        }
        sections.append("META:" + json.dumps(header_obj, separators=(",", ":"), sort_keys=True))

        if include_full_liqmap and "liqmap" in micro:
            sections.append(self.encode_liqmap_csv(micro["liqmap"]))
        if "orderbook" in ctx:
            sections.append(self.encode_orderbook_csv(ctx["orderbook"], top_n=15))

        strat_tfs, tact_tfs = resolve_tf_hierarchy(ohlcv)
        for tf in (*strat_tfs, *tact_tfs):
            bars = ohlcv.get(tf) or []
            if not bars:
                continue
            ref_hash = hashlib.sha256(
                json.dumps(bars, separators=(",", ":"), sort_keys=True).encode("utf-8")
            ).hexdigest()[:12]
            raw_refs[f"{symbol}:{tf}"] = ref_hash
            if mode == "tick_delta":
                sections.append(self.encode_ohlcv_tick_delta(symbol, tf, bars))
            elif mode == "delta_bps":
                sections.append(self.encode_ohlcv_delta_bps(symbol, tf, bars))
            elif mode == "multiscale":
                prof = self.build_structural_profile(tf, bars)
                sections.append(
                    f"PROF_{tf}:"
                    + self.encode_structural_profile_wire(prof)
                    + f"|ref={ref_hash}"
                )
                sections.append(self.encode_ohlcv_delta_bps(symbol, tf, bars, tail=ohlcv_tail))
            elif mode == "hybrid_webp":
                prof = self.build_structural_profile(tf, bars)
                sections.append(
                    f"PROF_{tf}:" + self.encode_structural_profile_wire(prof)
                )
                if tf in tact_tfs:
                    # Exact lossless tick_delta tail for precise SL/TP anchoring in Stage 2
                    sections.append(self.encode_ohlcv_tick_delta(symbol, tf, bars[-ohlcv_tail:]))

        if mode == "hybrid_webp":
            images.append(self.render_candidate_5tf_webp(symbol, ctx))

        full_text = "\n".join(sections)
        ctx_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()[:16]
        return SerializedMarketPayload(
            mode=mode,
            text=full_text,
            images=images,
            context_hash=ctx_hash,
            raw_refs=raw_refs,
        )


def _extract_specs_from_ccxt(client_or_markets: Any) -> dict[str, tuple[Decimal, Decimal]]:
    """Helper used by bingx_vst_adapter.extract_instrument_specs."""
    if isinstance(client_or_markets, Mapping):
        markets = client_or_markets
        pm = getattr(client_or_markets, "precisionMode", ccxt.TICK_SIZE)
    else:
        markets = getattr(client_or_markets, "markets", None) or {}
        pm = getattr(client_or_markets, "precisionMode", ccxt.TICK_SIZE)
    is_tick_size = int(pm) == int(ccxt.TICK_SIZE)
    return MarketContextSerializer.from_ccxt_markets(
        markets, is_tick_size_mode=is_tick_size
    ).specs


__all__ = [
    "DEFAULT_ALL_TFS",
    "DEFAULT_INSTRUMENT_SPECS",
    "LEGACY_ALL_TFS",
    "LEGACY_STRATEGIC_TFS",
    "LEGACY_TACTICAL_TFS",
    "MarketContextSerializer",
    "STRATEGIC_TFS",
    "SerializationMode",
    "SerializedMarketPayload",
    "TACTICAL_TFS",
    "TF_STEP_MS",
    "_extract_specs_from_ccxt",
    "resolve_tf_hierarchy",
]
