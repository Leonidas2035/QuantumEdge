import ccxt.async_support as ccxt
import logging
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction


class BinanceExecutionGateway:
    def __init__(self, config):
        self.logger = logging.getLogger("BinanceGateway")
        self.symbol = config.symbol

        # Init CCXT Binance Futures
        self.exchange = ccxt.binance(
            {
                "apiKey": config.binance_api_key,
                "secret": config.binance_secret,
                "options": {
                    "defaultType": "future",
                },
            }
        )

        if config.use_testnet:
            self.exchange.set_sandbox_mode(True)
            self.logger.warning("⚠️ RUNNING IN BINANCE TESTNET MODE")

    async def execute(self, action: TradeAction) -> bool:
        """
        Executes a TradeAction on Binance via CCXT.
        """
        side = "buy" if "BUY" in action.action_type else "sell"
        # Hedge Short logic:
        if "SHORT" in action.action_type:
            side = "sell"

        amount = action.qty

        try:
            self.logger.info(
                f"🚀 BINANCE: Sending Market {side.upper()} {amount} {self.symbol}..."
            )

            # Execute Market Order
            # Warning: create_market_order signature: symbol, side, amount, price=None, params={}
            order = await self.exchange.create_order(
                symbol=self.symbol, type="market", side=side, amount=amount
            )

            self.logger.info(f"✅ BINANCE: Order Filled! ID: {order['id']}")
            return True

        except Exception as e:
            self.logger.error(f"❌ BINANCE Error: {e}")
            return False

    async def close(self):
        await self.exchange.close()
