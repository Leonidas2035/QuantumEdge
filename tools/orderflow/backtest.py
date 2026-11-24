"""
================================================================
QuantumEdge — Order Flow & Manipulation Backtester
================================================================
Проводить аналіз зібраного стакану та угод.
Перевіряє 4 ключові гіпотези H1-H4 та розраховує статистику (win-rate, n).
Реалізує Bridge з картою ліквідацій.
================================================================
"""
from __future__ import annotations
import sys
import json
import argparse
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.orderflow.detectors import (
    detect_book_imbalance,
    detect_icebergs,
    detect_spoofing,
    detect_layering,
    detect_trades_delta
)

RUNTIME = Path("/home/layman/QuantumEdge/runtime")


def generate_synthetic_orderflow_data(file_path: Path, n_events: int = 500):
    """Генерує невеликий синтетичний набір стаканів та угод для smoke-тесту."""
    print(f"Generating synthetic orderflow dataset at {file_path}...")
    events = []
    ts = 1_700_000_000_000
    price = 64300.0
    
    for i in range(n_events):
        ts += 100  # 100ms крок
        if i % 5 == 0:
            # Створюємо стакан
            bids = []
            asks = []
            # Базовий розкид
            for k in range(1, 20):
                # Додаємо велику стіну (спуфінг) на кроці 50
                bid_size = 15.0 if (i == 50 and k == 5) else 1.0 + np.random.rand() * 2.0
                ask_size = 15.0 if (i == 150 and k == 5) else 1.0 + np.random.rand() * 2.0
                bids.append([price - k * 1.0, bid_size])
                asks.append([price + k * 1.0, ask_size])
            events.append({
                "type": "book",
                "timestamp": ts,
                "bids": bids,
                "asks": asks
            })
        else:
            # Створюємо угоду
            side = "buy" if np.random.rand() > 0.48 else "sell"
            price_change = 0.5 if side == "buy" else -0.5
            price += price_change
            
            # Робимо велику угоду (Iceberg) на кроці 220
            amount = 12.0 if i == 220 else 0.1 + np.random.rand() * 1.0
            events.append({
                "type": "trade",
                "timestamp": ts,
                "price": price,
                "amount": amount,
                "side": side
            })
            
    with open(file_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def backtest_orderflow(file_path: Path, horizon_seconds: float = 5.0):
    """
    Зчитує JSONL файл та проводить бектест гіпотез.
    """
    if not file_path.exists():
        return None
        
    events = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
                
    if len(events) < 50:
        return None

    # Списки для накопичення результатів по гіпотезах
    h1_results = []  # Spoofing
    h2_results = []  # Imbalance
    h3_results = []  # Trades Delta
    h4_results = []  # Iceberg / Reversal

    # Проганяємо детектори по всьому масиву
    spoofs = detect_spoofing(events, large_mult=3.0, max_seconds=3.0)
    icebergs = detect_icebergs(events)

    # Для H2 та H3 робимо ковзний прохід
    for i, ev in enumerate(events):
        if ev["type"] == "book":
            imb = detect_book_imbalance(ev, depth=10)
            
            # Шукаємо зміну ціни у майбутньому через `horizon_seconds`
            now_ts = ev["timestamp"]
            future_price = None
            entry_price = ev["bids"][0][0]
            
            for k in range(i + 1, len(events)):
                if events[k]["timestamp"] - now_ts > horizon_seconds * 1000:
                    if events[k]["type"] == "trade":
                        future_price = events[k]["price"]
                        break
            
            if future_price is not None:
                price_change = (future_price - entry_price) / entry_price * 100.0
                
                # H2: Book imbalance > 0.3 -> Ціна росте
                if imb > 0.3:
                    h2_results.append(1 if price_change > 0 else 0)
                elif imb < -0.3:
                    h2_results.append(1 if price_change < 0 else 0)

        elif ev["type"] == "trade":
            # H3: Trades Delta (10с) > 0 і ціна росте -> Ціна продовжує рости
            sub_events = events[:i+1]
            delta = detect_trades_delta(sub_events, window_ms=10000)
            
            now_ts = ev["timestamp"]
            future_price = None
            entry_price = ev["price"]
            
            for k in range(i + 1, len(events)):
                if events[k]["timestamp"] - now_ts > horizon_seconds * 1000:
                    if events[k]["type"] == "trade":
                        future_price = events[k]["price"]
                        break
            
            if future_price is not None:
                price_change = (future_price - entry_price) / entry_price * 100.0
                if delta > 2.0:
                    h3_results.append(1 if price_change > 0 else 0)
                elif delta < -2.0:
                    h3_results.append(1 if price_change < 0 else 0)

    # H1: Spoofing зникнення стіни -> Ціна йде проти стіни
    # (якщо BUY_SPOOF зник -> ціна йде вниз, SELL_SPOOF -> вгору)
    for sp in spoofs:
        sp_ts = sp["timestamp"]
        future_price = None
        for ev in events:
            if ev["timestamp"] > sp_ts and ev["type"] == "trade":
                future_price = ev["price"]
                break
        if future_price is not None:
            price_change = future_price - sp["price"]
            if sp["side"] == "BUY_SPOOF":
                h1_results.append(1 if price_change < 0 else 0)
            else:
                h1_results.append(1 if price_change > 0 else 0)

    # H4: Iceberg відскок
    for ic in icebergs:
        ic_ts = ic["timestamp"]
        future_price = None
        for ev in events:
            if ev["timestamp"] > ic_ts and ev["type"] == "trade":
                future_price = ev["price"]
                break
        if future_price is not None:
            price_change = future_price - ic["price"]
            if ic["side"] == "LONG_ICEBERG":
                h4_results.append(1 if price_change > 0 else 0)  # відскок вгору
            else:
                h4_results.append(1 if price_change < 0 else 0)  # відскок вниз

    return {
        "H1_spoofing": {"n": len(h1_results), "hit_rate": round(sum(h1_results)/len(h1_results), 3) if h1_results else 0.0},
        "H2_imbalance": {"n": len(h2_results), "hit_rate": round(sum(h2_results)/len(h2_results), 3) if h2_results else 0.0},
        "H3_delta": {"n": len(h3_results), "hit_rate": round(sum(h3_results)/len(h3_results), 3) if h3_results else 0.0},
        "H4_iceberg": {"n": len(h4_results), "hit_rate": round(sum(h4_results)/len(h4_results), 3) if h4_results else 0.0}
    }


def check_liqmap_bridge(zone, events_slice):
    """
    Поєднує сигнал зони ліквідації та підтвердження зі стакана/дельти.
    zone: dict (зона з liqmap_latest.json)
    events_slice: список останніх ордерфлоу подій.
    Повертає True, якщо є підтвердження зі стакана, інакше False.
    """
    # Останній стакан
    last_book = None
    for ev in reversed(events_slice):
        if ev["type"] == "book":
            last_book = ev
            break
            
    if last_book is None:
        return False
        
    imb = detect_book_imbalance(last_book, depth=10)
    delta = detect_trades_delta(events_slice, window_ms=10000)
    
    if zone["side"] == "LONG_LIQ":
        # Ціна падає в лонг-ліквідацію. Стакан має показувати тиск вниз (imb < -0.1 або delta < 0)
        return imb < -0.1 or delta < 0
    else:
        # Ціна росте в шорт-ліквідацію. Стакан має показувати тиск вгору (imb > 0.1 or delta > 0)
        return imb > 0.1 or delta > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="", help="шлях до файлу збору")
    ap.add_argument("--horizon", type=float, default=5.0, help="горизонт оцінки (сек)")
    args = ap.parse_args()

    file_path = Path(args.file) if args.file else None
    
    # Шукаємо автоматично файли в runtime/orderflow_raw
    raw_dir = RUNTIME / "orderflow_raw"
    is_synthetic = False
    
    if not file_path or not file_path.exists():
        files = sorted(raw_dir.glob("orderflow_*.jsonl"), reverse=True)
        if files:
            file_path = files[0]
        else:
            # Якщо файлів немає — створюємо синтетичний
            file_path = raw_dir / "orderflow_synthetic_BTC_USDT.jsonl"
            generate_synthetic_orderflow_data(file_path)
            is_synthetic = True

    print(f"Reading orderflow data from: {file_path}")
    res = backtest_orderflow(file_path, horizon_seconds=args.horizon)
    
    if res is None:
        print("Error: insufficient events for backtest.")
        return

    # Зберігаємо результати
    out_path = RUNTIME / "orderflow_calibration.json"
    payload = {
        "source_file": str(file_path),
        "is_synthetic": is_synthetic,
        "results": res
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Saved calibration to {out_path}")

    print("\nORDERFLOW EDGE BACKTEST RESULTS:")
    if is_synthetic:
        print("⚠️ УВАГА: Результати отримано на СИНТЕТИЧНИХ ДАНИХ (для smoke-тесту)!")
        print("Для реальної торгівлі запустіть tools.orderflow.collector на кілька днів.")
        
    print(f"{'Hypothesis':15s} {'n_samples':>9s} {'hit_rate':>9s} {'status':>10s}")
    for h, v in res.items():
        status = "PASS (edge)" if v["hit_rate"] >= 0.55 and v["n"] >= 10 else "NO EDGE"
        print(f"{h:15s} {v['n']:9d} {v['hit_rate']:9.1%} {status:>10s}")


if __name__ == "__main__":
    main()
