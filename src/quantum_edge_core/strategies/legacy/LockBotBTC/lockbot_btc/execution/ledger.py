"""Execution ledger JSONL writer."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict


class ExecutionLedger:
    def __init__(self, path: str, logger: logging.Logger | None = None) -> None:
        self._path = Path(path)
        self._logger = logger or logging.getLogger(__name__)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: Dict[str, Any]) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError as exc:
            self._logger.warning("Execution ledger write failed: %s", exc)
