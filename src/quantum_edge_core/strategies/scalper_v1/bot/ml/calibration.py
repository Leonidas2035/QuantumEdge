"""Lightweight probability calibration helpers (stdlib only)."""

from __future__ import annotations

import math
from typing import Dict, Optional


def apply_calibration(p_up: float, calibration: Optional[Dict[str, object]]) -> float:
    if not calibration:
        return p_up
    calib_type = str(calibration.get("type", "")).lower()
    if calib_type != "platt":
        return p_up
    try:
        coef = float(calibration.get("coef", 1.0))
        intercept = float(calibration.get("intercept", 0.0))
        z = coef * float(p_up) + intercept
        return 1.0 / (1.0 + math.exp(-z))
    except Exception:
        return p_up
