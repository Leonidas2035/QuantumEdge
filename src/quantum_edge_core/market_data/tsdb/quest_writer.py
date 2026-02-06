"""
src/quantum_edge_core/market_data/tsdb/quest_writer.py

High-performance Influx Line Protocol (ILP) Writer for QuestDB.
Uses Asyncio for non-blocking TCP interfacing.
"""

import asyncio
import logging
import structlog
from typing import Dict, Any

logger = structlog.get_logger()

class QuestILPWriter:
    """
    Async ILP Writer.
    Buffers metrics in a queue and pushes to QuestDB via TCP.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9009):
        self.host = host
        self.port = port
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self.writer = None
        self.reader = None # not used but returned by open_connection
        self._stop_event = asyncio.Event()
        self._worker_task = None
        self.logger = logger.bind(component="QuestILPWriter", host=host, port=port)

    async def connect(self):
        """Start the background worker."""
        if self._worker_task:
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(self._worker())
        self.logger.info("QuestILPWriter started")

    async def stop(self):
        """Flush and stop."""
        self._stop_event.set()
        if self._worker_task:
            await self._worker_task
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    def enqueue(self, table: str, symbols: Dict[str, Any], columns: Dict[str, Any]):
        """
        Convert to ILP and enqueue.
        Format: table,sym1=val,sym2=val col1=val,col2=val timestamp\n
        """
        try:
            line = self._format_ilp(table, symbols, columns)
            self.queue.put_nowait(line)
        except asyncio.QueueFull:
            self.logger.warning("QuestDB Queue Full - Dropping metric", table=table)
        except Exception as e:
            self.logger.error("Failed to format/enqueue ILP", error=str(e))

    def _format_ilp(self, table: str, symbols: Dict[str, Any], columns: Dict[str, Any]) -> str:
        # 1. Table
        sb = [table]
        
        # 2. Symbols (Tags) - Sorted by key, comma separated, no spaces
        if symbols:
            # Escape keys/values if needed (simplified here: assume safe chars for High Perf)
            # Influx requires escaping space, comma, equals in keys/tags.
            # For HFT, we control inputs, so we skip heavy regex for speed, but handle basic string casting.
            sorted_syms = sorted(symbols.items())
            for k, v in sorted_syms:
                sb.append(f",{k}={v}")
                
        # 3. Separator
        sb.append(" ")
        
        # 4. Columns (Fields)
        if columns:
            parts = []
            for k, v in columns.items():
                val_str = ""
                if isinstance(v, int):
                    val_str = f"{v}i" # ILP Integer
                elif isinstance(v, float):
                    val_str = f"{v}"
                elif isinstance(v, str):
                    val_str = f'"{v}"' # Quoted string
                elif isinstance(v, bool):
                    val_str = "T" if v else "F"
                else:
                    val_str = f'"{str(v)}"'
                
                parts.append(f"{k}={val_str}")
            sb.append(",".join(parts))
            
        # 5. Timestamp (Server ingestion time is usually fine, but strictly ILP allows passing nanos)
        # We let QuestDB apply server timestamp for max ingestion speed unless specified. 
        # But if we wanted to pass one: sb.append(f" {ts_ns}")
        # For this implementation, we append newline.
        sb.append("\n")
        
        return "".join(sb)

    async def _worker(self):
        """Background task to drain queue and write to socket."""
        backoff = 1
        
        while not self._stop_event.is_set():
            # 1. Ensure connection
            if self.writer is None:
                try:
                    self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                    self.logger.info("Connected to QuestDB")
                    backoff = 1
                except Exception as e:
                    self.logger.warning("Connection failed, retrying...", error=str(e))
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue

            # 2. Batch read
            batch = []
            batch_size = 0
            MAX_BATCH_BYTES = 4096
            
            try:
                # Wait for first item
                line = await self.queue.get()
                batch.append(line)
                batch_size += len(line)
                self.queue.task_done()
                
                # Opportunistic batching
                while not self.queue.empty() and batch_size < MAX_BATCH_BYTES:
                    line = self.queue.get_nowait()
                    batch.append(line)
                    batch_size += len(line)
                    self.queue.task_done()
                
                # 3. Write
                payload = "".join(batch).encode('utf-8')
                self.writer.write(payload)
                await self.writer.drain()
                
            except Exception as e:
                self.logger.error("Write error", error=str(e))
                self.writer.close()
                await self.writer.wait_closed()
                self.writer = None # Trigger reconnect
