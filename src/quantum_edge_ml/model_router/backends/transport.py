"""Unified LLM transport abstraction and AgyProxyTransport adapter."""

from __future__ import annotations

import abc
import base64
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx

from quantum_edge_core.contracts.optimization import (
    RequestStatus,
    ThinkingLevel,
    TransportChannel,
    UsageRecordV1,
)

if TYPE_CHECKING:
    from quantum_edge_ml.model_router.usage_ledger import UsageLedger

# Official Gemini 4 media_resolution token budget table
GEMINI4_IMAGE_TOKENS: dict[str, int] = {
    "low": 280,
    "medium": 560,
    "high": 1_120,
    "ultra_high": 2_240,
}


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """In-memory image attachment with explicit Gemini 4 media resolution."""

    data: bytes
    mime_type: Literal["image/webp", "image/png", "image/jpeg"] = "image/webp"
    resolution: Literal["low", "medium", "high", "ultra_high"] = "medium"

    def to_b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @property
    def token_budget(self) -> int:
        return GEMINI4_IMAGE_TOKENS[self.resolution]


@dataclass(slots=True)
class LLMRequest:
    """Cache-aware LLM request with separate P1 slow prefix, P2 tail, and trailing images."""

    decision_id: str
    stage: Literal["stage1_screen", "stage2_drill", "single_shot", "shadow_ab", "proxy_legacy"]
    model: str  # Canonical ID: e.g. "gemini-4-carbon" or "gemini-4-neon"
    system_instruction: str  # P0 byte-stable prefix
    p1_slow_prefix: str = ""  # P1 stable hourly layer + append-only ThesisMemory
    p2_dynamic_tail: str = ""  # P2 dynamic market tail
    images: list[ImageAttachment] = field(default_factory=list)  # ALWAYS after P1+P2!
    thinking_level: ThinkingLevel = ThinkingLevel.MEDIUM
    max_output_tokens: int = 16_384  # >= 8192 (covers thinking + output)
    response_schema: dict[str, Any] | None = None
    store: bool = False
    previous_interaction_id: str | None = None
    timeout_sec: float = 90.0
    prefix_hash: str | None = None
    context_hash: str | None = None
    snapshot_id: str | None = None
    retry_of: str | None = None

    @property
    def combined_user_text(self) -> str:
        if self.p1_slow_prefix and self.p2_dynamic_tail:
            return self.p1_slow_prefix + "\n\n" + self.p2_dynamic_tail
        return self.p1_slow_prefix or self.p2_dynamic_tail


@dataclass(slots=True)
class LLMResponse:
    """Normalized LLM response paired with its recorded UsageRecordV1."""

    text: str
    usage: UsageRecordV1
    interaction_id: str | None = None
    raw_status: str = "completed"


class LLMTransport(abc.ABC):
    """Single transport contract for direct Gemini 4 SDK and A/B proxy adapters."""

    def __init__(self, ledger: UsageLedger) -> None:
        self.ledger = ledger

    @abc.abstractmethod
    async def generate(self, req: LLMRequest) -> LLMResponse:
        ...

    @abc.abstractmethod
    async def count_tokens(
        self,
        model: str,
        text_parts: list[str],
        images: list[ImageAttachment] | None = None,
    ) -> dict[str, int]:
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        ...


