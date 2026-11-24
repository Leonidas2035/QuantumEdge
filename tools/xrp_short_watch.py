"""
================================================================
QuantumEdge — XRP & BTC Timeframe Hierarchy Short-Watch Daemon
================================================================
Аналізує XRP та BTC кожні 5 хв через скіли (sigtest + liqmap).
Ієрархія ТФ:
  - 4h  — ГЛОБАЛЬНИЙ контекст (лонг-тренд? бічник? чи взагалі не шортити?)
  - 15m — СЕТАПИ шорту (коли 4h не проти, шукаємо 3+ голоси + LONG_LIQ зона)
  - 5m  — ЛОКАЛЬНА оцінка + ТАЙМІНГ входу (демон прокидається -> дивиться 5m)
НЕ закриває існуючий LONG (правило власника 1).
Пише звіт у runtime/xrp_short_watch.jsonl
================================================================
"""
from __future__ import annotations
import sys, json, time, datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd, numpy as np
import ccxt
from dotenv import load_dotenv
load_dotenv()

from sigtest.indicators import enrich
from liqmap.model import build_heatmap, _extract_zones
from qe_trade import snapshot as qe_snapshot

QE = Path("/home/layman/QuantumEdge")
WATCH = QE / "runtime" / "xrp_short_watch.jsonl"
WATCH.parent.mkdir(parents=True, exist_ok=True)
SYM = "XRP/USDT:USDT"

# Telegram-нотифікація (дубль звіту власнику)
try:
    sys.path.insert(0, str(QE / "src" / "quantum_edge_core"))
    from telegram_notifier import send_telegram_message as tg_send
except Exception:
    def tg_send(text):  # фолбек, якщо модуль недоступний
        return False


def analyze_tf(symbol: str, tf: str, limit: int = 200) -> dict:
    """Універсальна функція аналізу окремого таймфрейму."""
    ex = ccxt.bingx({"enableRateLimit": True})
    ohlcv = ex.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    df = enrich(df)
    r = df.iloc[-1]
    last = float(r.close)

    # голоси
    long_v = short_v = 0
    if r.ema9 > r.ema21 > r.ema50: long_v += 1
    elif r.ema9 < r.ema21 < r.ema50: short_v += 1
    if r.macd_hist > 0: long_v += 1
    else: short_v += 1
    if r.rsi > 55: long_v += 1
    elif r.rsi < 45: short_v += 1
    if r.obv_slope > 0: long_v += 1
    else: short_v += 1
    bias = "SHORT" if short_v >= 3 else ("LONG" if long_v >= 3 else "NEUTRAL")

    # карта ліквідацій
    heat = build_heatmap(ohlcv, price_bins=140)
    prof = np.array(heat["grid"])[:, -1]
    cen = np.array(heat["price_levels"])
    zones = _extract_zones(prof, cen, last, top=8)
    near = [z for z in zones if abs(z["dist_pct"]) <= 2.0]

    # drop_pct за останні 4 свічки
    cl = df["close"].values
    drop_pct = 0.0
    if len(cl) >= 4:
        drop_pct = (cl[-4] - cl[-1]) / cl[-4] * 100.0

    return {
        "tf": tf,
        "price": last,
        "bias": bias,
        "long_votes": long_v,
        "short_votes": short_v,
        "rsi": round(float(r.rsi), 1),
        "macd_hist": round(float(r.macd_hist), 3),
        "obv_slope": round(float(r.obv_slope), 1),
        "near_zones": near,
        "drop_pct": round(drop_pct, 3),
        "ema9": r.ema9,
        "ema21": r.ema21,
        "ema50": r.ema50
    }


