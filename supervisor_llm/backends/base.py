from __future__ import annotations

from typing import Protocol


class Backend(Protocol):
    name: str

    def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        ...
