"""
QuantumEdge Trading Toolkit — CLI helpers the AGENT calls each cycle.

The agent (not a hardcoded script) is the brain. Each 5-min cron wakes the
agent, which runs these subcommands to SEE the market and ACT on it, then
reasons about grid placement, session-based breakout forecast, and TP.

BingX VST (testnet) hedge-mode. All actions REAL on the connected account.

Subcommands:
  snapshot                 -> JSON: price, book(±20), imbalance, balance, positions, open orders count, session
  book <depth>             -> JSON: order book walls (top clusters bid/ask)
  place <side> <posSide> <qty> <price> [tp]
                           -> limit order; if tp given, remembers intended TP in tag
  market <side> <posSide> <qty>
                           -> market order (used to take profit / cover)
  cancel_all               -> cancel all open XRP orders
  cancel <id>              -> cancel one order
  grid <lo> <hi> <n_side> <qty>
                           -> place n_side buy-limits below mid + n_side sell-limits above,
                              spread across [lo,hi] by volatility, +2 breakout orders each edge
  trades <hours>           -> realized PnL + closed trades in last N hours

Run: .venv/bin/python3 tools/qe_trade.py <subcommand> [args...]
"""
import asyncio, json, os, sys, time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
import ccxt.async_support as ccxt

SYMBOL = os.getenv("MARKET_DATA_SYMBOLS", "XRPUSDT").split(",")[0]
USE_TESTNET = str(os.getenv("USE_TESTNET", "1")).lower() in {"1","true","t"}
BASE = SYMBOL.replace("USDT","")
CCXT_SYMBOL = f"{BASE}/USDT:USDT"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

try:
    from quantum_edge_core.execution.bingx_intent import build_bingx_order_params
except ImportError:
    def build_bingx_order_params(intent: str, hedged: bool = True) -> dict:
        mapping = {
            "open_long": {"side": "buy", "positionSide": "LONG", "reduceOnly": False, "hedged": hedged},
            "open_short": {"side": "sell", "positionSide": "SHORT", "reduceOnly": False, "hedged": hedged},
            "close_long": {"side": "sell", "positionSide": "LONG", "reduceOnly": True, "hedged": hedged},
            "close_short": {"side": "buy", "positionSide": "SHORT", "reduceOnly": True, "hedged": hedged},
        }
        if not isinstance(intent, str) or intent not in mapping:
            raise ValueError(f"Unknown order intent: {intent!r}")
        return dict(mapping[intent])

JOURNAL = os.path.join(ROOT, "runtime", "trade_journal.jsonl")


def _resolve_hedge_intent(side: str, pos_side: str, closing_only: bool = False) -> str:
    s = str(side).strip().lower()
    ps = str(pos_side).strip().upper()
    if (s, ps) == ("sell", "LONG"):
        return "close_long"
    if (s, ps) == ("buy", "SHORT"):
        return "close_short"
    if not closing_only:
        if (s, ps) == ("buy", "LONG"):
            return "open_long"
        if (s, ps) == ("sell", "SHORT"):
            return "open_short"
    raise ValueError(
        f"Invalid BingX Hedge Mode order parameters (closing_only={closing_only}): side={side!r}, pos_side={pos_side!r}"
    )


