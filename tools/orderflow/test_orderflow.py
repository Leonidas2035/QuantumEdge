"""
Unit tests для модуля аналізу потоку ордерів (Order Flow).
Запуск: cd /home/layman/QuantumEdge && .venv/bin/python3 -m pytest tools/orderflow/test_orderflow.py -q
"""
import pytest
from tools.orderflow.detectors import (
    detect_book_imbalance,
    detect_layering,
    detect_trades_delta,
    detect_icebergs,
    detect_spoofing
)
from tools.orderflow.backtest import backtest_orderflow, generate_synthetic_orderflow_data


def test_detect_book_imbalance():
    snap = {
        "bids": [[100.0, 10.0], [99.0, 10.0]],
        "asks": [[101.0, 5.0], [102.0, 5.0]]
    }
    # bids sum = 20, asks sum = 10 -> (20 - 10) / (20 + 10) = 10 / 30 = 0.333
    imb = detect_book_imbalance(snap)
    assert abs(imb - 0.333) < 0.01


def test_detect_layering():
    # Медіана обсягу bids+asks = 2.0. Великий лот >= 6.0
    snap = {
        "bids": [[100.0, 1.0], [99.0, 8.0], [98.0, 7.0]],
        "asks": [[101.0, 1.0], [102.0, 1.0]]
    }
    layers = detect_layering(snap, large_mult=3.0)
    assert layers["bids_layers"] == 2
    assert layers["asks_layers"] == 0


def test_detect_trades_delta():
    events = [
        {"type": "trade", "timestamp": 1000, "price": 100.0, "amount": 2.0, "side": "buy"},
        {"type": "trade", "timestamp": 2000, "price": 100.0, "amount": 1.0, "side": "sell"},
        {"type": "trade", "timestamp": 3000, "price": 100.0, "amount": 3.0, "side": "buy"}
    ]
    # delta = 2.0 - 1.0 + 3.0 = 4.0
    delta = detect_trades_delta(events, window_ms=10000)
    assert delta == 4.0


def test_detect_icebergs():
    # Створюємо послідовність: стакан -> велика угода на рівні -> наступний стакан знову повний
    events = [
        {
            "type": "book",
            "timestamp": 1000,
            "bids": [[100.0, 2.0], [99.0, 2.0]],
            "asks": [[101.0, 2.0], [102.0, 2.0]]
        },
        {
            "type": "trade",
            "timestamp": 1100,
            "price": 100.0,
            "amount": 5.0,  # обсяг угоди перевищує видимий ліміт 2.0
            "side": "sell"  # б'є в bids
        },
        {
            "type": "book",
            "timestamp": 1200,
            "bids": [[100.0, 2.0], [99.0, 2.0]],  # рівень знову поновився
            "asks": [[101.0, 2.0], [102.0, 2.0]]
        }
    ]
    ice = detect_icebergs(events)
    assert len(ice) == 1
    assert ice[0]["price"] == 100.0
    assert ice[0]["side"] == "LONG_ICEBERG"


def test_detect_spoofing():
    # Велика стіна з'являється і зникає без виконання
    events = [
        {
            "type": "book",
            "timestamp": 1000,
            "bids": [[100.0, 1.0], [99.0, 1.0]],
            "asks": [[101.0, 10.0], [102.0, 1.0]]  # велика стіна на 101.0 (asks)
        },
        {
            "type": "book",
            "timestamp": 2000,
            "bids": [[100.0, 1.0], [99.0, 1.0]],
            "asks": [[101.0, 1.0], [102.0, 1.0]]  # стіна зникла
        }
    ]
    spoofs = detect_spoofing(events, large_mult=3.0, max_seconds=2.0)
    assert len(spoofs) == 1
    assert spoofs[0]["price"] == 101.0
    assert spoofs[0]["side"] == "SELL_SPOOF"


def test_backtest_runs(tmp_path):
    synthetic_file = tmp_path / "test_raw.jsonl"
    generate_synthetic_orderflow_data(synthetic_file, n_events=100)
    res = backtest_orderflow(synthetic_file, horizon_seconds=2.0)
    assert isinstance(res, dict)
    assert "H1_spoofing" in res
    assert "H2_imbalance" in res
    assert "H3_delta" in res
    assert "H4_iceberg" in res


def test_liqmap_bridge():
    from tools.orderflow.backtest import check_liqmap_bridge
    # LONG_LIQ zone below price (e.g. price=99.0 when last price is 100.0)
    zone = {"side": "LONG_LIQ", "price": 99.0}
    
    # 1. Сценарій з підтвердженням (продаж дельти)
    events_confirm = [
        {
            "type": "book",
            "timestamp": 1000,
            "bids": [[100.0, 1.0]],
            "asks": [[101.0, 1.0]]
        },
        {
            "type": "trade",
            "timestamp": 1100,
            "price": 100.0,
            "amount": 2.0,
            "side": "sell"  # продаж дельти
        }
    ]
    assert check_liqmap_bridge(zone, events_confirm) is True
    
    # 2. Сценарій без підтвердження (купівля дельти)
    events_no_confirm = [
        {
            "type": "book",
            "timestamp": 1000,
            "bids": [[100.0, 1.0]],
            "asks": [[101.0, 1.0]]
        },
        {
            "type": "trade",
            "timestamp": 1100,
            "price": 100.0,
            "amount": 2.0,
            "side": "buy"  # купівля дельти
        }
    ]
    assert check_liqmap_bridge(zone, events_no_confirm) is False


def test_writer_loop_rotation(tmp_path):
    """Ротація файлів запобігає гігантським файлам і захищає диск."""
    import asyncio
    from tools.orderflow.collector import writer_loop

    async def _run():
        base = tmp_path / "rot"
        q = asyncio.Queue()
        writer = asyncio.create_task(writer_loop(q, base, rotate_minutes=60, max_bytes=300))
        for i in range(6):
            await q.put({"type": "book", "n": i, "pad": "x" * 50})
        await asyncio.sleep(0.1)
        await q.put(None)
        await writer
        files = sorted(tmp_path.glob("rot_*.jsonl"))
        assert len(files) >= 2, f"ротація не спрацювала, файлів: {len(files)}"
        for f in files:
            assert f.stat().st_size <= 400, f"файл завеликий: {f.stat().st_size}"

    asyncio.run(_run())

