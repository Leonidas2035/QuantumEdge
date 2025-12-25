import os
from typing import List, Optional

from openai import OpenAI


class CodexClient:
    def __init__(self, mode: Optional[str] = None):
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
        self.model = "gpt-4.1"

        # max chunk size to avoid 400 errors
        self.chunk_size = 12000

    def _chunk_prompt(self, text: str) -> List[str]:
        """Split large prompts into smaller chunks."""
        return [text[i:i + self.chunk_size] for i in range(0, len(text), self.chunk_size)]

    def send(self, prompt: str) -> str:
        """
        Sends prompt to Codex with safe chunking and stable formatting.
        Avoids invalid_request_error and ensures compatibility with chat models.
        """

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
                temperature=0,
            )

            return response.choices[0].message.content

        except Exception as e:
            return f"[ERROR] CodexClient failed: {str(e)}"
