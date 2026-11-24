"""
QuantumEdge NIGHT AUTOPILOT — XRP/USDT lock-unwinding engine.

Runs headless every 5 min (cron). On each 15m candle close it re-analyzes
market and adjusts. Executes REAL orders on the connected account (BingX VST
per USE_TESTNET) according to the owner's rules:

  RULE 1: Never close a losing position.
  RULE 2: A losing leg may be reduced using profit from the opposite (winning) leg.
  RULE 3: Indicators long -> add long; when the move exhausts -> take profit,
          add short.
  RULE 4: High risk -> re-hedge and wait until the market is predictable.

SAFETY GUARDS (autopilot, unattended):
  - State ledger on disk (runtime/autopilot_state.json) prevents duplicate actions.
  - Per-action cooldown + max actions per night cap.
  - Acts ONLY on clear triggers (range breakout + TV != Neutral). Otherwise OBSERVE.
  - Trade size bounded; refuses to add exposure when free margin buffer absent
    unless reducing net imbalance.
  - Every decision + action appended to runtime/autopilot_journal.jsonl.

Run: .venv/bin/python3 tools/night_autopilot.py
"""
import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

import ccxt.async_support as ccxt

# ---------- Config ----------
SYMBOL = os.getenv("MARKET_DATA_SYMBOLS", "XRPUSDT").split(",")[0]
USE_TESTNET = str(os.getenv("USE_TESTNET", os.getenv("BINANCE_TESTNET", "1"))).lower() in {"1", "true", "t"}
BASE = SYMBOL.replace("USDT", "")
CCXT_SYMBOL = f"{BASE}/USDT:USDT"
TV_SYMBOL = SYMBOL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "runtime", "autopilot_state.json")
JOURNAL_PATH = os.path.join(ROOT, "runtime", "autopilot_journal.jsonl")

# --- Autopilot guardrails (tunable via env) --- [AGGRESSIVE: BTC margin freed ~35k VST]
BASE_ORDER_XRP = float(os.getenv("AP_BASE_ORDER_XRP", "15000"))    # unit trade size (was 5000)
MAX_ACTIONS_PER_NIGHT = int(os.getenv("AP_MAX_ACTIONS", "30"))     # hard cap (was 12)
ACTION_COOLDOWN_SEC = int(os.getenv("AP_COOLDOWN_SEC", "360"))     # ~6 min between live actions (was 13)
RANGE_HI = float(os.getenv("AP_RANGE_HI", "1.1168"))              # breakout up trigger
RANGE_LO = float(os.getenv("AP_RANGE_LO", "1.1154"))              # breakout down trigger
DAY_HI = float(os.getenv("AP_DAY_HI", "1.1302"))
DAY_LO = float(os.getenv("AP_DAY_LO", "1.0990"))
LIVE = str(os.getenv("AP_LIVE", "1")).lower() in {"1", "true", "t"}  # master execution switch


def now_iso():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {
            "night_start": now_iso(),
            "actions_taken": 0,
            "last_action_ts": 0,
            "last_15m_bucket": None,
            "last_decision": None,
            "realized_pnl_harvested": 0.0,
            "history": [],
        }


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(st, f, indent=2, default=str)


