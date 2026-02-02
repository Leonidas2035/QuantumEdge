"""Policy publishing helpers (atomic JSON file write)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .policy_contract import Policy


def write_atomic_json(path: Path, payload: dict) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


class PolicyPublisher:
    """Publish policy to a local JSON file."""

    def __init__(self, file_path: Path, logger=None) -> None:
        self.file_path = file_path
        self.logger = logger

    def publish(self, policy: Policy) -> bool:
        try:
            write_atomic_json(self.file_path, policy.to_dict())
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.error("Policy publish failed: %s", exc)
            return False
        return True

