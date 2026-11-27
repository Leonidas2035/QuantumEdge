"""BingX VST (Sandbox) read/execution adapter for QuantumEdge HarnessBridge."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
import logging
import os
from typing import Any
import uuid

import ccxt
from dotenv import load_dotenv

from quantum_edge_core.contracts.lean import FullMarketContext, MarketSnapshot
from quantum_edge_core.execution.analytics_enricher import compute_market_analytics
from quantum_edge_core.execution.full_market_context import build_full_market_context
from quantum_edge_core.execution.risk_guard import RiskConfig


def to_ccxt_swap_symbol(symbol: str) -> str:
    """Convert 'BTCUSDT' -> 'BTC/USDT:USDT' for CCXT BingX perpetual swap."""
    sym = symbol.strip()
    if "/" in sym:
        return sym
    upper = sym.upper()
    if upper.endswith("USDT") and len(upper) > 4:
        return f"{upper[:-4]}/USDT:USDT"
    return upper


def normalize_compact_symbol(ccxt_symbol: str | None) -> str:
    """Convert 'BTC/USDT:USDT' or 'BTC-USDT' -> 'BTCUSDT'."""
    if not ccxt_symbol:
        return ""
    s = str(ccxt_symbol).split(":")[0]
    return s.replace("/", "").replace("-", "").upper()


def _opt_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def extract_instrument_specs(client_or_markets: Any) -> dict[str, tuple[Decimal, Decimal]]:
    """Extract (tick_size, qty_step) Decimal steps from a CCXT client or markets dict.

    Supports both ``ccxt.TICK_SIZE`` (4) and ``ccxt.DECIMAL_PLACES`` (2) without
    issuing network calls when ``markets`` is already populated.
    """
    from quantum_edge_core.execution.market_context_serializer import DEFAULT_INSTRUMENT_SPECS

    specs: dict[str, tuple[Decimal, Decimal]] = dict(DEFAULT_INSTRUMENT_SPECS)
    if isinstance(client_or_markets, Mapping):
        markets = client_or_markets
        precision_mode = ccxt.TICK_SIZE
    else:
        markets = getattr(client_or_markets, "markets", None) or {}
        precision_mode = getattr(client_or_markets, "precisionMode", ccxt.TICK_SIZE)

    is_tick_mode = int(precision_mode) == int(ccxt.TICK_SIZE)
    for m in markets.values():
        if not isinstance(m, Mapping):
            continue
        raw_sym = normalize_compact_symbol(str(m.get("symbol") or m.get("id") or ""))
        prec = m.get("precision") or {}
        p_raw, a_raw = prec.get("price"), prec.get("amount")
        if raw_sym and p_raw is not None and a_raw is not None:
            if is_tick_mode:
                specs[raw_sym] = (Decimal(str(p_raw)), Decimal(str(a_raw)))
            else:
                specs[raw_sym] = (
                    Decimal("10") ** (-int(p_raw)),
                    Decimal("10") ** (-int(a_raw)),
                )
    return specs


def create_bingx_vst_client(*, load_env: bool = True) -> ccxt.bingx:
    """Create a synchronous CCXT BingX swap client strictly locked to VST sandbox mode.

    Loads credentials from environment / local .env without logging or exposing secret values.
    """
    if load_env:
        load_dotenv()

    if str(os.getenv("REAL_TRADING", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError(
            "REAL_TRADING is prohibited in create_bingx_vst_client(); only BingX VST sandbox is permitted."
        )

    api_key = (
        os.getenv("BINGX_TESTNET_API_KEY")
        or os.getenv("BINGX_DEMO_API_KEY")
        or os.getenv("BINGX_API_KEY")
        or ""
    )
    api_secret = (
        os.getenv("BINGX_TESTNET_SECRET")
        or os.getenv("BINGX_DEMO_API_SECRET")
        or os.getenv("BINGX_SECRET")
        or ""
    )

    client = ccxt.bingx(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "options": {
                "defaultType": "swap",
                "warnOnFetchOpenOrdersWithoutSymbol": False,
            },
            "enableRateLimit": True,
            "timeout": 30000,
        }
    )
    client.set_sandbox_mode(True)
    client._qe_sandbox_verified = True
    return client


def is_vst_sandbox_client(client: Any) -> bool:
    """Verify that the exchange client is strictly locked to BingX VST sandbox and REAL_TRADING is off."""
    if str(os.getenv("REAL_TRADING", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    if getattr(client, "_qe_sandbox_verified", False) is True:
        return True
    urls = getattr(client, "urls", None)
    if isinstance(urls, dict):
        api_urls = urls.get("api")
        if "open-api-vst" in str(api_urls):
            return True
    return False


_HARMLESS_MARGIN_LEV_FRAGMENTS: tuple[str, ...] = (
    "no need to change",
    "already",
    "not modified",
    "same",
)


def _is_harmless_setup_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(frag in msg for frag in _HARMLESS_MARGIN_LEV_FRAGMENTS)


def ensure_isolated_margin_and_leverage(
    client: Any,
    *,
    symbol: str,
    leverage: int,
    position_side: str,
    require_isolated: bool = True,
) -> dict[str, Any]:
    """Ensure ISOLATED margin mode and target leverage on BingX VST before opening a trade.

    Fail-safe on paper/offline clients that do not implement set_margin_mode / set_leverage.
    Enforces VST sandbox check for real ccxt.Exchange clients.
    """
    if str(os.getenv("REAL_TRADING", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError(
            "REAL_TRADING is prohibited in ensure_isolated_margin_and_leverage()."
        )

    set_mm = getattr(client, "set_margin_mode", None)
    set_lev = getattr(client, "set_leverage", None)
    if not callable(set_mm) and not callable(set_lev):
        return {"applied": False, "reason": "client_unsupported"}

    if isinstance(client, ccxt.Exchange) and not is_vst_sandbox_client(client):
        raise RuntimeError(
            "Refusing to set margin mode / leverage: ccxt client is not verified in VST sandbox mode."
        )

    ccxt_sym = to_ccxt_swap_symbol(symbol)
    side_upper = position_side.strip().upper()
    mm_status = "skipped"
    lev_status = "skipped"

    if require_isolated and callable(set_mm):
        try:
            set_mm("ISOLATED", ccxt_sym)
            mm_status = "applied"
        except Exception as exc:
            if _is_harmless_setup_error(exc):
                mm_status = "already_isolated"
            else:
                raise RuntimeError(f"set_margin_mode('ISOLATED', {ccxt_sym}) failed: {exc}") from exc

    if callable(set_lev):
        try:
            set_lev(int(leverage), ccxt_sym, params={"side": side_upper})
            lev_status = "applied"
        except Exception as exc:
            if _is_harmless_setup_error(exc):
                lev_status = "already_set"
            else:
                raise RuntimeError(
                    f"set_leverage({int(leverage)}, {ccxt_sym}, side={side_upper}) failed: {exc}"
                ) from exc

    return {
        "applied": True,
        "symbol": normalize_compact_symbol(ccxt_sym) or symbol.upper(),
        "ccxt_symbol": ccxt_sym,
        "margin_mode": mm_status,
        "leverage": int(leverage),
        "leverage_status": lev_status,
        "position_side": side_upper,
    }


def cancel_vst_open_orders(
    client: Any,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Safely cancel open orders on BingX VST sandbox for a symbol (or ALL symbols)."""
    if not is_vst_sandbox_client(client):
        raise RuntimeError(
            "Refusing to cancel orders: client is not verified in BingX VST sandbox mode or REAL_TRADING is enabled."
        )

    compact_sym = normalize_compact_symbol(symbol) if (symbol and symbol.upper() != "ALL") else "ALL"
    if compact_sym != "ALL":
        ccxt_sym = to_ccxt_swap_symbol(compact_sym)
        open_orders = list(client.fetch_open_orders(ccxt_sym) or [])
    else:
        status_info = fetch_vst_status(client)
        open_orders = [
            {"id": o["id"], "symbol": o["ccxt_symbol"] or to_ccxt_swap_symbol(str(o["symbol"]))}
            for o in status_info.get("open_orders", [])
            if o.get("id")
        ]

    cancelled_ids: list[str] = []
    errors: list[dict[str, str]] = []
    for o in open_orders:
        oid = str(o.get("id") or "")
        ord_sym = o.get("symbol") or (to_ccxt_swap_symbol(compact_sym) if compact_sym != "ALL" else "")
        if not oid or not ord_sym:
            continue
        try:
            client.cancel_order(oid, ord_sym)
            cancelled_ids.append(oid)
        except Exception as exc:
            errors.append({"order_id": oid, "symbol": str(ord_sym), "error": str(exc)})

    return {
        "sandbox": True,
        "symbol": compact_sym,
        "found_open_orders": len(open_orders),
        "cancelled_count": len(cancelled_ids),
        "cancelled_order_ids": cancelled_ids,
        "errors": errors,
    }


