"""
================================================================
QuantumEdge — LIQUIDATION HEATMAP CLI
================================================================
Генерує:
  1) runtime/liqmap_latest.json  — компактний JSON для торгового бота
     (топ-зони магнітів, найближчі лонг/шорт кластери, метадані).
  2) runtime/liqmap_dashboard.html — інтерактивний дашборд (свічки + теплокарта).

Використання (з /home/layman/QuantumEdge, venv .venv/bin/python3):
  python3 -m tools.liqmap.cli build [--tf 1h] [--limit 200] [--bins 140]
  python3 -m tools.liqmap.cli zones          # тільки надрукувати зони (для бота)
================================================================
"""
from __future__ import annotations
import argparse, json, sys, datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.liqmap.datafeed import fetch_ohlcv, fetch_oi_history, fetch_context
from tools.liqmap.model import build_heatmap, classify_regime
from tools.liqmap.dashboard import render_dashboard

RUNTIME = Path("/home/layman/QuantumEdge/runtime")
RUNTIME.mkdir(parents=True, exist_ok=True)
JSON_OUT = RUNTIME / "liqmap_latest.json"
HTML_OUT = RUNTIME / "liqmap_dashboard.html"

BUCKETS = [(1.0, 1.01), (0.8, 1.0), (0.6, 0.8), (0.4, 0.6), (0.2, 0.4)]


def _bucket(v):
    for lo, hi in BUCKETS:
        if lo <= v < hi:
            return f"{lo:.1f}-{hi:.2f}"
    return None


def _dist_bucket(dist):
    ad = abs(dist)
    if ad <= 1.0:
        return "0-1%"
    elif ad <= 2.0:
        return "1-2%"
    elif ad <= 3.0:
        return "2-3%"
    elif ad <= 5.0:
        return "3-5%"
    else:
        return "5%+"


def _load_confidence(regime, distance_bucket, bucket, side):
    """Шукає calibrated_confidence у калібрувальній таблиці для заданого режиму, відстані, бакету та сторони."""
    calib_path = RUNTIME / "liqmap_calibration.json"
    if not calib_path.exists():
        return None
    try:
        data = json.loads(calib_path.read_text(encoding="utf-8"))
        regimes_data = data.get("regimes", {})
        
        # Спершу шукаємо у специфічному режимі
        for item in regimes_data.get(regime, []):
            if (item.get("distance_bucket") == distance_bucket and 
                item.get("bucket") == bucket and 
                item.get("side") == side):
                return item.get("calibrated_confidence")
                
        # Фолбек на загальний режим 'all'
        for item in regimes_data.get("all", []):
            if (item.get("distance_bucket") == distance_bucket and 
                item.get("bucket") == bucket and 
                item.get("side") == side):
                return item.get("calibrated_confidence")
    except Exception:
        pass
    return None


def build(tf="1h", limit=200, bins=140, write_html=True):
    ohlcv, ohlcv_src = fetch_ohlcv(timeframe=tf, limit=limit)
    oi_series, oi_src = fetch_oi_history(timeframe=tf, limit=limit)
    ctx = fetch_context()

    hm = build_heatmap(ohlcv, price_bins=bins, oi_series=oi_series)
    hm["meta"]["tf"] = tf
    hm["meta"]["ohlcv_src"] = ohlcv_src
    hm["meta"]["oi_src"] = oi_src or "-"

    # Визначимо поточний режим ринку
    current_regime = classify_regime(ohlcv, len(ohlcv))
    hm["meta"]["regime"] = current_regime

    last = hm["meta"]["last_price"]
    zones = hm["zones"]
    
    # Додаємо впевненість (confidence) до кожної зони
    for z in zones:
        b = _bucket(z["intensity"])
        d = _dist_bucket(z["dist_pct"])
        confidence = _load_confidence(current_regime, d, b, z["side"]) if b and d else None
        z["confidence"] = confidence

    longs = [z for z in zones if z["side"] == "LONG_LIQ"]   # нижче ціни
    shorts = [z for z in zones if z["side"] == "SHORT_LIQ"]  # вище ціни
    nearest_long = max(longs, key=lambda z: z["price"]) if longs else None
    nearest_short = min(shorts, key=lambda z: z["price"]) if shorts else None
    strongest = max(zones, key=lambda z: z["intensity"]) if zones else None

    # компактний JSON для бота
    bot_json = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": "BTC/USDT",
        "timeframe": tf,
        "ohlcv_source": ohlcv_src,
        "oi_source": oi_src,
        "last_price": round(last, 2),
        "current_regime": current_regime,
        "funding": ctx.get("funding"),
        "oi_usd": ctx.get("oi_usd"),
        "zones": zones,
        "nearest_long_liq": nearest_long,     # магніт при падінні
        "nearest_short_liq": nearest_short,   # магніт при рості
        "strongest_zone": strongest,
        "interpretation": _interpret(last, nearest_long, nearest_short, strongest),
    }
    JSON_OUT.write_text(json.dumps(bot_json, indent=2, ensure_ascii=False))

    if write_html:
        render_dashboard(hm, ctx, HTML_OUT)

    return bot_json


def _interpret(last, nl, ns, strong):
    """Короткий словесний висновок для бота/людини."""
    parts = []
    if strong:
        side = "ЛОНГ-ліквідацій (магніт ВНИЗ)" if strong["side"] == "LONG_LIQ" else "ШОРТ-ліквідацій (магніт ВГОРУ)"
        parts.append(f"Найсильніше скупчення {side} @ {strong['price']} ({strong['dist_pct']:+.2f}%).")
    if nl:
        parts.append(f"Найближчий магніт вниз (лонг-ліквідації): {nl['price']} ({nl['dist_pct']:+.2f}%).")
    if ns:
        parts.append(f"Найближчий магніт вгору (шорт-ліквідації): {ns['price']} ({ns['dist_pct']:+.2f}%).")
    return " ".join(parts) if parts else "Немає виражених скупчень."


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--tf", default="1h")
    b.add_argument("--limit", type=int, default=200)
    b.add_argument("--bins", type=int, default=140)
    z = sub.add_parser("zones")
    z.add_argument("--tf", default="1h")
    z.add_argument("--limit", type=int, default=200)
    z.add_argument("--bins", type=int, default=140)
    args = ap.parse_args()

    if args.cmd == "build":
        out = build(tf=args.tf, limit=args.limit, bins=args.bins)
        print(f"OK. JSON -> {JSON_OUT}")
        print(f"    HTML -> {HTML_OUT}")
        print(f"last_price={out['last_price']} funding={out['funding']} oi_usd={out['oi_usd']}")
        print(f"src: ohlcv={out['ohlcv_source']} oi={out['oi_source']}")
        print("INTERPRETATION:", out["interpretation"])
        print("\nTOP ZONES:")
        for zn in out["zones"]:
            print(f"  {zn['side']:9s} @ {zn['price']:>10.2f}  int={zn['intensity']:.2f}  {zn['dist_pct']:+.2f}%")
    elif args.cmd == "zones":
        out = build(tf=args.tf, limit=args.limit, bins=args.bins, write_html=False)
        print(json.dumps({"zones": out["zones"],
                          "nearest_long_liq": out["nearest_long_liq"],
                          "nearest_short_liq": out["nearest_short_liq"],
                          "strongest_zone": out["strongest_zone"],
                          "last_price": out["last_price"]}, ensure_ascii=False))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
