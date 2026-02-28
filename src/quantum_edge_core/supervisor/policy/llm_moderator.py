"""Optional LLM moderation for policy adjustments."""

from __future__ import annotations

import json
import re
from typing import Dict, Any

from supervisor.llm.chat_client import ChatCompletionsClient

ALLOWED_KEYS = {
    "allow_trading",
    "mode",
    "size_multiplier",
    "cooldown_sec",
    "spread_max_bps",
    "max_daily_loss",
    "reason",
}


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that LLMs often add."""
    stripped = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", stripped, re.DOTALL)
    return m.group(1).strip() if m else stripped


class LlmModerator:
    def __init__(
        self,
        api_url: str,
        api_key_env: str,
        model: str,
        timeout_sec: float,
        temperature: float = 0.1,
    ) -> None:
        self.client = ChatCompletionsClient(api_url, api_key_env)
        self.model = model
        self.timeout_sec = timeout_sec
        self.temperature = temperature

    def suggest(
        self, signals: Dict[str, Any], base_policy: Dict[str, Any]
    ) -> Dict[str, Any]:
        prompt = (
            "Return JSON only. Allowed keys: allow_trading, mode, size_multiplier, cooldown_sec, "
            "spread_max_bps, max_daily_loss, reason. Do not include extra keys. "
            "If no changes needed, return an empty JSON object {}."
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {"signals": signals, "base_policy": base_policy}, ensure_ascii=False
                ),
            },
        ]
        response = self.client.complete(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            timeout_seconds=self.timeout_sec,
        )
        try:
            cleaned = _strip_markdown_fences(response)
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("LLM response must be a JSON object")
        for key in data:
            if key not in ALLOWED_KEYS:
                raise ValueError(f"LLM response includes invalid key: {key}")
        return data
