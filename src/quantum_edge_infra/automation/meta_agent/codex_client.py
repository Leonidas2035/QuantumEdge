import os
import re
from typing import List, Optional, Tuple

from openai import OpenAI


class CodexClient:
    def __init__(
        self,
        mode: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        chunk_size: Optional[int] = None,
        request_timeout_seconds: Optional[int] = None,
    ):
        self.mock_response = os.getenv("META_AGENT_MOCK_LLM_RESPONSE")
        if self.mock_response is not None:
            self.client = None
            self.model = model or "mock"
            self.temperature = 0 if temperature is None else temperature
            self.request_timeout_seconds = request_timeout_seconds
            self.chunk_size = chunk_size or 12000
            self.mode = "dev"
            return
        # Determine mode: env has priority, then provided arg, default dev
        env_mode = os.getenv("META_AGENT_MODE")
        resolved_mode = (env_mode or mode or "dev").strip().lower()
        if resolved_mode not in {"dev", "prod"}:
            resolved_mode = "dev"
        self.mode = resolved_mode

        env_key_name = f"OPENAI_API_KEY_{self.mode.upper()}"
        self.api_key = os.getenv(env_key_name)
        if not self.api_key:
            raise RuntimeError("API key not set in environment variables")

        self.client = OpenAI(api_key=self.api_key)

        # more stable model for long prompts
        self.model = model or "gpt-4.1"
        self.temperature = 0 if temperature is None else temperature
        self.request_timeout_seconds = request_timeout_seconds

        # max chunk size to avoid 400 errors
        self.chunk_size = chunk_size or 12000

    def _chunk_prompt(self, text: str) -> List[str]:
        """Split large prompts into smaller chunks without breaking file markers."""
        preamble, file_blocks = self._split_prompt_blocks(text)
        chunks = self._split_preamble(preamble) if preamble else [""]

        current = chunks[-1]
        for block in file_blocks:
            if not current:
                current = block
                chunks[-1] = current
                continue
            if len(current) + len(block) <= self.chunk_size:
                current += block
                chunks[-1] = current
            else:
                chunks.append(block)
                current = block

        return [chunk for chunk in chunks if chunk]

    def _split_prompt_blocks(self, text: str) -> Tuple[str, List[str]]:
        marker_re = re.compile(r"(?m)^(===FILE:.*?===|### FILE: .*)$")
        markers = list(marker_re.finditer(text))
        if not markers:
            return text, []

        preamble = text[: markers[0].start()]
        blocks: List[str] = []
        for idx, marker in enumerate(markers):
            start = marker.start()
            end = markers[idx + 1].start() if idx + 1 < len(markers) else len(text)
            blocks.append(text[start:end])
        return preamble, blocks

    def _split_preamble(self, preamble: str) -> List[str]:
        if len(preamble) <= self.chunk_size:
            return [preamble]

        chunks: List[str] = []
        current = ""
        for para in preamble.split("\n\n"):
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) <= self.chunk_size or not current:
                current = candidate
            else:
                chunks.append(current)
                current = para
        if current:
            chunks.append(current)
        return chunks

    def send(self, prompt: str) -> str:
        """
        Sends prompt to Codex with safe chunking and stable formatting.
        Avoids invalid_request_error and ensures compatibility with chat models.
        """
        if self.mock_response is not None:
            return self.mock_response

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an autonomous code-generation and refactoring agent "
                    "inside a Meta-Agent pipeline. Follow instructions precisely, "
                    "output only code or patches when required."
                ),
            }
        ]

        for chunk in self._chunk_prompt(prompt):
            messages.append({"role": "user", "content": chunk})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=4096,
                temperature=self.temperature,
                timeout=self.request_timeout_seconds,
            )

            return response.choices[0].message.content

        except Exception as e:
            return f"[ERROR] CodexClient failed: {e!s}"
