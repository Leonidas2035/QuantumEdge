"""
Supervisor Market Analysis — XRP/USDT
Combines:
  - TradingView technical read (Puppeteer -> tv_analyzer/tv_parse.js)
  - BingX funding rate + open interest (ccxt, testnet-safe)
  - Live market metrics from MarketDataHub (ZMQ :5555) — last snapshot
  - Optional QuestDB klines history (if symbol present)

Outputs a structured trading plan (Supervisor decision), NO orders placed.

Run: .venv/bin/python3 tools/supervisor_market_analysis.py
"""
import asyncio
import json
import os
import subprocess
import sys
import time

from dotenv import load_dotenv
load_dotenv()

import ccxt.async_support as ccxt

SYMBOL = os.getenv("MARKET_DATA_SYMBOLS", "XRPUSDT").split(",")[0]
BASE = SYMBOL.replace("USDT", "")
CCXT_SYMBOL = f"{BASE}/USDT:USDT"
TV_SYMBOL = SYMBOL  # TradingView uses XRPUSDT


def run_tv_parse(symbol: str) -> dict:
    try:
        r = subprocess.run(
            ["node", "tools/tv_analyzer/tv_parse.js", symbol],
            capture_output=True, text=True, timeout=130, cwd=os.getcwd(),
        )
        if r.returncode != 0:
            return {"error": r.stderr[:300]}
        return json.loads(r.stdout)
    except Exception as e:
        return {"error": str(e)}


async def fetch_exchange_ctx():
    use_testnet = str(os.getenv("USE_TESTNET", "1")).lower() in {"1", "true", "t"}
    ak = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY", "")
    sc = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET", "")
    ex = ccxt.bingx({
        "apiKey": ak, "secret": sc,
        "options": {"defaultType": "swap"}, "enableRateLimit": True, "timeout": 30000,
    })
    if use_testnet:
        ex.set_sandbox_mode(True)
    out = {}
    try:
        fr = await ex.fetch_funding_rate(CCXT_SYMBOL)
        out["funding_rate"] = fr.get("fundingRate")
        out["mark_price"] = fr.get("markPrice")
        out["index_price"] = fr.get("indexPrice")
    except Exception as e:
        out["funding_error"] = str(e)
    try:
        oi = await ex.fetch_open_interest(CCXT_SYMBOL)
        out["open_interest_usd"] = oi.get("openInterestValue")
    except Exception as e:
        out["oi_error"] = str(e)
    await ex.close()
    return out


CCXT_BTC = "BTC/USDT:USDT"


async def fetch_btc_ctx() -> dict:
    """Leading indicator: BTC/USDT (ticker, funding, OI) + derived XRP/BTC ratio."""
    import ccxt.async_support as ccxt
    from dotenv import load_dotenv
    load_dotenv()
    out = {}
    try:
        ex = ccxt.bingx({
            "apiKey": os.getenv("BINGX_TESTNET_API_KEY"),
            "secret": os.getenv("BINGX_TESTNET_SECRET"),
            "options": {"defaultType": "swap"},
        })
        ex.set_sandbox_mode(True)
        tk = await ex.fetch_ticker(CCXT_BTC)
        out["btc_last"] = tk.get("last")
        out["btc_change24"] = tk.get("percentage")
        fr = await ex.fetch_funding_rate(CCXT_BTC)
        out["btc_funding"] = fr.get("fundingRate")
        oi = await ex.fetch_open_interest(CCXT_BTC)
        out["btc_oi_usd"] = oi.get("openInterestValue")
        # XRP/BTC derived (BingX has no direct XRP/BTC pair)
        try:
            xrp = await ex.fetch_ticker(CCXT_SYMBOL)
            out["xrp_btc"] = round(xrp.get("last") / tk.get("last"), 8)
        except Exception:
            pass
        await ex.close()
    except Exception as e:
        out["btc_error"] = str(e)
    return out


