import requests
import json
from datetime import datetime

SYMBOL = "BTCUSDT"

def fetch_binance_data():
    print(f"📊 Збір макро- та мікро-даних для {SYMBOL}...")
    snapshot = {}

    try:
        BASE_URL = "https://testnet.binancefuture.com/fapi"
        # 1. Поточна ціна та 24h статистика (Spot)
        ticker_resp = requests.get(f"{BASE_URL}/v1/ticker/24hr?symbol={SYMBOL}").json()
        snapshot['price'] = float(ticker_resp['lastPrice'])
        snapshot['vol_24h_btc'] = float(ticker_resp['volume'])
        snapshot['price_change_pct'] = float(ticker_resp['priceChangePercent'])

        # 2. Книги ордерів / Стакан (Depth) - шукаємо "плити"
        depth_resp = requests.get(f"{BASE_URL}/v1/depth?symbol={SYMBOL}&limit=100").json()
        bids = depth_resp.get('bids', [])
        asks = depth_resp.get('asks', [])
        
        # Рахуємо загальний об'єм у видимому стакані
        snapshot['l2_bid_vol'] = sum(float(b[1]) for b in bids)
        snapshot['l2_ask_vol'] = sum(float(a[1]) for a in asks)
        
        # Шукаємо найбільшу стінку на покупку та продаж
        biggest_bid = max(bids, key=lambda x: float(x[1])) if bids else [0, 0]
        biggest_ask = max(asks, key=lambda x: float(x[1])) if asks else [0, 0]
        snapshot['biggest_bid_wall'] = {"price": float(biggest_bid[0]), "vol": float(biggest_bid[1])}
        snapshot['biggest_ask_wall'] = {"price": float(biggest_ask[0]), "vol": float(biggest_ask[1])}

        # 3. Свічки (Klines) - Останні 4H та 1H свічки
        kline_4h = requests.get(f"{BASE_URL}/v1/klines?symbol={SYMBOL}&interval=4h&limit=2").json()
        if len(kline_4h) > 1:
            last_closed_4h = kline_4h[-2] # Беремо останню ЗАКРИТУ свічку
            snapshot['4H_trend'] = "UP" if float(last_closed_4h[4]) > float(last_closed_4h[1]) else "DOWN"

        # 4. Ф'ючерсні метрики (Дуже важливо для LLM аналізу!)
        # Funding Rate (Хто кому платить - лонги чи шорти)
        funding_resp = requests.get(f"{BASE_URL}/v1/premiumIndex?symbol={SYMBOL}").json()
        snapshot['funding_rate'] = float(funding_resp.get('lastFundingRate', 0))

        # Open Interest (Скільки грошей відкрито в позиціях)
        oi_resp = requests.get(f"{BASE_URL}/v1/openInterest?symbol={SYMBOL}").json()
        snapshot['open_interest_btc'] = float(oi_resp.get('openInterest', 0))

    except Exception as e:
        print(f"Помилка збору даних: {e}")
        return

    result_text = json.dumps(snapshot, indent=2, ensure_ascii=False)
    print("\n✅ Сирі дані зібрано. Ось як це виглядає у JSON:")
    print(result_text)
    
    with open("ОТРИМАНІ_ДАНІ.txt", "w", encoding="utf-8") as f:
        f.write(result_text)
    print("\nФайл ОТРИМАНІ_ДАНІ.txt успішно збережено!")

if __name__ == "__main__":
    fetch_binance_data()
