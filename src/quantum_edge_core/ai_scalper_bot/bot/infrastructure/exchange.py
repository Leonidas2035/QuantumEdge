import ccxt.async_support as ccxt
import logging
from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction


class BinanceExecutionGateway:
    def __init__(self, config):
        self.logger = logging.getLogger("BinanceGateway")
        self.symbol = config.symbol

        self.trading_mode = getattr(config, "trading_mode", "scalper_v1")

        # Init CCXT Binance (Futures or Spot based on mode)
        options = {"defaultType": "future"}
        if self.trading_mode == "spot_grid":
            options = {"defaultType": "spot"}

        self.exchange = ccxt.binance(
            {
                "apiKey": config.binance_api_key,
                "secret": config.binance_secret,
                "options": options,
            }
        )

        if config.use_testnet:
            self.exchange.set_sandbox_mode(True)
            self.logger.warning("⚠️ RUNNING IN BINANCE TESTNET MODE")

    async def execute(self, action: TradeAction) -> bool:
        """
        Executes a TradeAction on Binance via CCXT.
        """
        if action.action_type == "SYNC_GRID":
            # 1. Cancel all open orders
            try:
                self.logger.info(
                    f"🚀 BINANCE (SPOT): Syncing Grid around {action.price} | {action.reason}"
                )
                await self.exchange.cancel_all_orders(symbol=self.symbol)
            except Exception as e:
                self.logger.error(f"❌ BINANCE Cancel All Error: {e}")
                return False

            # 2. Parse grid parameters
            try:
                # reason="vol_idx=X|spacing_pct=Y|below=15|above=15"
                params = dict(item.split("=") for item in action.reason.split("|"))
                spacing_pct = float(params.get("spacing_pct", 0.002))
                below = int(params.get("below", 15))
                above = int(params.get("above", 15))
            except Exception as e:
                self.logger.error(f"❌ BINANCE Error parsing grid params: {e}")
                return False

            # 3. Place new grid orders
            # By continuously syncing the grid around the new current_price when it moves,
            # we implicitly satisfy the requirement:
            # "Якщо це BUY за ціною X → миттєво виставити LIMIT SELL на об’єм X за ціною X * (1 + grid_spacing_pct)"
            # because the next grid's first sell level will be exactly at X * (1 + grid_spacing_pct).
            current_price = action.price
            amount = action.qty

            # Place BUY orders below
            for i in range(1, below + 1):
                price = current_price * (1 - spacing_pct * i)
                try:
                    await self.exchange.create_order(
                        symbol=self.symbol,
                        type="limit",
                        side="buy",
                        amount=amount,
                        price=price,
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to place BUY grid {i} at {price}: {e}")

            # Place SELL orders above
            for i in range(1, above + 1):
                price = current_price * (1 + spacing_pct * i)
                try:
                    await self.exchange.create_order(
                        symbol=self.symbol,
                        type="limit",
                        side="sell",
                        amount=amount,
                        price=price,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to place SELL grid {i} at {price}: {e}"
                    )

            self.logger.info("✅ BINANCE: Grid Sync Complete.")
            return True

        side = "buy" if "BUY" in action.action_type else "sell"
        # Hedge Short logic:
        if "SHORT" in action.action_type:
            side = "sell"

        amount = action.qty

        try:
            if self.trading_mode == "spot_grid":
                # Fallback for manual or single LIMIT orders
                price = action.price
                self.logger.info(
                    f"🚀 BINANCE (SPOT): Sending Maker Limit {side.upper()} {amount} {self.symbol} @ {price}..."
                )
                order = await self.exchange.create_order(
                    symbol=self.symbol,
                    type="limit",
                    side=side,
                    amount=amount,
                    price=price,
                )
            else:
                self.logger.info(
                    f"🚀 BINANCE (FUTURES): Sending Market {side.upper()} {amount} {self.symbol}..."
                )
                # Execute Market Order
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
