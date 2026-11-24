"""
================================================================
QuantumEdge — COMPOSITE SIGNAL RUNNER
================================================================
Обчислює інтегральний сигнал для поточного моменту та записує
його статус разом із каліброваною впевненістю.

Вихід: runtime/composite_latest.json
================================================================
"""
from __future__ import annotations
import sys
import json
import argparse
import datetime as dt
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.liqmap.datafeed import fetch_ohlcv, fetch_oi_history, fetch_context
from tools.liqmap.model import classify_regime
from tools.composite_signal.engine import calculate_composite_score
from tools.composite_signal.backtest import _score_bucket

RUNTIME = Path("/home/layman/QuantumEdge/runtime")
JSON_OUT = RUNTIME / "composite_latest.json"


def _load_confidence(regime, bucket, side):
    calib_path = RUNTIME / "composite_calibration.json"
    if not calib_path.exists():
        return None
    try:
        data = json.loads(calib_path.read_text(encoding="utf-8"))
        regimes_data = data.get("regimes", {})
        
        # Спершу шукаємо у специфічному режимі
        for item in regimes_data.get(regime, []):
            if item.get("bucket") == bucket and item.get("side") == side:
                return item.get("calibrated_confidence")
                
        # Фолбек на режим 'all'
        for item in regimes_data.get("all", []):
            if item.get("bucket") == bucket and item.get("side") == side:
                return item.get("calibrated_confidence")
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--limit", type=int, default=150)
    args = ap.parse_args()

    ohlcv, ohlcv_src = fetch_ohlcv(timeframe=args.tf, limit=args.limit)
    oi_series, oi_src = fetch_oi_history(timeframe=args.tf, limit=args.limit)
    ctx = fetch_context()
    funding_rate = ctx.get("funding", 0.0)

    # Завантажуємо зони ліквідації для 5-ї осі
    zones_path = RUNTIME / "liqmap_latest.json"
    liq_zones = None
    if zones_path.exists():
        try:
            zones_data = json.loads(zones_path.read_text(encoding="utf-8"))
            liq_zones = zones_data.get("zones", [])
        except Exception:
            pass

    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    current_regime = classify_regime(ohlcv, len(ohlcv))

    res = calculate_composite_score(df, funding_rate=funding_rate, oi_series=oi_series, liq_zones=liq_zones)
    score = res["score"]
    
    side = "LONG" if score > 10 else "SHORT" if score < -10 else "NEUTRAL"
    bucket = _score_bucket(score)
    confidence = _load_confidence(current_regime, bucket, side) if side != "NEUTRAL" else None

    latest_signal = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": "BTC/USDT",
        "timeframe": args.tf,
        "current_regime": current_regime,
        "last_price": round(df["close"].iloc[-1], 2),
        "composite_score": score,
        "signal_side": side,
        "score_bucket": bucket,
        "calibrated_confidence": confidence,
        "axes": {
            "trend": res["trend"],
            "momentum": res["momentum"],
            "volatility": res["volatility"],
            "volume_flow": res["volume_flow"],
            "liq_map": res["liq_map"]
        },
        "atr": round(res["atr"], 2)
    }

    JSON_OUT.write_text(json.dumps(latest_signal, indent=2, ensure_ascii=False))
    print(f"OK. Latest composite signal saved to {JSON_OUT}")

    # Вивід деталей на екран
    print(f"\nCOMPOSITE SIGNAL SUMMARY:")
    print(f"  Price: {latest_signal['last_price']} | Regime: {current_regime}")
    print(f"  Composite Score: {score:+} / 100 ({side})")
    if confidence is not None:
        print(f"  Calibrated Confidence: {confidence:.1%}")
    else:
        print(f"  Calibrated Confidence: N/A (Neutral or No Calibration)")
        
    print("\nAXES BREAKDOWN:")
    print(f"  1. Trend Axis (EMA+ADX):       {res['trend']:+2.1f} / 25")
    print(f"  2. Momentum Axis (MACD+RSI):   {res['momentum']:+2.1f} / 25")
    print(f"  3. Volatility Axis (BB+ATR):   {res['volatility']:+2.1f} / 25")
    print(f"  4. Volume/Flow Axis (OI+OBV):  {res['volume_flow']:+2.1f} / 25")
    print(f"  5. Liquidation Map Axis:       {res['liq_map']:+2.1f} / 25")


if __name__ == "__main__":
    main()
