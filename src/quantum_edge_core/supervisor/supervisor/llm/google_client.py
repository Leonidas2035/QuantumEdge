"""Google AI Client for concise risk assessment queries."""

import logging
import os
import asyncio
from typing import Dict, Any, Optional, List, Mapping
import google.generativeai as genai

logger = logging.getLogger(__name__)


class GoogleClient:
    """
    Client for interacting with Google AI (Gemini) with concise prompts.
    """

    def __init__(
        self,
        api_key_env: str = "GOOGLE_API_KEY",
        logger: Optional[logging.Logger] = None,
    ):
        self.api_key_env = api_key_env
        self.logger = logger or logging.getLogger(__name__)
        self.api_key = os.getenv(api_key_env)
        if self.api_key:
            genai.configure(api_key=self.api_key)
        else:
            self.logger.warning(f"Google API Key ({api_key_env}) not set.")

    async def generate_content_async(
        self, prompt: str, model_name: str = "gemini-2.0-flash"
    ) -> Optional[str]:
        """
        Asynchronously generates content using Google AI.
        Non-blocking wrapper around synchronous API.
        """
        if not self.api_key:
            self.logger.error("API key not configured.")
            return None

        def _call():
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                return response.text
            except Exception as e:
                self.logger.error(f"Google AI request failed: {e}")
                return None

        return await asyncio.to_thread(_call)

    def complete(
        self,
        model: str,
        messages: List[Mapping[str, str]],
        temperature: float,
        timeout_seconds: float,
    ) -> str:
        """Synchronous wrapper for Google AI (ChatCompletionsClient compatible)."""
        if not self.api_key:
            raise RuntimeError(f"Google API Key ({self.api_key_env}) not configured.")

        # Convert chat messages to a single prompt string
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")

        full_prompt = "\n".join(prompt_parts)

        try:
            gen_model = genai.GenerativeModel(model)
            config = genai.types.GenerationConfig(temperature=temperature)
            response = gen_model.generate_content(full_prompt, generation_config=config)
            return response.text if response and response.text else ""
        except Exception as exc:
            self.logger.error(f"Google AI request failed: {exc}")
            raise RuntimeError(f"Google AI request failed: {exc}") from exc

    def generate_risk_query(self, context: Dict[str, Any]) -> str:
        """
        Generates a compressed prompt for risk assessment.
        Format: "SYS:HFT_SUPERVISOR. MODE:SCALP. PNL:-1.2%. DD:0.5%. VOL:HIGH. SPREAD:25bps. Q: RISK_ASSESSMENT? OUTPUT: JSON {verdict: 'CONTINUE'|'REDUCE'|'HALT', reason: '...'}"
        """
        # Extract metrics with defaults
        pnl = context.get("pnl_pct", 0.0)
        dd = context.get("drawdown_pct", 0.0)
        vol = context.get("volatility", "MEDIUM")
        spread = context.get("spread_bps", 0)
        mode = context.get("mode", "SCALP").upper()

        # Format values
        pnl_str = f"{pnl:.1f}%" if isinstance(pnl, (int, float)) else str(pnl)
        dd_str = f"{dd:.1f}%" if isinstance(dd, (int, float)) else str(dd)
        spread_str = f"{spread}bps"

        prompt = (
            f"SYS:HFT_SUPERVISOR. MODE:{mode}. "
            f"PNL:{pnl_str}. DD:{dd_str}. VOL:{vol}. SPREAD:{spread_str}. "
            "Q: RISK_ASSESSMENT? OUTPUT: JSON {verdict: 'CONTINUE'|'REDUCE'|'HALT', reason: '...'}"
        )
        return prompt
