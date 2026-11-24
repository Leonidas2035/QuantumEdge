import asyncio, os, time, json
from dotenv import load_dotenv
load_dotenv()
import ccxt.async_support as ccxt
CCXT_SYMBOL="XRP/USDT:USDT"
async def main():
    ak=os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY","")
    sc=os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET","")
    ex=ccxt.bingx({"apiKey":ak,"secret":sc,"options":{"defaultType":"swap"},"enableRateLimit":True,"timeout":30000})
    ex.set_sandbox_mode(True)
    since=int((time.time()-2*3600)*1000)
    await ex.load_markets()
    # 1) inspect one trade's raw info keys
    tr=await ex.fetch_my_trades(CCXT_SYMBOL, since=since, limit=5)
    print("=== sample trade.info keys ===")
    if tr:
        print(list(tr[-1].get("info",{}).keys()))
        print("last trade info:", json.dumps(tr[-1].get("info",{}), default=str)[:600])
    # 2) try income/ledger for REALIZED_PNL
    print("\n=== try fetchLedger ===")
    try:
        led=await ex.fetch_ledger(code="VST", since=since, limit=50)
        print("ledger entries:", len(led))
        for l in led[:10]:
            print("  ", l.get("datetime"), l.get("type"), l.get("amount"), l.get("info",{}).get("incomeType") if l.get("info") else "")
    except Exception as e:
        print("ledger err:", e)
    await ex.close()
asyncio.run(main())