def analyze_btc() -> dict:
    """Аналіз BTC на 4h/15m/5m."""
    h4 = analyze_tf("BTC/USDT:USDT", "4h")
    m15 = analyze_tf("BTC/USDT:USDT", "15m")
    m5 = analyze_tf("BTC/USDT:USDT", "5m")

    # key_level_hit: перевіряємо чи ціна торкалася 65000 і закрилася нижче за останні 6 свічок 1h
    key_level_hit = False
    try:
        ex = ccxt.bingx({"enableRateLimit": True})
        ohlcv_1h = ex.fetch_ohlcv("BTC/USDT:USDT", "1h", limit=6)
        for row in ohlcv_1h:
            high = float(row[2])
            close = float(row[4])
            if high >= 65000.0 and close < 65000.0:
                key_level_hit = True
                break
    except Exception:
        pass

    return {
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "price": m5["price"],
        "h4": h4,
        "m15": m15,
        "m5": m5,
        "key_level_hit": key_level_hit,
        "bias": m15["bias"],  # для сумісності
        "short_votes": m15["short_votes"],
        "long_votes": m15["long_votes"],
        "near_zones": m15["near_zones"]
    }


def analyze_xrp(sig_btc=None) -> dict:
    """Аналіз XRP з ієрархією ТФ 4h/15m/5m та BTC кореляцією."""
    if sig_btc is None:
        try:
            sig_btc = analyze_btc()
        except Exception:
            sig_btc = None

    h4 = analyze_tf(SYM, "4h")
    m15 = analyze_tf(SYM, "15m")
    m5 = analyze_tf(SYM, "5m")
    price = m5["price"]

    # BTC-КОРЕЛЯЦІЯ
    btc_short_confirmed = False
    if sig_btc:
        btc_short_confirmed = (sig_btc["m15"]["bias"] == "SHORT" or sig_btc["h4"]["bias"] == "SHORT" or sig_btc.get("key_level_hit"))
        if btc_short_confirmed:
            m15["short_votes"] += 1

    threshold = 2 if btc_short_confirmed else 3
    bias = "SHORT" if m15["short_votes"] >= threshold else ("LONG" if m15["long_votes"] >= 3 else "NEUTRAL")

    # Карта ліквідацій з m15
    long_z = [z for z in m15["near_zones"] if z["side"] == "LONG_LIQ"]

    target = None
    dump_blocked = False
    timing_postponed = False

    # 1. СЕТАП ШОРТУ (m15)
    if bias == "SHORT" and long_z:
        z = min(long_z, key=lambda x: abs(x["dist_pct"]))
        short_type = "TREND_SHORT"
        if h4["bias"] == "LONG":
            short_type = "SCALP_SHORT"  # Шорт-скальп всередині лонг-тренду
            
        target = {
            "side": "SHORT", "tp": z["price"], "zone_dist": z["dist_pct"],
            "sl_wide": round(price * 1.05, 4), "type": short_type
        }

        # 2. ФІЛЬТР ДАМПУ (m15 drop_pct >= 1.5% або m5 drop_pct >= 1.0%)
        if m15["drop_pct"] >= 1.5 or m5["drop_pct"] >= 1.0:
            target = None
            dump_blocked = True

        # 3. ТАЙМІНГ ВХОДУ (m5)
        # Вхід дозволено, якщо m5 підтверджує шорт
        m5_confirm_short = (m5["short_votes"] >= 1 or m5["rsi"] > 55 or m5["macd_hist"] < 0)
        if target and not m5_confirm_short:
            target = None
            timing_postponed = True

    return {
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "price": price,
        "h4": h4,
        "m15": m15,
        "m5": m5,
        "bias": bias,
        "short_votes": m15["short_votes"],
        "long_votes": m15["long_votes"],
        "rsi": m15["rsi"],
        "macd_hist": m15["macd_hist"],
        "short_target": target,
        "btc_confirmed": btc_short_confirmed,
        "dump_blocked": dump_blocked,
        "timing_postponed": timing_postponed
    }


def current_position():
    """Поточна LONG позиція XRP (НЕ закриваємо)."""
    try:
        snap = qe_snapshot_raw()
        for l in snap.get("legs", []):
            if l["side"] == "long" and l["size"] > 0:
                return {"size": l["size"], "entry": l["entry"], "uPnL": l["uPnL"]}
    except Exception:
        pass
    return None


