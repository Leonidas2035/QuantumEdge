#!/usr/bin/env python3
"""Paired A/B Transport Benchmark Harness (AgyProxy vs GeminiDirectClient Interactions vs OpenAI-compat).

Uses RAM-First storage (/dev/shm/quantumedge/ab_bench) for intermediate SQLite
ledgers before writing any final JSON report. Supports ``--mode dry-run``
(deterministic offline mocks for CI/pytest) and ``--mode live`` (explicit
operator benchmark).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from quantum_edge_core.contracts.optimization import (  # noqa: E402
    RequestStatus,
    ThinkingLevel,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution.storage_policy import get_ram_tmp_dir  # noqa: E402
from quantum_edge_ml.model_router.backends.google_gemini import GeminiDirectClient  # noqa: E402
from quantum_edge_ml.model_router.backends.transport import (  # noqa: E402
    AgyProxyTransport,
    ImageAttachment,
    LLMRequest,
    LLMResponse,
    LLMTransport,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger  # noqa: E402


class _MockAgyTransport(LLMTransport):
    """Deterministic mock simulating CLI overhead (~11.3k tokens) and 1 censored timeout."""

    async def generate(self, req: LLMRequest) -> LLMResponse:
        rec_id = f"mock-agy-{uuid.uuid4().hex[:12]}"
        if req.decision_id.endswith("-0"):
            self.ledger.record_usage(
                UsageRecordV1(
                    record_id=rec_id,
                    decision_id=req.decision_id,
                    stage=req.stage,
                    transport=TransportChannel.AGY_CLI_PROXY,
                    model_provider_id=req.model,
                    agy_alias=f"{req.model}-{req.thinking_level.value}",
                    thinking_level=req.thinking_level,
                    request_status=RequestStatus.TIMEOUT,
                    error_code="ReadTimeout",
                    input_chars=len(req.system_instruction) + len(req.combined_user_text),
                    latency_ms=req.timeout_sec * 1000.0,
                    timeout_ms=req.timeout_sec * 1000.0,
                    is_censored=True,
                )
            )
            raise httpx.ReadTimeout("Simulated agy 180s timeout")

        base_in = 4200 + 11281  # P1+P2 context + agy CLI system overhead
        usage = UsageRecordV1(
            record_id=rec_id,
            decision_id=req.decision_id,
            stage=req.stage,
            transport=TransportChannel.AGY_CLI_PROXY,
            model_provider_id=req.model,
            agy_alias=f"{req.model}-{req.thinking_level.value}",
            thinking_level=req.thinking_level,
            request_status=RequestStatus.OK,
            provider_input_tokens=base_in,
            cache_read_tokens=0,
            output_tokens=160,
            thinking_tokens=950,
            total_tokens=base_in + 160 + 950,
            input_chars=len(req.system_instruction) + len(req.combined_user_text),
            output_chars=120,
            latency_ms=4200.0,
            timeout_ms=req.timeout_sec * 1000.0,
            is_censored=False,
        )
        self.ledger.record_usage(usage)
        return LLMResponse(text='{"action":"HOLD"}', usage=usage)

    async def count_tokens(
        self, model: str, text_parts: list[str], images: list[ImageAttachment] | None = None
    ) -> dict[str, int]:
        return {"text_tokens": 0, "image_tokens_media_res": 0, "total_tokens": 0}

    async def close(self) -> None:
        return None


class _MockGeminiDirectTransport(LLMTransport):
    """Deterministic mock simulating direct Gemini 4 SDK (Interactions or OpenAI-compat)."""

    def __init__(self, ledger: UsageLedger, channel: TransportChannel) -> None:
        super().__init__(ledger)
        self.channel = channel
        self._calls = 0

    async def generate(self, req: LLMRequest) -> LLMResponse:
        self._calls += 1
        rec_id = f"mock-gem-{uuid.uuid4().hex[:12]}"
        p_in = 4200
        cached = 3100 if self._calls > 1 else 0
        out_tok = 155
        think_tok = 820
        usage = UsageRecordV1(
            record_id=rec_id,
            decision_id=req.decision_id,
            stage=req.stage,
            transport=self.channel,
            model_provider_id=req.model,
            thinking_level=req.thinking_level,
            request_status=RequestStatus.OK,
            provider_input_tokens=p_in,
            cache_read_tokens=cached,
            output_tokens=out_tok,
            thinking_tokens=think_tok,
            total_tokens=p_in + out_tok + think_tok,
            input_chars=len(req.system_instruction) + len(req.combined_user_text),
            output_chars=115,
            latency_ms=1350.0 if self._calls > 1 else 1950.0,
            timeout_ms=req.timeout_sec * 1000.0,
            is_censored=False,
            interaction_id=f"int-{rec_id}" if self.channel == TransportChannel.GEMINI_INTERACTIONS else None,
        )
        self.ledger.record_usage(usage)
        return LLMResponse(text='{"action":"HOLD"}', usage=usage, interaction_id=usage.interaction_id)

    async def count_tokens(
        self, model: str, text_parts: list[str], images: list[ImageAttachment] | None = None
    ) -> dict[str, int]:
        toks = sum(max(1, len(t) // 4) for t in text_parts)
        img_toks = sum(img.token_budget for img in (images or []))
        return {"text_tokens": toks, "image_tokens_media_res": img_toks, "total_tokens": toks + img_toks}

    async def close(self) -> None:
        return None


def load_benchmark_prompts(fixtures_path: Path | None, runs: int) -> list[str]:
    """Load context prompts from a JSON fixture or synthesize compact market prompts."""
    if fixtures_path and fixtures_path.is_file():
        raw = json.loads(fixtures_path.read_text(encoding="utf-8"))
        base = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))[:16000]
        return [f"FIXTURE_RUN_{i}\n{base}" for i in range(runs)]
    return [
        f"BTCUSDT_SNAPSHOT_{i}: bid=65000.{i} ask=65001.{i} spread_bps=0.15 regime=range"
        for i in range(runs)
    ]


async def run_benchmark(
    *,
    mode: str,
    runs: int,
    model: str,
    thinking_level: ThinkingLevel,
    fixtures_path: Path | None,
    proxy_url: str = "http://127.0.0.1:8045/v1",
    out_json: Path | None = None,
) -> dict[str, Any]:
    ram_dir = get_ram_tmp_dir("ab_bench")
    db_path = ram_dir / f"ab_bench_{uuid.uuid4().hex[:10]}.sqlite3"
    prompts = load_benchmark_prompts(fixtures_path, runs)

    ledger = UsageLedger(db_path=db_path)
    transports: list[tuple[str, TransportChannel, LLMTransport]]
    if mode == "dry-run":
        transports = [
            ("A_agy_proxy", TransportChannel.AGY_CLI_PROXY, _MockAgyTransport(ledger)),
            (
                "B_gemini_interactions",
                TransportChannel.GEMINI_INTERACTIONS,
                _MockGeminiDirectTransport(ledger, TransportChannel.GEMINI_INTERACTIONS),
            ),
            (
                "C_gemini_openai_compat",
                TransportChannel.GEMINI_OPENAI_COMPAT,
                _MockGeminiDirectTransport(ledger, TransportChannel.GEMINI_OPENAI_COMPAT),
            ),
        ]
    else:
        transports = [
            ("A_agy_proxy", TransportChannel.AGY_CLI_PROXY, AgyProxyTransport(ledger, base_url=proxy_url)),
            (
                "B_gemini_interactions",
                TransportChannel.GEMINI_INTERACTIONS,
                GeminiDirectClient(ledger, mode="interactions"),
            ),
            (
                "C_gemini_openai_compat",
                TransportChannel.GEMINI_OPENAI_COMPAT,
                GeminiDirectClient(ledger, mode="openai_compat"),
            ),
        ]

    system_p0 = (
        "You are QuantumEdge Hybrid Trading Risk & Execution Strategist. "
        "Return compact JSON action decisions."
    )
    p1_prefix = "P1_SESSION_POLICY: max_leverage=3x max_risk_pct=1.0 vst_mode=true"

    try:
        for branch_name, _channel, transport in transports:
            for idx, tail in enumerate(prompts):
                req = LLMRequest(
                    decision_id=f"{branch_name}-{idx}",
                    stage="shadow_ab",
                    model=model,
                    system_instruction=system_p0,
                    p1_slow_prefix=p1_prefix,
                    p2_dynamic_tail=tail,
                    thinking_level=thinking_level,
                    max_output_tokens=16_384,
                    timeout_sec=90.0,
                )
                try:
                    await transport.generate(req)
                except Exception:
                    # Recorded in UsageLedger by the transport; benchmark continues
                    continue
    finally:
        for _name, _ch, transport in transports:
            await transport.close()

    results: dict[str, Any] = {
        "mode": mode,
        "runs": runs,
        "model": model,
        "thinking_level": thinking_level.value,
        "ram_ledger_path": str(db_path),
        "branches": {},
    }
    for branch_name, channel, _transport in transports:
        results["branches"][branch_name] = ledger.summarize(
            transport=channel.value, model_provider_id=model
        )
    ledger.close()

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")

    return results


def _format_table(results: dict[str, Any]) -> str:
    headers = [
        "Branch",
        "OK/Tot",
        "FailRate",
        "Unknown",
        "InputTok",
        "FreshIn",
        "Cached",
        "ThinkTok",
        "IECU",
        "CacheFrac",
        "KM-p95(ms)",
    ]
    lines = [" | ".join(headers), " | ".join(["---"] * len(headers))]
    for name, s in results["branches"].items():
        lines.append(
            " | ".join(
                [
                    name,
                    f"{s.get('ok_requests', 0)}/{s.get('total_requests', 0)}",
                    str(s.get("failure_rate", 0.0)),
                    str(s.get("unknown_usage_records", 0)),
                    str(s.get("sum_provider_input_tokens", 0)),
                    str(s.get("sum_fresh_input_tokens", 0)),
                    str(s.get("sum_cache_read_tokens", 0)),
                    str(s.get("sum_thinking_tokens", 0)),
                    str(s.get("sum_iecu", 0.0)),
                    str(s.get("cache_token_fraction", 0.0)),
                    str(s.get("latency_km_p95_ms")),
                ]
            )
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ab_transport_bench",
        description="Paired A/B Transport Benchmark (AgyProxy vs GeminiDirectClient)",
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "live"),
        default="dry-run",
        help="Execution mode: offline deterministic dry-run or live endpoints",
    )
    parser.add_argument("--fixtures", type=Path, default=None, help="Optional JSON context fixture")
    parser.add_argument("--runs", type=int, default=6, help="Number of paired runs per transport")
    parser.add_argument("--model", default="gemini-4-carbon", help="Canonical Gemini 4 model ID")
    parser.add_argument(
        "--thinking-level",
        choices=("low", "medium", "high"),
        default="medium",
        help="Thinking level",
    )
    parser.add_argument(
        "--proxy-url",
        default="http://127.0.0.1:8045/v1",
        help="Local agy_openai_proxy URL for live mode",
    )
    parser.add_argument("--out-json", type=Path, default=None, help="Optional output JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = asyncio.run(
        run_benchmark(
            mode=args.mode,
            runs=args.runs,
            model=args.model,
            thinking_level=ThinkingLevel(args.thinking_level),
            fixtures_path=args.fixtures,
            proxy_url=args.proxy_url,
            out_json=args.out_json,
        )
    )
    print(_format_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
