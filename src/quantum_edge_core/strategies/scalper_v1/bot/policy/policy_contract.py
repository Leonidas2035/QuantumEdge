import json
import time
from pathlib import Path
from typing import Dict, Any, Optional

class PolicyContract:
    def __init__(self, policy_path: str):
        self.policy_path = Path(policy_path)

    def read_policy(self) -> Dict[str, Any]:
        if not self.policy_path.exists():
            return self._default_safe_policy()
            
        try:
            with open(self.policy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # Validate required fields
            required = ["allow_trading", "mode", "size_multiplier", "max_daily_loss", "ttl_sec", "ts"]
            for req in required:
                if req not in data:
                    return self._default_safe_policy()
                    
            # Enforce TTL
            now = time.time()
            if now - data["ts"] > data["ttl_sec"]:
                return self._default_safe_policy()
                
            return data
            
        except Exception:
            return self._default_safe_policy()

    def _default_safe_policy(self) -> Dict[str, Any]:
        return {
            "allow_trading": False,
            "mode": "safe_mode",
            "size_multiplier": 0.0,
            "max_daily_loss": 0.0,
            "ttl_sec": 0,
            "ts": 0.0
        }
