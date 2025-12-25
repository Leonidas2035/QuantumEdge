import json
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from typing import Dict, List, Optional, Tuple

from bot.core.config_loader import config


class _FileState:
    def __init__(self, path: Path):
        self.path = path
        self.started_at = time.time()


class DataManager:
    """
    Buffered persistence for trades/orderbooks and tick CSVs.

    - Trades/orderbooks: JSONL with rotation.
    - Ticks: CSV stream per symbol (rotated).
    - All writes go through a background worker to avoid blocking the hot loop.
    """

    def __init__(self):
        storage = config.get("storage", {}) or {}
        self.base = Path(config.get("app.data_path", "./data"))
        self.base.mkdir(exist_ok=True)
        (self.base / "ticks").mkdir(parents=True, exist_ok=True)
        self.write_json_trades = bool(storage.get("save_trades", False))
        self.write_orderbook = bool(storage.get("save_orderbook", False))
        self.write_json_orderbook = bool(storage.get("save_orderbook_json", True))
        self.write_tick_csv = True

        self.max_bytes = int(storage.get("max_jsonl_size_mb", 5) * 1024 * 1024)
        self.max_age_sec = int(storage.get("max_jsonl_minutes", 60) * 60)
        self.retention_days = int(storage.get("retention_days", 3))
        self.batch_size = int(storage.get("flush_batch", 200))
        self.flush_interval = float(storage.get("flush_interval_seconds", 1.0))

        self._queue: "Queue[Tuple[str, dict]]" = Queue()
        self._stop = threading.Event()
        self._files: Dict[Tuple[str, Optional[str]], _FileState] = {}
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # Public API
    async def save_trade(self, data: dict):
        if self.write_json_trades:
            self._queue.put(("trade_jsonl", data))
        try:
            ts = int(data.get("T") or data.get("E") or datetime.utcnow().timestamp() * 1000)
            price = float(data.get("p"))
            qty = float(data.get("q"))
            side = "sell" if data.get("m") else "buy"
            line = f"{ts},{price},{qty},{side}\n"
            self._queue.put(("tick_csv", {"symbol": data.get("s", "UNKNOWN"), "line": line}))
        except Exception:
            pass

    async def save_orderbook(self, data: dict):
        if self.write_orderbook and self.write_json_orderbook:
            self._queue.put(("orderbook_jsonl", data))

    def close(self):
        self._stop.set()
        self._queue.put(("__stop__", {}))
        self._worker.join(timeout=2.0)

    # Internal worker
    def _run(self):
        buffers: Dict[str, List[dict]] = {"trade_jsonl": [], "orderbook_jsonl": [], "tick_csv": []}
        last_flush = time.time()
        while not self._stop.is_set():
            try:
                kind, payload = self._queue.get(timeout=self.flush_interval)
                if kind == "__stop__":
                    break
                buffers[kind].append(payload)
            except Empty:
                pass

            now = time.time()
            should_flush = any(len(v) >= self.batch_size for v in buffers.values()) or (now - last_flush) >= self.flush_interval
            if should_flush:
                try:
                    self._flush(buffers)
                except Exception:
                    pass
                last_flush = now

        # final flush
        try:
            self._flush(buffers)
        except Exception:
            pass

    def _flush(self, buffers: Dict[str, List[dict]]):
        self._flush_jsonl(buffers.get("trade_jsonl", []), subdir="trades", prefix="trades")
        self._flush_jsonl(buffers.get("orderbook_jsonl", []), subdir="orderbooks", prefix="orderbooks")
        self._flush_ticks(buffers.get("tick_csv", []))
        for k in buffers:
            buffers[k].clear()

    def _flush_jsonl(self, events: List[dict], subdir: str, prefix: str):
        if not events:
            return
        path = self._current_file(prefix, self.base / subdir, key=None)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for evt in events:
                f.write(json.dumps(evt, separators=(",", ":")))
                f.write("\n")
        self._rotate_if_needed(prefix, None, path)

    def _flush_ticks(self, entries: List[dict]):
        if not entries:
            return
        by_symbol: Dict[str, List[str]] = {}
        for item in entries:
            sym = item.get("symbol", "UNKNOWN")
            by_symbol.setdefault(sym, []).append(item.get("line", ""))
        for sym, lines in by_symbol.items():
            path = self._current_file("ticks", self.base / "ticks", key=sym, suffix="csv", prefix=f"{sym}_stream")
            header_needed = not path.exists()
            with open(path, "a", encoding="utf-8") as f:
                if header_needed:
                    f.write("timestamp,price,qty,side\n")
                f.writelines(lines)
            self._rotate_if_needed("ticks", sym, path)

    def _current_file(self, kind: str, directory: Path, key: Optional[str] = None, suffix: str = "jsonl", prefix: Optional[str] = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        file_key = (kind, key)
        state = self._files.get(file_key)
        if state:
            return state.path
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        name = prefix or kind
        path = directory / f"{name}_{ts}.{suffix}"
        state = _FileState(path)
        self._files[file_key] = state
        return path

    def _rotate_if_needed(self, kind: str, key: Optional[str], path: Path):
        state = self._files.get((kind, key))
        if not state:
            return
        too_big = path.exists() and path.stat().st_size >= self.max_bytes
        too_old = (time.time() - state.started_at) >= self.max_age_sec
        if too_big or too_old:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            directory = path.parent
            name = path.stem.split("_")[0]
            new_path = directory / f"{name}_{ts}{path.suffix}"
            self._files[(kind, key)] = _FileState(new_path)
            self._cleanup(directory)

    def _cleanup(self, directory: Path):
        if self.retention_days <= 0:
            return
        cutoff = time.time() - self.retention_days * 86400
        for f in directory.glob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                continue