def fetch_vst_status(
    client: Any,
    *,
    fallback_symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
) -> dict[str, Any]:
    """Read-only fetch of VST balance, active positions, and open orders on BingX sandbox."""
    provider_gets = 0

    balance_raw = client.fetch_balance()
    provider_gets += 1
    vst_bucket = (
        balance_raw.get("VST")
        if isinstance(balance_raw, dict) and isinstance(balance_raw.get("VST"), dict)
        else (balance_raw.get("USDT", {}) if isinstance(balance_raw, dict) else {})
    )
    free_vst = _opt_float(vst_bucket.get("free"))
    total_vst = _opt_float(vst_bucket.get("total"))
    used_vst = _opt_float(vst_bucket.get("used"))

    positions_raw = client.fetch_positions()
    provider_gets += 1
    active_positions: list[dict[str, Any]] = []
    pos_symbols: set[str] = set()
    for p in positions_raw or []:
        contracts = _opt_float(p.get("contracts"))
        if contracts is None and isinstance(p.get("info"), dict):
            contracts = _opt_float(
                p["info"].get("positionAmt") or p["info"].get("availableAmt")
            )
        if contracts is not None and abs(contracts) > 0:
            compact_sym = normalize_compact_symbol(p.get("symbol"))
            if compact_sym:
                pos_symbols.add(compact_sym)
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            active_positions.append(
                {
                    "symbol": compact_sym or p.get("symbol"),
                    "ccxt_symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "positionSide": info.get("positionSide")
                    or (str(p.get("side") or "").upper() or None),
                    "contracts": abs(contracts),
                    "entry_price": _opt_float(p.get("entryPrice")),
                    "mark_price": _opt_float(p.get("markPrice")),
                    "unrealized_pnl": _opt_float(p.get("unrealizedPnl")),
                    "leverage": _opt_float(p.get("leverage")),
                    "liquidation_price": _opt_float(p.get("liquidationPrice")),
                    "margin_mode": p.get("marginMode"),
                }
            )

    open_orders_raw: list[dict[str, Any]] = []
    try:
        open_orders_raw = list(client.fetch_open_orders() or [])
        provider_gets += 1
    except Exception:
        seen_ids: set[str] = set()
        scan_syms = set(fallback_symbols) | pos_symbols
        for sym in sorted(scan_syms):
            try:
                per_sym = client.fetch_open_orders(to_ccxt_swap_symbol(sym)) or []
                provider_gets += 1
                for o in per_sym:
                    oid = str(o.get("id") or "")
                    if oid not in seen_ids:
                        seen_ids.add(oid)
                        open_orders_raw.append(o)
            except Exception:
                continue

    open_orders: list[dict[str, Any]] = []
    order_symbols: set[str] = set()
    for o in open_orders_raw:
        compact_sym = normalize_compact_symbol(o.get("symbol"))
        if compact_sym:
            order_symbols.add(compact_sym)
        info = o.get("info") if isinstance(o.get("info"), dict) else {}
        trigger_price = None
        for candidate in (
            o.get("stopPrice"),
            o.get("triggerPrice"),
            o.get("stopLossPrice"),
            o.get("takeProfitPrice"),
            info.get("stopPrice"),
            info.get("StopPrice"),
            info.get("triggerPrice"),
            info.get("stopLossPrice"),
            o.get("price"),
            info.get("price"),
        ):
            parsed = _opt_float(candidate)
            if parsed is not None and parsed > 0:
                trigger_price = parsed
                break

        open_orders.append(
            {
                "id": str(o.get("id")) if o.get("id") is not None else None,
                "clientOrderId": (
                    o.get("clientOrderId")
                    or info.get("clientOrderId")
                    or info.get("clientOrderID")
                ),
                "symbol": compact_sym or o.get("symbol"),
                "ccxt_symbol": o.get("symbol"),
                "type": o.get("type"),
                "rawType": info.get("type") or info.get("rawType") or o.get("type"),
                "workingType": info.get("workingType"),
                "side": o.get("side"),
                "positionSide": o.get("positionSide") or info.get("positionSide"),
                "reduceOnly": o.get("reduceOnly") if o.get("reduceOnly") is not None else info.get("reduceOnly"),
                "price": _opt_float(o.get("price")),
                "triggerPrice": trigger_price,
                "stopPrice": trigger_price,
                "amount": _opt_float(o.get("amount")),
                "filled": _opt_float(o.get("filled")),
                "status": o.get("status"),
                "timestamp": o.get("timestamp"),
            }
        )

    return {
        "sandbox": True,
        "balance_vst": {
            "free": free_vst,
            "total": total_vst,
            "used": used_vst,
        },
        "open_positions_count": len(active_positions),
        "open_positions_symbols": sorted(pos_symbols),
        "open_positions": active_positions,
        "open_orders_count": len(open_orders),
        "open_orders_symbols": sorted(order_symbols),
        "open_orders": open_orders,
        "provider_gets": provider_gets,
    }


