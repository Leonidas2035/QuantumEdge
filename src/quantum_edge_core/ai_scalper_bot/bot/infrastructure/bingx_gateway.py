"""
BingX Execution Gateway — Spot & Futures (VST Demo / Mainnet) via CCXT.
Drop-in replacement for BinanceExecutionGateway.
"""

import asyncio
import ccxt.async_support as ccxt
import logging
from typing import List, Union, Optional

from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import TradeAction


class BingXExecutionGateway:
    """
    Execution gateway for BingX (Spot / Futures) via CCXT.

    Supports:
    - VST Demo mode (set_sandbox_mode(True))
    - Mainnet mode (default)
    - Spot Grid (limit orders)
    - Futures (market orders with hedge mode support)
    - SYNC_GRID batch order placement
    - ORDER_FILLED counter-order logic
    """

    def __init__(self, config):
        self.logger = logging.getLogger("BingXGateway")
        self.symbol = config.symbol
        self.config = config

        # Trading mode determines market type
        self.trading_mode = getattr(config, "trading_mode", "spot_grid")

        # CCXT options - VST funds are in swap/futures account
        options = {"defaultType": "spot"}
        if self.trading_mode in ("futures", "perp", "swap", "spot_grid"):
            # For VST demo, grid trading uses futures account
            options = {"defaultType": "swap"}  # BingX perpetual futures

        # API credentials from config
        api_key = getattr(config, "bingx_api_key", "") or getattr(
            config, "binance_api_key", ""
        )
        secret = getattr(config, "bingx_secret", "") or getattr(
            config, "binance_secret", ""
        )

        # Testnet/VST credentials
        testnet_api_key = getattr(config, "bingx_testnet_api_key", "") or getattr(
            config, "binance_testnet_api_key", ""
        )
        testnet_secret = getattr(config, "bingx_testnet_secret", "") or getattr(
            config, "binance_testnet_secret", ""
        )

        use_testnet = getattr(config, "use_testnet", False)

        if use_testnet:
            api_key = testnet_api_key
            secret = testnet_secret
            self.logger.warning("⚠️ RUNNING IN BINGX VST (DEMO) MODE")
        else:
            self.logger.warning("🚀 RUNNING IN BINGX MAINNET MODE (REAL MONEY)")

        # Initialize CCXT BingX
        self.exchange = ccxt.bingx(
            {
                "apiKey": api_key,
                "secret": secret,
                "options": options,
                "enableRateLimit": True,
            }
        )

        if use_testnet:
            self.exchange.set_sandbox_mode(True)

        # Symbol format for BingX: Spot=BTC/USDT, Futures/Swap=BTC/USDT:USDT
        self._ccxt_symbol = self._normalize_symbol(self.symbol)

        # For VST, we need to query swap account for balance
        self._balance_account_type = (
            "swap"
            if use_testnet
            and self.trading_mode in ("futures", "perp", "swap", "spot_grid")
            else "spot"
        )

        # Track grid config for SYNC_GRID
        self.grid_levels_below = 15
        self.grid_levels_above = 15
        self.grid_spacing_pct = 0.002

        # State
        self.entries_paused = False
        self.status = "RUNNING"

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert internal symbol (BTCUSDT) to CCXT format."""
        # Spot: BTC/USDT, Futures/Swap: BTC/USDT:USDT
        base = symbol.replace("USDT", "")
        if self.trading_mode in ("futures", "perp", "swap", "spot_grid"):
            # For VST demo and futures, use swap format
            return f"{base}/USDT:USDT"
        else:
            return f"{base}/USDT"

    def _get_position_side(self, side: str) -> Optional[str]:
        """Determine positionSide for hedge mode if running on swap/futures."""
        position_mode = getattr(self.config, "bingx_position_mode", "hedge").lower()
        if position_mode == "oneway":
            return None

        # positionSide is required on swap/futures in hedge mode
        is_swap = (
            self.trading_mode in ("futures", "perp", "swap", "spot_grid")
            and getattr(self, "_balance_account_type", "spot") == "swap"
        )
        if not is_swap:
            return None

        return "LONG" if side.lower() == "buy" else "SHORT"

    @property
    def quote_balance(self) -> float:
        """Fetch VST/USDT free balance (lazy async fetch with sync fallback)."""
        # Return cached if available
        if hasattr(self, "_cached_balance") and self._cached_balance is not None:
            return self._cached_balance

        # Try sync fetch (only works if no running loop)
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                # Temporarily switch account type for balance fetch
                original_type = self.exchange.options.get("defaultType", "swap")
                self.exchange.options["defaultType"] = self._balance_account_type

                balance = loop.run_until_complete(self.exchange.fetch_balance())

                # Restore
                self.exchange.options["defaultType"] = original_type

                # VST uses 'VST' key, USDT uses 'USDT'
                quote_asset = "VST" if self._balance_account_type == "swap" else "USDT"
                usdt_free = float(balance.get(quote_asset, {}).get("free", 100000.0))
                self._cached_balance = usdt_free
                self.logger.info(
                    f"💰 Fetched actual {quote_asset} balance: {usdt_free}"
                )
                return usdt_free
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to fetch balance via CCXT, fallback 100000.0. Error: {e}"
            )

        # Fallback for VST demo
        self._cached_balance = 100000.0
        return 100000.0

    async def fetch_balance_async(self) -> float:
        """Async balance fetch for use in async context."""
        try:
            original_type = self.exchange.options.get("defaultType", "swap")
            self.exchange.options["defaultType"] = self._balance_account_type

            balance = await self.exchange.fetch_balance()

            # Restore
            self.exchange.options["defaultType"] = original_type

            quote_asset = "VST" if self._balance_account_type == "swap" else "USDT"
            usdt_free = float(balance.get(quote_asset, {}).get("free", 100000.0))
            self._cached_balance = usdt_free
            self.logger.info(
                f"💰 Fetched actual {quote_asset} balance (async): {usdt_free}"
            )
            return usdt_free
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch balance async: {e}")
            return getattr(self, "_cached_balance", 100000.0)

    async def execute(self, action: Union[TradeAction, List[TradeAction]]) -> bool:
        """Execute single TradeAction or list (DCA Grid batch)."""
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
        """Process a single TradeAction."""
        if action.action_type == "CANCEL_ALL":
            try:
                self.logger.warning(
                    f"🚀 BINGX: Canceling all open orders for {self._ccxt_symbol} | {action.reason}"
                )
                await self.exchange.cancel_all_orders(symbol=self._ccxt_symbol)
                return True
            except Exception as e:
                self.logger.error(f"❌ BINGX Cancel All Error: {e}")
                return False

        if action.action_type == "SYNC_GRID":
            return await self._sync_grid(action)

        if action.action_type == "ORDER_FILLED":
            return await self._order_filled_counter(action)

        # Regular BUY/SELL limit/market
        side = "buy" if "BUY" in action.action_type else "sell"
        if "SHORT" in action.action_type:
            side = "sell"

        amount = float(action.qty)

        try:
            params = {}
            pos_side = self._get_position_side(side)
            if pos_side:
                params["positionSide"] = pos_side

            if self.trading_mode == "spot_grid":
                # Spot LIMIT order
                price = float(action.price)
                self.logger.warning(
                    f"🚀 BINGX (SPOT): Limit {side.upper()} {amount} {self._ccxt_symbol} @ {price} | params={params}"
                )
                order = await self.exchange.create_order(
                    symbol=self._ccxt_symbol,
                    type="limit",
                    side=side,
                    amount=amount,
                    price=price,
                    params=params,
                )
            else:
                # Futures MARKET order with positionSide for hedge mode
                self.logger.warning(
                    f"🚀 BINGX (FUTURES): Market {side.upper()} {amount} {self._ccxt_symbol} | params={params}"
                )
                order = await self.exchange.create_order(
                    symbol=self._ccxt_symbol,
                    type="market",
                    side=side,
                    amount=amount,
                    params=params,
                )

            self.logger.warning(f"✅ BINGX: Order Filled! ID: {order['id']}")
            self.logger.warning(
                f"✅ REAL ORDER PLACED: {side.upper()} {amount:.6f} @ {price if self.trading_mode == 'spot_grid' else 'market'}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ BINGX Error: {e}")
            return False

    async def _sync_grid(self, action: TradeAction) -> bool:
        """
        Sync grid: cancel all + place new grid orders around center price.
        Expects action.reason to contain: spacing_pct=X|below=N|above=M
        """
        try:
            self.logger.warning(
                f"🚀 BINGX: Syncing Grid around {action.price} | {action.reason}"
            )
            await self.exchange.cancel_all_orders(symbol=self._ccxt_symbol)
        except Exception as e:
            self.logger.error(f"❌ BINGX Cancel All Error: {e}")
            return False

        # Parse grid params
        try:
            params = dict(item.split("=") for item in action.reason.split("|"))
            spacing_pct = float(params.get("spacing_pct", 0.002))
            below = int(params.get("below", 15))
            above = int(params.get("above", 15))
        except Exception as e:
            self.logger.error(f"❌ BINGX Error parsing grid params: {e}")
            return False

        current_price = float(action.price)
        amount = float(action.qty)

        # Place orders concurrently using semaphore to limit rate
        sem = asyncio.Semaphore(5)
        tasks = []

        async def safe_create_order(side: str, price: float):
            async with sem:
                try:
                    order_params = {}
                    pos_side = self._get_position_side(side)
                    if pos_side:
                        order_params["positionSide"] = pos_side

                    await self.exchange.create_order(
                        symbol=self._ccxt_symbol,
                        type="limit",
                        side=side,
                        amount=amount,
                        price=price,
                        params=order_params,
                    )
                    self.logger.warning(
                        f"✅ REAL ORDER PLACED: {side.upper()} {amount:.6f} @ {price:.2f}"
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to place {side} grid at {price}: {e}")
                # Space out concurrent requests slightly
                await asyncio.sleep(0.1)

        # BUY orders below (LONG for futures)
        for i in range(1, below + 1):
            price = round(current_price * (1 - spacing_pct * i), 2)
            tasks.append(asyncio.create_task(safe_create_order("buy", price)))

        # SELL orders above (SHORT for futures)
        for i in range(1, above + 1):
            price = round(current_price * (1 + spacing_pct * i), 2)
            tasks.append(asyncio.create_task(safe_create_order("sell", price)))

        await asyncio.gather(*tasks)
        self.logger.warning("✅ BINGX: Grid Sync Complete (Concurrent).")
        return True

    async def _order_filled_counter(self, action: TradeAction) -> bool:
        """
        Counter-order logic: when BUY fills -> place SELL at +spacing, when SELL fills -> place BUY at -spacing.
        Expects reason to contain: side=BUY|spacing_pct=X
        """
        try:
            params = dict(item.split("=") for item in action.reason.split("|"))
            spacing_pct = float(params.get("spacing_pct", 0.002))
            filled_side = params.get("side", "BUY").upper()
        except Exception:
            spacing_pct = 0.002
            filled_side = "BUY"

        orig_price = float(action.price)
        amount = float(action.qty)

        if filled_side == "BUY":
            new_side = "sell"
            new_price = round(orig_price * (1 + spacing_pct), 2)
        else:
            new_side = "buy"
            new_price = round(orig_price * (1 - spacing_pct), 2)

        self.logger.warning(
            f"✅ BINGX: Counter-Order -> {new_side.upper()} @ {new_price}"
        )

        try:
            order_params = {}
            pos_side = self._get_position_side(new_side)
            if pos_side:
                order_params["positionSide"] = pos_side

            await self.exchange.create_order(
                symbol=self._ccxt_symbol,
                type="limit",
                side=new_side,
                amount=amount,
                price=new_price,
                params=order_params,
            )
            self.logger.warning(
                f"✅ REAL ORDER PLACED: {new_side.upper()} {amount:.6f} @ {new_price:.2f}"
            )
            return True
        except Exception as e:
            self.logger.error(f"❌ BINGX Counter-Order Error: {e}")
            return False

    async def close(self):
        await self.exchange.close()
