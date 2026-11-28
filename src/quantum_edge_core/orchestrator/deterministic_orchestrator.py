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
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, TypeVar

import numpy as np
import pandas as pd
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
    ApiBudgetStats,
    BingXRestRateLimiter,
    GLOBAL_API_BUDGET_STATS,
    OhlcvRamTtlCache,
    _exec_rest_call,
    cancel_vst_order_by_id,
    fetch_full_vst_context,
    fetch_vst_status,
    is_vst_sandbox_client,
    normalize_compact_symbol,
    to_ccxt_swap_symbol,
)
from quantum_edge_core.execution.harness_bridge import HarnessBridge
from quantum_edge_core.execution.market_context_serializer import (
    MarketContextSerializer,
    _compute_macd,
    _compute_rsi_wilder,
    resolve_tf_hierarchy,
)
from quantum_edge_core.execution.position_sizer import ConvictionPositionSizer
from quantum_edge_core.execution.risk_guard import RiskConfig
from quantum_edge_core.orchestrator.prompts import build_p0_static_prefix
from quantum_edge_ml.model_router.backends.transport import (
    ImageAttachment,
    LLMRequest,
    LLMTransport,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger

logger = logging.getLogger("quantum_edge_core.orchestrator.deterministic_orchestrator")

ModelT = TypeVar("ModelT", bound=BaseModel)


def extract_account_risk_limits(
    bundle: Any,
    override: RiskConfig | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact session risk_limits block for P2.account so the LLM sees actual caps."""
    if isinstance(override, RiskConfig):
        return {
            "max_margin_vst": override.max_margin_vst,
            "max_leverage": override.max_leverage,
            "max_notional_vst": override.max_notional_per_pair_vst,
            "max_open_pos": override.max_open_positions,
        }
    if isinstance(override, Mapping):
        return {
            "max_margin_vst": override.get("max_margin_vst", 1000.0),
            "max_leverage": override.get("max_leverage", 50),
            "max_notional_vst": override.get("max_notional_vst", override.get("max_notional_per_pair_vst", 50_000.0)),
            "max_open_pos": override.get("max_open_pos", override.get("max_open_positions", 3)),
        }
    vst_rl = (getattr(bundle, "vst_status", None) or {}).get("risk_limits")
    if isinstance(vst_rl, Mapping):
        return dict(vst_rl)
    for ctx in (getattr(bundle, "contexts_by_symbol", None) or {}).values():
        ctx_rl = (ctx or {}).get("risk_limits")
        if isinstance(ctx_rl, Mapping) and ctx_rl:
            return {
                "max_margin_vst": ctx_rl.get("max_margin_vst", 1000.0),
                "max_leverage": ctx_rl.get("max_leverage", 50),
                "max_notional_vst": ctx_rl.get(
                    "max_notional_vst",
                    float(ctx_rl.get("max_margin_vst", 1000.0)) * float(ctx_rl.get("max_leverage", 50)),
                ),
                "max_open_pos": ctx_rl.get("max_open_positions", ctx_rl.get("max_open_pos", 3)),
            }
    dflt = RiskConfig()
    return {
        "max_margin_vst": dflt.max_margin_vst,
        "max_leverage": dflt.max_leverage,
        "max_notional_vst": dflt.max_notional_per_pair_vst,
        "max_open_pos": dflt.max_open_positions,
    }


class LocalForecastProvider:
    """In-RAM cached ForecastPipeline manager producing calibrated ForecastBundleV1 per cycle."""

    def __init__(
        self,
        *,
        horizon: Literal["5m", "15m", "1h"] = "15m",
        timeframe: str = "15m",
        horizon_steps: int = 3,
        refit_interval_sec: float = 900.0,
        random_state: int = 42,
    ) -> None:
        self.horizon = horizon
        self.timeframe = timeframe
        self.horizon_steps = int(horizon_steps)
        self.refit_interval_sec = float(refit_interval_sec)
        self.random_state = int(random_state)
        self._pipelines: dict[str, tuple[float, Any]] = {}
        self._hmm_1h: dict[str, tuple[float, Any]] = {}
        self.last_used_tf_by_symbol: dict[str, str] = {}
        self.last_hmm_tf_by_symbol: dict[str, str] = {}

    @staticmethod
    def _bars_to_ohlcv_df(bars: Sequence[Any]) -> pd.DataFrame:
        rows: list[dict[str, float]] = []
        for b in bars:
            if isinstance(b, Mapping):
                rows.append(
                    {
                        "ts": float(b.get("ts") if b.get("ts") is not None else b.get("timestamp", 0)),
                        "open": float(b["open"]),
                        "high": float(b["high"]),
                        "low": float(b["low"]),
                        "close": float(b["close"]),
                        "volume": float(b.get("volume", 0.0)),
                    }
                )
            elif isinstance(b, (list, tuple)) and len(b) >= 6:
                rows.append(
                    {
                        "ts": float(b[0]),
                        "open": float(b[1]),
                        "high": float(b[2]),
                        "low": float(b[3]),
                        "close": float(b[4]),
                        "volume": float(b[5]),
                    }
                )
        rows.sort(key=lambda r: r["ts"])
        return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"]).astype(float)

    def build_forecasts(
        self,
        bundle: PreFetchedBundle,
        *,
        now_monotonic: float | None = None,
    ) -> dict[str, ForecastBundleV1]:
        """Fit/reuse cached in-RAM ForecastPipelines (15m LGBM + 1h CausalHMM) and return ForecastBundleV1."""
        from quantum_edge_ml.forecasting.pipeline import CausalHMMFilter, ForecastPipeline

        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        out: dict[str, ForecastBundleV1] = {}
        for sym, ctx in bundle.contexts_by_symbol.items():
            ohlcv = (ctx or {}).get("ohlcv_by_tf") or {}
            chosen_tf: str | None = None
            bars: Sequence[Any] = ()
            seen_tfs: set[str] = set()
            for cand_tf in (self.timeframe, "15m", "1h", "5m"):
                if cand_tf in seen_tfs:
                    continue
                seen_tfs.add(cand_tf)
                cand_bars = ohlcv.get(cand_tf) or []
                if len(cand_bars) >= 40:
                    chosen_tf = cand_tf
                    bars = cand_bars
                    break
            if len(bars) < 40 or chosen_tf is None:
                continue
            self.last_used_tf_by_symbol[sym] = chosen_tf
            try:
                df = self._bars_to_ohlcv_df(bars)
                if len(df) < 40 or df["close"].nunique() < 5:
                    continue
                cached = self._pipelines.get(sym)
                if cached is None or (now - cached[0]) >= self.refit_interval_sec:
                    pipe = ForecastPipeline.fit_from_ohlcv(
                        df,
                        horizon_steps=self.horizon_steps,
                        random_state=self.random_state,
                    )
                    self._pipelines[sym] = (now, pipe)
                else:
                    pipe = cached[1]

                hmm_features_override: np.ndarray | None = None
                self.last_hmm_tf_by_symbol[sym] = chosen_tf
                bars_1h = ohlcv.get("1h") or []
                if len(bars_1h) >= 60:
                    df_1h = self._bars_to_ohlcv_df(bars_1h)
                    if len(df_1h) >= 60 and df_1h["close"].nunique() >= 5:
                        hmm_1h_x, _ = ForecastPipeline.build_causal_features(df_1h)
                        cached_h = self._hmm_1h.get(sym)
                        if cached_h is None or (now - cached_h[0]) >= self.refit_interval_sec:
                            hmm_1h = CausalHMMFilter(
                                n_states=3, random_state=self.random_state
                            ).fit(hmm_1h_x)
                            self._hmm_1h[sym] = (now, hmm_1h)
                        else:
                            hmm_1h = cached_h[1]
                        pipe.hmm = hmm_1h
                        hmm_features_override = hmm_1h_x
                        self.last_hmm_tf_by_symbol[sym] = "1h"

                out[sym] = pipe.predict_bundle(
                    symbol=sym,
                    ohlcv_df=df,
                    hmm_features=hmm_features_override,
                    horizon=self.horizon,
                    horizon_steps=self.horizon_steps,
                )
            except Exception as exc:
                logger.warning("LocalForecastProvider failed soft for %s: %s", sym, exc)
        return out


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


def format_btc_leader_line(market_leader: Mapping[str, Any] | None) -> str:
    """Render compact read-only BTC_LEADER macro pulse line for Stage-1/Stage-2 P2."""
    if not isinstance(market_leader, Mapping) or market_leader.get("px") is None:
        return ""
    return (
        f"BTC_LEADER|px={market_leader.get('px')}"
        f"|15m={market_leader.get('chg_15m_bps', 0.0)}bps"
        f"|1h={market_leader.get('chg_1h_bps', 0.0)}bps"
        f"|4h={market_leader.get('chg_4h_bps', 0.0)}bps"
        f"|rsi15m={market_leader.get('rsi14_15m', 50.0)}"
        f"|mc15m={market_leader.get('macd_15m', 'FLAT')}"
        f"|trend={market_leader.get('trend', 'RANGE')}"
    )


def _fetch_btc_leader_pulse(
    client: Any,
    *,
    ohlcv_cache: OhlcvRamTtlCache | None = None,
    rate_limiter: BingXRestRateLimiter | None = None,
    api_stats: ApiBudgetStats | None = None,
) -> dict[str, Any] | None:
    """Lightweight fail-soft read-only BTCUSDT pulse (30x15m + 20x1h bars, RAM-cached)."""
    if not hasattr(client, "fetch_ohlcv"):
        return None
    target_stats = api_stats or GLOBAL_API_BUDGET_STATS
    try:
        ccxt_sym = to_ccxt_swap_symbol("BTCUSDT")

        def _get_tf_closes(tf: str, limit: int) -> list[float]:
            if ohlcv_cache is not None:
                cached = ohlcv_cache.get("BTCUSDT", tf, limit)
                if cached is not None:
                    target_stats.inc_cache_hit(1)
                    return [float(b["close"]) for b in cached]
            raw = (
                _exec_rest_call(
                    client,
                    client.fetch_ohlcv,
                    ccxt_sym,
                    timeframe=tf,
                    limit=limit,
                    rate_limiter=rate_limiter,
                    stats=target_stats,
                )
                or []
            )
            parsed = [
                {
                    "ts": int(b[0]),
                    "open": float(b[1]),
                    "high": float(b[2]),
                    "low": float(b[3]),
                    "close": float(b[4]),
                    "volume": float(b[5]),
                }
                for b in raw
            ]
            if ohlcv_cache is not None and parsed:
                ohlcv_cache.put("BTCUSDT", tf, limit, parsed)
            return [b["close"] for b in parsed]

        closes_15m_list = _get_tf_closes("15m", 30)
        if len(closes_15m_list) < 2:
            return None
        try:
            closes_1h_list = _get_tf_closes("1h", 20)
        except Exception:
            closes_1h_list = []

        closes_15m = np.array(closes_15m_list, dtype=np.float64)
        px = float(closes_15m[-1])
        if px <= 0:
            return None
        prev_15m = float(closes_15m[-2])
        chg_15m_bps = ((px - prev_15m) / prev_15m * 10_000.0) if prev_15m > 0 else 0.0

        chg_1h_bps = 0.0
        if len(closes_1h_list) >= 2 and float(closes_1h_list[-2]) > 0:
            c1 = float(closes_1h_list[-2])
            chg_1h_bps = (float(closes_1h_list[-1]) - c1) / c1 * 10_000.0
        elif len(closes_15m) >= 5 and float(closes_15m[-5]) > 0:
            c1 = float(closes_15m[-5])
            chg_1h_bps = (px - c1) / c1 * 10_000.0

        chg_4h_bps = 0.0
        if len(closes_1h_list) >= 5 and float(closes_1h_list[-5]) > 0:
            c4 = float(closes_1h_list[-5])
            chg_4h_bps = (float(closes_1h_list[-1]) - c4) / c4 * 10_000.0
        elif len(closes_15m) >= 17 and float(closes_15m[-17]) > 0:
            c4 = float(closes_15m[-17])
            chg_4h_bps = (px - c4) / c4 * 10_000.0

        rsi14_15m = round(float(_compute_rsi_wilder(closes_15m, 14)[-1]), 1)
        macd_l, sig_l, hist = _compute_macd(closes_15m)
        if float(hist[-1]) > 0 and float(macd_l[-1]) >= float(sig_l[-1]):
            macd_15m = "BULL"
        elif float(hist[-1]) < 0 and float(macd_l[-1]) <= float(sig_l[-1]):
            macd_15m = "BEAR"
        else:
            macd_15m = "FLAT"

        if chg_4h_bps >= 15.0 or (chg_1h_bps >= 10.0 and macd_15m == "BULL"):
            trend = "BULL"
        elif chg_4h_bps <= -15.0 or (chg_1h_bps <= -10.0 and macd_15m == "BEAR"):
            trend = "BEAR"
        else:
            trend = "RANGE"

        return {
            "symbol": "BTCUSDT",
            "px": round(px, 2),
            "chg_15m_bps": round(chg_15m_bps, 1),
            "chg_1h_bps": round(chg_1h_bps, 1),
            "chg_4h_bps": round(chg_4h_bps, 1),
            "rsi14_15m": rsi14_15m,
            "macd_15m": macd_15m,
            "trend": trend,
        }
    except Exception:
        return None


def format_tactical_indicators_line(
    serializer: MarketContextSerializer,
    symbol: str,
    ctx: Mapping[str, Any],
) -> str:
    """Render compact tactical TF indicators (1h/15m/5m or legacy) for Stage-1 screening."""
    ohlcv = (ctx or {}).get("ohlcv_by_tf") or {}
    _, tact_tfs = resolve_tf_hierarchy(ohlcv)
    chunks: list[str] = []
    for tf in tact_tfs:
        bars = ohlcv.get(tf) or []
        if not bars:
            continue
        p = serializer.build_structural_profile(tf, bars)
        chunks.append(
            f"{tf}:rsi={p['rsi14']},adx={p['adx14']},di={p['di_diff']},mc={p['macd_cross']},"
            f"bb={p['bb_pos']},div={p['rsi_div']}/{p['macd_div']}"
        )
    if not chunks:
        return ""
    return f"TAC_IND|{symbol.upper()}|" + "|".join(chunks)


def serialize_stage2_candidate(
    serializer: MarketContextSerializer,
    cand_ctx: Mapping[str, Any],
    *,
    include_images: bool,
    ohlcv_tail: int = 25,
) -> tuple[str, list[ImageAttachment]]:
    """Serialize Stage-2 candidate context without duplicating 25-bar tactical candles."""
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

    strat_tfs, tact_tfs = resolve_tf_hierarchy(ohlcv)
    for tf in (*strat_tfs, *tact_tfs):
        bars = ohlcv.get(tf) or []
        if not bars:
            continue
        ref_hash = hashlib.sha256(
            json.dumps(bars, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        prof = serializer.build_structural_profile(tf, bars)
        sections.append(
            f"PROF_{tf}:"
            + serializer.encode_structural_profile_wire(prof)
            + f"|ref={ref_hash}"
        )
        if tf in tact_tfs:
            # Lossless OHLCV_TICK_V1 for tactical TFs only (no duplicate DELTA_BPS_V1)
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
    market_leader: dict[str, Any] | None = None


class PreFetchCoordinator:
    """Parallel in-memory collector for VST status and pair-scoped FullMarketContexts."""

    def __init__(
        self,
        exchange_client: Any,
        symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
        risk_config: RiskConfig | None = None,
        *,
        include_btc_leader: bool = True,
        ohlcv_cache: OhlcvRamTtlCache | None = None,
        market_bridge: Any | None = None,
        replay_recorder: Any | None = None,
        rate_limiter: BingXRestRateLimiter | None = None,
        api_stats: ApiBudgetStats | None = None,
    ) -> None:
        self.client = exchange_client
        self.symbols = tuple(s.upper() for s in symbols)
        self.risk_config = risk_config or RiskConfig()
        self.include_btc_leader = bool(include_btc_leader)
        self.ohlcv_cache = ohlcv_cache if ohlcv_cache is not None else OhlcvRamTtlCache()
        self.market_bridge = market_bridge
        self.replay_recorder = replay_recorder
        self.rate_limiter = rate_limiter
        self.api_stats = api_stats

    async def fetch_all(self, *, include_analytics: bool = True) -> PreFetchedBundle:
        t0 = time.perf_counter()
        raw_vst_status = await asyncio.to_thread(
            fetch_vst_status,
            self.client,
            fallback_symbols=self.symbols,
            rate_limiter=self.rate_limiter,
            stats=self.api_stats,
        )
        ctx_tasks = [
            asyncio.to_thread(
                fetch_full_vst_context,
                self.client,
                sym,
                self.risk_config,
                ("5m", "15m", "1h", "4h", "1d"),
                100,
                50,
                include_analytics,
                ohlcv_cache=self.ohlcv_cache,
                market_bridge=self.market_bridge,
                shared_vst_status=raw_vst_status,
                rate_limiter=self.rate_limiter,
                api_stats=self.api_stats,
            )
            for sym in self.symbols
        ]
        fetch_btc = self.include_btc_leader and "BTCUSDT" not in self.symbols
        btc_task = (
            asyncio.to_thread(
                _fetch_btc_leader_pulse,
                self.client,
                ohlcv_cache=self.ohlcv_cache,
                rate_limiter=self.rate_limiter,
                api_stats=self.api_stats,
            )
            if fetch_btc
            else None
        )
        if btc_task is not None:
            market_leader, *ctx_models = await asyncio.gather(btc_task, *ctx_tasks)
        else:
            ctx_models = list(await asyncio.gather(*ctx_tasks))
            market_leader = None
        vst_status = scope_vst_status_to_symbols(raw_vst_status, self.symbols)
        contexts = {
            sym: m.model_dump(mode="json") for sym, m in zip(self.symbols, ctx_models)
        }
        snap_id = f"snap-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        bundle = PreFetchedBundle(
            snapshot_id=snap_id,
            vst_status=vst_status,
            contexts_by_symbol=contexts,
            fetch_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            market_leader=market_leader,
        )
        if self.replay_recorder is not None and hasattr(self.replay_recorder, "record_bundle"):
            self.replay_recorder.record_bundle(bundle)
        return bundle


class StatelessStagePromptAssembler:
    """Phase 3 prompt assembler supporting both text-only OAuth proxy and multimodal WebP."""

    def __init__(
        self,
        serializer: MarketContextSerializer | None = None,
        *,
        include_images: bool = False,
        extra_reference_playbook: str = "",
        risk_limits: RiskConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.serializer = serializer or MarketContextSerializer()
        self.include_images = include_images
        self.risk_limits = risk_limits
        self.p0_static = build_p0_static_prefix(
            extra_reference_playbook=extra_reference_playbook
        )
        self._last_p1_by_snapshot: dict[str, str] = {}

    def _build_p1_slow(self, contexts_by_symbol: dict[str, dict[str, Any]]) -> str:
        parts: list[str] = ["=== P1_MACRO_STRUCTURE ==="]
        for sym in sorted(contexts_by_symbol.keys()):
            ctx = contexts_by_symbol[sym]
            ohlcv = ctx.get("ohlcv_by_tf") or {}
            strat_tfs, _ = resolve_tf_hierarchy(ohlcv)
            for tf in strat_tfs:
                bars = ohlcv.get(tf) or []
                if bars:
                    prof = self.serializer.build_structural_profile(tf, bars)
                    parts.append(
                        f"MACRO|{sym}|{tf}|"
                        + self.serializer.encode_structural_profile_wire(prof)
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
                        "risk_limits": extract_account_risk_limits(bundle, self.risk_limits),
                    },
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        ]
        btc_line = format_btc_leader_line(getattr(bundle, "market_leader", None))
        if btc_line:
            p2_parts.append(btc_line)
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
            tac_line = format_tactical_indicators_line(self.serializer, sym, ctx)
            if tac_line:
                p2_parts.append(tac_line)
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
                "risk_limits": extract_account_risk_limits(bundle, self.risk_limits),
            },
        }
        if not isinstance(fc_fmt, str):
            s2_header["forecast"] = fc_fmt

        p2_parts = [
            "=== P2_STAGE2_DEEP_DRILLDOWN ===",
            json.dumps(s2_header, separators=(",", ":"), sort_keys=True),
        ]
        btc_line = format_btc_leader_line(getattr(bundle, "market_leader", None))
        if btc_line:
            p2_parts.append(btc_line)
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
        position_sizer: ConvictionPositionSizer | None = None,
        model_id: str = "gemini-4-carbon",
        stage1_timeout_sec: float = 90.0,
        stage2_timeout_sec: float = 150.0,
    ) -> None:
        self.transport = transport
        self.ledger = ledger
        self.assembler = assembler or StatelessStagePromptAssembler(include_images=False)
        self.bridge = bridge
        self.effort_router = effort_router
        self.position_sizer = position_sizer
        self.model_id = model_id
        self.stage1_timeout_sec = float(stage1_timeout_sec)
        self.stage2_timeout_sec = float(stage2_timeout_sec)

    def _get_risk_config(self) -> RiskConfig:
        executor = getattr(self.bridge, "executor", None)
        guard = getattr(executor, "risk_guard", None)
        cfg = getattr(guard, "config", None)
        return cfg if isinstance(cfg, RiskConfig) else RiskConfig()

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
        risk_cfg = self._get_risk_config()
        scoped_vst = scope_vst_status_to_symbols(
            bundle.vst_status, list(bundle.contexts_by_symbol.keys())
        )
        scoped_vst["risk_limits"] = extract_account_risk_limits(bundle, risk_cfg)
        scoped_bundle = PreFetchedBundle(
            snapshot_id=bundle.snapshot_id,
            vst_status=scoped_vst,
            contexts_by_symbol=bundle.contexts_by_symbol,
            fetch_ms=bundle.fetch_ms,
            market_leader=bundle.market_leader,
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
        decision_payload = decision_v2.model_dump(mode="json")

        if ok_val and decision_v2.action != "HOLD":
            target_ctx = scoped_bundle.contexts_by_symbol[decision_v2.symbol]
            active_side, pos_qty = self._find_open_position(
                scoped_bundle.vst_status, decision_v2.symbol
            )
            lean_dict = decision_v2.to_lean_decision_dict(
                decision_id, active_position_side=active_side
            )
            ref_price = float(
                (target_ctx.get("market_snapshot") or {}).get("last_price") or 0.0
            )
            if self.position_sizer is not None and decision_v2.action in ("OPEN_LONG", "OPEN_SHORT"):
                sizing = self.position_sizer.size_order(
                    decision=decision_v2,
                    ctx=target_ctx,
                    forecast=cand_fc,
                    risk_config=risk_cfg,
                    current_price=ref_price,
                    market_leader=scoped_bundle.market_leader,
                )
                lean_dict["margin_vst"] = sizing.margin_vst
                lean_dict["leverage"] = sizing.leverage
                decision_payload["sizing"] = sizing.to_audit_dict()

            replaced_entry_ids: list[str] = []
            if (
                not dry_run
                and decision_v2.action in ("OPEN_LONG", "OPEN_SHORT")
                and decision_v2.order_type == "LIMIT"
            ):
                ex_client = getattr(
                    getattr(self.bridge, "executor", None), "exchange_client", None
                )
                if ex_client is not None and is_vst_sandbox_client(ex_client):
                    for ord_obj in scoped_bundle.vst_status.get("open_orders") or []:
                        if (
                            str(ord_obj.get("symbol") or "").upper() == decision_v2.symbol.upper()
                            and ord_obj.get("reduceOnly") is not True
                            and str(ord_obj.get("rawType") or ord_obj.get("type") or "").upper()
                            == "LIMIT"
                            and ord_obj.get("id")
                        ):
                            oid_str = str(ord_obj["id"])
                            c_res = cancel_vst_order_by_id(
                                ex_client, decision_v2.symbol, oid_str
                            )
                            if c_res.get("cancelled"):
                                replaced_entry_ids.append(oid_str)

            lean_dec = TradeDecision.model_validate(lean_dict)
            res = self.bridge.submit_decision(
                lean_dec,
                current_price=ref_price,
                open_positions_count=int(
                    scoped_bundle.vst_status.get("open_positions_count") or 0
                ),
                position_qty=pos_qty,
            )
            exec_res = res.model_dump(mode="json")
            if replaced_entry_ids:
                exec_res["replaced_entry_order_ids"] = replaced_entry_ids
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
            decision_payload=decision_payload,
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
    "LocalForecastProvider",
    "PreFetchCoordinator",
    "PreFetchedBundle",
    "StagePromptAssemblerProtocol",
    "StatelessStagePromptAssembler",
    "extract_account_risk_limits",
    "format_btc_leader_line",
    "format_forecast_for_prompt",
    "format_tactical_indicators_line",
    "parse_structured_model_json",
    "scope_vst_status_to_symbols",
    "serialize_stage2_candidate",
]