def fetch_full_vst_context(
    client: Any,
    symbol: str,
    risk_config: RiskConfig | None = None,
    timeframes: Sequence[str] = ("5m", "15m", "1h", "4h", "1d"),
    ohlcv_limit: int = 100,
    orderbook_limit: int = 50,
    include_analytics: bool = True,
) -> FullMarketContext:
    """Fetch uncompressed multi-TF market, orderbook, derivatives, and VST account context."""
    effective_risk = risk_config or RiskConfig()
    compact_symbol = normalize_compact_symbol(symbol) or symbol.upper()
    ccxt_symbol = to_ccxt_swap_symbol(compact_symbol)
    provider_gets = 0

    orderbook_raw = client.fetch_order_book(ccxt_symbol, limit=orderbook_limit)
    provider_gets += 1
    bids = [
        [float(level[0]), float(level[1])]
        for level in (orderbook_raw.get("bids") or [])
    ]
    asks = [
        [float(level[0]), float(level[1])]
        for level in (orderbook_raw.get("asks") or [])
    ]

    ticker = client.fetch_ticker(ccxt_symbol)
    provider_gets += 1
    best_bid = _opt_float(ticker.get("bid"))
    if (best_bid is None or best_bid <= 0) and bids:
        best_bid = bids[0][0]
    best_ask = _opt_float(ticker.get("ask"))
    if (best_ask is None or best_ask <= 0) and asks:
        best_ask = asks[0][0]
    last_price = _opt_float(ticker.get("last"))
    if last_price is None or last_price <= 0:
        if best_bid and best_ask:
            last_price = (best_bid + best_ask) / 2.0
        else:
            last_price = best_bid or best_ask or 0.0

    bid_val = best_bid if best_bid is not None else last_price
    ask_val = best_ask if best_ask is not None else last_price
    mid_val = (bid_val + ask_val) / 2.0 if (bid_val > 0 and ask_val > 0) else last_price
    spread_bps = (
        round(((ask_val - bid_val) / mid_val) * 10_000.0, 4)
        if (mid_val and mid_val > 0 and ask_val >= bid_val)
        else 0.0
    )

    ohlcv_by_tf: dict[str, list[dict[str, Any]]] = {}
    for tf in timeframes:
        raw_bars = client.fetch_ohlcv(ccxt_symbol, timeframe=tf, limit=ohlcv_limit) or []
        provider_gets += 1
        ohlcv_by_tf[str(tf)] = [
            {
                "ts": int(bar[0]),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
                "volume": float(bar[5]),
            }
            for bar in raw_bars
        ]

    derivatives: dict[str, Any] = {
        "funding_rate": None,
        "next_funding_time": None,
        "open_interest": None,
        "mark_price": None,
        "index_price": None,
        "long_short_ratio": None,
    }
    try:
        fr = client.fetch_funding_rate(ccxt_symbol)
        provider_gets += 1
        if isinstance(fr, dict):
            fr_info = fr.get("info") if isinstance(fr.get("info"), dict) else {}
            derivatives["funding_rate"] = _opt_float(fr.get("fundingRate"))
            derivatives["next_funding_time"] = (
                fr.get("fundingTimestamp")
                or fr.get("fundingDatetime")
                or fr_info.get("nextFundingTime")
            )
            derivatives["mark_price"] = _opt_float(
                fr.get("markPrice") if fr.get("markPrice") is not None else fr_info.get("markPrice")
            )
            derivatives["index_price"] = _opt_float(
                fr.get("indexPrice") if fr.get("indexPrice") is not None else fr_info.get("indexPrice")
            )
    except Exception:
        pass

    try:
        oi = client.fetch_open_interest(ccxt_symbol)
        provider_gets += 1
        if isinstance(oi, dict):
            oi_info = oi.get("info") if isinstance(oi.get("info"), dict) else {}
            oi_val = (
                oi.get("openInterestAmount")
                if oi.get("openInterestAmount") is not None
                else (
                    oi.get("openInterestValue")
                    if oi.get("openInterestValue") is not None
                    else oi_info.get("openInterest")
                )
            )
            derivatives["open_interest"] = _opt_float(oi_val)
    except Exception:
        pass

    bid_vol_10 = sum(b[1] for b in bids[:10])
    ask_vol_10 = sum(a[1] for a in asks[:10])
    imbalance_top10 = (
        round((bid_vol_10 - ask_vol_10) / (bid_vol_10 + ask_vol_10), 6)
        if (bid_vol_10 + ask_vol_10) > 0
        else 0.0
    )
    microstructure: dict[str, Any] = {
        "bid_vol_top10": round(bid_vol_10, 6),
        "ask_vol_top10": round(ask_vol_10, 6),
        "imbalance_top10": imbalance_top10,
        "bid_levels_count": len(bids),
        "ask_levels_count": len(asks),
    }

    orderbook_dict: dict[str, Any] = {
        "bids": bids,
        "asks": asks,
        "timestamp": orderbook_raw.get("timestamp"),
        "datetime": orderbook_raw.get("datetime"),
    }

    if include_analytics:
        analytics = compute_market_analytics(
            symbol=compact_symbol,
            ohlcv_by_tf=ohlcv_by_tf,
            orderbook=orderbook_dict,
            derivatives=derivatives,
            ticker=ticker if isinstance(ticker, dict) else None,
            client=client,
        )
        provider_gets += int(analytics.pop("_provider_gets", 0))
        microstructure["analytics_enabled"] = True
        microstructure.update(analytics)
    else:
        microstructure["analytics_enabled"] = False

    status_info = fetch_vst_status(client)
    provider_gets += int(status_info.get("provider_gets", 0))

    recent_trades: list[dict[str, Any]] = []
    if hasattr(client, "fetch_my_trades"):
        try:
            raw_my_trades = client.fetch_my_trades(ccxt_symbol, limit=20) or []
            provider_gets += 1
            for t in raw_my_trades:
                recent_trades.append(
                    {
                        "id": str(t.get("id")) if t.get("id") is not None else None,
                        "order": str(t.get("order")) if t.get("order") is not None else None,
                        "symbol": compact_symbol,
                        "side": t.get("side"),
                        "price": _opt_float(t.get("price")),
                        "amount": _opt_float(t.get("amount")),
                        "cost": _opt_float(t.get("cost")),
                        "timestamp": t.get("timestamp"),
                    }
                )
        except Exception:
            pass

    account_state = {
        "free_margin_vst": status_info["balance_vst"]["free"],
        "total_equity_vst": status_info["balance_vst"]["total"],
        "used_margin_vst": status_info["balance_vst"]["used"],
        "open_positions": status_info["open_positions"],
        "open_orders": status_info["open_orders"],
        "recent_trades": recent_trades,
    }

    snapshot = MarketSnapshot(
        symbol=compact_symbol,
        bid=bid_val,
        ask=ask_val,
        last_price=last_price,
        spread_bps=spread_bps,
        ts_utc=datetime.now(timezone.utc),
        source="bingx_vst_sandbox",
        is_stale=False,
        indicators={},
    )

    return build_full_market_context(
        snapshot,
        ohlcv_by_tf=ohlcv_by_tf,
        orderbook={
            "bids": bids,
            "asks": asks,
            "timestamp": orderbook_raw.get("timestamp"),
            "datetime": orderbook_raw.get("datetime"),
        },
        derivatives=derivatives,
        microstructure_and_liquidity=microstructure,
        account_state=account_state,
        risk_limits=effective_risk,
        raw_extras={
            "ccxt_symbol": ccxt_symbol,
            "sandbox": True,
            "provider_gets": provider_gets,
        },
    )


