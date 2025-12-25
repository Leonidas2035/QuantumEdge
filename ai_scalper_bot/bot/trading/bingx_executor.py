from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set, Any
import asyncio
import os
import random
import time

import yaml

from bot.core.config_loader import config
from bot.engine.decision_engine import Decision
from bot.exchanges.bingx_swap import BingXClient, BingXSwapExchange, OrderRequest
from bot.exchanges.bingx_swap.client import BingXAPIError
from bot.exchanges.bingx_swap.mapper import normalize_symbol, round_price_to_tick, round_qty_to_step


@dataclass
class SymbolMeta:
    step_size: float
    min_qty: float
    tick_size: float
    min_notional: float = 0.0


class BingXDemoExecutor:
    """
    Minimal BingX swap demo executor for live-demo trading.
    """

    def __init__(self, symbol: Optional[str] = None):
        self.config = config
        self.demo_cfg: Dict = config.get("bingx_demo", {}) or config.get("binance_demo", {}) or {}
        self._validate_config()

        self.allowed_symbols = self._load_allowed_symbols()
        default_symbol = symbol or self.demo_cfg.get("default_symbol") or self._fallback_symbol()
        self.symbol = normalize_symbol(default_symbol or "BTCUSDT")
        if self.allowed_symbols and self.symbol not in self.allowed_symbols:
            self.allowed_symbols.add(self.symbol)

        self.max_notional = float(self.demo_cfg.get("max_notional_per_trade", 50))
        self.max_open_positions = int(self.demo_cfg.get("max_open_positions", 1))
        self.recv_window = int(os.getenv("BINGX_RECV_WINDOW", self.demo_cfg.get("recv_window", 5000)))
        self.taker_fee_rate = float(self.demo_cfg.get("taker_fee_bps", 5.0)) / 10_000
        self.fee_rate = float(self.demo_cfg.get("fee_rate", self.taker_fee_rate))
        self.position_pct = float(self.demo_cfg.get("position_pct", 0.02))
        self.equity_override = float(self.demo_cfg.get("equity_override", 0))
        self.healthcheck_only = bool(self.demo_cfg.get("healthcheck_only", False))

        self.client: Optional[BingXClient] = None
        self.exchange: Optional[BingXSwapExchange] = None
        self.position: float = 0.0
        self.entry_price: Optional[float] = None
        self.last_price: Optional[float] = None
        self.realized_pnl: float = 0.0
        self.trades: int = 0
        self._symbol_meta: Dict[str, SymbolMeta] = {}
        self._client_order_salt = random.randint(1, 1_000_000)
        self._bracket: Optional[Dict[str, Any]] = None
        self.trade_stats = None

    def _validate_config(self) -> None:
        env = os.getenv("BINGX_ENV", "")
        if env and env.lower() != "demo":
            self._log(f"BINGX_ENV={env}; demo mode expects demo keys.", level="WARN")

    def _load_allowed_symbols(self) -> Set[str]:
        pairs_path = Path(__file__).resolve().parents[2] / "config" / "pairs.yaml"
        allowed: Set[str] = set()
        try:
            data = yaml.safe_load(pairs_path.read_text()) or {}
            demo_pairs = data.get("futures_demo") or data.get("demo") or []
            allowed.update(normalize_symbol(str(sym)) for sym in demo_pairs if sym)
        except Exception:
            pass

        symbols_cfg = self.demo_cfg.get("symbols") or config.get("binance.symbols", [])
        allowed.update(normalize_symbol(str(sym)) for sym in symbols_cfg if sym)
        return allowed

    def _fallback_symbol(self) -> Optional[str]:
        if self.allowed_symbols:
            return sorted(self.allowed_symbols)[0]
        symbols_cfg = config.get("binance.symbols", [])
        return normalize_symbol(symbols_cfg[0]) if symbols_cfg else None

    async def _to_thread(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def _get_symbol_meta(self, symbol: str) -> Optional[SymbolMeta]:
        sym = normalize_symbol(symbol)
        if sym in self._symbol_meta:
            return self._symbol_meta[sym]
        if not await self.initialize():
            return None
        try:
            filters = await self._to_thread(self.exchange.get_symbol_filters, sym)
        except Exception as exc:
            self._log(f"[ERROR] Failed to fetch symbol filters: {exc}", level="ERROR")
            return None
        meta = SymbolMeta(
            step_size=filters.step_size,
            min_qty=filters.min_qty,
            tick_size=filters.tick_size,
            min_notional=filters.min_notional,
        )
        self._symbol_meta[sym] = meta
        return meta

    async def _normalize_qty(self, symbol: str, raw_qty: float) -> float:
        meta = await self._get_symbol_meta(symbol)
        if not meta or meta.step_size <= 0:
            return 0.0
        qty = round_qty_to_step(raw_qty, meta.step_size)
        if meta.min_qty and qty < meta.min_qty:
            self._log(f"[WARN] Qty below minQty for {symbol}, skipping order (qty={raw_qty}, minQty={meta.min_qty}).", "WARN")
            return 0.0
        return qty

    async def _normalize_price(self, symbol: str, price: float) -> float:
        meta = await self._get_symbol_meta(symbol)
        if not meta or meta.tick_size <= 0:
            return price
        return round_price_to_tick(price, meta.tick_size)

    async def _get_demo_equity_usdt(self) -> float:
        if self.equity_override and self.equity_override > 0:
            return float(self.equity_override)
        if not await self.initialize():
            return 0.0
        try:
            balances = await self._to_thread(self.exchange.get_balances)
            for b in balances:
                if str(b.asset).upper() == "USDT":
                    return float(b.total or b.available or 0.0)
        except Exception as exc:
            self._log(f"[WARN] Unable to fetch balance: {exc}", level="WARN")
        return 0.0

    async def initialize(self) -> bool:
        if self.client and self.exchange:
            return True

        api_key = self.config.secret("BINGX_DEMO_API_KEY")
        api_secret = self.config.secret("BINGX_DEMO_API_SECRET")
        if not api_key or not api_secret:
            self._log("Missing BINGX_DEMO_API_KEY/SECRET in secrets store.", level="ERROR")
            return False

        try:
            base_url = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.com")
            self.client = BingXClient(base_url, api_key, api_secret, recv_window=self.recv_window, timeout=10.0)
            self.exchange = BingXSwapExchange(self.client)
            self._log("[DEMO] BingX client initialized.")
            return True
        except Exception as exc:
            self._log(f"Failed to initialize BingX client: {exc}", level="ERROR")
            self.client = None
            self.exchange = None
            return False

    async def healthcheck(self) -> bool:
        if not await self.initialize():
            return False
        try:
            await self._to_thread(self.client.request, "GET", "/openApi/swap/v2/server/time", None, False)
            self._log("[DEMO] BingX ping OK.")
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

    async def submit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        reduce_only: bool = False,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ):
        if not await self.initialize():
            return None
        symbol = normalize_symbol(symbol)
        norm_qty = await self._normalize_qty(symbol, qty)
        if norm_qty <= 0:
            return None
        norm_price = price
        if price is not None:
            norm_price = await self._normalize_price(symbol, price)

        try:
            side_up = side.upper()
            position_side = "LONG" if side_up == "BUY" else "SHORT"
            if reduce_only:
                if self.position > 0:
                    position_side = "LONG"
                    side_up = "SELL"
                elif self.position < 0:
                    position_side = "SHORT"
                    side_up = "BUY"
            order_type = "MARKET" if norm_price is None else "LIMIT"
            req = OrderRequest(
                symbol=symbol,
                side=side_up,
                position_side=position_side,
                order_type=order_type,
                qty=norm_qty,
                price=norm_price,
                reduce_only=reduce_only,
                client_order_id=client_order_id,
                time_in_force="GTC" if order_type == "LIMIT" else None,
            )
            return await self._to_thread(self.exchange.place_order, req)
        except BingXAPIError as exc:
            self._log(f"BingX error during order: {exc}", level="ERROR")
        except Exception as exc:
            self._log(f"Unexpected error during order: {exc}", level="ERROR")
        return None

    async def close_position(self, symbol: Optional[str] = None):
        if self.position == 0:
            return
        sym = normalize_symbol(symbol or self.symbol)
        side = "SELL" if self.position > 0 else "BUY"
        qty = abs(self.position)
        result = await self.submit_order(sym, side, qty, reduce_only=True)
        if result:
            executed = float(result.filled_qty or qty)
            pnl_price = float(result.avg_price or 0.0)
            pnl = (pnl_price - (self.entry_price or pnl_price)) * (self.position if executed >= abs(self.position) else executed) - self._fee(
                pnl_price * executed
            ) if self.entry_price else 0.0
            self._record_trade(pnl, side)
            self.position = 0.0
            self.entry_price = None
            self.trades += 1
            self._bracket = None

    async def sync_positions(self) -> Dict:
        if not await self.initialize():
            return {}
        try:
            positions = await self._to_thread(self.exchange.get_positions, self.symbol)
            long_pos = next((p for p in positions if p.position_side == "LONG" and p.qty), None)
            short_pos = next((p for p in positions if p.position_side == "SHORT" and p.qty), None)
            if long_pos and long_pos.qty:
                self.position = float(long_pos.qty)
                self.entry_price = float(long_pos.entry_price or 0.0) or None
            elif short_pos and short_pos.qty:
                self.position = -float(short_pos.qty)
                self.entry_price = float(short_pos.entry_price or 0.0) or None
            else:
                self.position = 0.0
                self.entry_price = None
            return {"positions": [p.raw for p in positions]}
        except Exception as exc:
            self._log(f"[DEMO] Failed to sync positions: {exc}", level="WARN")
            return {}

    async def process(self, decision: Decision, price: float, timestamp: int, symbol: Optional[str] = None):
        sym = normalize_symbol(symbol or self.symbol)
        self.last_price = price
        if self.allowed_symbols and sym not in self.allowed_symbols:
            self._log(f"[DEMO] Symbol {sym} not allowed in demo config; skipping.")
            return

        if decision.action == "hold":
            return

        await self.check_brackets(price, timestamp)

        tp_price = getattr(decision, "tp_price", None)
        sl_price = getattr(decision, "sl_price", None)

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
            self._log(f"[DEMO] Placing MARKET {side} {sym} qty={qty} (BingX demo).")
            client_id = self._client_order_id(sym, decision.action)
            result = await self.submit_order(sym, side, qty, reduce_only=False, client_order_id=client_id)
            if result:
                filled_qty = float(result.filled_qty or qty)
                self._apply_fill(side, filled_qty, price, result)
                self.trades += 1
                self._log(
                    f"[DEMO] Order acknowledged id={result.order_id} clientId={result.client_order_id} "
                    f"status={result.status} filled={filled_qty}."
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
                executed = float(result.filled_qty or qty)
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
                    f"[DEMO] Close order id={result.order_id} clientId={result.client_order_id} "
                    f"status={result.status} filled={executed} pnl_est={pnl:.4f}."
                )

    async def shutdown(self):
        self.client = None
        self.exchange = None

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

    def _apply_fill(self, side: str, executed: float, price: float, result: Optional[Any] = None) -> None:
        status = ""
        if result is not None:
            status = getattr(result, "status", "") or ""
        if executed <= 0:
            return
        if side.upper() == "BUY":
            self.position += executed
        else:
            self.position -= executed
        avg_price = getattr(result, "avg_price", None) if result is not None else None
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

    async def check_brackets(self, price: float, timestamp: int) -> bool:
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
            executed = float(result.filled_qty or qty)
            pnl_price = float(result.avg_price or price)
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