def qe_snapshot_raw():
    import subprocess
    r = subprocess.run(
        [str(QE / ".venv" / "bin" / "python3"), str(QE / "tools" / "qe_trade.py"), "snapshot"],
        capture_output=True, text=True, cwd=str(QE), timeout=60)
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return {}


def decide_short(sig, pos):
    """Рішення по шорту XRP."""
    rec = {
        "ts": sig["ts"],
        "pair": "XRP",
        "price": sig["price"],
        "h4_bias": sig["h4"]["bias"],
        "m15_bias": sig["m15"]["bias"],
        "m5_bias": sig["m5"]["bias"],
        "short_votes": sig["m15"]["short_votes"],
        "long_votes": sig["m15"]["long_votes"],
        "rsi": sig["m15"]["rsi"],
        "macd_hist": sig["m15"]["macd_hist"],
        "drop_pct": sig["m15"]["drop_pct"],
        "dump_blocked": sig["dump_blocked"],
        "timing_postponed": sig["timing_postponed"],
        "long_pos": pos,
        "action": "HOLD"
    }
    
    if sig.get("short_target") and pos:
        t = sig["short_target"]
        rec["action"] = "SHORT_SETUP_READY"
        rec["plan"] = (f"Шорт XRP готовий ({t['type']}): індикатори {sig['short_votes']}/4 (btc_corr={sig['btc_confirmed']}), "
                       f"LONG_LIQ @ {t['tp']} ({t['zone_dist']:+.2f}%) -> ТП. "
                       f"Широкий СЛ @ {t['sl_wide']}. LONG позиція НЕ закривається.")
    else:
        reasons = []
        if sig["dump_blocked"]: reasons.append("dump_blocked")
        if sig["timing_postponed"]: reasons.append("timing_postponed")
        rec["plan"] = f"Шорт XRP НЕ готовий. h4={sig['h4']['bias']}, m15_votes={sig['short_votes']}/4, blocks={reasons}"
        
    with open(WATCH, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def execute_short_setup(sig, pos):
    """Автономне виконання для XRP."""
    import subprocess
    qe = [str(QE / ".venv" / "bin" / "python3"), str(QE / "tools" / "qe_trade.py")]

    def qe_cmd(args):
        r = subprocess.run(qe + args, capture_output=True, text=True, cwd=str(QE), timeout=60)
        try:
            return json.loads(r.stdout.strip())
        except Exception:
            return {"error": (r.stdout or "")[:200], "stderr": (r.stderr or "")[:200]}

    short_leg = None
    try:
        snap = qe_cmd(["snapshot"])
        for l in snap.get("legs", []):
            if l["side"] == "short" and l["size"] > 0:
                short_leg = l
                break
    except Exception:
        pass

    if sig.get("short_target") and not short_leg:
        t = sig["short_target"]
        qty = 50000
        entry_p = round(sig["price"] * 1.001, 4)
        res = qe_cmd(["place", "sell", "SHORT", str(qty), str(entry_p),
                      str(round(t["tp"], 4)), str(round(t["sl_wide"], 4))])
        if res.get("id") is not None:
            tg_send(f"📉 <b>OPEN SHORT XRP</b>\nqty={qty} entry≈{entry_p}\n"
                    f"TP={t['tp']} SL={t['sl_wide']} ({t['type']})\nprice={sig['price']} | "
                    f"bias={sig['bias']}\n"
                    f"long uPnL={pos['uPnL']:.0f} (не чіпаємо)")
            print(f"    >> OPEN SHORT XRP: qty={qty} entry={entry_p} TP={t['tp']} SL={t['sl_wide']} -> {res.get('id','?')}")
            return {"executed": "OPEN_SHORT", "res": res}
        else:
            print(f"    >> FAILED OPEN SHORT XRP: {res.get('error') or res}")
            return {"executed": None, "res": res}

    if short_leg and short_leg["uPnL"] > 0:
        price = sig["price"]
        t = sig.get("short_target")
        hit_tp = t and price <= t["tp"]
        reversal = sig["m15"]["rsi"] < 30 or sig["m15"]["macd_hist"] > 0
        if hit_tp or reversal:
            res = qe_cmd(["market", "buy", "SHORT", str(int(short_leg["size"]))])
            tg_send(f"📈 <b>CLOSE SHORT XRP (profit)</b>\nuPnL={short_leg['uPnL']:.1f} "
                    f"reason={'TP' if hit_tp else 'reversal'}\n"
                    f"price={price} | bias={sig['bias']}")
            print(f"    >> CLOSE SHORT XRP (profit): uPnL={short_leg['uPnL']:.1f} reason={'TP' if hit_tp else 'reversal'} -> {res.get('id','?')}")
            return {"executed": "CLOSE_SHORT_PROFIT", "res": res}

    return {"executed": None}


def qe_cmd_btc(args):
    """Викликає qe_trade.py для BTC."""
    import os
    import subprocess
    env = {**os.environ, "MARKET_DATA_SYMBOLS": "BTCUSDT"}
    r = subprocess.run(
        [str(QE / ".venv" / "bin" / "python3"), str(QE / "tools" / "qe_trade.py"), *args],
        capture_output=True, text=True, cwd=str(QE), timeout=60, env=env
    )
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return {"error": (r.stdout or "")[:200], "stderr": (r.stderr or "")[:200]}


def current_position_btc():
    """Поточна SHORT позиція BTC."""
    try:
        snap = qe_cmd_btc(["snapshot"])
        for l in snap.get("legs", []):
            if l["side"] == "short" and l["size"] > 0:
                return {"size": l["size"], "entry": l["entry"], "uPnL": l["uPnL"]}
    except Exception:
        pass
    return None


def decide_and_execute_btc(sig_btc, pos_btc):
    """Аналіз та автономне виконання шортів для BTC."""
    rec = {
        "ts": sig_btc["ts"],
        "pair": "BTC",
        "price": sig_btc["price"],
        "h4_bias": sig_btc["h4"]["bias"],
        "m15_bias": sig_btc["m15"]["bias"],
        "m5_bias": sig_btc["m5"]["bias"],
        "short_votes": sig_btc["m15"]["short_votes"],
        "long_votes": sig_btc["m15"]["long_votes"],
        "rsi": sig_btc["m15"]["rsi"],
        "macd_hist": sig_btc["m15"]["macd_hist"],
        "drop_pct": sig_btc["m15"]["drop_pct"],
        "dump_blocked": False,
        "timing_postponed": False,
        "short_pos": pos_btc,
        "action": "HOLD"
    }

    # Шукаємо сетап шорту по BTC на m15
    long_z = [z for z in sig_btc["m15"]["near_zones"] if z["side"] == "LONG_LIQ"]
    target = None
    dump_blocked = False
    timing_postponed = False

    # 1. СЕТАП ШОРТУ (m15)
    if sig_btc["m15"]["bias"] == "SHORT" and long_z:
        z = min(long_z, key=lambda x: abs(x["dist_pct"]))
        short_type = "TREND_SHORT"
        if sig_btc["h4"]["bias"] == "LONG":
            short_type = "SCALP_SHORT"

        target = {
            "side": "SHORT", "tp": z["price"], "zone_dist": z["dist_pct"],
            "sl_wide": round(sig_btc["price"] * 1.05, 4), "type": short_type
        }

        # 2. ФІЛЬТР ДАМПУ (m15 drop_pct >= 1.5% або m5 drop_pct >= 1.0%)
        if sig_btc["m15"]["drop_pct"] >= 1.5 or sig_btc["m5"]["drop_pct"] >= 1.0:
            target = None
            dump_blocked = True

        # 3. ТАЙМІНГ ВХОДУ (m5)
        m5_confirm_short = (sig_btc["m5"]["short_votes"] >= 1 or sig_btc["m5"]["rsi"] > 55 or sig_btc["m5"]["macd_hist"] < 0)
        if target and not m5_confirm_short:
            target = None
            timing_postponed = True

    rec["short_target"] = target
    rec["dump_blocked"] = dump_blocked
    rec["timing_postponed"] = timing_postponed

    # Відкриваємо шорт
    if target and not pos_btc:
        qty = 0.5  # Обережний лот BTC (0.5 контракта VST)
        entry_p = round(sig_btc["price"] * 1.001, 4)
        res = qe_cmd_btc(["place", "sell", "SHORT", str(qty), str(entry_p),
                          str(round(target["tp"], 4)), str(round(target["sl_wide"], 4))])
        if res.get("id") is not None:
            tg_send(f"📉 <b>OPEN SHORT BTC</b>\nqty={qty} entry≈{entry_p}\n"
                    f"TP={target['tp']} SL={target['sl_wide']} ({target['type']})\nprice={sig_btc['price']}")
            print(f"    >> OPEN SHORT BTC: qty={qty} entry={entry_p} TP={target['tp']} SL={target['sl_wide']} -> {res.get('id','?')}")
            rec["action"] = "OPEN_SHORT"
            rec["result"] = res
        else:
            print(f"    >> FAILED OPEN SHORT BTC: {res.get('error') or res}")
            rec["action"] = "HOLD"
            rec["result"] = res

    # Закриваємо прибутковий шорт
    elif pos_btc and pos_btc["uPnL"] > 0:
        price = sig_btc["price"]
        hit_tp = target and price <= target["tp"]
        reversal = sig_btc["m15"]["rsi"] < 30 or sig_btc["m15"]["macd_hist"] > 0
        if hit_tp or reversal:
            res = qe_cmd_btc(["market", "buy", "SHORT", str(pos_btc["size"])])
            tg_send(f"📈 <b>CLOSE SHORT BTC (profit)</b>\nuPnL={pos_btc['uPnL']:.1f} "
                    f"reason={'TP' if hit_tp else 'reversal'}\n"
                    f"price={price}")
            print(f"    >> CLOSE SHORT BTC (profit): uPnL={pos_btc['uPnL']:.1f} reason={'TP' if hit_tp else 'reversal'} -> {res.get('id','?')}")
            rec["action"] = "CLOSE_SHORT_PROFIT"
            rec["result"] = res

    with open(WATCH, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def main_loop(once=False):
    print(f"[XRP-SHORT-WATCH & BTC-WATCH] start {dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S UTC')}")
    while True:
        try:
            # 1. Аналіз та виконання BTC
            sig_btc = analyze_btc()
            pos_btc = current_position_btc()
            rec_btc = decide_and_execute_btc(sig_btc, pos_btc)
            print(f"  BTC: {rec_btc['ts']} | {rec_btc['action']} | bias={sig_btc['m15']['bias']} | price={sig_btc['price']}")

            # 2. Аналіз та виконання XRP
            sig_xrp = analyze_xrp(sig_btc=sig_btc)
            pos_xrp = current_position()
            rec_xrp = decide_short(sig_xrp, pos_xrp)
            print(f"  XRP: {rec_xrp['ts']} | {rec_xrp['action']} | bias={sig_xrp['bias']} "
                  f"({sig_xrp['short_votes']}S/{sig_xrp['long_votes']}L) | price={sig_xrp['price']}")
            if sig_xrp.get("short_target"):
                print(f"    -> XRP SHORT TARGET: TP={sig_xrp['short_target']['tp']} "
                      f"SL={sig_xrp['short_target']['sl_wide']} ({sig_xrp['short_target']['type']})")
            ex_xrp = execute_short_setup(sig_xrp, pos_xrp)
            if ex_xrp.get("executed"):
                print(f"    [EXEC XRP] {ex_xrp['executed']}")

        except Exception as e:
            print(f"  ERR: {e}")
        if once:
            break
        time.sleep(300)


if __name__ == "__main__":
    once = "--once" in sys.argv
    main_loop(once=once)
