import ccxt, json, datetime as dt

def try_ex(name, symci):
    try:
        e=getattr(ccxt,name)({"enableRateLimit":True})
        e.load_markets()
    except Exception as ex:
        return {"ex":name,"load_err":str(ex)[:80]}
    out={"ex":name}
    sym=None
    for s in symci:
        if s in e.markets: sym=s; break
    out["symbol"]=sym
    if not sym: return out
    # ohlcv
    try:
        oh=e.fetch_ohlcv(sym,"1h",limit=5)
        out["ohlcv_n"]=len(oh); out["last_close"]=oh[-1][4]
    except Exception as ex: out["ohlcv_err"]=str(ex)[:80]
    # funding
    try:
        fr=e.fetch_funding_rate(sym)
        out["funding"]=fr.get("fundingRate")
    except Exception as ex: out["funding_err"]=str(ex)[:60]
    # open interest
    try:
        oi=e.fetch_open_interest(sym)
        out["oi"]={"amount":oi.get("openInterestAmount"),"value":oi.get("openInterestValue")}
    except Exception as ex: out["oi_err"]=str(ex)[:60]
    # OI history (magnet building needs this)
    try:
        oih=e.fetch_open_interest_history(sym, "1h", limit=5)
        out["oi_hist_n"]=len(oih)
    except Exception as ex: out["oi_hist_err"]=str(ex)[:60]
    return out

for name,syms in [("bingx",["BTC/USDT:USDT","BTC/USDT"]),
                  ("bybit",["BTC/USDT:USDT","BTC/USDT"])]:
    print(json.dumps(try_ex(name,syms), default=str, ensure_ascii=False))
