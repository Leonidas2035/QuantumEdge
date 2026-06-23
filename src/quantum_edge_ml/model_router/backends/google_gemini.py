from __future__ import annotations

import os
import time
import json
import logging
from typing import Optional
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger("GoogleGeminiBackend")

class DecisionV1Schema(BaseModel):
    v: int = Field(1, description="Version of schema, always 1")
    s: str = Field(description="Verdict/Side: BUY, SELL, HOLD, REDUCE, or CLOSE")
    c: float = Field(description="Confidence value between 0.0 and 1.0")
    sl: Optional[float] = Field(None, description="Stop loss price or null")
    tp: Optional[float] = Field(None, description="Take profit price or null")
    r: str = Field(description="Reason for decision, maximum 60 characters, no newlines")
    rk: str = Field(description="Risk categorization: LOW, MED, HIGH, or CRIT")


class GoogleGeminiBackend:
    def __init__(self, transport=None) -> None:
        self.api_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
        self.model = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")
        self.max_tokens = int(os.environ.get("GOOGLE_MAX_TOKENS", "128"))
        self.name = "google_gemini"
        
        # Initialize Google GenAI client with key fallback for tests
        client_key = self.api_key or "MOCK_KEY"
        self.client = genai.Client(api_key=client_key)

    async def generate(
        self, prompt: str, *, system_prompt: str, timeout_s: float
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=DecisionV1Schema,
            temperature=0.0,
            max_output_tokens=self.max_tokens,
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            raw_text = response.text
            if not raw_text:
                raise RuntimeError("gemini_empty_response")

            # Write decision to QuestDB llm_decisions table
            try:
                from quantum_edge_core.market_data.tsdb.ilp_writer import get_ilp_writer
                dec_obj = json.loads(raw_text)
                get_ilp_writer().write_row(
                    "llm_decisions",
                    symbols={
                        "bot_id": "hermes",
                        "verdict": str(dec_obj.get("s", "HOLD"))
                    },
                    columns={
                        "reason": str(dec_obj.get("r", "approved"))[:255],
                        "raw_prompt": str(prompt)[:4000],
                        "raw_response": str(raw_text)[:4000]
                    },
                    ts=time.time()
                )
            except Exception as db_err:
                logger.warning(f"Failed to write LLM decision to QuestDB: {db_err}")

            return raw_text.strip()
        except Exception as e:
            raise RuntimeError(f"gemini_error: {e}")

