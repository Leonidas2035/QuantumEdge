from __future__ import annotations


class TrtllmLocalBackend:
    def __init__(self) -> None:
        self.name = "trtllm_local"
        raise RuntimeError(
            "Direct TRT-LLM runtime backend not implemented in this repo. "
            "Use trtllm-serve OpenAI-compatible backend instead."
        )

    def generate(self, prompt: str, *, system_prompt: str, timeout_s: float) -> str:
        raise RuntimeError("trtllm_local backend is unavailable")
