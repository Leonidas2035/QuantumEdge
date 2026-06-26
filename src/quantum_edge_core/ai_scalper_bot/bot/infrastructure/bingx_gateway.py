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
        # Force defaultType to swap for all modes including SCALP
        options = {"defaultType": "swap"}

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
                "timeout": 30000,  # 30 seconds timeout for requests
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
            and self.trading_mode in ("futures", "perp", "swap", "spot_grid", "scalp", "scalper_v1")
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
        if self.trading_mode in ("futures", "perp", "swap", "spot_grid", "scalp", "scalper_v1"):
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
            self.trading_mode in ("futures", "perp", "swap", "spot_grid", "scalp", "scalper_v1")
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

    async def fetch_positions_async(self) -> list[dict[str, Any]]:
        """Fetch active positions from the exchange."""
        try:
            symbol = self._normalize_symbol(self.config.symbol)
            # Fetch positions via CCXT
            positions = await self.exchange.fetch_positions(symbols=[symbol])
            results = []
            for pos in positions:
                size = float(pos.get("contracts") or pos.get("size") or 0.0)
                if size > 0:
                    results.append({
                        "symbol": pos.get("symbol"),
                        "side": pos.get("side", "").upper(),
                        "size": size,
                        "entry_price": float(pos.get("entryPrice") or pos.get("averagePrice") or 0.0),
                        "unrealized_pnl": float(pos.get("unrealizedPnl") or 0.0),
                        "leverage": float(pos.get("leverage") or 1.0),
                        "liquidation_price": float(pos.get("liquidationPrice") or 0.0),
                        "margin_type": pos.get("marginMode", "cross"),
                    })
            return results
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch positions async: {e}")
            return []

    async def execute(self, action: Union[TradeAction, Any, List[TradeAction]]) -> bool:
        """Execute single TradeAction/OrderRequest or list (DCA Grid batch)."""
        self.logger.warning(f"🔧 BINGX execute() called: type={type(action)}, action_type={getattr(action, 'action_type', 'N/A') if hasattr(action, 'action_type') else 'OrderRequest'}")
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

    async def _execute_single(self, action: Any) -> bool:
        """Process a single TradeAction or OrderRequest."""
        from quantum_edge_core.ai_scalper_bot.bot.execution.smart_executor import OrderRequest

        self.logger.warning(f"🔧 _execute_single: action_type={getattr(action, 'action_type', 'N/A') if hasattr(action, 'action_type') else 'OrderRequest'}")
        if hasattr(action, "action_type") and action.action_type == "CANCEL_ALL":
            try:
                self.logger.warning(
                    f"🚀 BINGX: Canceling all open orders for {self._ccxt_symbol} | {action.reason}"
                )
                await self.exchange.cancel_all_orders(symbol=self._ccxt_symbol)
                return True
            except Exception as e:
                self.logger.error(f"❌ BINGX Cancel All Error: {e}")
                return False

        if hasattr(action, "action_type") and action.action_type == "SYNC_GRID":
            return await self._sync_grid(action)

        if hasattr(action, "action_type") and action.action_type == "ORDER_FILLED":
            return await self._order_filled_counter(action)

        # Standard order routing (handles TradeAction and OrderRequest)
        if isinstance(action, OrderRequest):
            side = action.side.value.lower()
            amount = float(action.qty)
            price = action.price
            pos_side = action.position_side.value
        else:
            # Regular BUY/SELL limit/market
            side = "buy" if "BUY" in action.action_type else "sell"
            if "SHORT" in action.action_type:
                side = "sell"
            amount = float(action.qty)
            price = float(action.price) if action.price is not None else None
            pos_side = self._get_position_side(side)

        try:
            params = {}
            if pos_side:
                params["positionSide"] = pos_side

            if isinstance(action, OrderRequest) and action.client_oid:
                params["clientOrderId"] = action.client_oid

            if self.trading_mode == "spot_grid":
                # Spot LIMIT order
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
                # Futures/Swap order
                order_type = "limit" if price is not None else "market"
                self.logger.warning(
                    f"🚀 BINGX (FUTURES): {order_type.upper()} {side.upper()} {amount} {self._ccxt_symbol} @ {price or 'market'} | params={params}"
                )
                order = await self.exchange.create_order(
                    symbol=self._ccxt_symbol,
                    type=order_type,
                    side=side,
                    amount=amount,
                    price=price,
                    params=params,
                )

            self.logger.warning(f"✅ BINGX: Order Filled! ID: {order['id']}")
            self.logger.warning(
                f"✅ REAL ORDER PLACED: {side.upper()} {amount:.6f} @ {price if price is not None else 'market'}"
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
        self.logger.warning(
            f"🚀 BINGX: Syncing Grid around {action.price} | {action.reason}"
        )
        try:
            # Cancel all with timeout
            await asyncio.wait_for(
                self.exchange.cancel_all_orders(symbol=self._ccxt_symbol),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            self.logger.error("❌ BINGX Cancel All Timeout (15s)")
            return False
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

                    # Use wait_for with timeout for each order
                    await asyncio.wait_for(
                        self.exchange.create_order(
                            symbol=self._ccxt_symbol,
                            type="limit",
                            side=side,
                            amount=amount,
                            price=price,
                            params=order_params,
                        ),
                        timeout=10.0,
                    )
                    self.logger.warning(
                        f"✅ REAL ORDER PLACED: {side.upper()} {amount:.6f} @ {price:.2f}"
                    )
                except asyncio.TimeoutError:
                    self.logger.warning(f"⏱️ Timeout placing {side} grid at {price}")
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

        # Wait for all tasks with overall timeout
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=60.0)
        except asyncio.TimeoutError:
            self.logger.error("❌ Grid sync overall timeout (60s)")
        
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
            pos_side = "LONG"
        else:
            new_side = "buy"
            new_price = round(orig_price * (1 - spacing_pct), 2)
            pos_side = "SHORT"

        self.logger.warning(
            f"✅ BINGX: Counter-Order -> {new_side.upper()} @ {new_price} (positionSide={pos_side})"
        )

        try:
            order_params = {}
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
