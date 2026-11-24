import asyncio, os
from dotenv import load_dotenv
load_dotenv()
import ccxt.async_support as ccxt
SYM="XRP/USDT:USDT"
async def t():
    ex=ccxt.bingx({'apiKey':os.getenv('BINGX_TESTNET_API_KEY'),'secret':os.getenv('BINGX_TESTNET_SECRET'),'options':{'defaultType':'swap'},'enableRateLimit':True})
    ex.set_sandbox_mode(True)
    try:
        fr=await ex.fetch_funding_rate(SYM); print('FUNDING:', {k:fr.get(k) for k in ['symbol','fundingRate','fundingTimestamp','markPrice','indexPrice']})
    except Exception as e: print('FUNDING_ERR', repr(e))
    try:
        oi=await ex.fetch_open_interest(SYM); print('OI:', oi)
    except Exception as e: print('OI_ERR', repr(e))
    await ex.close()
asyncio.run(t())
