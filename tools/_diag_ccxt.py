import asyncio, os
from dotenv import load_dotenv
load_dotenv()
import ccxt.async_support as ccxt

async def t():
    ex = ccxt.bingx({
        'apiKey': os.getenv('BINGX_TESTNET_API_KEY'),
        'secret': os.getenv('BINGX_TESTNET_SECRET'),
        'options': {'defaultType': 'swap'},
        'enableRateLimit': True,
    })
    ex.set_sandbox_mode(True)
    print('type(load_markets):', type(ex.load_markets))
    print('type(fetch_balance):', type(ex.fetch_balance))
    lm = ex.load_markets
    print('is coroutine fn:', asyncio.iscoroutinefunction(lm))
    try:
        m = ex.load_markets()
        print('load_markets returned type:', type(m))
        if hasattr(m, '__await__'):
            m = await m
            print('after await, type:', type(m), 'nkeys:', len(m))
    except Exception as e:
        print('load_markets err:', repr(e))
    await ex.close()

asyncio.run(t())
