"""
Збірач ринкових даних для карти ліквідацій.
OHLCV з BingX (консистентно з торговим ботом), OI-історія з Bybit
(BingX не підтримує fetchOpenInterestHistory).
"""
from __future__ import annotations
import ccxt
import time
import json
from pathlib import Path

SYMBOL = "BTC/USDT:USDT"
CACHE_DIR = Path("/home/layman/QuantumEdge/runtime/cache")


def _mk(name):
    e = getattr(ccxt, name)({"enableRateLimit": True})
    e.load_markets()
    return e


def get_cache_data(cache_key: str, max_age_seconds: int = 6 * 3600):
    """Отримати дані з кешу, якщо вони не застаріли."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{cache_key}.json"
    if cache_path.exists():
        try:
            cache_mtime = cache_path.stat().st_mtime
            if time.time() - cache_mtime < max_age_seconds:
                return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def write_cache_data(cache_key: str, data):
    """Записати дані в кеш."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{cache_key}.json"
    try:
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def timeframe_to_ms(timeframe: str) -> int:
    """Конвертувати таймфрейм у мілісекунди."""
    amount = int(timeframe[:-1])
    unit = timeframe[-1]
    if unit == 'm':
        return amount * 60 * 1000
    elif unit == 'h':
        return amount * 60 * 60 * 1000
    elif unit == 'd':
        return amount * 24 * 60 * 60 * 1000
    elif unit == 'w':
        return amount * 7 * 24 * 60 * 60 * 1000
    raise ValueError(f"Unknown timeframe unit: {unit}")


def fetch_ohlcv(timeframe="1h", limit=200, symbol=SYMBOL, primary="bingx"):
    """OHLCV з BingX, фолбек Bybit."""
    last_err = None
    for name in [primary, "bybit", "okx"]:
        try:
            e = _mk(name)
            sym = symbol if symbol in e.markets else "BTC/USDT"
            data = e.fetch_ohlcv(sym, timeframe, limit=limit)
            if data:
                return data, name
        except Exception as ex:
            last_err = f"{name}: {ex}"
    raise RuntimeError(f"OHLCV fetch failed: {last_err}")


def fetch_oi_history(timeframe="1h", limit=200, symbol=SYMBOL):
    """OI-історія з Bybit -> [(ts, oi_value_usd)]. None якщо недоступно."""
    for name in ["bybit", "okx"]:
        try:
            e = _mk(name)
            sym = symbol if symbol in e.markets else "BTC/USDT"
            oih = e.fetch_open_interest_history(sym, timeframe, limit=limit)
            out = []
            for x in oih:
                ts = x.get("timestamp")
                val = x.get("openInterestValue")
                if val is None:
                    amt = x.get("openInterestAmount")
                    info = x.get("info", {}) or {}
                    val = amt  # fallback: amount as proxy
                if ts and val:
                    out.append((float(ts), float(val)))
            if out:
                return out, name
        except Exception:
            continue
    return None, None


def fetch_ohlcv_history(timeframe="1h", days=365, symbol=SYMBOL, exchange="bybit", refresh=False):
    """Багатосторінкове завантаження OHLCV історії з диск-кешем."""
    cache_key = f"ohlcv_{symbol.replace('/', '_').replace(':', '_')}_{timeframe}_{exchange}_{days}d"
    if not refresh:
        cached = get_cache_data(cache_key)
        if cached is not None:
            return cached, f"{exchange}_cache"

    e = _mk(exchange)
    sym = symbol if symbol in e.markets else "BTC/USDT"

    now = int(time.time() * 1000)
    tf_ms = timeframe_to_ms(timeframe)
    since = now - days * 24 * 60 * 60 * 1000

    all_ohlcv = []
    current_since = since
    while current_since < now:
        page = e.fetch_ohlcv(sym, timeframe, since=current_since, limit=1000)
        if not page:
            break
        all_ohlcv.extend(page)
        last_ts = page[-1][0]
        next_since = last_ts + tf_ms
        if next_since <= current_since:
            break
        current_since = next_since
        time.sleep(0.05)

    # Дедуплікація та сортування
    seen = set()
    unique_ohlcv = []
    for candle in all_ohlcv:
        ts = candle[0]
        if ts not in seen:
            seen.add(ts)
            unique_ohlcv.append(candle)
    unique_ohlcv.sort(key=lambda x: x[0])

    write_cache_data(cache_key, unique_ohlcv)
    return unique_ohlcv, exchange


def fetch_oi_history_long(timeframe="1h", days=365, symbol=SYMBOL, exchange="bybit", refresh=False):
    """Багатосторінкове завантаження Open Interest історії з диск-кешем."""
    cache_key = f"oi_{symbol.replace('/', '_').replace(':', '_')}_{timeframe}_{exchange}_{days}d"
    if not refresh:
        cached = get_cache_data(cache_key)
        if cached is not None:
            return cached, f"{exchange}_cache"

    try:
        e = _mk(exchange)
        sym = symbol if symbol in e.markets else "BTC/USDT"

        now = int(time.time() * 1000)
        since = now - days * 24 * 60 * 60 * 1000

        # Використовуємо вбудовану пагінацію ccxt через params
        oih = e.fetch_open_interest_history(sym, timeframe, since=since, params={"paginate": True})
        out = []
        for x in oih:
            ts = x.get("timestamp")
            val = x.get("openInterestValue")
            if val is None:
                val = x.get("openInterestAmount")
            if ts and val:
                out.append((float(ts), float(val)))

        # Дедуплікація та сортування за часом (ccxt повертає від нових до старих)
        seen = set()
        unique_oi = []
        for item in out:
            ts = item[0]
            if ts not in seen:
                seen.add(ts)
                unique_oi.append(item)
        unique_oi.sort(key=lambda x: x[0])

        write_cache_data(cache_key, unique_oi)
        return unique_oi, exchange
    except Exception as ex:
        print(f"Warning: Failed to fetch open interest history: {ex}")
        return None, None


def fetch_context(symbol=SYMBOL):
    """Поточний funding + OI value для контексту."""
    ctx = {}
    try:
        e = _mk("bingx")
        sym = symbol if symbol in e.markets else "BTC/USDT"
        fr = e.fetch_funding_rate(sym)
        ctx["funding"] = fr.get("fundingRate")
        oi = e.fetch_open_interest(sym)
        ctx["oi_usd"] = oi.get("openInterestValue")
    except Exception as ex:
        ctx["err"] = str(ex)[:80]
    return ctx
