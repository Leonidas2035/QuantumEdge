"""Replay spooled L2 events into QuestDB via ILP with cursor state."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
from urllib.parse import quote
from urllib.request import urlopen

from market_data.models import L2Envelope, decode_l2


class ReplayState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.last_file: Optional[str] = None
        self.last_line: int = 0
        self._loaded = False

    def load(self) -> None:
        if not self._path.exists():
            self._loaded = True
            return
        with self._path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.last_file = data.get("last_file")
        self.last_line = int(data.get("last_line", 0))
        self._loaded = True

    def update(self, file_rel: str, line_no: int) -> None:
        self.last_file = file_rel
        self.last_line = line_no

    def persist(self) -> None:
        data = {
            "last_file": self.last_file,
            "last_line": self.last_line,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self._path)


class ILPClient:
    def __init__(self, host: str, port: int, dry_run: bool = False) -> None:
        self._host = host
        self._port = port
        self._dry_run = dry_run
        self._sock: Optional[socket.socket] = None
        self._backoff = 1.0

    def send(self, lines: Sequence[str]) -> bool:
        if not lines:
            return True
        if self._dry_run:
            logging.info("dry-run: %d ILP lines prepared (no network)", len(lines))
            return True
        try:
            self._ensure_connected()
            payload = "\n".join(lines) + "\n"
            self._sock.sendall(payload.encode("utf-8"))
            self._backoff = 1.0
            return True
        except (OSError, socket.timeout) as exc:
            logging.warning("ILP send failed: %s", exc)
            self._close()
            time.sleep(min(self._backoff, 10.0))
            self._backoff = min(self._backoff * 2, 10.0)
            return False

    def _ensure_connected(self) -> None:
        if self._sock:
            return
        while True:
            try:
                self._sock = socket.create_connection((self._host, self._port), timeout=5.0)
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                logging.info("QuestDB ILP connected to %s:%d", self._host, self._port)
                return
            except OSError as exc:
                logging.warning("QuestDB ILP connect failed: %s", exc)
                time.sleep(min(self._backoff, 10.0))
                self._backoff = min(self._backoff * 2, 10.0)

    def _close(self) -> None:
        if self._sock:
            with contextlib.suppress(Exception):
                self._sock.close()
        self._sock = None


def _list_spool_files(spool_dir: Path, from_date: Optional[str], to_date: Optional[str]) -> List[Path]:
    files = []
    base = spool_dir
    if not base.exists():
        return []
    for path in sorted(base.rglob("*.jsonl.gz")):
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) < 3:
            continue
        date_dir = parts[0]
        if from_date and date_dir < from_date:
            continue
        if to_date and date_dir > to_date:
            continue
        files.append(path)
    return sorted(files)


def _format_l2_line(event: L2Envelope) -> Optional[str]:
    table = _entity_to_table(event.entity)
    if not table:
        return None
    tags = []
    if event.symbol:
        tags.append(f"symbol={event.symbol}")
    if event.source:
        tags.append(f"source={event.source}")
    measurement = table + ("," + ",".join(tags) if tags else "")
    fields = {}
    if event.seq is not None:
        fields["seq"] = event.seq
    if event.event_id:
        fields["event_id"] = event.event_id
    payload = event.payload or {}
    fields["payload_json"] = json.dumps(payload, separators=(",", ":"))
    if event.entity == "fills":
        for key in ("order_id", "side", "qty", "price", "fee", "pnl", "exchange", "account"):
            fields[key] = payload.get(key)
    elif event.entity == "positions":
        for key in ("side", "qty", "entry_price", "mark_price", "unrealized_pnl", "leverage", "margin"):
            fields[key] = payload.get(key)
    elif event.entity == "equity":
        for key in ("equity", "balance", "available", "currency"):
            fields[key] = payload.get(key)
    elif event.entity == "risk":
        for key in ("risk_mode", "max_dd", "exposure", "notes"):
            fields[key] = payload.get(key)
    field_pairs = []
    for key, value in fields.items():
        if value is None:
            continue
        formatted = _format_value(value)
        if formatted is None:
            continue
        field_pairs.append(f"{key}={formatted}")
    if not field_pairs:
        return None
    return f"{measurement} {','.join(field_pairs)} {event.ts_ns}"


def _entity_to_table(entity: str) -> Optional[str]:
    mapping = {
        "fills": "l2_fills",
        "positions": "l2_positions",
        "equity": "l2_equity",
        "risk": "l2_risk",
    }
    return mapping.get(entity)


def _format_value(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    return json.dumps(value)


def _verify_http(quest_host: str, http_port: int) -> None:
    tables = ("l2_fills", "l2_positions", "l2_equity", "l2_risk")
    for table in tables:
        query = quote(f"select count(*) from {table}")
        url = f"http://{quest_host}:{http_port}/exec?query={query}"
        try:
            with urlopen(url, timeout=5) as resp:
                logging.info("HTTP verify %s -> %s", table, resp.status)
        except Exception as exc:
            logging.warning("HTTP verification for %s failed: %s", table, exc)


def run_replay(
    spool_dir: Path,
    state_file: Path,
    quest_host: str,
    ilp_port: int,
    batch_rows: int,
    flush_interval_ms: int,
    max_files: Optional[int],
    dry_run: bool,
    from_date: Optional[str],
    to_date: Optional[str],
    verify_http: bool,
    http_port: int,
) -> None:
    logging.info("Replay L2 spool from %s", spool_dir)
    files = _list_spool_files(spool_dir, from_date, to_date)
    state = ReplayState(state_file)
    state.load()
    if state.last_file:
        resume_path = spool_dir / state.last_file
        if not resume_path.exists():
            logging.warning("Replay state refers to %s which is missing; replaying from start", state.last_file)
            state.last_file = None
            state.last_line = 0
    client = ILPClient(quest_host, ilp_port, dry_run=dry_run)
    batch: List[str] = []
    pending: List[Tuple[str, int]] = []
    last_flush = time.time()
    files_processed = 0
    total_lines = 0
    for file_path in files:
        rel = str(file_path.relative_to(spool_dir))
        if max_files and files_processed >= max_files:
            break
        if state.last_file and rel < state.last_file:
            continue
        start_line = 0
        if state.last_file == rel:
            start_line = state.last_line
        elif state.last_file and rel == state.last_file:
            start_line = state.last_line
        if rel == state.last_file:
            logging.info("Resuming %s at line %d", rel, start_line)
        with gzip.open(file_path, "rb") as fh:
            for idx, raw in enumerate(fh, start=1):
                if idx <= start_line:
                    continue
                if not raw.strip():
                    continue
                try:
                    envelope = decode_l2(raw)
                except Exception as exc:
                    logging.warning("Failed to decode %s:%d: %s", rel, idx, exc)
                    continue
                line = _format_l2_line(envelope)
                if line:
                    batch.append(line)
                    pending.append((rel, idx))
                    total_lines += 1
                now = time.time()
                if len(batch) >= batch_rows or (now - last_flush) * 1000 >= flush_interval_ms:
                    if client.send(batch):
                        for file_checkpoint, file_line in pending:
                            state.update(file_checkpoint, file_line)
                        state.persist()
                        pending.clear()
                        batch.clear()
                        last_flush = now
                    else:
                        logging.warning("Retrying batch after failure")
                        time.sleep(1)
        files_processed += 1
    if batch:
        if client.send(batch):
            for file_checkpoint, file_line in pending:
                state.update(file_checkpoint, file_line)
            state.persist()
            pending.clear()
            batch.clear()
    if verify_http:
        _verify_http(quest_host, http_port)
    logging.info("Replay completed: %d files, %d lines", files_processed, total_lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay spooled L2 events into QuestDB.")
    parser.add_argument("--spool-dir", default="spool/l2", help="Spool directory root (default: %(default)s)")
    parser.add_argument("--state-file", default="spool/l2/.replay_state.json", help="Replay cursor file")
    parser.add_argument("--quest-host", default="127.0.0.1")
    parser.add_argument("--ilp-port", type=int, default=9009)
    parser.add_argument("--batch-rows", type=int, default=5000)
    parser.add_argument("--flush-interval-ms", type=int, default=200)
    parser.add_argument("--max-files", type=int, help="Limit number of files to replay")
    parser.add_argument("--from-date", help="Start replay at date (YYYY-MM-DD)")
    parser.add_argument("--to-date", help="Stop replay after date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Do not send to QuestDB")
    parser.add_argument("--verify-http", action="store_true", help="Hit QuestDB HTTP /exec after replay")
    parser.add_argument("--http-port", type=int, default=9000)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    spool_dir = Path(args.spool_dir)
    state_file = Path(args.state_file)
    run_replay(
        spool_dir=spool_dir,
        state_file=state_file,
        quest_host=args.quest_host,
        ilp_port=args.ilp_port,
        batch_rows=args.batch_rows,
        flush_interval_ms=args.flush_interval_ms,
        max_files=args.max_files,
        dry_run=args.dry_run,
        from_date=args.from_date,
        to_date=args.to_date,
        verify_http=args.verify_http,
        http_port=args.http_port,
    )


if __name__ == "__main__":
    main()
