"""
Supervisor ACTION script — executes user-sanctioned operations B + C.

B) Cancel ALL open orders for XRP/USDT (does NOT close positions).
C) Partially close SHORT to reduce net-short imbalance (buy-to-cover in hedge mode).

Mode: BingX VST (testnet) per USE_TESTNET env. These are REAL actions on the
connected account (testnet), authorized by the user.

Run: .venv/bin/python3 tools/supervisor_action_bc.py
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

import ccxt.async_support as ccxt

SYMBOL = os.getenv("MARKET_DATA_SYMBOLS", "XRPUSDT").split(",")[0]
USE_TESTNET = str(os.getenv("USE_TESTNET", os.getenv("BINANCE_TESTNET", "1"))).lower() in {"1", "true", "t"}
BASE = SYMBOL.replace("USDT", "")
CCXT_SYMBOL = f"{BASE}/USDT:USDT"

# C) volume to close from SHORT (conservative: ~half of 850,692)
SHORT_CLOSE_QTY = float(os.getenv("SUPERVISOR_SHORT_CLOSE_QTY", "425000"))


async def main():
    if USE_TESTNET:
        api_key = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY", "")
        secret = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET", "")
    else:
        api_key = os.getenv("BINGX_API_KEY", "")
        secret = os.getenv("BINGX_SECRET", "")

    ex = ccxt.bingx({
        "apiKey": api_key,
        "secret": secret,
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
        "timeout": 30000,
    })
    if USE_TESTNET:
        ex.set_sandbox_mode(True)
        print("[ACTION] ⚠️ BINGX VST (DEMO) MODE")

    try:
        # ---- B) CANCEL ALL OPEN ORDERS ----
        print(f"\n=== B) CANCEL ALL OPEN ORDERS ({CCXT_SYMBOL}) ===")
        try:
            cancelled = await ex.cancel_all_orders(symbol=CCXT_SYMBOL)
            print(f"  ✅ Cancelled. Response type: {type(cancelled).__name__}, count={len(cancelled) if isinstance(cancelled, list) else 'n/a'}")
        except Exception as e:
            print(f"  ⚠️ cancel_all_orders error: {e}")

        # ---- C) PARTIALLY CLOSE SHORT (buy-to-cover, hedge mode) ----
        print(f"\n=== C) PARTIAL CLOSE SHORT ({SHORT_CLOSE_QTY} contracts, buy/SHORT) ===")
        try:
            order = await ex.create_order(
                symbol=CCXT_SYMBOL,
                type="market",
                side="buy",
                amount=SHORT_CLOSE_QTY,
                params={"positionSide": "SHORT"},
            )
            print(f"  ✅ Order placed: id={order.get('id')} side={order.get('side')} "
                  f"amt={order.get('amount')} status={order.get('status')}")
        except Exception as e:
            print(f"  ❌ Partial-close SHORT error: {e}")

        # ---- VERIFY: current positions after action ----
        print(f"\n=== VERIFY: POSITIONS AFTER ACTION ({CCXT_SYMBOL}) ===")
        try:
            await ex.load_markets()
            positions = await ex.fetch_positions(symbols=[CCXT_SYMBOL])
            open_pos = [p for p in positions
                        if float(p.get('contracts') or p.get('size') or 0) != 0]
            if not open_pos:
                print("  (немає відкритих позицій)")
            for p in open_pos:
                print(f"  {p.get('symbol')} side={p.get('side')} size={p.get('contracts')} "
                      f"entry={p.get('entryPrice')} uPnL={p.get('unrealizedPnl')} lev={p.get('leverage')}")
        except Exception as e:
            print(f"  ⚠️ verify positions error: {e}")

        # ---- VERIFY: open orders count after cancel ----
        print(f"\n=== VERIFY: OPEN ORDERS AFTER CANCEL ({CCXT_SYMBOL}) ===")
        try:
            orders = await ex.fetch_open_orders(symbol=CCXT_SYMBOL)
            print(f"  remaining open orders: {len(orders)}")
            for o in orders[:10]:
                print(f"    id={o.get('id')} {o.get('side')} {o.get('type')} amt={o.get('amount')} @ {o.get('price')}")
        except Exception as e:
            print(f"  ⚠️ verify orders error: {e}")

    except Exception as e:
        print(f"[ACTION] ❌ Fatal error: {e}")
    finally:
        try:
            await ex.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
