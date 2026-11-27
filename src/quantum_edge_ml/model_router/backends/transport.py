"""Unified LLM transport abstraction and AgyProxyTransport adapter."""

from __future__ import annotations

import abc
import asyncio
import base64
import inspect
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

import httpx

from quantum_edge_core.contracts.optimization import (
    RequestStatus,
    ThinkingLevel,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution.storage_policy import get_ram_tmp_dir

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


DEFAULT_AGY_BIN = os.environ.get("AGY_BIN", "/home/layman/.local/bin/agy")
AGY_SESSION_RESUME_FLAG = "--resume"


class AgyDirectCliTransport(LLMTransport):
    """Direct subprocess adapter invoking `/home/layman/.local/bin/agy` without HTTP proxy.

    Runs `agy` headlessly inside a RAM-backed temporary workspace with
    `--sandbox --disable-slash-commands --input-format text --output-format json`,
    parses native 5-field usage counters directly into UsageLedger, and reaps
    subprocesses cleanly on timeout (with NULL native token fields + is_censored=True).
    """

    def __init__(
        self,
        ledger: UsageLedger,
        *,
        agy_bin: str = DEFAULT_AGY_BIN,
        workdir: Path | str | None = None,
        runner_override: Callable[..., Any] | None = None,
        reuse_session: bool = False,
        record_to_ledger: bool = True,
        pass_json_schema: bool = False,
    ) -> None:
        super().__init__(ledger)
        self.agy_bin = agy_bin
        self.workdir = Path(workdir) if workdir is not None else get_ram_tmp_dir("agy_stateless_ws")
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.runner_override = runner_override
        self.reuse_session = bool(reuse_session)
        self.record_to_ledger = bool(record_to_ledger)
        self.pass_json_schema = bool(pass_json_schema)
        self._session_ids_by_decision: dict[str, str] = {}

    @staticmethod
    def _format_stdin_prompt(req: LLMRequest) -> str:
        user_text = req.combined_user_text
        if req.system_instruction.strip():
            return f"[SYSTEM]\n{req.system_instruction}\n\n[USER]\n{user_text}"
        return user_text

    def build_command(
        self,
        *,
        agy_alias: str,
        timeout_sec: float,
        resume_session_id: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> list[str]:
        timeout_arg = f"{max(int(timeout_sec), 10)}s"
        cmd: list[str] = [
            self.agy_bin,
            "--model",
            agy_alias,
            "--sandbox",
            "--disable-slash-commands",
            "--input-format",
            "text",
            "--output-format",
            "json",
            "--print-timeout",
            timeout_arg,
        ]
        if resume_session_id:
            cmd.extend([AGY_SESSION_RESUME_FLAG, resume_session_id])
        if self.pass_json_schema and response_schema is not None:
            cmd.append(f"--json-schema={json.dumps(response_schema, separators=(',', ':'))}")
        return cmd

    async def _invoke_subprocess(self, cmd: list[str], prompt_text: str, timeout_sec: float) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workdir),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=prompt_text.encode("utf-8")),
                timeout=float(timeout_sec) + 10.0,
            )
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise TimeoutError(f"agy CLI timed out after {timeout_sec}s") from exc

        stdout_str = stdout_b.decode("utf-8", errors="replace")
        stderr_str = stderr_b.decode("utf-8", errors="replace")
        if proc.returncode != 0 and not stdout_str.strip():
            raise RuntimeError(
                f"agy exited with code {proc.returncode}: {stderr_str.strip()[:400]}"
            )
        try:
            payload = json.loads(stdout_str)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Failed to decode agy JSON output: {exc}; stdout={stdout_str[:300]!r}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
            err_detail = payload.get("error") if isinstance(payload, dict) else payload
            raise RuntimeError(f"agy returned non-SUCCESS status: {err_detail}")
        return payload

    async def generate(self, req: LLMRequest) -> LLMResponse:
        rec_id = f"agydir-{uuid.uuid4().hex[:14]}"
        agy_alias = normalize_agy_model_alias(req.model, req.thinking_level)
        effective_level = ThinkingLevel(agy_alias.rsplit("-", 1)[-1])
        prompt_text = self._format_stdin_prompt(req)
        input_chars = len(prompt_text)
        resume_id = (
            self._session_ids_by_decision.get(req.decision_id)
            if self.reuse_session
            else None
        )
        cmd = self.build_command(
            agy_alias=agy_alias,
            timeout_sec=req.timeout_sec,
            resume_session_id=resume_id,
            response_schema=req.response_schema,
        )
        t0 = time.perf_counter()
        try:
            if self.runner_override is not None:
                res = self.runner_override(
                    prompt=prompt_text,
                    agy_model=agy_alias,
                    cmd=cmd,
                    timeout_sec=req.timeout_sec,
                    workdir=self.workdir,
                )
                payload = await res if inspect.isawaitable(res) else res
                if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
                    err_detail = payload.get("error") if isinstance(payload, dict) else payload
                    raise RuntimeError(f"agy returned non-SUCCESS status: {err_detail}")
            else:
                payload = await self._invoke_subprocess(cmd, prompt_text, req.timeout_sec)

            text = str(payload.get("response") or "")
            u = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            in_tok = int(u["input_tokens"]) if u.get("input_tokens") is not None else None
            out_tok = int(u["output_tokens"]) if u.get("output_tokens") is not None else None
            think_tok = int(u["thinking_tokens"]) if u.get("thinking_tokens") is not None else None
            cache_tok = int(u["cache_read_tokens"]) if u.get("cache_read_tokens") is not None else None
            tot_tok = int(u["total_tokens"]) if u.get("total_tokens") is not None else None
            num_turns = int(payload.get("num_turns") or 1)
            conv_id = payload.get("conversation_id") or payload.get("session_id")
            if self.reuse_session and isinstance(conv_id, str) and conv_id.strip():
                self._session_ids_by_decision[req.decision_id] = conv_id.strip()

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
                provider_input_tokens=in_tok,
                cache_read_tokens=cache_tok,
                output_tokens=out_tok,
                thinking_tokens=think_tok,
                total_tokens=tot_tok,
                input_chars=input_chars,
                output_chars=len(text),
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                timeout_ms=req.timeout_sec * 1000.0,
                is_censored=False,
                num_turns=num_turns,
                prefix_hash=req.prefix_hash,
                context_hash=req.context_hash,
                snapshot_id=req.snapshot_id,
                interaction_id=str(conv_id) if conv_id else None,
            )
            if self.record_to_ledger:
                self.ledger.record_usage(usage)
            return LLMResponse(
                text=text,
                usage=usage,
                interaction_id=str(conv_id) if conv_id else None,
            )
        except (TimeoutError, asyncio.TimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException) as exc:
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
        self._session_ids_by_decision.clear()


__all__ = [
    "AGY_SESSION_RESUME_FLAG",
    "AgyDirectCliTransport",
    "AgyProxyTransport",
    "DEFAULT_AGY_BIN",
    "GEMINI4_IMAGE_TOKENS",
    "ImageAttachment",
    "LLMRequest",
    "LLMResponse",
    "LLMTransport",
    "normalize_agy_model_alias",
]
