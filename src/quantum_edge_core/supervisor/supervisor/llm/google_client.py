"""Google GenAI Client for concise risk assessment queries.

Migrated from deprecated ``google.generativeai`` to the official
``google-genai`` SDK (>= 1.x).  API key resolution order:
  1. ``GOOGLE_API_KEY`` environment variable
  2. ``config/config.yaml`` → key ``google_api_key``
  3. Raise ``ValueError``
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[5]  # …/QuantumEdge
_CONFIG_YAML = _PROJECT_ROOT / "config" / "config.yaml"


def _resolve_api_key(env_var: str = "GOOGLE_API_KEY") -> str:
    """Return the Google API key using a secure fallback chain.

    1. Environment variable  ``env_var``  (preferred).
    2. ``config/config.yaml`` field ``google_api_key``.
    3. ``ValueError`` – no key found anywhere.
    """
    # --- 1. Environment variable -------------------------------------------
    key = os.environ.get(env_var)
    if key:
        logger.debug("Google API key loaded from env var '%s'.", env_var)
        return key

    # --- 2. config/config.yaml ---------------------------------------------
    if _CONFIG_YAML.is_file():
        try:
            with open(_CONFIG_YAML, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            key = cfg.get("google_api_key")
            if key:
                logger.debug(
                    "Google API key loaded from %s.",
                    _CONFIG_YAML,
                )
                return str(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read %s: %s", _CONFIG_YAML, exc)

    # --- 3. Fail -----------------------------------------------------------
    raise ValueError(
        f"Google API key not found. Set the '{env_var}' environment variable "
        f"or add 'google_api_key' to {_CONFIG_YAML}."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GoogleClient:
    """Client for interacting with Google GenAI (Gemini).

    Uses the new ``google-genai`` SDK and ``client.models.generate_content``
    calling convention.
    """

    def __init__(
        self,
        api_key_env: str = "GOOGLE_API_KEY",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.api_key_env = api_key_env
        self.logger = logger or logging.getLogger(__name__)
        self.api_key: str = _resolve_api_key(api_key_env)
        self.client: genai.Client = genai.Client(api_key=self.api_key)
        self.logger.info("GoogleClient initialised (genai SDK v2).")

    # ---- async wrapper ----------------------------------------------------

    async def generate_content_async(
        self,
        prompt: str,
        model_name: str = "gemini-2.5-pro",
    ) -> Optional[str]:
        """Asynchronously generate content via Google GenAI.

        Offloads the synchronous SDK call to a thread so the event-loop
        stays non-blocking.
        """

        def _call() -> Optional[str]:
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response.text
            except Exception as exc:
                self.logger.error("Google GenAI async request failed: %s", exc)
                return None

        return await asyncio.to_thread(_call)

    # ---- sync ChatCompletions-like wrapper --------------------------------

    def complete(
        self,
        model: str,
        messages: List[Mapping[str, str]],
        temperature: float,
        timeout_seconds: float,
        response_schema: Optional[Any] = None,
    ) -> Any:
        """Synchronous generation (ChatCompletionsClient-compatible API)."""
        # Convert chat messages → single prompt string
        prompt_parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")

        full_prompt = "\n".join(prompt_parts)

        try:
            config_kwargs = {"temperature": temperature}
            if response_schema is not None:
                config_kwargs["response_mime_type"] = "application/json"
                config_kwargs["response_schema"] = response_schema
                
            config = types.GenerateContentConfig(**config_kwargs)
            response = self.client.models.generate_content(
                model=model,
                contents=full_prompt,
                config=config,
            )
            # If a schema is provided, return the parsed object, otherwise text
            if response_schema is not None and hasattr(response, "parsed"):
                return response.parsed
            return response.text if response and response.text else ""
        except Exception as exc:
            self.logger.error("Google GenAI request failed: %s", exc)
            raise RuntimeError(f"Google GenAI request failed: {exc}") from exc

    # ---- prompt builder ---------------------------------------------------

    def generate_risk_query(self, context: Dict[str, Any]) -> str:
        """Build a compressed prompt for risk assessment.

        Returns a string like:
        ``SYS:HFT_SUPERVISOR. MODE:SCALP. PNL:-1.2%. DD:0.5%.
          VOL:HIGH. SPREAD:25bps.
          Q: RISK_ASSESSMENT? OUTPUT: JSON {verdict, reason}``
        """
        pnl = context.get("pnl_pct", 0.0)
        dd = context.get("drawdown_pct", 0.0)
        vol = context.get("volatility", "MEDIUM")
        spread = context.get("spread_bps", 0)
        mode = context.get("mode", "SCALP").upper()

        pnl_str = f"{pnl:.1f}%" if isinstance(pnl, (int, float)) else str(pnl)
        dd_str = f"{dd:.1f}%" if isinstance(dd, (int, float)) else str(dd)
        spread_str = f"{spread}bps"

        return (
            f"SYS:HFT_SUPERVISOR. MODE:{mode}. "
            f"PNL:{pnl_str}. DD:{dd_str}. VOL:{vol}. SPREAD:{spread_str}. "
            "Q: RISK_ASSESSMENT? OUTPUT: JSON "
            "{verdict: 'CONTINUE'|'REDUCE'|'HALT', reason: '...'}"
        )
