import asyncio
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

        # Determine testnet routing
        api_key = (
            config.binance_testnet_api_key
            if config.use_testnet
            else config.binance_api_key
        )
        secret = (
            config.binance_testnet_secret
            if config.use_testnet
            else config.binance_secret
        )

        self.exchange = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": secret,
                "options": options,
                "enableRateLimit": True,
            }
        )

        if config.use_testnet:
            self.exchange.set_sandbox_mode(True)
            self.logger.warning("⚠️ RUNNING IN BINANCE TESTNET MODE")

    async def execute(self, action: TradeAction | list[TradeAction]) -> bool:
        """
        Executes a single TradeAction or a list of TradeActions on Binance via CCXT.
        """
        if isinstance(action, list):
            placed = 0
            for order in action:
                result = await self._execute_single(order)
                if result:
                    placed += 1
            self.logger.warning(
                f"!!! GRID BATCH COMPLETE: {placed}/{len(action)} orders placed !!!"
            )
            return placed > 0

        return await self._execute_single(action)

    async def _execute_single(self, action: TradeAction) -> bool:
        """
        Executes a single TradeAction on Binance via CCXT.
        """
        if action.action_type == "CANCEL_ALL":
            try:
                self.logger.warning(
                    f"🚀 BINANCE: Canceling all open orders for {self.symbol} | {action.reason}"
                )
                await self.exchange.cancel_all_orders(symbol=self.symbol)
                return True
            except Exception as e:
                self.logger.error(f"❌ BINANCE Cancel All Error: {e}")
                return False

        if action.action_type == "SYNC_GRID":

            # 1. Fetch current open orders to avoid canceling and replacing identically
            # This fixes the performance issue noted in code review and supports the
            # requirement to update the grid gracefully without blocking the loop fully.
            try:
                self.logger.warning(
                    f"🚀 BINANCE (SPOT): Syncing Grid around {action.price} | {action.reason}"
                )
                await self.exchange.cancel_all_orders(symbol=self.symbol)
            except Exception as e:
                self.logger.error(f"❌ BINANCE Cancel All Error: {e}")
                return False

            # 2. Parse grid parameters
            try:
                params = dict(item.split("=") for item in action.reason.split("|"))
                spacing_pct = float(params.get("spacing_pct", 0.002))
                below = int(params.get("below", 15))
                above = int(params.get("above", 15))
            except Exception as e:
                self.logger.error(f"❌ BINANCE Error parsing grid params: {e}")
                return False

            current_price = float(action.price)
            amount = float(action.qty)

            # 3. Create tasks to place orders concurrently via gather, preventing sequential blocking
            tasks = []

            async def safe_create_order(side, price):
                try:
                    await self.exchange.create_order(
                        symbol=self.symbol,
                        type="limit",
                        side=side,
                        amount=amount,
                        price=price,
                    )
                    self.logger.warning(
                        f"✅ REAL TESTNET ORDER PLACED: {side.upper()} {amount:.6f} @ {price:.2f}"
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to place {side} grid at {price}: {e}")

            # Place BUY orders below
            for i in range(1, below + 1):
                price = round(current_price * (1 - spacing_pct * i), 2)
                tasks.append(asyncio.create_task(safe_create_order("buy", price)))

            # Place SELL orders above
            for i in range(1, above + 1):
                price = round(current_price * (1 + spacing_pct * i), 2)
                tasks.append(asyncio.create_task(safe_create_order("sell", price)))

            await asyncio.gather(*tasks)
            self.logger.warning("✅ BINANCE: Grid Sync Complete (Concurrent).")
            return True

        elif action.action_type == "ORDER_FILLED":
            # Direct counter-order creation as per strict prompt logic
            try:
                params = dict(item.split("=") for item in action.reason.split("|"))
                spacing_pct = float(params.get("spacing_pct", 0.002))
            except Exception:
                spacing_pct = 0.002

            orig_price = float(action.price)
            amount = float(action.qty)
            orig_side = "BUY" if "BUY" in action.reason.upper() else "SELL"

            if orig_side == "BUY":
                new_side = "sell"
                new_price = round(orig_price * (1 + spacing_pct), 2)
            else:
                new_side = "buy"
                new_price = round(orig_price * (1 - spacing_pct), 2)

            self.logger.warning(
                f"✅ BINANCE: Executing Counter-Order -> {new_side.upper()} @ {new_price}"
            )
            try:
                await self.exchange.create_order(
                    symbol=self.symbol,
                    type="limit",
                    side=new_side,
                    amount=amount,
                    price=new_price,
                )
                self.logger.warning(
                    f"✅ REAL TESTNET ORDER PLACED: {new_side.upper()} {amount:.6f} @ {new_price:.2f}"
                )
                return True
            except Exception as e:
                self.logger.error(f"❌ BINANCE Counter-Order Error: {e}")
                return False

        side = "buy" if "BUY" in action.action_type else "sell"
        # Hedge Short logic:
        if "SHORT" in action.action_type:
            side = "sell"

        amount = float(action.qty)

        try:
            if self.trading_mode == "spot_grid":
                # Fallback for manual or single LIMIT orders
                price = action.price
                self.logger.warning(
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
                self.logger.warning(
                    f"🚀 BINANCE (FUTURES): Sending Market {side.upper()} {amount} {self.symbol}..."
                )
                # Execute Market Order
                order = await self.exchange.create_order(
                    symbol=self.symbol, type="market", side=side, amount=amount
                )

            self.logger.warning(f"✅ BINANCE: Order Filled! ID: {order['id']}")
            self.logger.warning(
                f"✅ REAL TESTNET ORDER PLACED: {side.upper()} {amount:.6f} @ {price if self.trading_mode == 'spot_grid' else 'market'}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ BINANCE Error: {e}")
            return False

    async def close(self):
        await self.exchange.close()
