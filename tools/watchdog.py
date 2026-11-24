#!/usr/bin/env python3
"""
Watchdog Agent — runs locally on the PC.
Monitors the market and trading state every 5 minutes.
Determines if the main trader agent (Hermes) needs to be woken up.
Writes status to runtime/watchdog_signal.json.
"""
import os
import sys
import json
import asyncio
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import ccxt.async_support as ccxt

SYMBOL = "XRP/USDT:USDT"
STATE_FILE = ROOT / "runtime" / "watchdog_state.json"
SIGNAL_FILE = ROOT / "runtime" / "watchdog_signal.json"


async def fetch_5m_ohlcv_async():
    ak = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY", "")
    sc = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET", "")
    ex = ccxt.bingx({
        "apiKey": ak,
        "secret": sc,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
        "timeout": 15000,
    })
    if str(os.getenv("USE_TESTNET", "1")).lower() in {"1", "true", "t"}:
        ex.set_sandbox_mode(True)
    try:
        ohlcv = await ex.fetch_ohlcv(SYMBOL, "5m", limit=30)  # AWAIT (ccxt 4.5.64)
        return ohlcv
    except Exception as e:
        print(f"[WATCHDOG] Failed to fetch 5m candles: {e}")
        return []
    finally:
        try:
            await ex.close()
        except Exception:
            pass

