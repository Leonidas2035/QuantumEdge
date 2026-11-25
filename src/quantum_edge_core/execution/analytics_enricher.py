"""Market analytics enricher integrating liqmap, orderflow, and composite_signal into FullMarketContext.

Strictly read-only and fail-soft:
- Zero exchange mutations (never places or cancels orders).
- Zero array/byte truncation.
- Independent fail-soft isolation per analytics module (liqmap, orderflow, composite_signal).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.composite_signal.backtest import _score_bucket
from tools.composite_signal.cli import _load_confidence as _load_composite_confidence
from tools.composite_signal.engine import calculate_composite_score
from tools.liqmap.cli import (
    _bucket as _liq_bucket,
    _dist_bucket as _liq_dist_bucket,
    _interpret as _liq_interpret,
    _load_confidence as _load_liq_confidence,
)
from tools.liqmap.model import build_heatmap, classify_regime
from tools.orderflow.detectors import (
    detect_book_imbalance,
    detect_icebergs,
    detect_layering,
    detect_liquidity_grab,
    detect_spoofing,
    detect_trades_delta,
)


def _to_ccxt_swap_symbol(symbol: str) -> str:
    sym = symbol.strip()
    if "/" in sym:
        return sym
    upper = sym.upper()
    if upper.endswith("USDT") and len(upper) > 4:
        return f"{upper[:-4]}/USDT:USDT"
    return upper


def _normalize_ohlcv_bars(bars: Sequence[Any]) -> list[list[float]]:
    rows: list[list[float]] = []
    for bar in bars or []:
        if isinstance(bar, Mapping):
            ts = bar.get("ts") if bar.get("ts") is not None else bar.get("timestamp")
            if ts is None:
                continue
            rows.append(
                [
                    float(ts),
                    float(bar["open"]),
                    float(bar["high"]),
                    float(bar["low"]),
                    float(bar["close"]),
                    float(bar.get("volume", 0.0)),
                ]
            )
        elif isinstance(bar, (list, tuple)) and len(bar) >= 6:
            rows.append([float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]), float(bar[5])])
    rows.sort(key=lambda r: r[0])
    return rows


def _select_timeframe(
    ohlcv_by_tf: Mapping[str, Sequence[Any]],
    preferred: Sequence[str],
    min_bars: int,
) -> tuple[str | None, list[list[float]]]:
    if not isinstance(ohlcv_by_tf, Mapping):
        return None, []
    for tf in preferred:
        if tf in ohlcv_by_tf:
            rows = _normalize_ohlcv_bars(ohlcv_by_tf[tf])
            if len(rows) >= min_bars:
                return tf, rows
    for tf, raw in ohlcv_by_tf.items():
        rows = _normalize_ohlcv_bars(raw)
        if len(rows) >= min_bars:
            return str(tf), rows
    return None, []


def _compute_liqmap_section(
    symbol: str,
    ohlcv_by_tf: Mapping[str, Sequence[Any]],
    derivatives: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tf_used, ohlcv_rows = _select_timeframe(
        ohlcv_by_tf,
        preferred=("1h", "15m", "4h", "5m", "1m"),
        min_bars=10,
    )
    if not tf_used or len(ohlcv_rows) < 10:
        raise ValueError("Insufficient OHLCV bars (need >= 10) for liqmap heatmap")

    hm = build_heatmap(ohlcv_rows, price_bins=140, oi_series=None)
    current_regime = classify_regime(ohlcv_rows, len(ohlcv_rows))
    last_price = float(hm["meta"]["last_price"])
    price_decimals = 6 if last_price < 1.0 else (4 if last_price < 100.0 else 2)

    zones: list[dict[str, Any]] = []
    centers = hm["price_levels"]
    for raw_zone in hm["zones"]:
        z = dict(raw_zone)
        if price_decimals > 2 and centers:
            nearest_center = min(centers, key=lambda c: abs(float(c) - float(z["price"])))
            if abs(nearest_center - float(z["price"])) <= max(float(hm["meta"]["bin_width"]), 0.01):
                z["price"] = round(float(nearest_center), price_decimals)
                z["dist_pct"] = round((z["price"] - last_price) / last_price * 100.0, 3)
        b = _liq_bucket(z["intensity"])
        d = _liq_dist_bucket(z["dist_pct"])
        z["confidence"] = (
            _load_liq_confidence(current_regime, d, b, z["side"]) if (b and d) else None
        )
        zones.append(z)

    longs = [z for z in zones if z["side"] == "LONG_LIQ"]
    shorts = [z for z in zones if z["side"] == "SHORT_LIQ"]
    nearest_long = max(longs, key=lambda z: z["price"]) if longs else None
    nearest_short = min(shorts, key=lambda z: z["price"]) if shorts else None
    strongest = max(zones, key=lambda z: z["intensity"]) if zones else None
    summary = _liq_interpret(last_price, nearest_long, nearest_short, strongest)

    return {
        "status": "OK",
        "symbol": symbol,
        "timeframe": tf_used,
        "regime": current_regime,
        "last_price": round(last_price, price_decimals),
        "funding_rate": (derivatives or {}).get("funding_rate"),
        "open_interest": (derivatives or {}).get("open_interest"),
        "zones": zones,
        "nearest_long_liq": nearest_long,
        "nearest_short_liq": nearest_short,
        "strongest_zone": strongest,
        "meta": hm["meta"],
        "price_levels": hm["price_levels"],
        "current_profile": hm["current_profile"],
        "summary": summary,
        "error": None,
    }


def _extract_valid_trade_events(raw_trades: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_trades, (list, tuple)):
        return []
    events: list[dict[str, Any]] = []
    for item in raw_trades:
        if not isinstance(item, Mapping):
            continue
        price = item.get("price")
        amount = item.get("amount")
        side = str(item.get("side") or "").lower()
        ts = item.get("timestamp")
        if price is None or amount is None or side not in ("buy", "sell"):
            continue
        try:
            events.append(
                {
                    "type": "trade",
                    "timestamp": int(ts) if ts is not None else 0,
                    "price": float(price),
                    "amount": float(amount),
                    "side": side,
                }
            )
        except (TypeError, ValueError):
            continue
    events.sort(key=lambda e: e["timestamp"])
    return events


def _compute_ohlcv_intrabar_delta(ohlcv_rows: Sequence[Sequence[float]], lookback: int = 20) -> float:
    recent = ohlcv_rows[-lookback:] if len(ohlcv_rows) > lookback else ohlcv_rows
    cvd = 0.0
    for _, o, h, l, c, v in recent:
        span = h - l
        if span > 0:
            buy_frac = (c - l) / span
            cvd += v * (2.0 * buy_frac - 1.0)
        elif c > o:
            cvd += v
        elif c < o:
            cvd -= v
    return cvd


def _compute_orderflow_section(
    symbol: str,
    ohlcv_by_tf: Mapping[str, Sequence[Any]],
    orderbook: Mapping[str, Any],
    client: Any = None,
) -> tuple[dict[str, Any], int]:
    provider_gets = 0
    if not isinstance(orderbook, Mapping):
        raise ValueError("Orderbook must be a mapping")

    bids = [[float(p), float(s)] for p, s in (orderbook.get("bids") or [])]
    asks = [[float(p), float(s)] for p, s in (orderbook.get("asks") or [])]
    if not bids and not asks:
        raise ValueError("Orderbook has empty bids and asks")

    book_ts = int(orderbook.get("timestamp") or 0)
    book_snap = {
        "type": "book",
        "timestamp": book_ts,
        "bids": bids,
        "asks": asks,
    }

    imb_top10 = float(detect_book_imbalance(book_snap, depth=10))
    full_depth = max(len(bids), len(asks), 1)
    imb_full = float(detect_book_imbalance(book_snap, depth=full_depth))
    layering = detect_layering(book_snap, large_mult=3.0)

    bid_vol_top10 = sum(s for _, s in bids[:10])
    ask_vol_top10 = sum(s for _, s in asks[:10])
    bid_vol_total = sum(s for _, s in bids)
    ask_vol_total = sum(s for _, s in asks)

    trade_events = _extract_valid_trade_events(orderbook.get("trades"))
    if not trade_events and client is not None and hasattr(client, "fetch_trades"):
        try:
            fetched = client.fetch_trades(_to_ccxt_swap_symbol(symbol), limit=100)
            parsed = _extract_valid_trade_events(fetched)
            if parsed:
                provider_gets += 1
                trade_events = parsed
        except Exception:
            trade_events = []

    if trade_events:
        last_ts = trade_events[-1]["timestamp"] or book_ts
        if book_snap["timestamp"] <= 0:
            book_snap["timestamp"] = trade_events[0]["timestamp"]
        events = [book_snap, *trade_events]
        # Window covers recent 10 minutes of trade prints
        cvd_val = float(detect_trades_delta(events, window_ms=600_000))
        icebergs = detect_icebergs(events)
        spoofing = detect_spoofing(events)
        grabs = detect_liquidity_grab(events)
        delta_source = "trades_tape"
        _ = last_ts
    else:
        _, fast_rows = _select_timeframe(
            ohlcv_by_tf,
            preferred=("1m", "5m", "15m", "1h", "4h"),
            min_bars=1,
        )
        cvd_val = float(_compute_ohlcv_intrabar_delta(fast_rows, lookback=20)) if fast_rows else 0.0
        icebergs = []
        spoofing = []
        grabs = []
        delta_source = "ohlcv_intrabar_proxy"

    bias = "BID_DOMINANT" if imb_top10 > 0.1 else ("ASK_DOMINANT" if imb_top10 < -0.1 else "BALANCED")
    summary = (
        f"{bias}: imbalance_top10={imb_top10:+.4f}, imbalance_full={imb_full:+.4f}, "
        f"cvd_or_delta={cvd_val:+.4f} ({delta_source}), "
        f"layers(bid={layering['bids_layers']}, ask={layering['asks_layers']})."
    )

    return (
        {
            "status": "OK",
            "metrics": {
                "imbalance_top10": round(imb_top10, 6),
                "imbalance_full": round(imb_full, 6),
                "bid_vol_top10": round(bid_vol_top10, 6),
                "ask_vol_top10": round(ask_vol_top10, 6),
                "bid_vol_total": round(bid_vol_total, 6),
                "ask_vol_total": round(ask_vol_total, 6),
                "bid_levels_count": len(bids),
                "ask_levels_count": len(asks),
                "layering": layering,
                "icebergs": icebergs,
                "spoofing": spoofing,
                "liquidity_grabs": grabs,
                "trades_sampled": len(trade_events),
                "delta_source": delta_source,
            },
            "imbalance": round(imb_top10, 6),
            "cvd_or_delta": round(cvd_val, 6),
            "summary": summary,
            "error": None,
        },
        provider_gets,
    )


def _compute_composite_section(
    symbol: str,
    ohlcv_by_tf: Mapping[str, Sequence[Any]],
    derivatives: Mapping[str, Any] | None,
    liq_zones: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    tf_used, ohlcv_rows = _select_timeframe(
        ohlcv_by_tf,
        preferred=("1h", "15m", "4h", "5m", "1m"),
        min_bars=55,
    )
    if not tf_used or len(ohlcv_rows) < 55:
        raise ValueError("Insufficient OHLCV bars (need >= 55) for composite_signal")

    df = pd.DataFrame(
        ohlcv_rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    funding_raw = (derivatives or {}).get("funding_rate")
    funding_rate = float(funding_raw) if funding_raw is not None else 0.0

    res = calculate_composite_score(
        df,
        funding_rate=funding_rate,
        oi_series=None,
        liq_zones=liq_zones,
    )
    score = int(res["score"])
    signal = "LONG" if score > 10 else ("SHORT" if score < -10 else "NEUTRAL")
    regime = classify_regime(ohlcv_rows, len(ohlcv_rows))
    bucket = _score_bucket(score)
    confidence = _load_composite_confidence(regime, bucket, signal) if signal != "NEUTRAL" else None

    atr_val = float(res.get("atr", 0.0))
    if np.isnan(atr_val):
        atr_val = 0.0

    return {
        "status": "OK",
        "signal": signal,
        "score": score,
        "components": {
            "symbol": symbol,
            "timeframe": tf_used,
            "regime": regime,
            "score_bucket": bucket,
            "calibrated_confidence": confidence,
            "trend": float(res["trend"]),
            "momentum": float(res["momentum"]),
            "volatility": float(res["volatility"]),
            "volume_flow": float(res["volume_flow"]),
            "liq_map": float(res["liq_map"]),
            "atr": round(atr_val, 6),
        },
        "error": None,
    }


def compute_market_analytics(
    symbol: str,
    ohlcv_by_tf: dict[str, Any],
    orderbook: dict[str, Any],
    derivatives: dict[str, Any],
    ticker: dict[str, Any] | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Compute untruncated liqmap, orderflow, and composite_signal sections with fail-soft isolation."""
    _ = ticker
    extra_provider_gets = 0

    try:
        liqmap_section = _compute_liqmap_section(
            symbol=symbol,
            ohlcv_by_tf=ohlcv_by_tf,
            derivatives=derivatives,
        )
    except Exception as exc:
        liqmap_section = {
            "status": "UNAVAILABLE",
            "zones": [],
            "nearest_long_liq": None,
            "nearest_short_liq": None,
            "summary": None,
            "error": str(exc),
        }

    try:
        orderflow_section, of_gets = _compute_orderflow_section(
            symbol=symbol,
            ohlcv_by_tf=ohlcv_by_tf,
            orderbook=orderbook,
            client=client,
        )
        extra_provider_gets += of_gets
    except Exception as exc:
        orderflow_section = {
            "status": "UNAVAILABLE",
            "metrics": {},
            "imbalance": None,
            "cvd_or_delta": None,
            "summary": None,
            "error": str(exc),
        }

    liq_zones = (
        liqmap_section.get("zones")
        if liqmap_section.get("status") == "OK"
        else None
    )
    try:
        composite_section = _compute_composite_section(
            symbol=symbol,
            ohlcv_by_tf=ohlcv_by_tf,
            derivatives=derivatives,
            liq_zones=liq_zones,
        )
    except Exception as exc:
        composite_section = {
            "status": "UNAVAILABLE",
            "signal": None,
            "score": None,
            "components": {},
            "error": str(exc),
        }

    return {
        "liqmap": liqmap_section,
        "orderflow": orderflow_section,
        "composite_signal": composite_section,
        "_provider_gets": extra_provider_gets,
    }
