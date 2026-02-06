import ccxt.async_support as ccxt
import asyncio
import logging

class BingXExecutionGateway:
    def __init__(self, config):
        self.logger = logging.getLogger("BingXGateway")
        self.symbol = config.symbol
        # Ініціалізація BingX (Swap)
        self.exchange = ccxt.bingx({
            'apiKey': config.bingx_api_key,
            'secret': config.bingx_secret,
            'options': { 'defaultType': 'swap' }
        })
        if config.use_sandbox:
            self.exchange.set_sandbox_mode(True)

    async def execute(self, action):
        side = 'buy' if 'BUY' in action.action_type else 'sell'
        if 'SHORT' in action.action_type: side = 'sell'
        amount = action.qty
        
        try:
            self.logger.info(f"🚀 BINGX SENDING: {side.upper()} {amount} {self.symbol}...")
            # Відправка ордера
            order = await self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side=side,
                amount=amount
            )
            self.logger.info(f"✅ BINGX FILLED! ID: {order['id']}")
            return True
        except Exception as e:
            self.logger.error(f"❌ BINGX ERROR: {e}")
            return False
