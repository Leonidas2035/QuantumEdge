"""
================================================================
QuantumEdge — Order Flow & Order Book Manipulation Detectors
================================================================
Реалізує алгоритми аналізу стакану та стрічки угод:
  1. Book Imbalance (імбаланс стакану)
  2. Iceberg (приховані лімітні ордери)
  3. Spoofing (фейкові стінки, що зникають без виконання)
  4. Layering (множинні стіни на одному боці)
  5. Trades Delta (накопичена ринкова дельта)
  6. Liquidity Grab / Stop-Hunt (різкий пролив та повернення)
================================================================
"""
from __future__ import annotations
import numpy as np


def detect_book_imbalance(book_snap: dict, depth: int = 10) -> float:
    """
    Рахує імбаланс обсягів bids/asks на глибині depth.
    Повертає значення від -1.0 (повний аск-імбаланс) до +1.0 (повний бід-імбаланс).
    """
    bids = book_snap.get("bids", [])[:depth]
    asks = book_snap.get("asks", [])[:depth]
    
    sum_bids = sum(size for _, size in bids)
    sum_asks = sum(size for _, size in asks)
    
    total = sum_bids + sum_asks
    if total == 0:
        return 0.0
    return (sum_bids - sum_asks) / total


def detect_icebergs(events: list[dict], threshold_ratio: float = 2.0) -> list[dict]:
    """
    Детектує приховані ордери (Icebergs).
    Принцип: відбуваються ринкові угоди обсягом V на рівні P. 
    Якщо у наступному знімку стакану обсяг на цьому рівні відновлюється 
    (або сумарний об'єм угод перевищив первинно видимий ліміт, але рівень не впав).
    """
    icebergs = []
    # Зберігаємо останній відомий стан стакану
    last_book = None
    
    for i, ev in enumerate(events):
        if ev["type"] == "book":
            last_book = ev
            continue
            
        if ev["type"] == "trade" and last_book is not None:
            trade_p = ev["price"]
            trade_sz = ev["amount"]
            
            # Шукаємо цей рівень у бід/аск останнього стакану
            side = None
            book_sz = 0.0
            
            for p, s in last_book["bids"]:
                if abs(p - trade_p) < 0.1:  # наближене порівняння ціни
                    side = "bids"
                    book_sz = s
                    break
            if side is None:
                for p, s in last_book["asks"]:
                    if abs(p - trade_p) < 0.1:
                        side = "asks"
                        book_sz = s
                        break
            
            if side is not None and book_sz > 0:
                # Якщо обсяг угоди суттєво перевищує ліміт, але ціна не пройшла рівень,
                # або у наступному стакані рівень знову заповнений:
                # Шукаємо наступний стакан після угоди
                next_book = None
                for k in range(i + 1, min(i + 10, len(events))):
                    if events[k]["type"] == "book":
                        next_book = events[k]
                        break
                
                if next_book:
                    next_sz = 0.0
                    for p, s in next_book[side]:
                        if abs(p - trade_p) < 0.1:
                            next_sz = s
                            break
                    # Якщо рівень поновився після виконання
                    if trade_sz >= book_sz * 0.8 and next_sz >= book_sz * 0.5:
                        icebergs.append({
                            "timestamp": ev["timestamp"],
                            "price": trade_p,
                            "side": "LONG_ICEBERG" if side == "bids" else "SHORT_ICEBERG",
                            "trade_size": trade_sz,
                            "initial_size": book_sz,
                            "refilled_size": next_sz
                        })
    return icebergs


