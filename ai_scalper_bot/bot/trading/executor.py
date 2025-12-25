from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set, Tuple, Any
import asyncio
import time
import random
from typing import Optional, Dict, Any

import yaml
from binance import AsyncClient
from binance.base_client import BaseClient
from binance.exceptions import BinanceAPIException, BinanceRequestException

from bot.core.config_loader import config
from bot.engine.decision_engine import Decision

from bot.core.config_loader import config as global_config


@dataclass
class SymbolMeta:
    step_size: float
    min_qty: float
    tick_size: float
    min_notional: float = 0.0


class BinanceDemoExecutor:
    """
    Minimal Binance testnet executor for live-demo trading.

    - Enforces testnet-only endpoints.
    - Caps notional per trade and number of open positions.
    - Uses MARKET orders only for simplicity and safety.
    """

    def __init__(self, symbol: Optional[str] = None):
        self.config = config
        self.demo_cfg: Dict = config.get("binance_demo", {}) or {}
        self._validate_config()

        self.exchange = str(self.demo_cfg.get("exchange", "futures")).lower()
        self.allowed_symbols = self._load_allowed_symbols()
        default_symbol = symbol or self.demo_cfg.get("default_symbol") or self._fallback_symbol()
        self.symbol = (default_symbol or "BTCUSDT").upper()
        if self.allowed_symbols and self.symbol not in self.allowed_symbols:
            self.allowed_symbols.add(self.symbol)

        self.max_notional = float(self.demo_cfg.get("max_notional_per_trade", 50))
        self.max_open_positions = int(self.demo_cfg.get("max_open_positions", 1))
        self.recv_window = int(self.demo_cfg.get("recv_window", 5000))
        self.taker_fee_rate = float(self.demo_cfg.get("taker_fee_bps", 5.0)) / 10_000
        self.fee_rate = float(self.demo_cfg.get("fee_rate", self.taker_fee_rate))
        self.position_pct = float(self.demo_cfg.get("position_pct", 0.02))
        self.equity_override = float(self.demo_cfg.get("equity_override", 0))
        self.healthcheck_only = bool(self.demo_cfg.get("healthcheck_only", False))

        self.client: Optional[AsyncClient] = None
        self.position: float = 0.0
        self.entry_price: Optional[float] = None
        self.last_price: Optional[float] = None
        self.realized_pnl: float = 0.0
        self.trades: int = 0
        self._symbol_meta: Dict[str, "SymbolMeta"] = {}
        self._client_order_salt = random.randint(1, 1_000_000)
        self._bracket: Optional[Dict[str, Any]] = None
        self.trade_stats = None

    def _validate_config(self) -> None:
        if not self.demo_cfg.get("testnet", True):
            raise ValueError("Demo mode enforces Binance testnet only. Set binance_demo.testnet to true.")
        base_url = self.demo_cfg.get("base_url") or BaseClient.FUTURES_TESTNET_URL
        if "testnet" not in base_url:
            raise ValueError("binance_demo.base_url must point to the Binance testnet endpoints.")

    def _load_allowed_symbols(self) -> Set[str]:
        pairs_path = Path(__file__).resolve().parents[2] / "config" / "pairs.yaml"
        allowed: Set[str] = set()
        try:
            data = yaml.safe_load(pairs_path.read_text()) or {}
            demo_pairs = data.get("futures_demo") or data.get("demo") or []
            allowed.update(str(sym).upper() for sym in demo_pairs if sym)
        except Exception:
            # best-effort; fall back to config
            pass

        symbols_cfg = self.demo_cfg.get("symbols") or config.get("binance.symbols", [])
        allowed.update(str(sym).upper() for sym in symbols_cfg if sym)
        return allowed

    def _fallback_symbol(self) -> Optional[str]:
        if self.allowed_symbols:
            return sorted(self.allowed_symbols)[0]
        symbols_cfg = config.get("binance.symbols", [])
        return symbols_cfg[0] if symbols_cfg else None

    async def _get_symbol_meta(self, symbol: str) -> Optional[SymbolMeta]:
        symbol = symbol.upper()
        if symbol in self._symbol_meta:
            return self._symbol_meta[symbol]
        if not await self.initialize():
            return None
        try:
            info = await self.client.futures_exchange_info()
        except Exception as exc:
            self._log(f"[ERROR] Failed to fetch exchangeInfo: {exc}", level="ERROR")
            return None

        sym_info = None
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                sym_info = s
                break
        if not sym_info:
            self._log(f"[ERROR] Symbol {symbol} not found in exchangeInfo; skipping.", level="ERROR")
            return None

        step_size = None
        min_qty = None
        tick_size = 0.0
        min_notional = 0.0
        for f in sym_info.get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                step_size = float(f.get("stepSize", 0))
                min_qty = float(f.get("minQty", 0))
            if f.get("filterType") == "PRICE_FILTER":
                tick_size = float(f.get("tickSize", 0))
            if f.get("filterType") == "MIN_NOTIONAL":
                min_notional = float(f.get("notional") or f.get("minNotional", 0.0))

        if not step_size or not min_qty:
            self._log(f"[ERROR] No LOT_SIZE filter for symbol {symbol} in exchangeInfo; skipping order.", level="ERROR")
            return None

        meta = SymbolMeta(step_size=step_size, min_qty=min_qty, tick_size=tick_size, min_notional=min_notional)
        self._symbol_meta[symbol] = meta
        return meta

    @staticmethod
    def _decimals_from_step(step: float) -> int:
        if step <= 0:
            return 0
        text = f"{step:.12f}".rstrip("0")
        if "." in text:
            return len(text.split(".")[1])
        return 0

    async def _normalize_qty(self, symbol: str, raw_qty: float) -> float:
        meta = await self._get_symbol_meta(symbol)
        if not meta:
            return 0.0
        step = meta.step_size
        min_qty = meta.min_qty
        if step <= 0:
            return 0.0
        steps = int(raw_qty / step)
        qty = steps * step
        if qty < min_qty:
            self._log(f"[WARN] Qty below minQty for {symbol}, skipping order (qty={raw_qty}, minQty={min_qty}).", "WARN")
            return 0.0
        decimals = self._decimals_from_step(step)
        return float(f"{qty:.{decimals}f}")

    async def _normalize_price(self, symbol: str, price: float) -> float:
        meta = await self._get_symbol_meta(symbol)
        if not meta or meta.tick_size <= 0:
            return price
        step = meta.tick_size
        steps = int(price / step)
        decimals = self._decimals_from_step(step)
        normalized = steps * step
        return float(f"{normalized:.{decimals}f}")

    async def _get_demo_equity_usdt(self) -> float:
        if self.equity_override and self.equity_override > 0:
            return float(self.equity_override)
        if not await self.initialize():
            return 0.0
        try:
            balances = await self.client.futures_account_balance()
            for b in balances:
                if b.get("asset") == "USDT":
                    return float(b.get("balance", 0.0))
        except Exception as exc:
            self._log(f"[WARN] Unable to fetch futures balance: {exc}", level="WARN")
        return 0.0

    async def initialize(self) -> bool:
        """Initialize Binance async client (testnet only)."""
        if self.client:
            return True

        api_key = self.config.secret("BINANCE_DEMO_API_KEY")
        api_secret = self.config.secret("BINANCE_DEMO_API_SECRET")
        if not api_key or not api_secret:
            self._log("Missing BINANCE_DEMO_API_KEY/SECRET in secrets store.", level="ERROR")
            return False

        try:
            self.client = await AsyncClient.create(api_key, api_secret, testnet=True)
            base_url = self.demo_cfg.get("base_url") or BaseClient.FUTURES_TESTNET_URL
            if self.exchange == "futures":
                # Force futures endpoints to the testnet host from config.
                self.client.FUTURES_TESTNET_URL = base_url
            else:
                self.client.API_TESTNET_URL = base_url
            self._log(f"[DEMO] Binance client initialized for {self.exchange} testnet.")
            return True
        except Exception as exc:
            self._log(f"Failed to initialize Binance client: {exc}", level="ERROR")
            self.client = None
            return False

    async def healthcheck(self) -> bool:
        """Ping testnet to verify connectivity without placing an order."""
        if not await self.initialize():
            return False
        try:
            if self.exchange == "futures":
                await self.client.futures_ping()
            else:
                await self.client.ping()
            self._log("[DEMO] Testnet ping OK.")
            return True
        except Exception as exc:
            self._log(f"[DEMO] Healthcheck failed: {exc}", level="ERROR")
            return False

    def _fee(self, notional: float) -> float:
        return abs(notional) * self.taker_fee_rate

    def _open_positions(self) -> int:
        return 1 if abs(self.position) > 0 else 0

    def _log(self, msg: str, level: str = "INFO") -> None:
        print(f"[{level}] {msg}")

    async def _compute_entry_qty(self, symbol: str, price: float) -> float:
        equity = await self._get_demo_equity_usdt()
        if equity <= 0:
            self._log("[WARN] Unable to determine demo equity; skipping order sizing.", level="WARN")
            return 0.0
        meta = await self._get_symbol_meta(symbol)
        target_notional = equity * max(self.position_pct, 0)
        if self.max_notional > 0:
            target_notional = min(target_notional, self.max_notional)
        if meta and meta.min_notional and target_notional < meta.min_notional:
            target_notional = meta.min_notional
        if price <= 0:
            return 0.0
        raw_qty = target_notional / price
        return await self._normalize_qty(symbol, raw_qty)

    def adjust_tp_for_fees(self, entry_price: float, raw_tp: float, side: str, fee_rate: float) -> float:
        if fee_rate <= 0 or entry_price <= 0:
            return raw_tp
        side_up = side.upper()
        adj = raw_tp
        if side_up in ("LONG", "BUY"):
            adj = raw_tp * (1 + 2 * fee_rate)
        else:
            adj = raw_tp * (1 - 2 * fee_rate)
        return adj

    def _client_order_id(self, symbol: str, action: str) -> str:
        ts = int(time.time() * 1000)
        base = f"QE-{symbol}-{action}-{ts}-{self._client_order_salt}"
        return base[:32]

    async def _submit_with_retries(self, fn, *args, **kwargs):
        backoff = 0.5
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            try:
                return await fn(*args, **kwargs)
            except (BinanceRequestException, BinanceAPIException) as exc:
                attempts += 1
                if isinstance(exc, BinanceAPIException) and exc.code and exc.code >= 500:
                    pass
                elif isinstance(exc, BinanceRequestException):
                    pass
                else:
                    raise
                self._log(f"[WARN] Order attempt {attempts} failed: {exc}; retrying in {backoff:.1f}s", level="WARN")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 4.0)
        raise

    async def submit_order(
        self, symbol: str, side: str, qty: float, reduce_only: bool = False, price: Optional[float] = None, client_order_id: Optional[str] = None
    ):
        if not await self.initialize():
            return None
        symbol = symbol.upper()
        norm_qty = await self._normalize_qty(symbol, qty)
        if norm_qty <= 0:
            return None
        norm_price = price
        if price is not None:
            norm_price = await self._normalize_price(symbol, price)

        try:
            if self.exchange == "futures":
                params = {
                    "symbol": symbol,
                    "side": side,
                    "type": "MARKET",
                    "quantity": norm_qty,
                    "reduceOnly": reduce_only,
                    "recvWindow": self.recv_window,
                }
                if client_order_id:
                    params["newClientOrderId"] = client_order_id
                if norm_price is not None and not reduce_only:
                    params["type"] = "LIMIT"
                    params["timeInForce"] = "GTC"
                    params["price"] = norm_price
                return await self._submit_with_retries(self.client.futures_create_order, **params)

            if reduce_only:
                self._log("Reduce-only not supported for spot; skipping order.", level="WARN")
                return None
            return await self._submit_with_retries(
                self.client.create_order,
                symbol=symbol,
                side=side,
                type="MARKET" if norm_price is None else "LIMIT",
                quantity=norm_qty,
                price=norm_price if norm_price is not None else None,
                recvWindow=self.recv_window,
                newClientOrderId=client_order_id,
            )
        except (BinanceAPIException, BinanceRequestException) as exc:
            msg = str(exc)
            if hasattr(exc, "code") and exc.code == -1111:
                self._log(
                    f"[ERROR] Binance precision issue for {symbol}, qty={norm_qty}, price={norm_price}, details={msg}",
                    level="ERROR",
                )
            else:
                self._log(f"Binance error during order: {exc}", level="ERROR")
        except Exception as exc:
            self._log(f"Unexpected error during order: {exc}", level="ERROR")
        return None

    async def close_position(self, symbol: Optional[str] = None):
        if self.position == 0:
            return
        sym = (symbol or self.symbol).upper()
        side = "SELL" if self.position > 0 else "BUY"
        qty = abs(self.position)
        result = await self.submit_order(sym, side, qty, reduce_only=True)
        if result:
            executed = float(result.get("executedQty") or qty)
            pnl_price = float(result.get("avgPrice") or 0.0)
            pnl = (pnl_price - (self.entry_price or pnl_price)) * (self.position if executed >= abs(self.position) else executed) - self._fee(
                pnl_price * executed
            ) if self.entry_price else 0.0
            self._record_trade(pnl, side)
            self.position = 0.0
            self.entry_price = None
            self.trades += 1
            self._bracket = None

    async def sync_positions(self) -> Dict:
        """Best-effort sync from testnet; keeps internal position close to account state."""
        if not await self.initialize():
            return {}
        if self.exchange != "futures":
            return {}
        try:
            info = await self.client.futures_position_information(symbol=self.symbol)
            if info:
                pos_amt = float(info[0].get("positionAmt", 0.0))
                entry_price = float(info[0].get("entryPrice", 0.0))
                self.position = pos_amt
                self.entry_price = entry_price if pos_amt else None
            return info
        except Exception as exc:
            self._log(f"[DEMO] Failed to sync positions: {exc}", level="WARN")
            return {}

    async def process(self, decision: Decision, price: float, timestamp: int, symbol: Optional[str] = None):
        sym = (symbol or self.symbol).upper()
        self.last_price = price
        if self.allowed_symbols and sym not in self.allowed_symbols:
            self._log(f"[DEMO] Symbol {sym} not allowed in demo config; skipping.")
            return

        if decision.action == "hold":
            return

        await self.check_brackets(price, timestamp)

        tp_price = getattr(decision, "tp_price", None)
        sl_price = getattr(decision, "sl_price", None)

        filled_qty: float = 0.0

        if decision.action in ("buy", "sell"):
            if self._open_positions() >= self.max_open_positions:
                self._log("[DEMO] Max open positions reached; not opening new trade.", level="WARN")
                return
            if self.position != 0:
                self._log("[DEMO] Position already open; waiting for close signal.", level="WARN")
                return

            qty = await self._compute_entry_qty(sym, price)
            if qty <= 0:
                self._log("[DEMO] Computed quantity <= 0 after normalization, skipping order.", level="WARN")
                return

            side = "BUY" if decision.action == "buy" else "SELL"
            self._log(f"[DEMO] Placing MARKET {side} {sym} qty={qty} (testnet).")
            client_id = self._client_order_id(sym, decision.action)
            result = await self.submit_order(sym, side, qty, reduce_only=False, client_order_id=client_id)
            if result:
                filled_qty = float(result.get("executedQty") or qty)
                self._apply_fill(side, filled_qty, price, result)
                self.trades += 1
                self._log(
                    f"[DEMO] Order acknowledged id={result.get('orderId')} clientId={result.get('clientOrderId')} "
                    f"status={result.get('status')} filled={filled_qty}."
                )
                if self.position != 0 and (tp_price or sl_price):
                    self.set_bracket(side, tp_price, sl_price)
            return

        if decision.action == "close":
            if self.position == 0:
                self._log("[DEMO] No open position to close.")
                return

            side = "SELL" if self.position > 0 else "BUY"
            qty = await self._normalize_qty(sym, abs(self.position))
            if qty <= 0:
                self._log(f"[WARN] Normalized close qty <= 0 for {sym}, skipping close.", level="WARN")
                return
            effective_price = price
            if self.entry_price is not None:
                is_profitable = (self.position > 0 and price > self.entry_price) or (
                    self.position < 0 and price < self.entry_price
                )
                if is_profitable:
                    effective_price = await self._normalize_price(
                        sym, self.adjust_tp_for_fees(self.entry_price, price, side, self.fee_rate)
                    )
            self._log(f"[DEMO] Closing position via MARKET {side} {sym} qty={qty} (reduce-only).")
            client_id = self._client_order_id(sym, "close")
            result = await self.submit_order(sym, side, qty, reduce_only=True, price=effective_price, client_order_id=client_id)
            if result:
                executed = float(result.get("executedQty") or qty)
                pnl_price = effective_price if self.entry_price and effective_price else price
                pnl = (
                    (pnl_price - self.entry_price) * (self.position if executed >= abs(self.position) else executed)
                    - self._fee(pnl_price * executed)
                ) if self.entry_price else 0.0
                self.realized_pnl += pnl
                self._record_trade(pnl, side)
                self._reduce_position(executed)
                self.trades += 1
                self._log(
                    f"[DEMO] Close order id={result.get('orderId')} clientId={result.get('clientOrderId')} "
                    f"status={result.get('status')} filled={executed} pnl_est={pnl:.4f}."
                )

    async def shutdown(self):
        if self.client:
            try:
                await self.client.close_connection()
            except Exception:
                pass
            self.client = None

    def summary(self):
        open_pnl = 0.0
        if self.position and self.entry_price and self.last_price is not None:
            open_pnl = (self.last_price - self.entry_price) * self.position
        return {
            "position": self.position,
            "entry_price": self.entry_price,
            "realized_pnl": self.realized_pnl,
            "open_pnl": open_pnl,
            "trades": self.trades,
        }

    def _apply_fill(self, side: str, executed: float, price: float, result: Optional[Dict[str, Any]] = None) -> None:
        status = (result or {}).get("status", "").upper() if result else ""
        if executed <= 0:
            return
        filled = executed
        if side.upper() == "BUY":
            self.position += filled
        else:
            self.position -= filled
        avg_price = result.get("avgPrice") if result else None
        if self.position != 0:
            self.entry_price = float(avg_price or price)
        else:
            self.entry_price = None
        if status == "PARTIALLY_FILLED":
            self._log(f"[DEMO] Partial fill detected status={status} executed={executed}", level="WARN")

    def _reduce_position(self, executed: float) -> None:
        remaining = abs(self.position) - executed
        if remaining <= 0:
            self.position = 0.0
            self.entry_price = None
            self._bracket = None
        else:
            self.position = self.position - executed if self.position > 0 else self.position + executed

    def set_bracket(self, side: str, tp_price: Optional[float], sl_price: Optional[float]) -> bool:
        if not tp_price and not sl_price:
            return False
        self._bracket = {
            "side": side.upper(),
            "tp": float(tp_price) if tp_price else None,
            "sl": float(sl_price) if sl_price else None,
        }
        self._log(f"[DEMO] Bracket set tp={self._bracket['tp']} sl={self._bracket['sl']}")
        return True

    async def check_brackets(self, price: float, timestamp: int) -> None:
        if not self._bracket or self.position == 0:
            return False
        side = self._bracket.get("side", "BUY")
        tp = self._bracket.get("tp")
        sl = self._bracket.get("sl")
        hit = None
        if tp and ((self.position > 0 and price >= tp) or (self.position < 0 and price <= tp)):
            hit = "tp"
        if sl and ((self.position > 0 and price <= sl) or (self.position < 0 and price >= sl)):
            hit = hit or "sl"
        if not hit:
            return False
        close_side = "SELL" if self.position > 0 else "BUY"
        qty = abs(self.position)
        client_id = self._client_order_id(self.symbol, f"bracket-{hit}")
        self._log(f"[DEMO] {hit.upper()} hit -> closing {qty} via reduce-only.", level="WARN")
        result = await self.submit_order(self.symbol, close_side, qty, reduce_only=True, client_order_id=client_id)
        if result:
            executed = float(result.get("executedQty") or qty)
            pnl_price = float(result.get("avgPrice") or price)
            pnl = (pnl_price - self.entry_price) * (self.position if executed >= abs(self.position) else executed) - self._fee(
                pnl_price * executed
            ) if self.entry_price else 0.0
            self.realized_pnl += pnl
            self._reduce_position(executed)
            return True
        return False

    def _record_trade(self, pnl: float, side: str) -> None:
        if self.trade_stats:
            try:
                self.trade_stats.record(pnl, time.time(), symbol=self.symbol, side=side)
            except Exception:
                pass
