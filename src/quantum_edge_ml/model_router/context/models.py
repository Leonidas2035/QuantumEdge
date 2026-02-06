from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

try:
    import msgspec
except Exception:  # pragma: no cover - optional
    msgspec = None


@dataclass(frozen=True)
class ContextPackV1:
    v: int
    sym: str
    lbm: int
    t0: str
    t1: str
    ohlcv: List[List[float]]
    chg: Optional[float]
    vol: Optional[float]

    def to_dict(self) -> dict:
        return {
            "v": self.v,
            "sym": self.sym,
            "lbm": self.lbm,
            "t0": self.t0,
            "t1": self.t1,
            "ohlcv": self.ohlcv,
            "chg": self.chg,
            "vol": self.vol,
        }


if msgspec:

    class ContextPackV1Struct(msgspec.Struct, forbid_unknown_fields=True):
        v: int
        sym: str
        lbm: int
        t0: str
        t1: str
        ohlcv: List[List[float]]
        chg: Optional[float]
        vol: Optional[float]
