from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RouterPolicy:
    mode: str
    teacher_ratio: float
    force_teacher: bool

    @classmethod
    def from_env(cls) -> "RouterPolicy":
        import os

        mode = os.environ.get("SUPERVISOR_ROUTER_MODE", "local_first")
        ratio = float(os.environ.get("SUPERVISOR_TEACHER_RATIO", "0.1"))
        force = os.environ.get("SUPERVISOR_FORCE_TEACHER", "0") == "1"
        return cls(mode=mode, teacher_ratio=ratio, force_teacher=force)

    def with_hints(
        self,
        mode: Optional[str] = None,
        teacher_ratio: Optional[float] = None,
        force_teacher: Optional[bool] = None,
    ) -> "RouterPolicy":
        return RouterPolicy(
            mode=mode or self.mode,
            teacher_ratio=(
                self.teacher_ratio if teacher_ratio is None else teacher_ratio
            ),
            force_teacher=(
                self.force_teacher if force_teacher is None else force_teacher
            ),
        )
