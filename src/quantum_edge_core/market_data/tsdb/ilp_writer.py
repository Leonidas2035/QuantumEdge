import logging
import os
import queue
import threading
import time
from questdb.ingress import Sender, IngressError, TimestampNanos

logger = logging.getLogger("QuestDB_ILP_Writer")

class QuestDBILPWriter:
    def __init__(self, conf: str = None, batch_size: int = 50, flush_interval_sec: float = 0.5):
        if conf is None:
            # Check host/port or fallback to default
            host = os.getenv("MARKET_DATA_QUEST_HOST", "127.0.0.1")
            port = os.getenv("MARKET_DATA_QUEST_ILP_PORT", "9009")
            conf = f"tcp::addr={host}:{port};"
            
        self.conf = conf
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self.queue = queue.Queue(maxsize=10000)
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        logger.info(f"QuestDB ILP Writer thread started targeting {conf}")

    def write_row(self, table: str, symbols: dict, columns: dict, ts=None):
        """Puts a row write task onto the background queue."""
        try:
            self.queue.put_nowait((table, symbols, columns, ts))
        except queue.Full:
            logger.warning(f"ILP Writer queue full! Dropping write to {table}")

    def _worker(self):
        while self.running:
            try:
                with Sender.from_conf(self.conf) as sender:
                    logger.info(f"QuestDB ILP Sender successfully connected to {self.conf}")
                    last_flush = time.time()
                    batch = []
                    
                    while self.running:
                        try:
                            # Wait for a item to arrive in the queue
                            item = self.queue.get(timeout=0.1)
                            batch.append(item)
                        except queue.Empty:
                            pass
                        
                        now = time.time()
                        # Flush if batch size limit met or time elapsed
                        if batch and (len(batch) >= self.batch_size or (now - last_flush) >= self.flush_interval_sec):
                            try:
                                for table, symbols, columns, ts in batch:
                                    at_val = None
                                    if ts is not None:
                                        if isinstance(ts, float):
                                            at_val = TimestampNanos(int(ts * 1_000_000_000))
                                        elif isinstance(ts, int):
                                            if ts < 2_000_000_000:
                                                at_val = TimestampNanos(ts * 1_000_000_000)
                                            else:
                                                at_val = TimestampNanos(ts)
                                    
                                    sender.row(table, symbols=symbols, columns=columns, at=at_val)
                                sender.flush()
                                last_flush = now
                                batch.clear()
                            except IngressError as ie:
                                logger.error(f"QuestDB IngressError during flush: {ie}")
                                batch.clear()  # Clear bad batch to avoid loop blocking
                                break  # Reconnect
                            except Exception as e:
                                logger.error(f"Unexpected error in ILP sender: {e}")
                                batch.clear()
                                break
            except Exception as e:
                logger.error(f"QuestDB ILP Connection to {self.conf} failed: {e}. Retrying in 5 seconds...")
                # Sleep in small increments to respond to shutdown signals
                for _ in range(50):
                    if not self.running:
                        break
                    time.sleep(0.1)

    def close(self):
        self.running = False
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)

# Global singleton instance
_writer = None

def get_ilp_writer() -> QuestDBILPWriter:
    global _writer
    if _writer is None:
        _writer = QuestDBILPWriter()
    return _writer