def jlog(entry):
    entry["ts"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def trading_session():
    """Return active FX/crypto session by UTC hour (heuristics)."""
    h = datetime.now(timezone.utc).hour
    # Asia ~00-08 UTC, Europe ~07-16, US ~13-21 (overlaps noted)
    if 13 <= h < 21:
        return "US"          # most volatile / trending
    if 7 <= h < 13:
        return "EU"
    if 0 <= h < 7 or h >= 22:
        return "ASIA"
    return "EU_US_OVERLAP"


def make_ex():
    ak = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY","")
    sc = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET","")
    ex = ccxt.bingx({"apiKey":ak,"secret":sc,"options":{"defaultType":"swap"},
                     "enableRateLimit":True,"timeout":30000})
    if USE_TESTNET:
        ex.set_sandbox_mode(True)
    return ex


def walls(levels, n=5):
    """Cluster order book into biggest 'walls' (price, size)."""
    s = sorted(levels, key=lambda x: x[1], reverse=True)[:n]
    return [[round(p,4), round(v,1)] for p, v in s]


async def snapshot(ex):
    await ex.load_markets()
    out = {"symbol": SYMBOL, "session": trading_session(),
           "utc": datetime.now(timezone.utc).strftime("%H:%M UTC")}
    try:
        fr = await ex.fetch_funding_rate(CCXT_SYMBOL)
        out["mark_price"] = float(fr.get("markPrice") or 0)
        out["funding_rate"] = fr.get("fundingRate")
    except Exception as e:
        out["funding_error"] = str(e)
    try:
        ob = await ex.fetch_order_book(CCXT_SYMBOL, limit=20)
        bids, asks = ob.get("bids",[]), ob.get("asks",[])
        bv = sum(b[1] for b in bids[:10]); av = sum(a[1] for a in asks[:10])
        out["book"] = {
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
            "bid_vol10": round(bv,1), "ask_vol10": round(av,1),
            "imbalance": round((bv-av)/(bv+av),3) if (bv+av) else 0,
            "bid_walls": walls(bids), "ask_walls": walls(asks),
        }
        if not out.get("mark_price") and bids and asks:
            out["mark_price"] = (bids[0][0]+asks[0][0])/2
    except Exception as e:
        out["book_error"] = str(e)
    try:
        bal = await ex.fetch_balance()
        q = bal.get("VST" if USE_TESTNET else "USDT", {})
        out["balance"] = {"total": q.get("total"), "free": q.get("free"), "used": q.get("used")}
    except Exception as e:
        out["balance_error"] = str(e)
    try:
        ps = await ex.fetch_positions(symbols=[CCXT_SYMBOL])
        out["legs"] = [{"side":p.get("side"),"size":float(p.get("contracts") or 0),
                        "entry":float(p.get("entryPrice") or 0),"uPnL":float(p.get("unrealizedPnl") or 0),
                        "lev":p.get("leverage")} for p in ps if float(p.get("contracts") or 0)!=0]
    except Exception as e:
        out["positions_error"] = str(e)
    try:
        oo = await ex.fetch_open_orders(CCXT_SYMBOL)
        out["open_orders_count"] = len(oo)
        out["open_orders"] = [{"id":o.get("id"),"side":o.get("side"),"type":o.get("type"),
                               "amt":o.get("amount"),"price":o.get("price"),"status":o.get("status")} for o in oo[:40]]
    except Exception as e:
        out["orders_error"] = str(e)
    return out


async def place(ex, side, pos_side, qty, price, tp=None, sl=None):
    intent = _resolve_hedge_intent(side, pos_side, closing_only=False)
    mapped = build_bingx_order_params(intent, hedged=True)
    params = {
        "positionSide": mapped["positionSide"],
        "reduceOnly": mapped["reduceOnly"],
        "hedged": mapped["hedged"],
    }
    if not mapped["reduceOnly"]:
        if tp is not None and str(tp).lower() not in ("none", "null", ""):
            params["takeProfit"] = {"triggerPrice": float(tp)}
        if sl is not None and str(sl).lower() not in ("none", "null", ""):
            params["stopLoss"] = {"triggerPrice": float(sl)}
    o = await ex.create_order(symbol=CCXT_SYMBOL, type="limit", side=mapped["side"],
                               amount=float(qty), price=float(price), params=params)
    jlog({"act":"place","side":mapped["side"],"pos":mapped["positionSide"],"reduceOnly":mapped["reduceOnly"],"qty":qty,"price":price,"tp":tp,"sl":sl,"id":o.get("id")})
    return {"id":o.get("id"),"side":o.get("side"),"amount":o.get("amount"),"price":o.get("price"),"status":o.get("status"),"reduceOnly":mapped["reduceOnly"],"intended_tp":tp,"intended_sl":sl}


async def set_sl(ex, pos_side, new_sl):
    """Трейлінг-стоп: виставляє/оновлює СЛ позиції (breakeven / міні-профіт).
    Для BingX: скасовує існуючий СЛ-ордер та виставляє новий з amount = розмір позиції.
    Повертає результат запиту."""
    try:
        await ex.load_markets()
        
        # 1. Отримуємо розмір поточної позиції
        ps = await ex.fetch_positions(symbols=[CCXT_SYMBOL])
        pos = next((p for p in ps if p.get("side", "").upper() == pos_side.upper() and float(p.get("contracts") or 0) > 0), None)
        if not pos:
            return {"modified": False, "error": f"no open position found for side {pos_side}"}
        qty = float(pos["contracts"])

        # 2. Шукаємо та скасовуємо існуючі СЛ-ордери для цієї сторони
        oo = await ex.fetch_open_orders(CCXT_SYMBOL)
        cancelled_count = 0
        for o in oo:
            info = o.get("info", {})
            is_sl = False
            if o.get("type") in ("stop", "trigger"):
                is_sl = True
            elif info.get("stopLossPrice") or info.get("triggerPrice"):
                is_sl = True
                
            pos_side_info = info.get("positionSide", "").upper()
            if is_sl and pos_side_info == pos_side.upper():
                try:
                    await ex.cancel_order(o["id"], CCXT_SYMBOL)
                    cancelled_count += 1
                    jlog({"act": "cancel_sl_order", "id": o["id"], "pos": pos_side})
                except Exception as ce:
                    pass

        # 3. Виставляємо новий СЛ stop-ордер
        sl_side = "sell" if pos_side.upper() == "LONG" else "buy"
        params = {
            "positionSide": pos_side.upper(),
            "stopLossPrice": float(new_sl)
        }
        o = await ex.create_order(
            symbol=CCXT_SYMBOL,
            type="market",
            side=sl_side,
            amount=qty,
            price=None,
            params=params
        )
        jlog({"act": "modify_sl", "pos": pos_side, "new_sl": new_sl, "qty": qty, "cancelled_old": cancelled_count, "ok": True})
        return {
            "modified": True,
            "new_sl": new_sl,
            "qty": qty,
            "cancelled_old": cancelled_count,
            "id": o.get("id"),
            "status": o.get("status")
        }
    except Exception as e:
        jlog({"act": "modify_sl", "pos": pos_side, "new_sl": new_sl, "ok": False, "err": str(e)[:200]})
        return {"modified": False, "error": str(e)[:300]}


async def market(ex, side, pos_side, qty):
    intent = _resolve_hedge_intent(side, pos_side, closing_only=True)
    mapped = build_bingx_order_params(intent, hedged=True)
    close_params = {
        "positionSide": mapped["positionSide"],
        "reduceOnly": True,
        "hedged": mapped["hedged"],
    }
    # snapshot position entry BEFORE closing, to compute realized PnL ourselves
    # (BingX VST does not return realizedPnl via ccxt; fetchLedger unsupported)
    entry = None; mark = None
    try:
        await ex.load_markets()
        ps = await ex.fetch_positions(symbols=[CCXT_SYMBOL])
        for p in ps:
            if str(p.get("side","")).upper() == mapped["positionSide"] and float(p.get("contracts") or 0)!=0:
                entry = float(p.get("entryPrice") or 0)
        ob = await ex.fetch_order_book(CCXT_SYMBOL, limit=5)
        bids, asks = ob.get("bids",[]), ob.get("asks",[])
        mark = (bids[0][0]+asks[0][0])/2 if bids and asks else None
    except Exception:
        pass
    o = await ex.create_order(symbol=CCXT_SYMBOL, type="market", side=mapped["side"],
                              amount=float(qty), params=close_params)
    # realized pnl: closing a LONG (side=sell) profits when mark>entry;
    # closing a SHORT (side=buy) profits when entry>mark.
    realized = None
    if entry and mark:
        if mapped["positionSide"]=="LONG":
            realized = round((mark-entry)*float(qty), 4)
        else:  # SHORT
            realized = round((entry-mark)*float(qty), 4)
    rec = {"act":"close","side":mapped["side"],"pos":mapped["positionSide"],"reduceOnly":True,"qty":float(qty),
           "entry":entry,"exit":mark,"realized_pnl":realized,"id":o.get("id")}
    jlog(rec)
    return {"id":o.get("id"),"side":o.get("side"),"amount":o.get("amount"),
            "status":o.get("status"),"reduceOnly":True,"entry":entry,"exit":mark,"realized_pnl":realized}


async def cancel_all(ex):
    r = await ex.cancel_all_orders(symbol=CCXT_SYMBOL)
    jlog({"act":"cancel_all"})
    return {"cancelled": len(r) if isinstance(r,list) else "ok"}


async def cancel_one(ex, oid):
    r = await ex.cancel_order(oid, CCXT_SYMBOL)
    jlog({"act":"cancel","id":oid})
    return {"cancelled": oid}


async def grid(ex, lo, hi, n_side, qty):
    """15 buy-limits below mid + 15 sell-limits above + 2 breakout orders each edge."""
    await ex.load_markets()
    lo, hi, n_side, qty = float(lo), float(hi), int(n_side), float(qty)
    mid = (lo+hi)/2
    placed = []
    # buy-limits (LONG) from just below mid down to lo
    step_dn = (mid - lo) / n_side
    for i in range(1, n_side+1):
        p = round(mid - step_dn*i, 4)
        tp = round(p*1.002, 4)  # +0.2% TP
        try:
            o = await place(ex, "buy", "LONG", qty, p, tp); placed.append(o)
        except Exception as e:
            placed.append({"err":str(e),"price":p})
    # sell-limits (SHORT) from just above mid up to hi
    step_up = (hi - mid) / n_side
    for i in range(1, n_side+1):
        p = round(mid + step_up*i, 4)
        tp = round(p*0.998, 4)  # -0.2% TP for short
        try:
            o = await place(ex, "sell", "SHORT", qty, p, tp); placed.append(o)
        except Exception as e:
            placed.append({"err":str(e),"price":p})
    # 2 breakout orders beyond each edge
    for k in (1,2):
        pu = round(hi + step_up*k, 4); pdn = round(lo - step_dn*k, 4)
        try: placed.append(await place(ex, "buy", "LONG", qty, pu, round(pu*1.002,4)))   # breakout up -> momentum long
        except Exception as e: placed.append({"err":str(e),"price":pu})
        try: placed.append(await place(ex, "sell", "SHORT", qty, pdn, round(pdn*0.998,4)))  # breakdown -> momentum short
        except Exception as e: placed.append({"err":str(e),"price":pdn})
    jlog({"act":"grid","lo":lo,"hi":hi,"n_side":n_side,"qty":qty,"count":len(placed)})
    return {"placed": len(placed), "orders": placed}


async def trades(ex, hours):
    # BingX VST returns no realizedPnl; read our own close-journal instead.
    since = time.time() - float(hours)*3600
    closed=0; wins=0; realized=0.0; detail=[]
    try:
        for line in open(JOURNAL):
            try: d=json.loads(line)
            except: continue
            if d.get("act")!="close": continue
            ts=d.get("ts","")
            try:
                import datetime as _dt
                t=_dt.datetime.strptime(ts,"%Y-%m-%d %H:%M:%S").timestamp()
            except: t=since
            if t<since: continue
            r=d.get("realized_pnl")
            if r is None: continue
            closed+=1; realized+=r
            if r>0: wins+=1
            detail.append({"ts":ts,"pos":d.get("pos"),"qty":d.get("qty"),"entry":d.get("entry"),"exit":d.get("exit"),"pnl":round(r,4)})
    except FileNotFoundError:
        pass
    return {"closed_with_pnl":closed,"winning":wins,"realized_pnl":round(realized,4),"detail":detail[-20:]}


async def run():
    cmd = sys.argv[1] if len(sys.argv)>1 else "snapshot"
    ex = make_ex()
    try:
        if cmd == "snapshot":
            print(json.dumps(await snapshot(ex), default=str))
        elif cmd == "place":
            side,pos,qty,price = sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5]
            tp = sys.argv[6] if len(sys.argv)>6 else None
            sl = sys.argv[7] if len(sys.argv)>7 else None
            print(json.dumps(await place(ex,side,pos,qty,price,tp,sl)))
        elif cmd == "modify_sl":
            pos_side = sys.argv[2]
            new_sl = sys.argv[3]
            print(json.dumps(await set_sl(ex, pos_side, new_sl)))
        elif cmd == "market":
            print(json.dumps(await market(ex,sys.argv[2],sys.argv[3],sys.argv[4])))
        elif cmd == "cancel_all":
            print(json.dumps(await cancel_all(ex)))
        elif cmd == "cancel":
            print(json.dumps(await cancel_one(ex,sys.argv[2])))
        elif cmd == "grid":
            print(json.dumps(await grid(ex,sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5]), default=str))
        elif cmd == "trades":
            print(json.dumps(await trades(ex, sys.argv[2] if len(sys.argv)>2 else 9), default=str))
        else:
            print(json.dumps({"error":f"unknown cmd {cmd}"}))
    except Exception as e:
        print(json.dumps({"error":str(e)}))
    finally:
        try: await ex.close()
        except: pass

if __name__ == "__main__":
    asyncio.run(run())
