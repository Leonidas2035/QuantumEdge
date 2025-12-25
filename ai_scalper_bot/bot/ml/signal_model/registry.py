import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from bot.ml.feature_schema import FEATURE_NAMES


def feature_schema_hash() -> str:
    payload = json.dumps(FEATURE_NAMES, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def load_registry(model_dir: Path) -> List[Dict]:
    path = model_dir / "registry.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or []
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def find_entry(registry: List[Dict], symbol: str, horizon: int) -> Optional[Dict]:
    for entry in registry:
        if str(entry.get("symbol")).upper() == symbol.upper() and int(entry.get("horizon", -1)) == int(horizon):
            return entry
    return None


def update_registry(model_dir: Path, symbol: str, horizon: int, model_path: Path) -> None:
    registry_path = model_dir / "registry.json"
    registry = load_registry(model_dir)
    registry = [r for r in registry if not (str(r.get("symbol")).upper() == symbol.upper() and int(r.get("horizon", -1)) == int(horizon))]
    entry = {
        "symbol": symbol.upper(),
        "horizon": int(horizon),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "feature_schema_hash": feature_schema_hash(),
        "model_path": str(model_path),
    }
    registry.append(entry)
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