def fetch_questdb_recent(symbol: str, minutes: int = 60) -> dict:
    """Recent 1m klines from QuestDB klines_1m (timestamp column is designated TS)."""
    try:
        import urllib.parse
        q = (f"SELECT close, volume FROM klines_1m "
             f"WHERE symbol='{symbol}' AND timestamp > dateadd('m', -{minutes}, now()) "
             f"ORDER BY timestamp DESC LIMIT 60")
        url = "http://127.0.0.1:9000/exec?query=" + urllib.parse.quote(q)
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout)
        if "dataset" in data and data["dataset"]:
            closes = [row[0] for row in data["dataset"]]
            return {"count": len(closes), "last": closes[0], "first": closes[-1],
                    "min": min(closes), "max": max(closes)}
        return {"count": 0, "note": data.get("error", "no data")}
    except Exception as e:
        return {"count": 0, "error": str(e)}


def fetch_news(symbol: str = SYMBOL, limit: int = 3) -> dict:
    """News/sentiment layer via Firecrawl (Free Tier — 1 request, keep minimal).

    Key resolution order: env FIRECRAWL_API_KEY -> config/firecrawl.key file.
    """
    import urllib.request
    key = os.getenv("FIRECRAWL_API_KEY")
    if not key:
        key_path = os.path.join(os.path.dirname(__file__), "..", "config", "firecrawl.key")
        try:
            with open(key_path) as f:
                key = f.read().strip()
        except Exception:
            key = None
    if not key:
        return {"error": "FIRECRAWL_API_KEY not set"}
    query = f"{symbol.replace('USDT','')} Ripple price news today"
    payload = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v1/search",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data", [])
        headlines = [{"title": i.get("title"), "desc": (i.get("description") or "")[:160]}
                     for i in items]
        # crude sentiment: count bull/bear keywords
        blob = " ".join((h["title"] or "") + " " + (h["desc"] or "") for h in headlines).lower()
        bull = sum(blob.count(w) for w in ["surge", "rise", "gain", "bull", "up", "momentum", "breakout"])
        bear = sum(blob.count(w) for w in ["drop", "fall", "bear", "down", "crash", "sell", "loss"])
        if bull > bear:
            sentiment = "BULLISH"
        elif bear > bull:
            sentiment = "BEARISH"
        else:
            sentiment = "NEUTRAL"
        return {"headlines": headlines, "sentiment": sentiment, "bull": bull, "bear": bear}
    except Exception as e:
        return {"error": str(e)}


