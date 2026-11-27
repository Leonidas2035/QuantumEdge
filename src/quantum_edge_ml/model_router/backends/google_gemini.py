from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from quantum_edge_core.contracts.optimization import (
    RequestStatus,
    ThinkingLevel,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_ml.model_router.backends.transport import (
    ImageAttachment,
    LLMRequest,
    LLMResponse,
    LLMTransport,
)

if TYPE_CHECKING:
    from quantum_edge_ml.model_router.usage_ledger import UsageLedger

logger = logging.getLogger("GoogleGeminiBackend")

OPENAI_COMPAT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

# Official thinking_level caps per Gemini 4 model family
MODEL_THINKING_CAPS: dict[str, set[ThinkingLevel]] = {
    "gemini-4-carbon": {ThinkingLevel.LOW, ThinkingLevel.MEDIUM, ThinkingLevel.HIGH},
    "gemini-4-argon": {ThinkingLevel.LOW, ThinkingLevel.MEDIUM, ThinkingLevel.HIGH},
    "gemini-4-neon": {
        ThinkingLevel.LOW,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.HIGH,
        ThinkingLevel.XHIGH,
    },
    "gemini-4-hydrogen": {
        ThinkingLevel.LOW,
        ThinkingLevel.MEDIUM,
        ThinkingLevel.HIGH,
        ThinkingLevel.XHIGH,
    },
}


def clamp_thinking_level(model: str, requested: ThinkingLevel) -> ThinkingLevel:
    """Clamp requested thinking_level to the target model's supported set."""
    allowed = MODEL_THINKING_CAPS.get(
        model, {ThinkingLevel.LOW, ThinkingLevel.MEDIUM, ThinkingLevel.HIGH}
    )
    if requested in allowed:
        return requested
    if requested == ThinkingLevel.XHIGH and ThinkingLevel.HIGH in allowed:
        return ThinkingLevel.HIGH
    return ThinkingLevel.MEDIUM


def _load_dotenv_if_needed() -> None:
    """Load GEMINI_API_KEY / GOOGLE_API_KEY from repo .env without overriding or logging."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return
    try:
        from dotenv import load_dotenv

        env_path = Path(__file__).resolve().parents[4] / ".env"
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        pass


class DecisionV1Schema(BaseModel):
    v: int = Field(1, description="Version of schema, always 1")
    s: str = Field(description="Verdict/Side: BUY, SELL, HOLD, REDUCE, or CLOSE")
    c: float = Field(description="Confidence value between 0.0 and 1.0")
    sl: Optional[float] = Field(None, description="Stop loss price or null")
    tp: Optional[float] = Field(None, description="Take profit price or null")
    r: str = Field(description="Reason for decision, maximum 60 characters, no newlines")
    rk: str = Field(description="Risk categorization: LOW, MED, HIGH, or CRIT")


class GoogleGeminiBackend:
    """Legacy model_router backend preserved for full backward compatibility."""

    def __init__(self, transport=None) -> None:
        self.api_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
        self.model = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")
        self.max_tokens = int(os.environ.get("GOOGLE_MAX_TOKENS", "128"))
        self.name = "google_gemini"

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

            try:
                from quantum_edge_core.market_data.tsdb.ilp_writer import get_ilp_writer

                dec_obj = json.loads(raw_text)
                get_ilp_writer().write_row(
                    "llm_decisions",
                    symbols={
                        "bot_id": "hermes",
                        "verdict": str(dec_obj.get("s", "HOLD")),
                    },
                    columns={
                        "reason": str(dec_obj.get("r", "approved"))[:255],
                        "raw_prompt": str(prompt)[:4000],
                        "raw_response": str(raw_text)[:4000],
                    },
                    ts=time.time(),
                )
            except Exception as db_err:
                logger.warning(f"Failed to write LLM decision to QuestDB: {db_err}")

            return raw_text.strip()
        except Exception as e:
            raise RuntimeError(f"gemini_error: {e}")


class GeminiDirectClient(LLMTransport):
    """Direct Gemini 4 transport (google-genai>=3.6.1): Interactions API + v1beta/openai + HTTP/2."""

    def __init__(
        self,
        ledger: UsageLedger,
        *,
        api_key: str | None = None,
        mode: Literal["interactions", "openai_compat"] = "interactions",
        api_version: str = "v1beta",
        sdk_client: Any | None = None,
        http2_async: httpx.AsyncClient | None = None,
        http2_sync: httpx.Client | None = None,
    ) -> None:
        super().__init__(ledger)
        if not api_key:
            _load_dotenv_if_needed()
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not self.api_key and sdk_client is None:
            raise ValueError("Missing GEMINI_API_KEY / GOOGLE_API_KEY in environment or .env.")
        self.mode = mode
        self._owns_http2_async = http2_async is None
        self._owns_http2_sync = http2_sync is None
        self._http2_async = http2_async or httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(180.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._http2_sync = http2_sync or httpx.Client(
            http2=True,
            timeout=httpx.Timeout(180.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        if sdk_client is not None:
            self._sdk = sdk_client
        else:
            self._sdk = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    api_version=api_version,
                    httpx_client=self._http2_sync,
                    httpx_async_client=self._http2_async,
                ),
            )

    async def generate(self, req: LLMRequest) -> LLMResponse:
        if req.max_output_tokens < 8_192:
            raise ValueError(
                f"Gemini 4 max_output_tokens must be >= 8192 (covers thinking+output), got {req.max_output_tokens}"
            )
        level = clamp_thinking_level(req.model, req.thinking_level)
        if self.mode == "openai_compat":
            return await self._generate_openai_compat(req, level)
        return await self._generate_interactions(req, level)

    async def _generate_interactions(self, req: LLMRequest, level: ThinkingLevel) -> LLMResponse:
        rec_id = f"gem-{uuid.uuid4().hex[:16]}"
        t0 = time.perf_counter()
        user_text = req.combined_user_text
        input_chars = len(req.system_instruction) + len(user_text)

        # CRITICAL FOR IMPLICIT CACHING: P1 slow prefix first, then P2 tail, and images strictly LAST!
        input_parts: list[dict[str, Any]] = []
        if req.p1_slow_prefix:
            input_parts.append({"type": "text", "text": req.p1_slow_prefix})
        if req.p2_dynamic_tail:
            input_parts.append({"type": "text", "text": req.p2_dynamic_tail})
        for img in req.images:
            input_parts.append(
                {
                    "type": "image",
                    "data": img.to_b64(),
                    "mime_type": img.mime_type,
                    "resolution": img.resolution,
                }
            )

        kwargs: dict[str, Any] = {
            "model": req.model,
            "system_instruction": req.system_instruction,
            "input": (
                input_parts
                if (req.images or (req.p1_slow_prefix and req.p2_dynamic_tail))
                else user_text
            ),
            "generation_config": {
                "thinking_level": level.value,
                "max_output_tokens": req.max_output_tokens,
            },
            "store": req.store,
        }
        if req.previous_interaction_id:
            kwargs["previous_interaction_id"] = req.previous_interaction_id
        if req.response_schema is not None:
            kwargs["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": req.response_schema,
            }

        try:
            interaction = await asyncio.wait_for(
                self._sdk.aio.interactions.create(**kwargs),
                timeout=req.timeout_sec,
            )
            text = str(getattr(interaction, "output_text", None) or "")
            u = getattr(interaction, "usage", None)
            p_in = getattr(u, "total_input_tokens", None) if u else None
            p_cache = getattr(u, "total_cached_tokens", None) if u else None
            p_out = getattr(u, "total_output_tokens", None) if u else None
            p_think = getattr(u, "total_thought_tokens", None) if u else None
            p_tool = getattr(u, "total_tool_use_tokens", None) if u else None
            p_total = getattr(u, "total_tokens", None) if u else None
            status_raw = str(getattr(interaction, "status", "completed"))
            has_usage = u is not None

            usage = UsageRecordV1(
                record_id=rec_id,
                decision_id=req.decision_id,
                stage=req.stage,
                transport=TransportChannel.GEMINI_INTERACTIONS,
                model_provider_id=req.model,
                thinking_level=level,
                request_status=(
                    RequestStatus.OK if status_raw == "completed" else RequestStatus.ERROR
                ),
                error_code=(
                    None if status_raw == "completed" else f"interaction_status:{status_raw}"
                ),
                retry_of=req.retry_of,
                provider_input_tokens=int(p_in) if p_in is not None else None,
                cache_read_tokens=(
                    int(p_cache) if p_cache is not None else (0 if has_usage else None)
                ),
                output_tokens=int(p_out) if p_out is not None else None,
                thinking_tokens=(
                    int(p_think) if p_think is not None else (0 if has_usage else None)
                ),
                tool_use_tokens=(
                    int(p_tool) if p_tool is not None else (0 if has_usage else None)
                ),
                total_tokens=int(p_total) if p_total is not None else None,
                input_chars=input_chars,
                output_chars=len(text),
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                timeout_ms=req.timeout_sec * 1000.0,
                is_censored=False,
                prefix_hash=req.prefix_hash,
                context_hash=req.context_hash,
                snapshot_id=req.snapshot_id,
                interaction_id=getattr(interaction, "id", None),
            )
            self.ledger.record_usage(usage)
            return LLMResponse(
                text=text,
                usage=usage,
                interaction_id=getattr(interaction, "id", None),
                raw_status=status_raw,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            self._log_failure(
                req, rec_id, level, t0, input_chars, RequestStatus.TIMEOUT, type(exc).__name__, True
            )
            raise
        except Exception as exc:
            err_str = str(exc)
            st = (
                RequestStatus.QUOTA_REACHED
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                else RequestStatus.ERROR
            )
            self._log_failure(req, rec_id, level, t0, input_chars, st, err_str[:180], False)
            raise

    async def _generate_openai_compat(self, req: LLMRequest, level: ThinkingLevel) -> LLMResponse:
        rec_id = f"oai-{uuid.uuid4().hex[:16]}"
        t0 = time.perf_counter()
        user_text = req.combined_user_text
        input_chars = len(req.system_instruction) + len(user_text)

        # Text P1+P2 precedes images to preserve KV prefix stability
        user_payload: Any
        if req.images:
            blocks: list[dict[str, Any]] = []
            if req.p1_slow_prefix:
                blocks.append({"type": "text", "text": req.p1_slow_prefix})
            if req.p2_dynamic_tail:
                blocks.append({"type": "text", "text": req.p2_dynamic_tail})
            if not blocks:
                blocks.append({"type": "text", "text": user_text})
            blocks.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.mime_type};base64,{img.to_b64()}"},
                }
                for img in req.images
            )
            user_payload = blocks
        else:
            user_payload = user_text

        body: dict[str, Any] = {
            "model": req.model,
            "messages": [
                {"role": "system", "content": req.system_instruction},
                {"role": "user", "content": user_payload},
            ],
            "reasoning_effort": level.value,
            "max_completion_tokens": req.max_output_tokens,
        }
        if req.response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "TradePayload",
                    "schema": req.response_schema,
                    "strict": True,
                },
            }

        try:
            resp = await self._http2_async.post(
                f"{OPENAI_COMPAT_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=req.timeout_sec,
            )
            resp.raise_for_status()
            data = resp.json()
            text = str(data["choices"][0]["message"].get("content") or "")
            u = data.get("usage") or {}
            p_det = u.get("prompt_tokens_details") or {}
            c_det = u.get("completion_tokens_details") or {}
            think_tok = int(c_det.get("reasoning_tokens") or 0) if u else None
            raw_comp = u.get("completion_tokens")
            out_tok = (
                max(0, int(raw_comp) - int(think_tok or 0))
                if raw_comp is not None
                else None
            )

            usage = UsageRecordV1(
                record_id=rec_id,
                decision_id=req.decision_id,
                stage=req.stage,
                transport=TransportChannel.GEMINI_OPENAI_COMPAT,
                model_provider_id=req.model,
                thinking_level=level,
                request_status=RequestStatus.OK,
                retry_of=req.retry_of,
                provider_input_tokens=u.get("prompt_tokens"),
                cache_read_tokens=int(p_det.get("cached_tokens") or 0) if u else None,
                output_tokens=out_tok,
                thinking_tokens=think_tok,
                total_tokens=u.get("total_tokens"),
                input_chars=input_chars,
                output_chars=len(text),
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                timeout_ms=req.timeout_sec * 1000.0,
                is_censored=False,
                prefix_hash=req.prefix_hash,
                context_hash=req.context_hash,
                snapshot_id=req.snapshot_id,
            )
            self.ledger.record_usage(usage)
            return LLMResponse(text=text, usage=usage)
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            self._log_failure(
                req, rec_id, level, t0, input_chars, RequestStatus.TIMEOUT, type(exc).__name__, True
            )
            raise
        except Exception as exc:
            err_str = str(exc)
            st = (
                RequestStatus.QUOTA_REACHED
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                else RequestStatus.ERROR
            )
            self._log_failure(req, rec_id, level, t0, input_chars, st, err_str[:180], False)
            raise

    def _log_failure(
        self,
        req: LLMRequest,
        rec_id: str,
        level: ThinkingLevel,
        t0: float,
        input_chars: int,
        status: RequestStatus,
        error_code: str,
        is_censored: bool,
    ) -> None:
        self.ledger.record_usage(
            UsageRecordV1(
                record_id=rec_id,
                decision_id=req.decision_id,
                stage=req.stage,
                transport=(
                    TransportChannel.GEMINI_OPENAI_COMPAT
                    if self.mode == "openai_compat"
                    else TransportChannel.GEMINI_INTERACTIONS
                ),
                model_provider_id=req.model,
                thinking_level=level,
                request_status=status,
                error_code=error_code,
                retry_of=req.retry_of,
                input_chars=input_chars,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                timeout_ms=req.timeout_sec * 1000.0,
                is_censored=is_censored,
                prefix_hash=req.prefix_hash,
                context_hash=req.context_hash,
                snapshot_id=req.snapshot_id,
            )
        )

    async def count_tokens(
        self,
        model: str,
        text_parts: list[str],
        images: list[ImageAttachment] | None = None,
    ) -> dict[str, int]:
        """Exact text token counting via SDK + Gemini 4 per-item image resolution budget."""
        text_contents = [types.Part.from_text(text=t) for t in text_parts if t]
        text_toks = 0
        if text_contents:
            res = await self._sdk.aio.models.count_tokens(model=model, contents=text_contents)
            text_toks = int(res.total_tokens)
        img_toks = sum(img.token_budget for img in (images or []))
        return {
            "text_tokens": text_toks,
            "image_tokens_media_res": img_toks,
            "total_tokens": text_toks + img_toks,
        }

    async def close(self) -> None:
        if self._owns_http2_async:
            await self._http2_async.aclose()
        if self._owns_http2_sync:
            self._http2_sync.close()


__all__ = [
    "DecisionV1Schema",
    "GeminiDirectClient",
    "GoogleGeminiBackend",
    "MODEL_THINKING_CAPS",
    "OPENAI_COMPAT_BASE_URL",
    "clamp_thinking_level",
]