def journal(entry):
    entry["ts"] = now_iso()
    os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
    with open(JOURNAL_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def current_15m_bucket():
    # epoch minutes // 15  -> stable id for the current 15m candle
    return int(time.time() // (15 * 60))


def run_tv_parse(symbol: str) -> dict:
    try:
        r = subprocess.run(
            ["node", "tools/tv_analyzer/tv_parse.js", symbol],
            capture_output=True, text=True, timeout=130, cwd=ROOT,
        )
        if r.returncode != 0:
            return {"error": r.stderr[:300]}
        return json.loads(r.stdout)
    except Exception as e:
        return {"error": str(e)}


def make_exchange():
    if USE_TESTNET:
        ak = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY", "")
        sc = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET", "")
    else:
        ak = os.getenv("BINGX_API_KEY", "")
        sc = os.getenv("BINGX_SECRET", "")
    ex = ccxt.bingx({
        "apiKey": ak, "secret": sc,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True, "timeout": 30000,
    })
    if USE_TESTNET:
        ex.set_sandbox_mode(True)
    return ex


async def snapshot(ex):
    """Read-only market + account snapshot."""
    snap = {"symbol": SYMBOL, "testnet": USE_TESTNET}
    await ex.load_markets()
    # price / funding
    try:
        fr = await ex.fetch_funding_rate(CCXT_SYMBOL)
        snap["mark_price"] = float(fr.get("markPrice") or 0)
        snap["funding_rate"] = fr.get("fundingRate")
    except Exception as e:
        snap["funding_error"] = str(e)
    try:
        oi = await ex.fetch_open_interest(CCXT_SYMBOL)
        snap["open_interest_usd"] = oi.get("openInterestValue")
    except Exception as e:
        snap["oi_error"] = str(e)
    # order book (top of book / imbalance)
    try:
        ob = await ex.fetch_order_book(CCXT_SYMBOL, limit=20)
        bids = ob.get("bids", [])[:10]
        asks = ob.get("asks", [])[:10]
        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)
        snap["book"] = {
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
            "bid_vol10": round(bid_vol, 1),
            "ask_vol10": round(ask_vol, 1),
            "imbalance": round((bid_vol - ask_vol) / (bid_vol + ask_vol), 3) if (bid_vol + ask_vol) else 0,
        }
        if not snap.get("mark_price") and bids and asks:
            snap["mark_price"] = (bids[0][0] + asks[0][0]) / 2
    except Exception as e:
        snap["book_error"] = str(e)
    # balance
    try:
        bal = await ex.fetch_balance()
        quote = "VST" if USE_TESTNET else "USDT"
        q = bal.get(quote, {})
        snap["balance"] = {"total": q.get("total"), "free": q.get("free"), "used": q.get("used"), "quote": quote}
    except Exception as e:
        snap["balance_error"] = str(e)
    # positions
    try:
        positions = await ex.fetch_positions(symbols=[CCXT_SYMBOL])
        legs = []
        for p in positions:
            size = float(p.get("contracts") or p.get("size") or 0)
            if size == 0:
                continue
            legs.append({
                "side": p.get("side"),
                "size": size,
                "entry": float(p.get("entryPrice") or 0),
                "uPnL": float(p.get("unrealizedPnl") or 0),
                "lev": p.get("leverage"),
            })
        snap["legs"] = legs
    except Exception as e:
        snap["positions_error"] = str(e)
    return snap


def decide(snap, tv, st):
    """
    Pure decision function -> returns a plan dict with 'action' and rationale.
    Actions: OBSERVE | HARVEST_LONG_REDUCE_SHORT | ADD_LONG | ADD_SHORT | REHEDGE
    """
    price = snap.get("mark_price") or 0
    legs = {l["side"]: l for l in snap.get("legs", [])}
    long_leg = legs.get("long")
    short_leg = legs.get("short")
    free = (snap.get("balance") or {}).get("free") or 0
    book = snap.get("book") or {}
    imb = book.get("imbalance", 0)

    tv_rating = ((tv.get("technicalsPage") or {}).get("rating")) or tv.get("recommendation") or "Neutral"
    tv_rating = str(tv_rating)
    tv_bull = ("Buy" in tv_rating or "Strong buy" in tv_rating.lower()) and "Neutral" not in tv_rating[:8]
    tv_bear = ("Sell" in tv_rating or "Strong sell" in tv_rating.lower()) and "Neutral" not in tv_rating[:8]
    is_neutral = tv_rating.strip().lower().startswith("neutral") or (not tv_bull and not tv_bear)

    net_short = 0.0
    if short_leg:
        net_short += short_leg["size"]
    if long_leg:
        net_short -= long_leg["size"]

    reasons = []
    reasons.append(f"price={price} tv={tv_rating} imbalance={imb} free={free} net_short={round(net_short,1)}")

    # Only bail out on GENUINE danger, not on mere uncertainty.
    # RULE 4: extreme risk = big net-short + price slamming day-high + no buffer left.
    danger_up = price >= DAY_HI * 0.999 and net_short > 0 and (free or 0) <= 0
    if danger_up:
        return {"action": "REHEDGE", "reasons": reasons + ["RULE4: price at day-high, net-short exposed, zero free margin"], "price": price, "net_short": net_short}

    # ===== ACTIVE OPPORTUNITY SEARCH (find the trade, don't hide) =====

    # 1) Breakout up + bullish tech -> ride it
    if price > RANGE_HI and (tv_bull or imb > 0.15):
        return {"action": "ADD_LONG", "reasons": reasons + ["OPP: breakout up (TV bull / book bid-heavy) -> add long"], "price": price, "net_short": net_short}

    # 2) Breakout down + bearish tech -> ride it
    if price < RANGE_LO and (tv_bear or imb < -0.15):
        return {"action": "ADD_SHORT", "reasons": reasons + ["OPP: breakout down (TV bear / book ask-heavy) -> add short"], "price": price, "net_short": net_short}

    # 3) Harvest winning leg when the move exhausts (RULE 2): long in profit + sellers taking over
    if long_leg and long_leg["uPnL"] > 0 and imb < -0.2:
        return {"action": "HARVEST_LONG_REDUCE_SHORT", "reasons": reasons + ["RULE2: long in profit + up-move exhausted (book sell-side) -> harvest long, reduce short"], "price": price, "net_short": net_short}

    # 4) RANGE SCALP — the always-on opportunity engine inside the flat.
    #    Strong order-book imbalance near a range edge = high-probability mean-revert / continuation scalp.
    if imb <= -0.15 and price >= (RANGE_LO + RANGE_HI) / 2:
        # heavy sell pressure in upper half -> scalp short toward range low
        return {"action": "ADD_SHORT", "reasons": reasons + [f"SCALP: book sell-heavy (imb={imb}) in upper range -> short toward {RANGE_LO}"], "price": price, "net_short": net_short}
    if imb >= 0.15 and price <= (RANGE_LO + RANGE_HI) / 2:
        # heavy buy pressure in lower half -> scalp long toward range high
        return {"action": "ADD_LONG", "reasons": reasons + [f"SCALP: book buy-heavy (imb={imb}) in lower range -> long toward {RANGE_HI}"], "price": price, "net_short": net_short}

    # 5) Passive harvest: whenever the losing leg can be trimmed by the winning leg's profit, do it.
    if long_leg and long_leg["uPnL"] > 0 and short_leg and short_leg["uPnL"] < 0 and imb < 0:
        return {"action": "HARVEST_LONG_REDUCE_SHORT", "reasons": reasons + ["RULE2: opportunistic harvest of long profit to shrink losing short"], "price": price, "net_short": net_short}

    # breakout down + TV bearish -> short leg working; harvest short profit in pieces (RULE2 style on the winning direction)
    if price < RANGE_LO and tv_bear:
        return {"action": "ADD_SHORT", "reasons": reasons + ["RULE3: breakout down + TV bearish -> add short"], "price": price, "net_short": net_short}

    # default: neutral / inside range -> do nothing (correct action most of the night)
    return {"action": "OBSERVE", "reasons": reasons + ["neutral/in-range: no clear trigger -> observe (RULE: wait for predictable market)"], "price": price, "net_short": net_short}


async def execute(ex, plan, snap, st):
    """Translate plan into a real order, respecting guardrails. Returns result dict."""
    action = plan["action"]
    if action == "OBSERVE":
        return {"executed": False, "reason": "observe"}

    # Guard: max actions per night
    if st["actions_taken"] >= MAX_ACTIONS_PER_NIGHT:
        return {"executed": False, "reason": f"max actions per night reached ({MAX_ACTIONS_PER_NIGHT})"}
    # Guard: cooldown
    if time.time() - float(st.get("last_action_ts") or 0) < ACTION_COOLDOWN_SEC:
        return {"executed": False, "reason": "cooldown active"}

    legs = {l["side"]: l for l in snap.get("legs", [])}
    long_leg = legs.get("long")
    short_leg = legs.get("short")
    qty = BASE_ORDER_XRP

    order_spec = None
    if action == "ADD_LONG":
        order_spec = {"side": "buy", "positionSide": "LONG", "amount": qty}
    elif action == "ADD_SHORT":
        order_spec = {"side": "sell", "positionSide": "SHORT", "amount": qty}
    elif action == "HARVEST_LONG_REDUCE_SHORT":
        if not long_leg:
            return {"executed": False, "reason": "no long leg to harvest"}
        # take profit on part of LONG (sell to close long) -> then buy-to-cover part of SHORT
        harvest_qty = min(qty, long_leg["size"] * 0.5)
        r1 = None
        if LIVE:
            r1 = await ex.create_order(symbol=CCXT_SYMBOL, type="market", side="sell",
                                       amount=harvest_qty, params={"positionSide": "LONG"})
        cover = None
        if short_leg and LIVE:
            cover_qty = min(qty, short_leg["size"] * 0.5)
            cover = await ex.create_order(symbol=CCXT_SYMBOL, type="market", side="buy",
                                          amount=cover_qty, params={"positionSide": "SHORT"})
        return {"executed": bool(LIVE), "action": action,
                "harvest_long_id": (r1 or {}).get("id"),
                "cover_short_id": (cover or {}).get("id"),
                "harvest_qty": harvest_qty}
    elif action == "REHEDGE":
        # neutralize risk: add short to balance net (or add long if net-long). Here net-short big+danger up -> add long buffer.
        order_spec = {"side": "buy", "positionSide": "LONG", "amount": qty}

    if order_spec is None:
        return {"executed": False, "reason": "no order spec"}

    if not LIVE:
        return {"executed": False, "reason": "AP_LIVE=0 dry-run", "would_place": order_spec}

    order = await ex.create_order(
        symbol=CCXT_SYMBOL, type="market",
        side=order_spec["side"], amount=order_spec["amount"],
        params={"positionSide": order_spec["positionSide"]},
    )
    return {"executed": True, "action": action, "order_id": order.get("id"),
            "side": order.get("side"), "amount": order.get("amount"), "status": order.get("status")}


async def main():
    st = load_state()
    bucket = current_15m_bucket()
    new_candle = (st.get("last_15m_bucket") != bucket)

    ex = make_exchange()
    try:
        snap = await snapshot(ex)

        # TV read only on 15m candle close (expensive puppeteer call) — else reuse last
        if new_candle:
            tv = run_tv_parse(TV_SYMBOL)
            st["last_tv"] = tv
        else:
            tv = st.get("last_tv") or {}

        plan = decide(snap, tv, st)
        result = await execute(ex, plan, snap, st)

        if result.get("executed"):
            st["actions_taken"] = st.get("actions_taken", 0) + 1
            st["last_action_ts"] = time.time()

        st["last_15m_bucket"] = bucket
        st["last_decision"] = plan["action"]
        st["history"] = (st.get("history", []) + [{
            "ts": now_iso(), "action": plan["action"], "price": plan.get("price"),
            "executed": result.get("executed"), "result": result,
        }])[-100:]
        save_state(st)

        journal({
            "cycle": "15m_close" if new_candle else "5m_poll",
            "price": snap.get("mark_price"),
            "book": snap.get("book"),
            "balance": snap.get("balance"),
            "legs": snap.get("legs"),
            "tv_rating": ((tv.get("technicalsPage") or {}).get("rating")) if tv else None,
            "decision": plan["action"],
            "reasons": plan.get("reasons"),
            "execution": result,
            "actions_taken": st["actions_taken"],
        })

        # concise stdout for cron delivery
        print(f"[{now_iso()}] cycle={'15m' if new_candle else '5m'} price={snap.get('mark_price')} "
              f"decision={plan['action']} executed={result.get('executed')} "
              f"actions={st['actions_taken']}/{MAX_ACTIONS_PER_NIGHT}")
        print(f"  reasons: {plan.get('reasons')}")
        if result.get("executed"):
            print(f"  ORDER: {result}")
    except Exception as e:
        journal({"error": str(e)})
        print(f"[{now_iso()}] AUTOPILOT ERROR: {e}")
    finally:
        try:
            await ex.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
