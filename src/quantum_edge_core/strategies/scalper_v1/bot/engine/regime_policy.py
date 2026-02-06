from typing import Dict

DEFAULT_REGIME_CONFIG: Dict[str, Dict] = {
    "flat": {"allow_long": True, "allow_short": True, "min_conf": 0.57, "style": "scalp"},
    "trending_up": {"allow_long": True, "allow_short": False, "min_conf": 0.52, "style": "trend"},
    "trending_down": {"allow_long": False, "allow_short": True, "min_conf": 0.52, "style": "trend"},
    "high_vol": {"allow_long": True, "allow_short": True, "min_conf": 0.58, "style": "scalp"},
}


class RegimePolicy:
    def __init__(self, cfg: Dict):
        self.cfg = DEFAULT_REGIME_CONFIG.copy()
        if cfg:
            for k, v in cfg.items():
                self.cfg[k] = {**self.cfg.get(k, {}), **v}

    def allow(self, regime: str, direction: str, base_min_conf: float) -> (bool, float, str):
        reg = self.cfg.get(regime, self.cfg.get("flat", {}))
        style = reg.get("style", "scalp")
        min_conf = max(base_min_conf, reg.get("min_conf", base_min_conf))
        if direction == "long" and not reg.get("allow_long", True):
            return False, min_conf, style
        if direction == "short" and not reg.get("allow_short", True):
            return False, min_conf, style
        return True, min_conf, style