def _to_float(s):
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def build_plan(tv: dict, ex: dict, qdb: dict, news: dict = None, hub_metrics: dict = None, btc: dict = None) -> str:
    tp = tv.get("technicalsPage", {})
    rec = tp.get("rating") or tv.get("recommendation") or "n/a"
    osc = tp.get("oscillators") or "n/a"
    ma = tp.get("movingAverages") or "n/a"
    price_raw = tv.get("priceText")
    price = _to_float(price_raw) or ex.get("mark_price") or qdb.get("last")
    fr = ex.get("funding_rate")
    oi = ex.get("open_interest_usd")

    # Derive bias from TV rating + funding + news sentiment
    rating_map = {"Strong Buy": "LONG", "Buy": "LONG", "Neutral": "NEUTRAL",
                  "Sell": "SHORT", "Strong Sell": "SHORT"}
    bias = rating_map.get(rec, "NEUTRAL")
    if fr is not None:
        if fr > 0 and bias == "NEUTRAL":
            bias = "NEUTRAL (funding mildly bullish)"
        elif fr < 0 and bias == "NEUTRAL":
            bias = "NEUTRAL (funding mildly bearish)"
    news_sent = (news or {}).get("sentiment")
    if news_sent == "BULLISH" and bias.startswith("NEUTRAL"):
        bias = "NEUTRAL (news + funding mildly bullish)"
    elif news_sent == "BEARISH" and bias.startswith("NEUTRAL"):
        bias = "NEUTRAL (news mildly bearish)"

    # --- OPERATIONAL LEVELS (Supervisor decision) ---
    # Volatility proxy from recent 1m range (QuestDB) or fallback 0.3%
    vol_pct = 0.003
    if qdb.get("count") and qdb.get("min") and qdb.get("max") and qdb["min"] > 0:
        vol_pct = max((qdb["max"] - qdb["min"]) / qdb["min"], 0.0005)
    # Day's range from TV (parse "1.0990 – 1.1139")
    day_lo = day_hi = None
    ftxt = tv.get("fullTextSample", "")
    import re
    m = re.search(r"Day's range\s*([\d.]+)\s*[–-]\s*([\d.]+)", ftxt)
    if m:
        day_lo, day_hi = float(m.group(1)), float(m.group(2))

    direction = "LONG" if bias.startswith("LONG") else ("SHORT" if bias.startswith("SHORT") else "RANGE/NEUTRAL")

    lines = []
    lines.append("=" * 64)
    lines.append(f"ТОРГОВИЙ ПЛАН СУПЕРВІЗОРА — {SYMBOL}")
    lines.append(f"Час: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 64)
    lines.append("")
    lines.append("1. КОНТЕКСТ")
    lines.append(f"   Ціна: {price} USDT | TV рейтинг: {rec} (osc={osc}, MA={ma})")
    lines.append(f"   Funding: {fr} | OI: {oi} USDT | News: {(news or {}).get('sentiment','n/a')}")
    if qdb.get("count"):
        lines.append(f"   QuestDB 1h: n={qdb['count']} min={qdb['min']} max={qdb['max']} (vol={vol_pct*100:.2f}%)")
    if day_lo and day_hi:
        lines.append(f"   Day's range: {day_lo} – {day_hi}")
    # --- Leading indicator: BTC (drives XRP per video analysis + correlation) ---
    if btc:
        btc_last = btc.get("btc_last")
        btc_ch = btc.get("btc_change24")
        xrp_btc = btc.get("xrp_btc")
        lines.append(f"   BTC: {btc_last} USDT ({btc_ch}%) | XRP/BTC: {xrp_btc}")
        # Video-based zones: BTC correction 63.6-64.2k = wait for sweep, then long
        if btc_last:
            if btc_last <= 64500:
                lines.append("   BTC: у зоні корекції 63.6-64.5k (відео) — чекати свіп вниз, НЕ купувати на хаях")
            elif btc_last >= 66000:
                lines.append("   BTC: пробій вгору >66k (відео) — лонг агресивніше")
            else:
                lines.append("   BTC: середина діапазону — нейтрально, чекати сигнал")
    lines.append("")
    lines.append(f"2. BIAS: {bias}")
    lines.append("")

    # --- ENTRY / EXIT / TRAILING stop block ---
    lines.append("3. ОПЕРАЦІЙНИЙ ПЛАН (входи / виходи / супровід)")
    if direction == "LONG":
        entry = round(price * (1 - vol_pct * 0.5), 4) if price else None
        tp1 = round(price * (1 + vol_pct), 4) if price else None
        tp2 = round(price * (1 + vol_pct * 2), 4) if price else None
        sl = round(price * (1 - vol_pct * 1.5), 4) if price else None
        be = round(price * (1 + vol_pct * 0.4), 4) if price else None
        lines.append(f"   НАПРЯМОК: LONG (купівля на просадці)")
        lines.append(f"   ВХІД:   ліміт ~{entry} (нижче ринку на {vol_pct*50:.2f}%)")
        lines.append(f"   TP1:    {tp1}   TP2: {tp2}")
        lines.append(f"   SL:     {sl} (початковий, за межами волатильності)")
        lines.append(f"   БЕЗЗБИТОК (BE): перенести SL у {be} коли ціна досягне +{vol_pct*40:.2f}%")
        lines.append(f"   ТРЕЙЛІНГ: далі рухати SL за ціною кроком {vol_pct*30:.2f}% кожні 1-2 хв, тримаючи мінімум +0.1%")
    elif direction == "SHORT":
        entry = round(price * (1 + vol_pct * 0.5), 4) if price else None
        tp1 = round(price * (1 - vol_pct), 4) if price else None
        tp2 = round(price * (1 - vol_pct * 2), 4) if price else None
        sl = round(price * (1 + vol_pct * 1.5), 4) if price else None
        be = round(price * (1 - vol_pct * 0.4), 4) if price else None
        lines.append(f"   НАПРЯМОК: SHORT (продаж на відскоку)")
        lines.append(f"   ВХІД:   ліміт ~{entry} (вище ринку на {vol_pct*50:.2f}%)")
        lines.append(f"   TP1:    {tp1}   TP2: {tp2}")
        lines.append(f"   SL:     {sl}")
        lines.append(f"   БЕЗЗБИТОК (BE): SL -> {be} коли ціна досягне -{vol_pct*40:.2f}%")
        lines.append(f"   ТРЕЙЛІНГ: рухати SL за ціною кроком {vol_pct*30:.2f}%, тримаючи мінімум +0.1%")
    else:
        mid = price
        buy_z = round(mid * (1 - vol_pct), 4) if mid else None
        sell_z = round(mid * (1 + vol_pct), 4) if mid else None
        lines.append(f"   НАПРЯМОК: RANGE/NEUTRAL — скальпінг у діапазоні")
        lines.append(f"   КУПІВЛЯ (long): ліміт ~{buy_z}  |  ПРОДАЖ (short): ліміт ~{sell_z}")
        lines.append(f"   TP: протилежний край діапазону | SL: {vol_pct*150:.2f}% за входом")
        lines.append(f"   БЕЗЗБИТОК: SL->вхід+0.05% після +{vol_pct*40:.2f}%")
        lines.append(f"   ТРЕЙЛІНГ: після BE рухати SL кроком {vol_pct*30:.2f}%")
    lines.append("")
    lines.append("4. СУПРОВІД ПОЗИЦІЇ (як Супервізор)")
    lines.append("   - Якщо позиція в ПЛЮСІ: одразу ставимо SL у беззбиток (захист капіталу).")
    lines.append("   - Далі рухаємо SL услід за ціною (trailing), фіксуючи прибуток частинами.")
    lines.append("   - Частковий Take-Profit на TP1 (50% розміру), залишок — за трейлінгом до TP2.")
    lines.append("   - Корекція за ринком: якщо TV рейтинг/новини змінюють bias —")
    lines.append("     скорочуємо розмір або виходимо; при пробої діапазону — перегляд плану.")
    lines.append("   - НЕ торгувати проти домінуючого TV-рейтингу без каталізатора.")
    lines.append("")
    lines.append(f"   Розмір: базовий 50 XRP/ордер, scale за волатильністю (vol={vol_pct*100:.2f}%).")
    lines.append("")
    lines.append("Примітка: авто-стоп ВИМКНЕНО (рішення власника) — захист через")
    lines.append("якісний аналіз + цей торговий план Супервізора.")
    lines.append("=" * 64)
    return "\n".join(lines)


def save_plan(plan_text: str, payload: dict):
    """Persist the supervisor plan for review (cron runs have no chat delivery)."""
    try:
        os.makedirs("runtime", exist_ok=True)
        with open("runtime/supervisor_plan.md", "w", encoding="utf-8") as f:
            f.write(plan_text + "\n")
        payload.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open("runtime/supervisor_plan.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        print(f"[SAVE] warn: {e}", flush=True)


async def main():
    print(f"[ANALYSIS] Збір даних по {SYMBOL}...", flush=True)
    tv = run_tv_parse(TV_SYMBOL)
    ex = await fetch_exchange_ctx()
    qdb = fetch_questdb_recent(SYMBOL, 60)
    news = fetch_news(SYMBOL, limit=3)
    btc = await fetch_btc_ctx()
    plan = build_plan(tv, ex, qdb, news, btc=btc)
    print(plan)
    save_plan(plan, {"tv": tv, "exchange": ex, "questdb": qdb,
                     "news": news, "btc": btc, "symbol": SYMBOL})
    try:
        with open("tools/_last_analysis.json", "w") as f:
            json.dump({"tv": tv, "exchange": ex, "questdb": qdb}, f, indent=2, default=str)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
