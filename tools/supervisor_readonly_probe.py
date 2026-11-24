"""
Supervisor READ-ONLY probe: connect to BingX (testnet/VST) and report
balance, open positions, and open orders for XRP/USDT.

NO orders are placed. This is strictly read-only (fetch_balance /
fetch_positions / fetch_open_orders).

Run: .venv/bin/python3 tools/supervisor_readonly_probe.py
"""
import asyncio
import os
import sys
import ccxt.async_support as ccxt

# Load .env if present (symlink to ~/.hermes/.env)
from dotenv import load_dotenv
load_dotenv()

SYMBOL = os.getenv("MARKET_DATA_SYMBOLS", "XRPUSDT").split(",")[0]
USE_TESTNET = str(os.getenv("USE_TESTNET", os.getenv("BINANCE_TESTNET", "1"))).lower() in {"1", "true", "t"}

# Prefer testnet creds when in testnet mode
if USE_TESTNET:
    api_key = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY", "")
    secret = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET", "")
else:
    api_key = os.getenv("BINGX_API_KEY", "")
    secret = os.getenv("BINGX_SECRET", "")

BASE = SYMBOL.replace("USDT", "")
CCXT_SYMBOL = f"{BASE}/USDT:USDT"   # swap/perp format for BingX


async def main():
    print(f"[SUPERVISOR PROBE] symbol={SYMBOL} ccxt_symbol={CCXT_SYMBOL} testnet={USE_TESTNET}")
    if not api_key or not secret:
        print("[SUPERVISOR PROBE] ❌ No API key/secret found in env. Aborting (read-only).")
        return

    ex = ccxt.bingx({
        "apiKey": api_key,
        "secret": secret,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
        "timeout": 30000,
    })
    if USE_TESTNET:
        ex.set_sandbox_mode(True)
        print("[SUPERVISOR PROBE] ⚠️ BINGX VST (DEMO) MODE — read-only")

    try:
        # Check connectivity first
        try:
            await ex.load_markets()
            print(f"[SUPERVISOR PROBE] ✅ Connected. markets loaded; {CCXT_SYMBOL} tradable: {CCXT_SYMBOL in ex.markets}")
        except Exception as e:
            print(f"[SUPERVISOR PROBE] ⚠️ load_markets warning: {e}")

        # 1) BALANCE (swap account)
        ex.options["defaultType"] = "swap"
        bal = await ex.fetch_balance()
        quote = "VST" if USE_TESTNET else "USDT"
        q = bal.get(quote, {})
        print("\n=== БАЛАНС (swap/{}".format(quote) + ") ===")
        print(f"  total={q.get('total')}  free={q.get('free')}  used={q.get('used')}")
        # also show any non-zero coin balances
        nonzero = {k: v for k, v in bal.get('total', {}).items() if v}
        print(f"  coins(total>0): {nonzero}")

        # 2) OPEN POSITIONS
        print(f"\n=== ВІДКРИТІ ПОЗИЦІЇ ({CCXT_SYMBOL}) ===")
        try:
            positions = await ex.fetch_positions(symbols=[CCXT_SYMBOL])
            open_pos = [p for p in positions
                        if float(p.get('contracts') or p.get('size') or 0) != 0]
            if not open_pos:
                print("  (немає відкритих позицій)")
            for p in open_pos:
                print(f"  {p.get('symbol')} side={p.get('side')} size={p.get('contracts')} "
                      f"entry={p.get('entryPrice')} uPnL={p.get('unrealizedPnl')} "
                      f"lev={p.get('leverage')} liq={p.get('liquidationPrice')}")
        except Exception as e:
            print(f"  ⚠️ fetch_positions error: {e}")

        # 3) OPEN ORDERS
        print(f"\n=== ВІДКРИТІ ОРДЕРИ ({CCXT_SYMBOL}) ===")
        try:
            orders = await ex.fetch_open_orders(symbol=CCXT_SYMBOL)
            if not orders:
                print("  (немає відкритих ордерів) — біржа, ймовірно, заблокувала нові через просадку")
            for o in orders[:20]:
                print(f"  id={o.get('id')} {o.get('side')} {o.get('type')} "
                      f"amt={o.get('amount')} @ {o.get('price')} status={o.get('status')}")
            if len(orders) > 20:
                print(f"  ...ще {len(orders)-20} ордерів")
        except Exception as e:
            print(f"  ⚠️ fetch_open_orders error: {e}")

    except Exception as e:
        print(f"[SUPERVISOR PROBE] ❌ Connection/query error: {e}")
    finally:
        try:
            ex.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
