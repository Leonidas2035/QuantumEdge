import asyncio, os, time
from dotenv import load_dotenv
load_dotenv()
import ccxt.async_support as ccxt

CCXT_SYMBOL = "XRP/USDT:USDT"
async def main():
    ak = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY","")
    sc = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET","")
    ex = ccxt.bingx({"apiKey":ak,"secret":sc,"options":{"defaultType":"swap"},"enableRateLimit":True,"timeout":30000})
    ex.set_sandbox_mode(True)
    since = int((time.time() - 9*3600)*1000)  # last 9h
    try:
        await ex.load_markets()
        trades = await ex.fetch_my_trades(CCXT_SYMBOL, since=since, limit=200)
        print(f"TRADES in last 9h: {len(trades)}")
        realized = 0.0
        closing = 0
        for t in trades:
            info = t.get("info", {})
            pnl = float(info.get("realizedPnl") or info.get("realisedPnl") or t.get("info",{}).get("profit") or 0)
            realized += pnl
            if pnl != 0:
                closing += 1
                print(f"  {t.get('datetime')} {t.get('side')} amt={t.get('amount')} @ {t.get('price')} realizedPnl={pnl}")
        print(f"\nTrades with realized PnL (closing fills): {closing}")
        print(f"SUM realized PnL (9h): {round(realized,4)} VST")
    except Exception as e:
        print("fetch_my_trades error:", e)
        # fallback: income history
        try:
            inc = await ex.fetch_ledger(code="VST", since=since, limit=200)
            print("ledger entries:", len(inc))
        except Exception as e2:
            print("ledger error:", e2)
    finally:
        await ex.close()
asyncio.run(main())