_vst_logger = logging.getLogger("quantum_edge_core.execution.bingx_vst_adapter")


def get_symbol_tick_size(symbol: str, *, client: Any | None = None) -> Decimal:
    """Return instrument tick_size Decimal for compact symbol (markets -> DEFAULT_INSTRUMENT_SPECS -> 0.0001)."""
    compact = normalize_compact_symbol(symbol) or str(symbol).strip().upper()
    specs = extract_instrument_specs(client) if client is not None else extract_instrument_specs({})
    pair = specs.get(compact)
    if pair is not None and pair[0] > 0:
        return pair[0]
    return Decimal("0.0001")


def quantize_price_for_symbol(
    symbol: str,
    price: float,
    *,
    client: Any | None = None,
    position_side: str | None = None,
) -> float:
    """Quantize price cleanly to exchange tick_size (ceiling for LONG SL protection, floor for SHORT)."""
    tick = get_symbol_tick_size(symbol, client=client)
    side_norm = str(position_side or "").strip().upper()
    if "LONG" in side_norm:
        rounding = ROUND_CEILING
    elif "SHORT" in side_norm:
        rounding = ROUND_FLOOR
    else:
        rounding = ROUND_HALF_UP
    # Clean binary float noise (e.g. 100.20000000000001) before Decimal rounding
    d_price = Decimal(f"{float(price):.10f}")
    return float(d_price.quantize(tick, rounding=rounding))


