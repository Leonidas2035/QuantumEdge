import os
import sys
import json
import logging
import time
from pathlib import Path
from decimal import Decimal
import ccxt

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - AI-Bridge - %(levelname)s - %(message)s")
logger = logging.getLogger("AI-Bridge")

def load_env() -> dict:
    env_path = Path("/home/layman/.hermes/.env")
    env_vars = {}
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env_vars[k.strip()] = v.strip()
    return env_vars

def main():
    logger.info("Initializing AI Bridge...")
    env = load_env()
    
    api_key = env.get("BINGX_TESTNET_API_KEY", "")
    secret = env.get("BINGX_TESTNET_SECRET", "")
    
    if not api_key or not secret:
        logger.error("BingX VST credentials not found in ~/.hermes/.env")
        sys.exit(1)
        
    exchange = ccxt.bingx({
        "apiKey": api_key,
        "secret": secret,
        "options": {"defaultType": "swap"}
    })
    exchange.set_sandbox_mode(True)
    
    state = {
        "timestamp": int(time.time()),
        "status": "SUCCESS",
        "balance": {},
        "positions": [],
        "market": {}
    }
    
    # 1. Fetch balance
    try:
        balance = exchange.fetch_balance()
        vst = balance.get("VST", {})
        state["balance"] = {
            "free": vst.get("free", 0.0),
            "used": vst.get("used", 0.0),
            "total": vst.get("total", 0.0)
        }
        logger.info(f"Fetched balance: {state['balance']}")
    except Exception as e:
        logger.error(f"Failed to fetch balance: {e}")
        state["status"] = "ERROR_BALANCE"
        
    # 2. Fetch positions
    try:
        positions = exchange.fetch_positions()
        open_positions = []
        for p in positions:
            if float(p.get("contracts", 0)) > 0 or float(p.get("initialMargin", 0)) > 0:
                open_positions.append({
                    "symbol": p["symbol"],
                    "side": p["side"],
                    "contracts": float(p["contracts"]),
                    "entry_price": float(p["entryPrice"]),
                    "unrealized_pnl": float(p["unrealizedPnl"]),
                    "leverage": float(p.get("leverage", 20.0))
                })
        state["positions"] = open_positions
        logger.info(f"Fetched {len(open_positions)} open positions")
    except Exception as e:
        logger.error(f"Failed to fetch positions: {e}")
        
    # 3. Fetch market metrics
    try:
        env_symbol = env.get("MARKET_DATA_SYMBOLS", "ETHUSDT").split(",")[0]
        symbol = env_symbol.replace("USDT", "/USDT:USDT") if "USDT" in env_symbol else "ETH/USDT:USDT"
        ticker = exchange.fetch_ticker(symbol)
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=30)
        
        # Calculate EMA, RSI, ATR
        closes = [c[4] for c in ohlcv]
        highs = [c[2] for c in ohlcv]
        lows = [c[3] for c in ohlcv]
        
        # Simple ATR calculation
        tr_list = []
        for i in range(1, len(ohlcv)):
            h_l = highs[i] - lows[i]
            h_pc = abs(highs[i] - closes[i-1])
            l_pc = abs(lows[i] - closes[i-1])
            tr = max(h_l, h_pc, l_pc)
            tr_list.append(tr)
        atr = sum(tr_list[-14:]) / 14 if len(tr_list) >= 14 else 0.0
        
        # Simple EMA calculation
        def calculate_ema(data, period):
            if len(data) < period:
                return data[-1] if data else 0.0
            k = 2 / (period + 1)
            ema = sum(data[:period]) / period
            for price in data[period:]:
                ema = price * k + ema * (1 - k)
            return ema

        ema_20 = calculate_ema(closes, 20)
        ema_50 = calculate_ema(closes, 50)
        
        # Simple RSI calculation
        def calculate_rsi(data, period=14):
            if len(data) < period + 1:
                return 50.0
            gains = []
            losses = []
            for i in range(1, len(data)):
                diff = data[i] - data[i-1]
                if diff > 0:
                    gains.append(diff)
                    losses.append(0.0)
                else:
                    gains.append(0.0)
                    losses.append(abs(diff))
            avg_gain = sum(gains[:period]) / period
            avg_loss = sum(losses[:period]) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            for i in range(period, len(gains)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                if avg_loss == 0:
                    return 100.0
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            return rsi

        rsi_14 = calculate_rsi(closes, 14)
        
        state["market"] = {
            "symbol": symbol,
            "last_price": ticker.get("last"),
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "atr_14": atr,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "rsi_14": rsi_14
        }
        logger.info(f"Calculated market metrics: {state['market']}")
    except Exception as e:
        logger.error(f"Failed to calculate market metrics: {e}")
        
    # Write to context file
    context_dir = Path("/home/layman/QuantumEdge/context")
    context_dir.mkdir(parents=True, exist_ok=True)
    report_path = context_dir / "market_state.json"
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        
    logger.info(f"Successfully wrote market state report to {report_path}")
    print(json.dumps(state, indent=2))

    # Send report to Telegram if configured
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    channel_id = env.get("TELEGRAM_CHANNEL_ID", "")
    if token and channel_id:
        send_telegram_report(state, token, channel_id)

    # Check for delegated tasks in the queue
    check_delegated_tasks()

def check_delegated_tasks():
    queue_path = Path("/home/layman/QuantumEdge/context/delegated_tasks.json")
    if not queue_path.exists():
        return
    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            queue = json.load(f)
        pending = [t for t in queue if t.get("status") == "PENDING"]
        if pending:
            logger.info(f"🚨 [DELEGATION BRIDGE] Found {len(pending)} PENDING delegated tasks:")
            for t in pending:
                logger.info(f"   - ID: {t['id']}, Task: {t['description']}")
    except Exception as e:
        logger.error(f"Failed to check delegated tasks: {e}")

def send_telegram_report(state: dict, token: str, channel_id: str):
    import requests
    # Format balance
    bal = state.get("balance", {})
    free = f"{bal.get('free', 0.0):,.2f}"
    used = f"{bal.get('used', 0.0):,.2f}"
    total = f"{bal.get('total', 0.0):,.2f}"
    
    # Format market
    m = state.get("market", {})
    price_val = m.get('last_price', 0.0)
    price = f"{price_val:,.4f}" if price_val < 10 else f"{price_val:,.2f}"
    atr = m.get('atr_14', 0.0)
    ema20_val = m.get('ema_20', 0.0)
    ema20 = f"${ema20_val:,.4f}" if ema20_val < 10 else f"${ema20_val:,.2f}"
    ema50_val = m.get('ema_50', 0.0)
    ema50 = f"${ema50_val:,.4f}" if ema50_val < 10 else f"${ema50_val:,.2f}"
    rsi = m.get('rsi_14', 0.0)
    
    # Format positions
    positions = state.get("positions", [])
    if not positions:
        pos_text = "• Немає відкритих позицій"
    else:
        pos_lines = []
        for p in positions:
            entry_p = p['entry_price']
            entry_fmt = f"{entry_p:.4f}" if entry_p < 10 else f"{entry_p:.2f}"
            pos_lines.append(
                f"• <b>{p['symbol']}</b> ({p['side'].upper()}): {p['contracts']:,} @ {entry_fmt}\n"
                f"  PnL: <code>{p['unrealized_pnl']:+,.2f} VST</code> | Плече: {p['leverage']}x"
            )
        pos_text = "\n".join(pos_lines)
        
    market_symbol = state.get("market", {}).get("symbol", "ETH/USDT:USDT").split(":")[0].replace("/", "")
    text = (
        f"<b>📊 Звіт QuantumEdge по {market_symbol}</b>\n\n"
        f"💰 <b>Баланс (VST):</b>\n"
        f"• Вільно: <code>{free} VST</code>\n"
        f"• Зайнято: <code>{used} VST</code>\n"
        f"• Загалом: <code>{total} VST</code>\n\n"
        f"📈 <b>Стан ринку ({market_symbol}):</b>\n"
        f"• Ціна: <code>{price}</code>\n"
        f"• ATR (14): <code>{atr:.4f}</code>\n"
        f"• EMA 20: <code>{ema20}</code> | EMA 50: <code>{ema50}</code>\n"
        f"• RSI (14): <code>{rsi:.2f}</code>\n\n"
        f"💼 <b>Відкриті позиції:</b>\n"
        f"{pos_text}"
    )
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": channel_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        r = requests.post(url, json=payload, timeout=15)
        logger.info(f"Telegram report sent status: {r.status_code}")
    except Exception as e:
        logger.error(f"Failed to send Telegram report: {e}")

if __name__ == "__main__":
    main()
