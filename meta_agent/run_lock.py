import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class RunLock:
    path: Path
    acquired: bool = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False

        payload = {
            "pid": os.getpid(),
            "started_at": datetime.utcnow().isoformat() + "Z",
        }
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self.acquired = False


def resolve_lock_path(base_dir: str) -> Path:
    runtime_dir = os.getenv("QE_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir).resolve() / "meta_agent" / "meta_agent.lock"
    return Path(base_dir).resolve() / "runtime" / "meta_agent" / "meta_agent.lock"


def describe_existing_lock(path: Path) -> Optional[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    pid = data.get("pid")
    started = data.get("started_at")
    if pid and started:
        return f"Lock held by pid={pid} since {started}"
    if pid:
        return f"Lock held by pid={pid}"
    return None