def get_snapshot():
    import subprocess
    try:
        # Run qe_trade.py snapshot
        venv_python = str(ROOT / ".venv" / "bin" / "python3")
        script_path = str(ROOT / "tools" / "qe_trade.py")
        res = subprocess.run([venv_python, script_path, "snapshot"], capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return json.loads(res.stdout)
        else:
            print(f"[WATCHDOG] Snapshot script failed: {res.stderr}")
            return None
    except Exception as e:
        print(f"[WATCHDOG] Failed to call snapshot: {e}")
        return None

def get_vol_index(ohlcv):
    if len(ohlcv) < 15:
        return 0.0, 0.0
    
    # Calculate True Ranges
    tr_list = []
    for i in range(1, len(ohlcv)):
        high = float(ohlcv[i][2])
        low = float(ohlcv[i][3])
        prev_close = float(ohlcv[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
        
    latest_tr = tr_list[-1]
    
    # Average ATR of previous 14 periods (elements -15..-2, i.e. 14 values)
    avg_atr = sum(tr_list[-15:-1]) / 14.0
    if avg_atr == 0:
        return 0.0, 0.0
        
    ratio = latest_tr / avg_atr
    # ratio of 1.0 (normal) -> index = 0.5
    # ratio of 2.0 (spike) -> index = 1.0
    vol_index = min(1.0, max(0.0, ratio / 2.0))
    return vol_index, latest_tr

def ask_local_llm(price, vol_index, price_move_pct, min_buffer_pct, alert_leg, now_minute):
    import httpx
    positions_text = alert_leg if alert_leg else "No active positions"
    
    prompt = f"""Ти — локальний ШШІ-агент моніторингу (Watchdog) для торгової системи QuantumEdge.
Твоє завдання — перевірити стан ринку та відкритих позицій і вирішити, чи потрібно розбудити основного трейдера (Hermes) для аналізу або екстрених дій.

ВХІДНІ ДАНІ:
- Ціна XRP: {price}
- 5m Volatility Index (0.0-1.0): {round(vol_index, 3)} (поріг тривоги > 0.75)
- Зміна ціни за 5хв: {round(price_move_pct, 3)}% (поріг тривоги > 1.0%)
- Запас до ліквідації: {round(min_buffer_pct, 2) if min_buffer_pct is not None else 'N/A'}% (поріг тривоги < 3.0%)
- Поточні активні позиції: {positions_text}
- Системний час (хвилина): {now_minute} (потрібно будити за розкладом кожні 15 хвилин, тобто на 00, 15, 30, 45 хвилинах)

ПРАВИЛА ПРИЙНЯТТЯ РІШЕННЯ ПРО БУДИЛЬНИК (wake: true):
1. Обов'язково будить (wake: true) кожні 15 хвилин за планом (якщо поточна хвилина {now_minute} близька до 0, 15, 30, 45, тобто в діапазоні відхилення +/- 1 хв). Reason: "scheduled_15m".
2. Негайно будить позапланово (wake: true), якщо запас до ліквідації < 3%. Reason: "liq_alert".
3. Негайно будить позапланово (wake: true), якщо індекс волатильності > 0.75 або зміна ціни > 1.0%. Reason: "vol_spike".
4. В інших випадках — НЕ будить (wake: false, reason: "scheduled_5m").

ФОРМАТ ВІДПОВІДІ (Тільки чистий JSON, без markdown, без пояснень, ключі: wake, reason, note):
{{
  "wake": true/false,
  "reason": "scheduled_15m|liq_alert|vol_spike|scheduled_5m",
  "note": "Коротке пояснення причини (1 речення)"
}}"""

    model = os.environ.get("LOCAL_LLM_MODEL", "hermes3:8b-llama3.1-q5_K_M")
    url = "http://localhost:11434/api/generate"
    try:
        with httpx.Client(timeout=15) as client:
            res = client.post(url, json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.1
                }
            })
            if res.status_code == 200:
                response_json = res.json()
                response_text = response_json.get("response", "")
                return json.loads(response_text)
    except Exception as e:
        print(f"[WATCHDOG] LLM call failed or parsed incorrectly: {e}")
    return None

def main():
    print(f"[WATCHDOG] Watchdog run start at {datetime.datetime.now(datetime.UTC)} UTC")
    
    # 1. Get Snapshot
    snap = get_snapshot()
    if not snap:
        print("[WATCHDOG] Error: Could not retrieve system snapshot.")
        sys.exit(1)
        
    price = snap.get("mark_price")
    if not price:
        print("[WATCHDOG] Error: Snapshot does not contain mark price.")
        sys.exit(1)
        
    # 2. Fetch 5m candles and compute volatility index
    ohlcv = asyncio.run(fetch_5m_ohlcv_async())
    vol_index, latest_tr = get_vol_index(ohlcv)
    
    # 3. Load state for price tracking
    os.makedirs(STATE_FILE.parent, exist_ok=True)
    last_price = None
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            last_price = state.get("last_price")
        except Exception:
            pass
            
    price_move_pct = 0.0
    if last_price:
        price_move_pct = abs(price - last_price) / last_price * 100.0
        
    # Save current price state
    try:
        STATE_FILE.write_text(json.dumps({"last_price": price, "timestamp": datetime.datetime.now(datetime.UTC).isoformat()}))
    except Exception as e:
        print(f"[WATCHDOG] Failed to save state file: {e}")
        
    # 4. Check buffer to liquidation
    min_buffer_pct = None
    alert_leg = None
    
    for leg in snap.get("legs", []):
        if leg["size"] <= 0:
            continue
        entry = leg["entry"]
        lev = leg.get("lev", 10.0)
        side = leg["side"]
        
        # Calculate manual liquidation price estimation
        if side == "long":
            liq = entry * (1.0 - 0.95 / lev)
            dist = (price - liq) / price * 100.0
        else:
            liq = entry * (1.0 + 0.95 / lev)
            dist = (liq - price) / price * 100.0
            
        if min_buffer_pct is None or dist < min_buffer_pct:
            min_buffer_pct = dist
            alert_leg = f"{side.upper()} (entry={entry}, liq={round(liq, 4)})"
            
    # 5. Determine if we should wake up via Local LLM
    now_minute = datetime.datetime.now().minute
    llm_decision = ask_local_llm(price, vol_index, price_move_pct, min_buffer_pct, alert_leg, now_minute)
    
    if llm_decision and "wake" in llm_decision and "reason" in llm_decision:
        print("[WATCHDOG] LLM successfully processed watchdog check.")
        wake = llm_decision["wake"]
        reason = llm_decision["reason"]
        note = llm_decision.get("note", "LLM-driven alert.")
    else:
        # Fallback to rule-based logic if LLM fails
        print("[WATCHDOG] Warning: LLM failed, falling back to rule-based math decision.")
        wake = False
        reason = "scheduled_5m"
        note = "Everything is quiet. (Rule-based Fallback)"
        
        if min_buffer_pct is not None and min_buffer_pct < 3.0:
            wake = True
            reason = "liq_alert"
            note = f"CRITICAL: Liquidation buffer for {alert_leg} is only {round(min_buffer_pct, 2)}%! (Rule-based Fallback)"
        elif vol_index > 0.75 or price_move_pct > 1.0:
            wake = True
            reason = "vol_spike"
            note = f"UNPLANNED WAKEUP: Volatility spike! (Rule-based Fallback)"
        else:
            is_15m = any(abs(now_minute - m) <= 1 for m in [0, 15, 30, 45])
            if is_15m:
                wake = True
                reason = "scheduled_15m"
                note = f"PLANNED WAKEUP: 15-minute cycle reached. (Rule-based Fallback)"

    # Write output signal
    signal_data = {
        "wake": wake,
        "reason": reason,
        "vol_index": round(vol_index, 3),
        "price_move_pct": round(price_move_pct, 3),
        "buffer_pct": round(min_buffer_pct, 2) if min_buffer_pct is not None else 100.0,
        "note": note,
        "timestamp_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    }
    
    try:
        os.makedirs(SIGNAL_FILE.parent, exist_ok=True)
        SIGNAL_FILE.write_text(json.dumps(signal_data, indent=2, ensure_ascii=False))
        print(f"[WATCHDOG] Signal updated: wake={wake}, reason={reason}, note='{note}'")
    except Exception as e:
        print(f"[WATCHDOG] Failed to write signal file: {e}")

def sleep_until_next_5m():
    import time
    now = datetime.datetime.now()
    minutes_to_next = 5 - (now.minute % 5)
    target = now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=minutes_to_next)
    sleep_secs = (target - datetime.datetime.now()).total_seconds()
    if sleep_secs < 0:
        sleep_secs = 300
    print(f"[WATCHDOG] Sleeping for {round(sleep_secs, 1)} seconds until next 5m boundary ({target.strftime('%H:%M:%S')})")
    time.sleep(sleep_secs)

if __name__ == "__main__":
    # Check if run with --oneshot (for testing/manual run)
    if len(sys.argv) > 1 and sys.argv[1] == "--oneshot":
        main()
    else:
        print("[WATCHDOG DAEMON] Starting continuous daemon...")
        while True:
            try:
                main()
            except Exception as e:
                print(f"[WATCHDOG DAEMON] Error inside cycle: {e}")
            sleep_until_next_5m()
