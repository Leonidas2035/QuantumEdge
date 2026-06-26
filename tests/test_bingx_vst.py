import sys
import os
import ccxt
sys.path.insert(0, "/home/korben/QuantumEdge-main/src")
from quantum_edge_core.dyn_dca_bot.main import _load_secrets
_load_secrets()

api_key = os.getenv("BINGX_DEMO_API_KEY", "")
api_secret = os.getenv("BINGX_DEMO_API_SECRET", "")

exchange = ccxt.bingx({
    'apiKey': api_key,
    'secret': api_secret,
    'options': {'defaultType': 'swap'},
})
exchange.set_sandbox_mode(True)

try:
    order = exchange.create_order(
        symbol='BTC/USDT:USDT',
        type='limit',
        side='buy',
        amount=0.01,
        price=60000.0,
        params={'positionSide': 'LONG'}
    )
    print("Result (CCXT):", order['id'])
except Exception as e:
    print("CCXT Error:", e)

