import ccxt.async_support as ccxt
import logging
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction

class BingXExecutionGateway:
    def __init__(self, config):
        self.logger = logging.getLogger("BingXGateway")
        self.symbol = config.symbol
        
        # Init CCXT BingX
        self.exchange = ccxt.bingx({
            'apiKey': config.bingx_api_key,
            'secret': config.bingx_secret,
            'options': {
                'defaultType': 'swap', 
            }
        })
        
        if config.use_sandbox:
            self.exchange.set_sandbox_mode(True)
            self.logger.warning("⚠️ RUNNING IN BINGX SANDBOX MODE (VST)")

    async def execute(self, action: TradeAction) -> bool:
        """
        Executes a TradeAction on BingX via CCXT.
        """
        side = 'buy' if 'BUY' in action.action_type else 'sell'
        # Hedge Short logic:
        if 'SHORT' in action.action_type:
            side = 'sell'
        
        amount = action.qty
        
        try:
            # Check for minimal amount (BingX often requires > 0.0001 BTC)
            # For test, we force a minimum valid size if needed, or trust the strategy
            
            self.logger.info(f"🚀 BINGX: Sending Market {side.upper()} {amount} {self.symbol}...")
            
            # Execute Market Order
            # Warning: create_market_order signature: symbol, side, amount, price=None, params={}
            order = await self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side=side,
                amount=amount
            )
            
            self.logger.info(f"✅ BINGX: Order Filled! ID: {order['id']}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ BINGX Error: {e}")
            return False
            
    async def close(self):
        await self.exchange.close()