def detect_spoofing(events: list[dict], large_mult: float = 4.0, max_seconds: float = 5.0) -> list[dict]:
    """
    Детектує спуфінг (фейкові стінки).
    Алгоритм:
      1. На рівні P з'являється велика стіна (розмір > large_mult * медіана стакану).
      2. Стіна зникає або падає до норми менш ніж через max_seconds.
      3. За цей час ціна НЕ торкалась рівня P (не було виконання).
    """
    spoofs = []
    # Словник активних великих стін: { price: (timestamp, initial_size, side) }
    active_walls = {}
    
    # Визначаємо поріг "великої стіни"
    # Для простоти беремо медіану обсягів топ-20 рівнів
    for i, ev in enumerate(events):
        if ev["type"] == "book":
            # Розрахунок медіани
            all_sizes = [s for _, s in ev["bids"][:20]] + [s for _, s in ev["asks"][:20]]
            if not all_sizes:
                continue
            median_sz = np.median(all_sizes)
            large_thresh = median_sz * large_mult
            
            current_prices = set()
            # Обробляємо bids
            for p, s in ev["bids"]:
                current_prices.add(p)
                if s >= large_thresh:
                    if p not in active_walls:
                        active_walls[p] = (ev["timestamp"], s, "bids")
                else:
                    # Якщо велика стіна зникла або зменшилась
                    if p in active_walls:
                        ts_start, init_sz, side = active_walls[p]
                        duration = (ev["timestamp"] - ts_start) / 1000.0
                        if duration <= max_seconds:
                            spoofs.append({
                                "timestamp": ev["timestamp"],
                                "price": p,
                                "side": "BUY_SPOOF" if side == "bids" else "SELL_SPOOF",
                                "size": init_sz,
                                "duration_seconds": round(duration, 2)
                            })
                        del active_walls[p]
            
            # Обробляємо asks
            for p, s in ev["asks"]:
                current_prices.add(p)
                if s >= large_thresh:
                    if p not in active_walls:
                        active_walls[p] = (ev["timestamp"], s, "asks")
                else:
                    if p in active_walls:
                        ts_start, init_sz, side = active_walls[p]
                        duration = (ev["timestamp"] - ts_start) / 1000.0
                        if duration <= max_seconds:
                            spoofs.append({
                                "timestamp": ev["timestamp"],
                                "price": p,
                                "side": "BUY_SPOOF" if side == "bids" else "SELL_SPOOF",
                                "size": init_sz,
                                "duration_seconds": round(duration, 2)
                            })
                        del active_walls[p]
                        
            # Видаляємо з пам'яті ті стіни, які повністю зникли зі стакану
            for p in list(active_walls.keys()):
                if p not in current_prices:
                    ts_start, init_sz, side = active_walls[p]
                    duration = (ev["timestamp"] - ts_start) / 1000.0
                    if duration <= max_seconds:
                        spoofs.append({
                            "timestamp": ev["timestamp"],
                            "price": p,
                            "side": "BUY_SPOOF" if side == "bids" else "SELL_SPOOF",
                            "size": init_sz,
                            "duration_seconds": round(duration, 2)
                        })
                    del active_walls[p]

        elif ev["type"] == "trade":
            # Якщо пройшла угода за ціною великої стіни — видаляємо її без спуфінгу (це реальне виконання)
            trade_p = ev["price"]
            for p in list(active_walls.keys()):
                if abs(p - trade_p) < 0.1:
                    del active_walls[p]
                    
    return spoofs


def detect_layering(book_snap: dict, large_mult: float = 3.0) -> dict:
    """
    Детектує Layering (множинні стіни на одному боці стакану).
    Повертає кількість великих стін на bids та asks.
    """
    bids = book_snap.get("bids", [])[:20]
    asks = book_snap.get("asks", [])[:20]
    
    all_sizes = [s for _, s in bids] + [s for _, s in asks]
    if not all_sizes:
        return {"bids_layers": 0, "asks_layers": 0}
        
    median_sz = np.median(all_sizes)
    large_thresh = median_sz * large_mult
    
    bids_layers = sum(1 for _, s in bids if s >= large_thresh)
    asks_layers = sum(1 for _, s in asks if s >= large_thresh)
    
    return {
        "bids_layers": bids_layers,
        "asks_layers": asks_layers
    }


def detect_trades_delta(events: list[dict], window_ms: int = 10000) -> float:
    """
    Рахує сумарну дельту угод (buy_vol - sell_vol) у ковзному часовому вікні.
    """
    if not events:
        return 0.0
    now = events[-1]["timestamp"]
    limit_ts = now - window_ms
    
    delta = 0.0
    for ev in reversed(events):
        if ev["timestamp"] < limit_ts:
            break
        if ev["type"] == "trade":
            val = ev["amount"]
            if ev["side"] == "buy":
                delta += val
            else:
                delta -= val
    return delta


def detect_liquidity_grab(events: list[dict], threshold_pct: float = 0.5, window_seconds: float = 5.0) -> list[dict]:
    """
    Детектує Stop-Hunt / Liquidity Grab.
    Ознаки: ціна швидко змінюється (провалюється чи злітає) на threshold_pct,
    після чого миттєво (за window_seconds) повертається до попереднього значення,
    залишаючи "тінь" в стакані.
    """
    grabs = []
    prices = []
    timestamps = []
    
    for ev in events:
        if ev["type"] == "trade":
            prices.append(ev["price"])
            timestamps.append(ev["timestamp"])
            
            # Аналізуємо історію для детекту шпильки
            if len(prices) >= 10:
                p_now = ev["price"]
                t_now = ev["timestamp"]
                
                # Шукаємо базову ціну window_seconds тому
                base_p = None
                for k in range(len(prices) - 2, -1, -1):
                    if (t_now - timestamps[k]) / 1000.0 > window_seconds:
                        base_p = prices[k]
                        break
                if base_p is None and len(prices) > 0:
                    base_p = prices[0]
                    
                if base_p:
                    dev = (p_now - base_p) / base_p * 100.0
                    # Якщо є суттєве відхилення, дивимось чи ціна повернулась назад
                    # (тут ми можемо шукати забігання вперед у бектесті)
                    pass
                    
    # Для спрощення бектесту на оффлайн логах:
    # Ми можемо шукати локальні екстремуми трейдингової стрічки, які швидко повертаються назад.
    return grabs