def normalize_agy_model_alias(model: str, level: ThinkingLevel) -> str:
    """Strip any existing thinking-level suffix, clamp per model, and return canonical alias."""
    from quantum_edge_ml.model_router.backends.google_gemini import clamp_thinking_level

    base = model.strip()
    for suffix in ("-xhigh", "-high", "-medium", "-low"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    clamped = clamp_thinking_level(base, level)
    return f"{base}-{clamped.value}"


class AgyProxyTransport(LLMTransport):
    """Adapter over local tools/agy_openai_proxy.py for paired A/B benchmarking.

    Note on UsageLedger isolation: ``AgyProxyTransport`` records full
    ``UsageRecordV1`` telemetry (including ``decision_id``, ``stage``,
    ``prefix_hash``, ``context_hash``, and ``snapshot_id``) when
    ``record_to_ledger=True``. Do not pass the same ``--ledger-db`` to
    ``tools/agy_openai_proxy.py`` concurrently unless ``record_to_ledger=False``.
    """

    def __init__(
        self,
        ledger: UsageLedger,
        base_url: str = "http://127.0.0.1:8045/v1",
        http_client: httpx.AsyncClient | None = None,
        *,
        record_to_ledger: bool = True,
    ) -> None:
        super().__init__(ledger)
        self.base_url = base_url.rstrip("/")
        self.record_to_ledger = bool(record_to_ledger)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(200.0, connect=5.0))

    async def generate(self, req: LLMRequest) -> LLMResponse:
        rec_id = f"agy-{uuid.uuid4().hex[:16]}"
        agy_alias = normalize_agy_model_alias(req.model, req.thinking_level)
        effective_level = ThinkingLevel(agy_alias.rsplit("-", 1)[-1])
        t0 = time.perf_counter()
        user_text = req.combined_user_text
        input_chars = len(req.system_instruction) + len(user_text)
        payload = {
            "model": agy_alias,
            "messages": [
                {"role": "system", "content": req.system_instruction},
                {"role": "user", "content": user_text},
            ],
            "stream": False,
        }
        try:
            resp = await self._http.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=req.timeout_sec,
            )
            resp.raise_for_status()
            data = resp.json()
            text = str(data["choices"][0]["message"].get("content") or "")
            u = data.get("usage") or {}
            p_det = u.get("prompt_tokens_details") or {}
            c_det = u.get("completion_tokens_details") or {}
            think_tok = c_det.get("reasoning_tokens")
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
                transport=TransportChannel.AGY_CLI_PROXY,
                model_provider_id=req.model,
                agy_alias=agy_alias,
                thinking_level=effective_level,
                request_status=RequestStatus.OK,
                retry_of=req.retry_of,
                provider_input_tokens=u.get("prompt_tokens"),
                cache_read_tokens=p_det.get("cached_tokens"),
                output_tokens=out_tok,
                thinking_tokens=int(think_tok) if think_tok is not None else None,
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
            if self.record_to_ledger:
                self.ledger.record_usage(usage)
            return LLMResponse(text=text, usage=usage)
        except httpx.TimeoutException as exc:
            if self.record_to_ledger:
                self.ledger.record_usage(
                    UsageRecordV1(
                        record_id=rec_id,
                        decision_id=req.decision_id,
                        stage=req.stage,
                        transport=TransportChannel.AGY_CLI_PROXY,
                        model_provider_id=req.model,
                        agy_alias=agy_alias,
                        thinking_level=effective_level,
                        request_status=RequestStatus.TIMEOUT,
                        error_code=type(exc).__name__,
                        retry_of=req.retry_of,
                        input_chars=input_chars,
                        latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                        timeout_ms=req.timeout_sec * 1000.0,
                        is_censored=True,
                        prefix_hash=req.prefix_hash,
                        context_hash=req.context_hash,
                        snapshot_id=req.snapshot_id,
                    )
                )
            raise
        except Exception as exc:
            if self.record_to_ledger:
                self.ledger.record_usage(
                    UsageRecordV1(
                        record_id=rec_id,
                        decision_id=req.decision_id,
                        stage=req.stage,
                        transport=TransportChannel.AGY_CLI_PROXY,
                        model_provider_id=req.model,
                        agy_alias=agy_alias,
                        thinking_level=effective_level,
                        request_status=RequestStatus.ERROR,
                        error_code=str(exc)[:180],
                        retry_of=req.retry_of,
                        input_chars=input_chars,
                        latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                        timeout_ms=req.timeout_sec * 1000.0,
                        is_censored=False,
                        prefix_hash=req.prefix_hash,
                        context_hash=req.context_hash,
                        snapshot_id=req.snapshot_id,
                    )
                )
            raise

    async def count_tokens(
        self,
        model: str,
        text_parts: list[str],
        images: list[ImageAttachment] | None = None,
    ) -> dict[str, int]:
        raise NotImplementedError("Use GeminiDirectClient.count_tokens() for exact token counting.")

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()


__all__ = [
    "AgyProxyTransport",
    "GEMINI4_IMAGE_TOKENS",
    "ImageAttachment",
    "LLMRequest",
    "LLMResponse",
    "LLMTransport",
    "normalize_agy_model_alias",
]
