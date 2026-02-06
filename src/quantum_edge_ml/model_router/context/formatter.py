from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from model_router.context.models import ContextPackV1


@dataclass
class ContextFormatter:
    max_candles: int = 5

    def format(self, pack: ContextPackV1) -> str:
        candles = pack.ohlcv[: self.max_candles]
        cndl = []
        for row in candles:
            cndl.append([self._round(v) for v in row])
        parts = [
            f"CTX|sym={pack.sym}",
            f"lbm={pack.lbm}",
            f"chg={self._fmt_float(pack.chg)}",
            f"vol={self._fmt_float(pack.vol)}",
            f"cndl={self._compact_list(cndl)}",
        ]
        return "|".join(parts)

    def _fmt_float(self, val):
        if val is None:
            return "na"
        return f"{val:.4f}".rstrip("0").rstrip(".")

    def _round(self, val):
        if isinstance(val, (int, float)):
            return round(val, 6)
        return val

    def _compact_list(self, data: List[List[float]]) -> str:
        return str(data).replace(" ", "")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pct_change(start: float, end: float) -> float:
    if start == 0:
        return 0.0
    return (end - start) / start


def realized_vol(closes: List[float]) -> float | None:
    if len(closes) < 2:
        return None
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] == 0:
            continue
        rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)
