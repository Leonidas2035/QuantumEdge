#!/usr/bin/env python3
"""
Local Market Analyzer — runs on the local RTX 4060 via Ollama (hermes3:8b-llama3.1-q5_K_M).
Autonomous: pulls fresh XRP/BTC data (BingX VST), builds indicators, asks the LOCAL LLM
to analyze, and writes runtime/local_analysis.json. This is the "brain" the Hermes trader reads.

Run:  .venv/bin/python3 tools/local_market_analyzer.py
(Usually driven by a no_agent cron, but can run standalone.)
"""
import os, sys, json, asyncio, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import ccxt.async_support as ccxt
import httpx  # for Ollama API

OLLAMA_URL = "http://localhost:11434/api/generate"
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "hermes3:8b-llama3.1-q5_K_M")
SYMBOL = "XRP/USDT:USDT"
BTC = "BTC/USDT:USDT"


def build_indicators(ohlcv, period=14):
    """Minimal RSI/MACD/BB/ATR — pure python, no TA lib needed."""
    import statistics
    closes = [float(c[4]) for c in ohlcv]
    n = len(closes)
    if n < period + 2:
        return {}
    # RSI
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    rsi = 100 - (100 / (1 + (ag / al if al else 99)))
    # MACD
    def ema(vals, p):
        k = 2 / (p + 1); e = vals[0]
        out = [e]
        for v in vals[1:]:
            e = v * k + e * (1 - k); out.append(e)
        return out
    ema12 = ema(closes, 12); ema26 = ema(closes, 26)
    macd = [a - b for a, b in zip(ema12, ema26)]
    sig = ema(macd[26:], 9)
    macd_line = macd[-1]; signal = sig[-1]; hist = macd_line - signal
    # Bollinger
    mid = statistics.mean(closes[-20:])
    sd = statistics.pstdev(closes[-20:])
    bb_up = mid + 2 * sd; bb_lo = mid - 2 * sd
    price = closes[-1]
    bb_pos = (price - bb_lo) / (bb_up - bb_lo) if bb_up != bb_lo else 0.5
    # ATR
    highs = [float(c[2]) for c in ohlcv]; lows = [float(c[3]) for c in ohlcv]
    tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
           for i in range(1, n)]
    atr = sum(tr[-period:]) / period
    return {
        "price": round(price, 4), "rsi": round(rsi, 2),
        "macd": round(macd_line, 5), "signal": round(signal, 5), "hist": round(hist, 5),
        "bb": [round(bb_lo, 4), round(mid, 4), round(bb_up, 4)], "bb_pos": round(bb_pos, 3),
        "atr": round(atr, 5), "atr_pct": round(atr / price * 100, 3),
    }


async def gather(symbol):
    ex = ccxt.bingx({"apiKey": os.environ.get("BINGX_TESTNET_API_KEY"),
                      "secret": os.environ.get("BINGX_TESTNET_SECRET"),
                      "options": {"defaultType": "swap"}})
    ex.set_sandbox_mode(True)
    await ex.load_markets()
    ohlcv1h = await ex.fetch_ohlcv(symbol, "1h", limit=60)
    ohlcv15 = await ex.fetch_ohlcv(symbol, "15m", limit=60)
    ohlv = await ex.fetch_ohlcv(symbol, "5m", limit=60)
    ticker = await ex.fetch_ticker(symbol)
    ob = await ex.fetch_order_book(symbol, 10)
    await ex.close()
    return ohlcv1h, ohlcv15, ohlv, ticker, ob


def book_summary(ob, depth=5):
    bids = ob["bids"][:depth]; asks = ob["asks"][:depth]
    bid_vol = sum(v for _, v in bids); ask_vol = sum(v for _, v in asks)
    return {
        "best_bid": bids[0][0], "best_ask": asks[0][0],
        "bid_vol10": round(bid_vol), "ask_vol10": round(ask_vol),
        "imbalance": round((bid_vol - ask_vol) / (bid_vol + ask_vol), 3),
        "bid_walls": [[round(p, 4), round(v)] for p, v in bids[:5]],
        "ask_walls": [[round(p, 4), round(v)] for p, v in asks[:5]],
    }


def analyze_with_local_llm(symbol, ind1h, ind15, ind5, book, btc1h):
    prompt = f"""Ти — аналітик ринку криптовалют (маркет-мейкер, 15+ років).
Дай структурований аналіз {symbol} і BTC для торгового агента (він сам ставить ордери).
НЕ став ордерів, тільки аналіз + рекомендації.

ВХІДНІ ДАНІ ({datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC):
{symbol} 1h: {json.dumps(ind1h)}
{symbol} 15m: {json.dumps(ind15)}
{symbol} 5m: {json.dumps(ind5)}
Ордербук {symbol}: {json.dumps(book)}
BTC 1h: {json.dumps(btc1h)}

ФОРМАТ ВІДПОВІДІ (JSON, без markdown):
{{
  "bias": "LONG|SHORT|NEUTRAL",
  "conviction": 0.0-1.0,
  "trend_state": "опис тренда (1 речення)",
  "signals": ["вхід/вихід сигнали"],
  "entry_long": число_або_null, "tp_long": число, "sl_long": число,
  "entry_short": число_або_null, "tp_short": число, "sl_short": число,
  "risk_note": "ризик-менеджмент 1 речення",
  "comment": "короткий висновок для трейдера"
}}"""
    try:
        with httpx.Client(timeout=120) as c:
            r = c.post(OLLAMA_URL, json={"model": LOCAL_MODEL, "prompt": prompt,
                                          "stream": False, "format": "json",
                                          "options": {"temperature": 0.2, "num_gpu": 40}})
            out = r.json().get("response", "")
        return json.loads(out)
    except Exception as e:
        return {"error": str(e), "bias": "NEUTRAL", "conviction": 0.0}


async def main():
    now = datetime.datetime.now(datetime.UTC)
    o1, o15, o5, tk, ob = await gather(SYMBOL)
    b1, _b15, _b5, btk, _bob = await gather(BTC)
    ind1h = build_indicators(o1); ind15 = build_indicators(o15); ind5 = build_indicators(o5)
    btc1h = build_indicators(b1)
    book = book_summary(ob)
    btc_book = book_summary(_bob)
    llm = analyze_with_local_llm(SYMBOL, ind1h, ind15, ind5, book, btc1h)

    result = {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": SYMBOL,
        "mark": tk.get("last"),
        "local_model": LOCAL_MODEL,
        "indicators_1h": ind1h, "indicators_15m": ind15, "indicators_5m": ind5,
        "book": book, "btc_1h": btc1h, "btc_book": btc_book,
        "llm_analysis": llm,
    }
    out = ROOT / "runtime" / "local_analysis.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    asyncio.run(main())
