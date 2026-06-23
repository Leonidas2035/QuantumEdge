import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add scalper_v1 directory to path so bot can be imported
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src/quantum_edge_core/strategies/scalper_v1"))

# Load secrets from config/secrets.local.env
env_path = PROJECT_ROOT / "config/secrets.local.env"
print(f"Loading env from {env_path}")
load_dotenv(dotenv_path=env_path)

from bot.exchanges.bingx_swap.client import BingXClient
from bot.exchanges.bingx_swap.execution import BingXExecution

async def main():
    api_key = os.getenv("BINGX_DEMO_API_KEY")
    api_secret = os.getenv("BINGX_DEMO_API_SECRET")
    
    if not api_key or not api_secret:
        print("Error: BINGX_DEMO_API_KEY or BINGX_DEMO_API_SECRET not set in env.")
        return 1

    # Base URL for demo trading: open-api-vst.bingx.com
    base_url = "https://open-api-vst.bingx.com"
    print(f"Connecting to BingX Demo API: {base_url}")
    
    client = BingXClient(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        recv_window=5000,
        timeout=10.0
    )
    
    exec_manager = BingXExecution(client)
    
    print("\n--- Fetching Account Balances ---")
    try:
        raw_balance = await client.request("GET", "/openApi/swap/v2/user/balance", params={}, signed=True)
        print("Raw Balance Response:", raw_balance)
        balances = await exec_manager.get_balances()
        print(f"Total balances returned: {len(balances)}")
        for b in balances:
            print(f"Asset: {b.asset} | Available: {b.available} | Total: {b.total}")
    except Exception as e:
        print(f"Failed to fetch balances: {e}")
        
    print("\n--- Fetching Open Positions ---")
    try:
        raw_positions = await client.request("GET", "/openApi/swap/v2/user/positions", params={}, signed=True)
        print("Raw Positions Response:", raw_positions)
        positions = await exec_manager.get_positions()
        active_positions = [p for p in positions if p.qty != 0]
        if not active_positions:
            print("No open positions.")
        for p in active_positions:
            print(
                f"Symbol: {p.symbol} | Side: {p.position_side} | Qty: {p.qty} | "
                f"Entry Price: {p.entry_price} | Unrealized PnL: {p.unrealized_pnl} | Leverage: {p.leverage}x"
            )
    except Exception as e:
        print(f"Failed to fetch positions: {e}")

if __name__ == "__main__":
    asyncio.run(main())
