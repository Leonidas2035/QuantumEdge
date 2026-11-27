"""Deterministic 2-Stage Progressive Disclosure Orchestrator & RAM-First PreFetchCoordinator.

Eliminates O(N^2) ReAct loops by gathering all 4 asset contexts in RAM prior to
inference, running a fast Stage-1 cross-asset screen (thinking_level=LOW), and
drilling down on at most 1 candidate in Stage-2 before delegating execution to
the canonical HarnessBridge -> LeanRiskGuard -> LeanExecutor -> OrderLedger pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, TypeVar

from pydantic import BaseModel

from quantum_edge_core.contracts.lean import TradeDecision
from quantum_edge_core.contracts.optimization import (
    DecisionAuditV1,
    ForecastBundleV1,
    ScreeningDecisionV1,
    ThinkingLevel,
    TradeDecisionV2,
)
from quantum_edge_core.execution.bingx_vst_adapter import (
    fetch_full_vst_context,
    fetch_vst_status,
    normalize_compact_symbol,
)
from quantum_edge_core.execution.harness_bridge import HarnessBridge
from quantum_edge_core.execution.market_context_serializer import MarketContextSerializer
from quantum_edge_core.execution.risk_guard import RiskConfig
from quantum_edge_core.orchestrator.prompts import build_p0_static_prefix
from quantum_edge_ml.model_router.backends.transport import (
    ImageAttachment,
    LLMRequest,
    LLMTransport,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger

ModelT = TypeVar("ModelT", bound=BaseModel)


def scope_vst_status_to_symbols(
    vst_status: Mapping[str, Any],
    allowed_symbols: Sequence[str],
) -> dict[str, Any]:
    """Return a copy of vst_status filtered strictly to allowed_symbols.

    Prevents manual trades or sibling bots on shared demo/live accounts from
    polluting LLM prompts, ReasoningEffortRouter exposure, or LeanRiskGuard caps.
    """
    allowed = {
        normalize_compact_symbol(str(s)) or str(s).strip().upper()
        for s in allowed_symbols
        if str(s).strip()
    }
    scoped = dict(vst_status)
    all_positions = list(vst_status.get("open_positions") or [])
    all_orders = list(vst_status.get("open_orders") or [])

    scoped_positions = [
        dict(p)
        for p in all_positions
        if (normalize_compact_symbol(str(p.get("symbol") or "")) or str(p.get("symbol") or "").strip().upper())
        in allowed
    ]
    scoped_orders = [
        dict(o)
        for o in all_orders
        if (normalize_compact_symbol(str(o.get("symbol") or "")) or str(o.get("symbol") or "").strip().upper())
        in allowed
    ]

    scoped["open_positions"] = scoped_positions
    scoped["open_orders"] = scoped_orders
    scoped["open_positions_count"] = len(scoped_positions)
    scoped["open_orders_count"] = len(scoped_orders)
    scoped["open_positions_symbols"] = sorted(
        {
            normalize_compact_symbol(str(p.get("symbol") or ""))
            for p in scoped_positions
            if p.get("symbol")
        }
    )
    scoped["open_orders_symbols"] = sorted(
        {
            normalize_compact_symbol(str(o.get("symbol") or ""))
            for o in scoped_orders
            if o.get("symbol")
        }
    )
    scoped["allowed_symbols"] = sorted(allowed)
    scoped["ignored_foreign_positions_count"] = len(all_positions) - len(scoped_positions)
    scoped["ignored_foreign_orders_count"] = len(all_orders) - len(scoped_orders)
    return scoped


def format_forecast_for_prompt(
    forecast: ForecastBundleV1 | Mapping[str, Any] | None,
) -> Any:
    """Render ForecastBundleV1 (or compatible dict) via dense compact wire string."""
    if forecast is None:
        return None
    if isinstance(forecast, ForecastBundleV1):
        return forecast.to_compact_prompt_str()
    if isinstance(forecast, Mapping):
        try:
            return ForecastBundleV1.model_validate(forecast).to_compact_prompt_str()
        except Exception:
            return dict(forecast)
    return forecast


def serialize_stage2_candidate(
    serializer: MarketContextSerializer,
    cand_ctx: Mapping[str, Any],
    *,
    include_images: bool,
    ohlcv_tail: int = 25,
) -> tuple[str, list[ImageAttachment]]:
    """Serialize Stage-2 candidate context without duplicating 25-bar 15m/5m/1m candles."""
    if include_images:
        ser_cand = serializer.serialize_single_asset(
            cand_ctx,
            mode="hybrid_webp",
            ohlcv_tail=ohlcv_tail,
            include_full_liqmap=True,
        )
        return ser_cand.text, ser_cand.images

    snap = cand_ctx.get("market_snapshot") or {}
    sym = str(snap.get("symbol") or "UNKNOWN").upper()
    micro = cand_ctx.get("microstructure_and_liquidity") or {}
    ohlcv = cand_ctx.get("ohlcv_by_tf") or {}
    sections: list[str] = []

    header_obj = {
        "sym": sym,
        "bid": snap.get("bid"),
        "ask": snap.get("ask"),
        "last": snap.get("last_price"),
        "spr_bps": snap.get("spread_bps"),
        "stale": snap.get("is_stale"),
        "deriv": cand_ctx.get("derivatives"),
        "of": (micro.get("orderflow") or {}).get("metrics", {}),
        "comp": {
            "sig": (micro.get("composite_signal") or {}).get("signal"),
            "score": (micro.get("composite_signal") or {}).get("score"),
            "c": (micro.get("composite_signal") or {}).get("components"),
        },
    }
    sections.append("META:" + json.dumps(header_obj, separators=(",", ":"), sort_keys=True))

    if "liqmap" in micro:
        sections.append(serializer.encode_liqmap_csv(micro["liqmap"]))
    if "orderbook" in cand_ctx:
        sections.append(serializer.encode_orderbook_csv(cand_ctx["orderbook"], top_n=15))

    for tf in ("4h", "1h", "15m", "5m", "1m"):
        bars = ohlcv.get(tf) or []
        if not bars:
            continue
        ref_hash = hashlib.sha256(
            json.dumps(bars, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        prof = serializer.build_structural_profile(tf, bars)
        sections.append(
            f"PROF_{tf}:"
            + json.dumps(prof, separators=(",", ":"), sort_keys=True)
            + f"|ref={ref_hash}"
        )
        if tf in ("15m", "5m", "1m"):
            # Lossless OHLCV_TICK_V1 for 15m/5m/1m only (no duplicate DELTA_BPS_V1)
            sections.append(serializer.encode_ohlcv_tick_delta(sym, tf, bars[-ohlcv_tail:]))

    return "\n".join(sections), []


class EffortRouterProtocol(Protocol):
    """Protocol for Phase 6 adaptive reasoning effort routing."""

    def select_level(
        self,
        *,
        symbol: str,
        ctx: dict[str, Any],
        forecast: dict[str, Any] | None,
        vst_status: dict[str, Any],
        stage: int = 2,
    ) -> tuple[float, ThinkingLevel]: ...


@dataclass(slots=True)
class AssembledStagePrompt:
    """Normalized 3-layer prompt payload consumed by DeterministicTradingOrchestrator."""

    p0_static: str
    p1_slow: str
    p2_dynamic: str
    images: list[ImageAttachment] = field(default_factory=list)
    prefix_hash: str = ""
    context_hash: str = ""


class StagePromptAssemblerProtocol(Protocol):
    """Protocol implemented by StatelessStagePromptAssembler (Phase 3) and PrefixContextAssembler (Phase 4)."""

    def assemble_stage1(
        self,
        *,
        bundle: PreFetchedBundle,
        trigger_summary: str,
        forecasts_by_symbol: dict[str, dict[str, Any] | ForecastBundleV1] | None = None,
    ) -> AssembledStagePrompt: ...

    def assemble_stage2(
        self,
        *,
        bundle: PreFetchedBundle,
        candidate_symbol: str,
        stage1_summary: dict[str, Any],
        forecast: dict[str, Any] | ForecastBundleV1 | None = None,
    ) -> AssembledStagePrompt: ...


@dataclass(slots=True)
class PreFetchedBundle:
    """In-memory multi-asset snapshot gathered before LLM invocation (zero SSD writes)."""

    snapshot_id: str
    vst_status: dict[str, Any]
    contexts_by_symbol: dict[str, dict[str, Any]]
    fetch_ms: float


class PreFetchCoordinator:
    """Parallel in-memory collector for VST status and pair-scoped FullMarketContexts."""

    def __init__(
        self,
        exchange_client: Any,
        symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
        risk_config: RiskConfig | None = None,
    ) -> None:
        self.client = exchange_client
        self.symbols = tuple(s.upper() for s in symbols)
        self.risk_config = risk_config or RiskConfig()

    async def fetch_all(self, *, include_analytics: bool = True) -> PreFetchedBundle:
        t0 = time.perf_counter()
        status_task = asyncio.to_thread(
            fetch_vst_status, self.client, fallback_symbols=self.symbols
        )
        ctx_tasks = [
            asyncio.to_thread(
                fetch_full_vst_context,
                self.client,
                sym,
                self.risk_config,
                ("1m", "5m", "15m", "1h", "4h"),
                100,
                50,
                include_analytics,
            )
            for sym in self.symbols
        ]
        raw_vst_status, *ctx_models = await asyncio.gather(status_task, *ctx_tasks)
        vst_status = scope_vst_status_to_symbols(raw_vst_status, self.symbols)
        contexts = {
            sym: m.model_dump(mode="json") for sym, m in zip(self.symbols, ctx_models)
        }
        snap_id = f"snap-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        return PreFetchedBundle(
            snapshot_id=snap_id,
            vst_status=vst_status,
            contexts_by_symbol=contexts,
            fetch_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        )


class StatelessStagePromptAssembler:
    """Phase 3 prompt assembler supporting both text-only OAuth proxy and multimodal WebP."""

    def __init__(
        self,
        serializer: MarketContextSerializer | None = None,
        *,
        include_images: bool = False,
        extra_reference_playbook: str = "",
    ) -> None:
        self.serializer = serializer or MarketContextSerializer()
        self.include_images = include_images
        self.p0_static = build_p0_static_prefix(
            extra_reference_playbook=extra_reference_playbook
        )
        self._last_p1_by_snapshot: dict[str, str] = {}

    def _build_p1_slow(self, contexts_by_symbol: dict[str, dict[str, Any]]) -> str:
        parts: list[str] = ["=== P1_MACRO_STRUCTURE ==="]
        for sym in sorted(contexts_by_symbol.keys()):
            ctx = contexts_by_symbol[sym]
            ohlcv = ctx.get("ohlcv_by_tf") or {}
            for tf in ("4h", "1h"):
                bars = ohlcv.get(tf) or []
                if bars:
                    prof = self.serializer.build_structural_profile(tf, bars)
                    parts.append(
                        f"MACRO|{sym}|{tf}|"
                        + json.dumps(prof, separators=(",", ":"), sort_keys=True)
                    )
            liq = (ctx.get("microstructure_and_liquidity") or {}).get("liqmap") or {}
            if liq.get("status") == "OK":
                top_liq = {
                    "near_L": liq.get("nearest_long_liq"),
                    "near_S": liq.get("nearest_short_liq"),
                    "strong": liq.get("strongest_zone"),
                }
                parts.append(
                    f"MACRO_LIQ|{sym}|"
                    + json.dumps(top_liq, separators=(",", ":"), sort_keys=True)
                )
        return "\n".join(parts)

    def _get_p1_for_bundle(self, bundle: PreFetchedBundle) -> str:
        cached = self._last_p1_by_snapshot.get(bundle.snapshot_id)
        if cached is not None:
            return cached
        p1 = self._build_p1_slow(bundle.contexts_by_symbol)
        # Keep bounded in RAM (at most 4 recent snapshot prefixes)
        if len(self._last_p1_by_snapshot) >= 4:
            self._last_p1_by_snapshot.clear()
        self._last_p1_by_snapshot[bundle.snapshot_id] = p1
        return p1

    def assemble_stage1(
        self,
        *,
        bundle: PreFetchedBundle,
        trigger_summary: str,
        forecasts_by_symbol: dict[str, dict[str, Any] | ForecastBundleV1] | None = None,
        current_seq: int = 0,
        regime_signature: str = "DEFAULT",
    ) -> AssembledStagePrompt:
        _ = (current_seq, regime_signature)
        p1_slow = self._get_p1_for_bundle(bundle)
        p2_parts: list[str] = [
            "=== P2_STAGE1_SCREENING ===",
            json.dumps(
                {
                    "snapshot_id": bundle.snapshot_id,
                    "trigger": trigger_summary,
                    "account": {
                        "free_vst": (bundle.vst_status.get("balance_vst") or {}).get("free"),
                        "open_pos": bundle.vst_status.get("open_positions", []),
                        "open_ord_cnt": bundle.vst_status.get("open_orders_count", 0),
                    },
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        ]
        for sym in sorted(bundle.contexts_by_symbol.keys()):
            ctx = bundle.contexts_by_symbol[sym]
            snap = ctx.get("market_snapshot") or {}
            micro = ctx.get("microstructure_and_liquidity") or {}
            fc_fmt = format_forecast_for_prompt((forecasts_by_symbol or {}).get(sym))
            row: dict[str, Any] = {
                "sym": sym,
                "last": snap.get("last_price"),
                "spr_bps": snap.get("spread_bps"),
                "of_imb10": (micro.get("orderflow") or {}).get("imbalance"),
                "cvd": (micro.get("orderflow") or {}).get("cvd_or_delta"),
                "comp_sig": (micro.get("composite_signal") or {}).get("signal"),
                "comp_score": (micro.get("composite_signal") or {}).get("score"),
                "funding": (ctx.get("derivatives") or {}).get("funding_rate"),
            }
            if not isinstance(fc_fmt, str):
                row["forecast"] = fc_fmt
            p2_parts.append(
                "PAIR_SUM|" + json.dumps(row, separators=(",", ":"), sort_keys=True)
            )
            if isinstance(fc_fmt, str):
                p2_parts.append(fc_fmt)
            bars_5m = (ctx.get("ohlcv_by_tf") or {}).get("5m") or []
            if bars_5m:
                p2_parts.append(
                    self.serializer.encode_ohlcv_delta_bps(sym, "5m", bars_5m, tail=10)
                )
        p2_parts.append("OUTPUT_CONTRACT: Return ONLY valid JSON matching STAGE1_SCHEMA.")
        images = (
            [self.serializer.render_screening_grid_webp(bundle.contexts_by_symbol, tf="15m")]
            if self.include_images
            else []
        )
        return self._finalize(p1_slow, "\n".join(p2_parts), images)

    def assemble_stage2(
        self,
        *,
        bundle: PreFetchedBundle,
        candidate_symbol: str,
        stage1_summary: dict[str, Any],
        forecast: dict[str, Any] | ForecastBundleV1 | None = None,
    ) -> AssembledStagePrompt:
        p1_slow = self._get_p1_for_bundle(bundle)
        sym = candidate_symbol.upper()
        cand_ctx = bundle.contexts_by_symbol[sym]
        cand_text, images = serialize_stage2_candidate(
            self.serializer,
            cand_ctx,
            include_images=self.include_images,
            ohlcv_tail=25,
        )
        fc_fmt = format_forecast_for_prompt(forecast)
        s2_header: dict[str, Any] = {
            "snapshot_id": bundle.snapshot_id,
            "candidate": sym,
            "stage1": stage1_summary,
            "account": {
                "free_vst": (bundle.vst_status.get("balance_vst") or {}).get("free"),
                "open_pos": bundle.vst_status.get("open_positions", []),
                "open_orders": bundle.vst_status.get("open_orders", []),
            },
        }
        if not isinstance(fc_fmt, str):
            s2_header["forecast"] = fc_fmt

        p2_parts = [
            "=== P2_STAGE2_DEEP_DRILLDOWN ===",
            json.dumps(s2_header, separators=(",", ":"), sort_keys=True),
        ]
        if isinstance(fc_fmt, str):
            p2_parts.append(fc_fmt)
        p2_parts.append(cand_text)
        p2_parts.append("OUTPUT_CONTRACT: Return ONLY valid JSON matching STAGE2_SCHEMA.")
        return self._finalize(p1_slow, "\n".join(p2_parts), images)

    def observe_cache_result(self, cache_read_tokens: int | None) -> bool:
        """No-op hook in Phase 3; overridden/delegated in Phase 4 SnapshotStore."""
        _ = cache_read_tokens
        return False

    def upsert_thesis(
        self,
        *,
        decision_id: str,
        decision: TradeDecisionV2,
        snapshot_id: str,
    ) -> None:
        """No-op hook in Phase 3; overridden/delegated in Phase 4 ThesisMemoryStore."""
        _ = (decision_id, decision, snapshot_id)

    def _finalize(
        self,
        p1_slow: str,
        p2_dynamic: str,
        images: list[ImageAttachment],
    ) -> AssembledStagePrompt:
        prefix_hash = hashlib.sha256(
            (self.p0_static + "\n" + p1_slow).encode("utf-8")
        ).hexdigest()[:16]
        context_hash = hashlib.sha256(
            (prefix_hash + "\n" + p2_dynamic).encode("utf-8")
        ).hexdigest()[:16]
        return AssembledStagePrompt(
            p0_static=self.p0_static,
            p1_slow=p1_slow,
            p2_dynamic=p2_dynamic,
            images=images,
            prefix_hash=prefix_hash,
            context_hash=context_hash,
        )


def parse_structured_model_json(raw_text: str, model_cls: type[ModelT]) -> ModelT:
    """Resiliently parse Pydantic JSON even when wrapped in markdown code fences."""
    stripped = raw_text.strip()
    try:
        return model_cls.model_validate_json(stripped)
    except Exception:
        pass

    candidate = stripped
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            candidate = "\n".join(lines[1:-1]).strip()
            try:
                return model_cls.model_validate_json(candidate)
            except Exception:
                pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        obj = json.loads(candidate[start : end + 1])
        return model_cls.model_validate(obj)

    return model_cls.model_validate_json(stripped)


class DeterministicTradingOrchestrator:
    """2-Stage Progressive Disclosure orchestrator coupled to HarnessBridge."""

    def __init__(
        self,
        *,
        transport: LLMTransport,
        ledger: UsageLedger,
        bridge: HarnessBridge,
        assembler: Any | None = None,
        effort_router: EffortRouterProtocol | None = None,
        model_id: str = "gemini-4-carbon",
        stage1_timeout_sec: float = 90.0,
        stage2_timeout_sec: float = 150.0,
    ) -> None:
        self.transport = transport
        self.ledger = ledger
        self.assembler = assembler or StatelessStagePromptAssembler(include_images=False)
        self.bridge = bridge
        self.effort_router = effort_router
        self.model_id = model_id
        self.stage1_timeout_sec = float(stage1_timeout_sec)
        self.stage2_timeout_sec = float(stage2_timeout_sec)

    def _notify_cache_result(
        self,
        cache_read_tokens: int | None,
        *,
        prompt_tokens: int | None = None,
    ) -> None:
        if hasattr(self.assembler, "snapshot_store") and hasattr(
            self.assembler.snapshot_store, "observe_cache_result"
        ):
            self.assembler.snapshot_store.observe_cache_result(
                cache_read_tokens, prompt_tokens=prompt_tokens, stage="stage2_drill"
            )
        elif hasattr(self.assembler, "observe_cache_result"):
            self.assembler.observe_cache_result(cache_read_tokens)

    def _notify_thesis_upsert(
        self,
        *,
        decision_id: str,
        decision: TradeDecisionV2,
        snapshot_id: str,
    ) -> None:
        if hasattr(self.assembler, "thesis_store") and hasattr(
            self.assembler.thesis_store, "upsert_from_decision"
        ):
            self.assembler.thesis_store.upsert_from_decision(
                decision_id=decision_id,
                decision=decision,
                snapshot_id=snapshot_id,
            )
        elif hasattr(self.assembler, "upsert_thesis"):
            self.assembler.upsert_thesis(
                decision_id=decision_id,
                decision=decision,
                snapshot_id=snapshot_id,
            )

    async def run_decision_cycle(
        self,
        bundle: PreFetchedBundle,
        *,
        trigger_summary: str = "HEARTBEAT_1H",
        forecasts_by_symbol: dict[str, dict[str, Any] | ForecastBundleV1] | None = None,
        dry_run: bool = False,
        current_seq: int = 0,
        regime_signature: str = "DEFAULT",
    ) -> DecisionAuditV1:
        decision_id = f"qe-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        usage_ids: list[str] = []

        # Defensive symbol scoping so foreign positions/orders never reach LLM, router, or RiskGuard
        scoped_vst = scope_vst_status_to_symbols(
            bundle.vst_status, list(bundle.contexts_by_symbol.keys())
        )
        scoped_bundle = PreFetchedBundle(
            snapshot_id=bundle.snapshot_id,
            vst_status=scoped_vst,
            contexts_by_symbol=bundle.contexts_by_symbol,
            fetch_ms=bundle.fetch_ms,
        )

        # === STAGE 1: Cross-Asset Screening (thinking_level=LOW) ===
        s1_prompt = self.assembler.assemble_stage1(
            bundle=scoped_bundle,
            trigger_summary=trigger_summary,
            forecasts_by_symbol=forecasts_by_symbol,
            current_seq=current_seq,
            regime_signature=regime_signature,
        )
        s1_resp = await self.transport.generate(
            LLMRequest(
                decision_id=decision_id,
                stage="stage1_screen",
                model=self.model_id,
                system_instruction=s1_prompt.p0_static,
                p1_slow_prefix=s1_prompt.p1_slow,
                p2_dynamic_tail=s1_prompt.p2_dynamic,
                images=s1_prompt.images,
                thinking_level=ThinkingLevel.LOW,
                max_output_tokens=8_192,
                response_schema=ScreeningDecisionV1.model_json_schema(),
                timeout_sec=self.stage1_timeout_sec,
                prefix_hash=s1_prompt.prefix_hash,
                context_hash=s1_prompt.context_hash,
                snapshot_id=scoped_bundle.snapshot_id,
            )
        )
        usage_ids.append(s1_resp.usage.record_id)
        screen = parse_structured_model_json(s1_resp.text, ScreeningDecisionV1)

        if screen.action == "HOLD":
            fallback_sym = next(iter(scoped_bundle.contexts_by_symbol.keys()), "BTCUSDT")
            primary_sym = screen.candidate_symbol or fallback_sym
            reg_str = (
                screen.market_regime_summary
                if len(screen.market_regime_summary) >= 8
                else f"Stage1: {screen.market_regime_summary}"
            )
            edge_str = (
                screen.candidate_rationale
                if len(screen.candidate_rationale) >= 8
                else f"Stage1: {screen.candidate_rationale}"
            )
            hold_decision = TradeDecisionV2(
                regime_read=reg_str,
                primary_edge=edge_str,
                counter_thesis="No asymmetric edge cleared Stage-1 screening.",
                action="HOLD",
                symbol=primary_sym,
                hold_blocker_code=screen.hold_blocker_code,
                thesis_status="CONFIRMED",
                confidence=0.80,
            )
            audit = DecisionAuditV1(
                decision_id=decision_id,
                snapshot_id=scoped_bundle.snapshot_id,
                context_hash=s1_prompt.context_hash,
                prefix_hash=s1_prompt.prefix_hash,
                model_provider_id=self.model_id,
                thinking_level=ThinkingLevel.LOW,
                usage_record_ids=usage_ids,
                decision_payload=hold_decision.model_dump(mode="json"),
                validator_passed=True,
                validator_reason="STAGE1_FAST_HOLD",
                execution_result={"status": "SKIPPED_HOLD"},
            )
            self.ledger.record_decision_audit(audit)
            return audit

        # === STAGE 2: Candidate Deep Drill-Down ===
        cand_sym = str(screen.candidate_symbol or "").strip().upper()
        if cand_sym not in scoped_bundle.contexts_by_symbol:
            oos_reason = f"SYMBOL_OUT_OF_SCOPE:{cand_sym}"
            audit = DecisionAuditV1(
                decision_id=decision_id,
                snapshot_id=scoped_bundle.snapshot_id,
                context_hash=s1_prompt.context_hash,
                prefix_hash=s1_prompt.prefix_hash,
                model_provider_id=self.model_id,
                thinking_level=ThinkingLevel.LOW,
                usage_record_ids=usage_ids,
                decision_payload=screen.model_dump(mode="json"),
                validator_passed=False,
                validator_reason=oos_reason,
                execution_result={"status": "REJECTED", "reason": oos_reason},
            )
            self.ledger.record_decision_audit(audit)
            return audit

        cand_ctx = scoped_bundle.contexts_by_symbol[cand_sym]
        cand_fc = (forecasts_by_symbol or {}).get(cand_sym)
        cand_fc_dict = (
            cand_fc.model_dump()
            if isinstance(cand_fc, ForecastBundleV1)
            else cand_fc
        )

        if self.effort_router is not None:
            complexity_score, s2_level = self.effort_router.select_level(
                symbol=cand_sym,
                ctx=cand_ctx,
                forecast=cand_fc_dict,
                vst_status=scoped_bundle.vst_status,
                stage=2,
            )
        else:
            complexity_score, s2_level = None, ThinkingLevel.MEDIUM

        s2_prompt = self.assembler.assemble_stage2(
            bundle=scoped_bundle,
            candidate_symbol=cand_sym,
            stage1_summary=screen.model_dump(mode="json"),
            forecast=cand_fc,
        )
        s2_resp = await self.transport.generate(
            LLMRequest(
                decision_id=decision_id,
                stage="stage2_drill",
                model=self.model_id,
                system_instruction=s2_prompt.p0_static,
                p1_slow_prefix=s2_prompt.p1_slow,
                p2_dynamic_tail=s2_prompt.p2_dynamic,
                images=s2_prompt.images,
                thinking_level=s2_level,
                max_output_tokens=16_384,
                response_schema=TradeDecisionV2.model_json_schema(),
                timeout_sec=self.stage2_timeout_sec,
                prefix_hash=s2_prompt.prefix_hash,
                context_hash=s2_prompt.context_hash,
                snapshot_id=scoped_bundle.snapshot_id,
            )
        )
        usage_ids.append(s2_resp.usage.record_id)
        decision_v2 = parse_structured_model_json(s2_resp.text, TradeDecisionV2)
        self._notify_cache_result(
            s2_resp.usage.cache_read_tokens,
            prompt_tokens=s2_resp.usage.provider_input_tokens,
        )

        if decision_v2.symbol not in scoped_bundle.contexts_by_symbol:
            ok_val, val_reason = False, f"SYMBOL_OUT_OF_SCOPE:{decision_v2.symbol}"
        else:
            target_ctx = scoped_bundle.contexts_by_symbol[decision_v2.symbol]
            ok_val, val_reason = self._post_validate_trade(decision_v2, target_ctx)
        exec_res: dict[str, Any] | None = None

        if ok_val and decision_v2.action != "HOLD":
            target_ctx = scoped_bundle.contexts_by_symbol[decision_v2.symbol]
            active_side, pos_qty = self._find_open_position(
                scoped_bundle.vst_status, decision_v2.symbol
            )
            lean_dec = TradeDecision.model_validate(
                decision_v2.to_lean_decision_dict(
                    decision_id, active_position_side=active_side
                )
            )
            ref_price = float(
                (target_ctx.get("market_snapshot") or {}).get("last_price") or 0.0
            )
            res = self.bridge.submit_decision(
                lean_dec,
                current_price=ref_price,
                open_positions_count=int(
                    scoped_bundle.vst_status.get("open_positions_count") or 0
                ),
                position_qty=pos_qty,
            )
            exec_res = res.model_dump(mode="json")
            if not dry_run:
                self._notify_thesis_upsert(
                    decision_id=decision_id,
                    decision=decision_v2,
                    snapshot_id=scoped_bundle.snapshot_id,
                )
        elif decision_v2.action == "HOLD":
            exec_res = {"status": "SKIPPED_HOLD"}

        audit = DecisionAuditV1(
            decision_id=decision_id,
            snapshot_id=scoped_bundle.snapshot_id,
            context_hash=s2_prompt.context_hash,
            prefix_hash=s2_prompt.prefix_hash,
            model_provider_id=self.model_id,
            thinking_level=s2_level,
            complexity_score=complexity_score,
            usage_record_ids=usage_ids,
            decision_payload=decision_v2.model_dump(mode="json"),
            validator_passed=ok_val,
            validator_reason=val_reason,
            execution_result=exec_res,
        )
        self.ledger.record_decision_audit(audit)
        return audit

    @staticmethod
    def _find_open_position(
        vst_status: dict[str, Any], symbol: str
    ) -> tuple[Literal["LONG", "SHORT"] | None, float | None]:
        for pos in vst_status.get("open_positions") or []:
            if str(pos.get("symbol") or "").upper() == symbol.upper():
                side_raw = str(pos.get("positionSide") or pos.get("side") or "").upper()
                side: Literal["LONG", "SHORT"] | None = (
                    "LONG"
                    if "LONG" in side_raw
                    else ("SHORT" if "SHORT" in side_raw else None)
                )
                return side, float(pos.get("contracts") or 0.0)
        return None, None

    @staticmethod
    def _post_validate_trade(
        dec: TradeDecisionV2, cand_ctx: dict[str, Any]
    ) -> tuple[bool, str]:
        if dec.action in ("HOLD", "CLOSE_LONG", "CLOSE_SHORT", "REDUCE"):
            return True, "OK"
        snap = cand_ctx.get("market_snapshot") or {}
        ref_price = float(dec.entry_price or snap.get("last_price") or 0.0)
        spread_bps = float(snap.get("spread_bps") or 0.0)
        if ref_price <= 0 or dec.sl_price is None or dec.tp_price is None:
            return False, "MISSING_PRICE_OR_SL_TP"
        if dec.action == "OPEN_LONG" and not (dec.sl_price < ref_price < dec.tp_price):
            return False, f"INVALID_LONG_BRACKET({dec.sl_price}, {ref_price}, {dec.tp_price})"
        if dec.action == "OPEN_SHORT" and not (dec.tp_price < ref_price < dec.sl_price):
            return False, f"INVALID_SHORT_BRACKET({dec.tp_price}, {ref_price}, {dec.sl_price})"

        tp_bps = abs(dec.tp_price - ref_price) / ref_price * 10_000.0
        if (
            dec.order_type == "MARKET"
            and spread_bps >= 15.0
            and tp_bps < max(70.0, 2.5 * spread_bps)
        ):
            return (
                False,
                f"ALT_MARKET_SPREAD_VIOLATION(spr={spread_bps:.1f}bps,tp={tp_bps:.1f}bps)",
            )
        return True, "OK"


__all__ = [
    "AssembledStagePrompt",
    "DeterministicTradingOrchestrator",
    "EffortRouterProtocol",
    "PreFetchCoordinator",
    "PreFetchedBundle",
    "StagePromptAssemblerProtocol",
    "StatelessStagePromptAssembler",
    "format_forecast_for_prompt",
    "parse_structured_model_json",
    "scope_vst_status_to_symbols",
    "serialize_stage2_candidate",
]