def move_vst_stop_loss(
    client: Any,
    *,
    symbol: str,
    position_side: str,
    contracts: float,
    new_stop_price: float,
    old_order_id: str | None = None,
    decision_id: str | None = None,
) -> dict[str, Any]:
    """Create a replacement reduceOnly STOP_MARKET order FIRST, then cancel old SL.

    Enforces strict BingX VST sandbox verification and NEVER cancels old_order_id
    unless the replacement STOP_MARKET order returns a confirmed exchange order id.
    """
    if not is_vst_sandbox_client(client):
        raise RuntimeError(
            "Refusing to move stop loss: client is not verified in BingX VST sandbox mode or REAL_TRADING is enabled."
        )
    qty = float(contracts)
    raw_stop_p = float(new_stop_price)
    if qty <= 0 or raw_stop_p <= 0:
        raise ValueError(
            f"move_vst_stop_loss requires positive contracts ({qty}) and new_stop_price ({raw_stop_p})."
        )
    compact = normalize_compact_symbol(symbol) or str(symbol).strip().upper()
    ccxt_sym = to_ccxt_swap_symbol(compact)
    side_norm = str(position_side or "").strip().upper()
    if "LONG" in side_norm:
        pos_side = "LONG"
        close_side = "sell"
    elif "SHORT" in side_norm:
        pos_side = "SHORT"
        close_side = "buy"
    else:
        raise ValueError(f"Unsupported position_side for move_vst_stop_loss: {position_side!r}")
    stop_p = quantize_price_for_symbol(
        compact, raw_stop_p, client=client, position_side=pos_side
    )

    suffix = (decision_id or uuid.uuid4().hex[:8]).replace(" ", "")[:18]
    coid = f"qe-be-{compact.lower()}-{suffix}"[:36]
    params = {
        "positionSide": pos_side,
        "reduceOnly": True,
        "hedged": True,
        "stopPrice": stop_p,
        "stopLossPrice": stop_p,
        "workingType": "MARK_PRICE",
        "clientOrderId": coid,
    }
    created = client.create_order(
        ccxt_sym,
        "STOP_MARKET",
        close_side,
        qty,
        None,
        params,
    )
    new_order_id: str | None = None
    if isinstance(created, Mapping):
        raw_id = created.get("id")
        if raw_id is None and isinstance(created.get("info"), Mapping):
            raw_id = created["info"].get("orderId")
        if raw_id is not None and str(raw_id).strip():
            new_order_id = str(raw_id).strip()
    if not new_order_id:
        raise RuntimeError(
            f"Replacement STOP_MARKET order for {compact} returned no order id; keeping old SL {old_order_id}."
        )

    old_cancelled = False
    cancel_error: str | None = None
    if old_order_id:
        try:
            client.cancel_order(str(old_order_id), ccxt_sym)
            old_cancelled = True
        except Exception as exc:
            cancel_error = str(exc)
            _vst_logger.warning(
                "Created replacement SL %s (%s @ %s), but cancelling old SL %s failed: %s",
                new_order_id,
                compact,
                stop_p,
                old_order_id,
                exc,
            )

    return {
        "symbol": compact,
        "ccxt_symbol": ccxt_sym,
        "position_side": pos_side,
        "contracts": qty,
        "new_stop_price": stop_p,
        "client_order_id": coid,
        "new_order_id": new_order_id,
        "old_order_id": old_order_id,
        "old_cancelled": old_cancelled,
        "cancel_error": cancel_error,
    }
