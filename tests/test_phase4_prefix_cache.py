"""Phase 4 tests: 3-layer PrefixContextAssembler, SQLite WAL ThesisMemoryStore, RAM SnapshotStore, and EvictionMonitor."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import tools.qe_harness_cli as harness_cli
from quantum_edge_core.contracts import (
    ForecastBundleV1,
    RequestStatus,
    ThinkingLevel,
    TradeDecisionV2,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution import (
    HarnessBridge,
    LeanExecutor,
    LeanRiskGuard,
    MarketContextSerializer,
    OrderLedger,
    PaperExchangeClient,
)
from quantum_edge_core.orchestrator import (
    DeterministicTradingOrchestrator,
    PrefixContextAssembler,
    PrefixPromptAssembler,
    QUANTUMEDGE_INTER_HOUR_PLAYBOOK,
    ResyncMode,
    SnapshotStore,
    ThesisMemoryStore,
)
from quantum_edge_ml.model_router.backends.transport import (
    ImageAttachment,
    LLMRequest,
    LLMResponse,
    LLMTransport,
)
from quantum_edge_ml.model_router.router.cache import ImplicitCacheEvictionMonitor
from quantum_edge_ml.model_router.usage_ledger import UsageLedger
from tests.test_phase3_orchestrator import _make_4pair_bundle
from tools.benchmark_count_tokens import estimate_gemini_sp_tokens


class _ScriptedCacheTransport(LLMTransport):
    def __init__(self, ledger: UsageLedger, replies: list[str], cache_hits: list[int]) -> None:
        super().__init__(ledger)
        self.replies = list(replies)
        self.cache_hits = list(cache_hits)
        self.requests: list[LLMRequest] = []

    async def generate(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        txt = self.replies.pop(0)
        c_hit = self.cache_hits.pop(0)
        in_chars = len(req.system_instruction) + len(req.p1_slow_prefix) + len(req.p2_dynamic_tail)
        p_in = max(1, in_chars // 4)
        rec = UsageRecordV1(
            record_id=f"p4-{uuid.uuid4().hex[:12]}",
            decision_id=req.decision_id,
            stage=req.stage,
            transport=TransportChannel.AGY_CLI_PROXY,
            model_provider_id=req.model,
            thinking_level=req.thinking_level,
            request_status=RequestStatus.OK,
            provider_input_tokens=p_in,
            cache_read_tokens=c_hit,
            output_tokens=60,
            thinking_tokens=200,
            total_tokens=p_in + 260,
            input_chars=in_chars,
            output_chars=len(txt),
            latency_ms=10.0,
            timeout_ms=req.timeout_sec * 1000.0,
            prefix_hash=req.prefix_hash,
            context_hash=req.context_hash,
            snapshot_id=req.snapshot_id,
        )
        self.ledger.record_usage(rec)
        return LLMResponse(text=txt, usage=rec)

    async def count_tokens(
        self, model: str, text_parts: list[str], images: list[ImageAttachment] | None = None
    ) -> dict[str, int]:
        return {"text_tokens": 0, "image_tokens_media_res": 0, "total_tokens": 0}

    async def close(self) -> None:
        return None


def test_stage1_and_stage2_share_identical_prefix_hash_and_bytes(tmp_path: Path) -> None:
    assert PrefixPromptAssembler is PrefixContextAssembler
    bundle = _make_4pair_bundle()

    for include_images in (False, True):
        store = ThesisMemoryStore(tmp_path / f"thesis_{include_images}.sqlite3")
        try:
            asm = PrefixContextAssembler(
                serializer=MarketContextSerializer(),
                thesis_store=store,
                include_images=include_images,
            )
            s1 = asm.assemble_stage1(
                bundle=bundle,
                trigger_summary="HEARTBEAT_1H",
                current_seq=1,
                now_ts=3_600_000.0,
            )
            s2 = asm.assemble_stage2(
                bundle=bundle,
                candidate_symbol="BTCUSDT",
                stage1_summary={"action": "DRILL_DOWN", "candidate_symbol": "BTCUSDT"},
            )
            assert s1.resync_mode == ResyncMode.RESYNC
            assert s1.p0_static == s2.p0_static
            assert s1.p1_slow == s2.p1_slow
            assert s1.prefix_hash == s2.prefix_hash
            if include_images:
                assert len(s1.images) == 1 and s1.images[0].resolution == "medium"
                assert len(s2.images) == 1 and s2.images[0].resolution == "high"
            else:
                assert s1.images == [] and s2.images == []
        finally:
            store.close()


def test_intra_hour_cycles_preserve_p0_p1_bytes_until_hour_or_regime_change(tmp_path: Path) -> None:
    store = ThesisMemoryStore(tmp_path / "thesis.sqlite3")
    try:
        asm = PrefixContextAssembler(thesis_store=store, include_images=False)
        b1 = _make_4pair_bundle()
        b2 = _make_4pair_bundle()
        b2.snapshot_id = "snap-test-002"

        t_hour0 = 3_600_000.0
        p_resync = asm.assemble_stage1(
            bundle=b1, trigger_summary="T0", current_seq=10, regime_signature="UP", now_ts=t_hour0
        )
        assert p_resync.resync_mode == ResyncMode.RESYNC

        # Same hour, same regime, seq advances -> LOCAL_RECONSTRUCT with byte-identical P0+P1
        p_rec = asm.assemble_stage1(
            bundle=b2, trigger_summary="T1", current_seq=11, regime_signature="UP", now_ts=t_hour0 + 300.0
        )
        assert p_rec.resync_mode == ResyncMode.LOCAL_RECONSTRUCT
        assert p_rec.prefix_hash == p_resync.prefix_hash
        assert p_rec.p1_slow == p_resync.p1_slow

        # Same seq -> CACHE_HIT
        p_hit = asm.assemble_stage1(
            bundle=b2, trigger_summary="T2", current_seq=11, regime_signature="UP", now_ts=t_hour0 + 600.0
        )
        assert p_hit.resync_mode == ResyncMode.CACHE_HIT
        assert p_hit.prefix_hash == p_resync.prefix_hash

        # Regime flip -> RESYNC
        p_flip = asm.assemble_stage1(
            bundle=b2, trigger_summary="T3", current_seq=12, regime_signature="DOWN", now_ts=t_hour0 + 900.0
        )
        assert p_flip.resync_mode == ResyncMode.RESYNC

        # Next hour -> RESYNC
        p_next_hr = asm.assemble_stage1(
            bundle=b2, trigger_summary="T4", current_seq=13, regime_signature="DOWN", now_ts=t_hour0 + 3605.0
        )
        assert p_next_hr.resync_mode == ResyncMode.RESYNC
    finally:
        store.close()


def test_thesis_memory_append_only_wal_and_expiration(tmp_path: Path) -> None:
    db_path = tmp_path / "thesis_memory.sqlite3"
    store = ThesisMemoryStore(db_path)
    try:
        with store._lock:
            jm = store._conn.execute("PRAGMA journal_mode;").fetchone()[0]
            ts = store._conn.execute("PRAGMA temp_store;").fetchone()[0]
            sync = store._conn.execute("PRAGMA synchronous;").fetchone()[0]
        assert str(jm).lower() == "wal"
        assert int(ts) == 2  # MEMORY
        assert int(sync) == 1  # NORMAL

        assert store.render_append_only_block() == "THESIS_MEMORY_V1:EMPTY"

        dec1 = TradeDecisionV2(
            regime_read="1h bullish trend",
            primary_edge="BTC SHORT_LIQ @ 60656",
            counter_thesis="5m pullback",
            invalidation_price=59550.0,
            action="OPEN_LONG",
            symbol="BTCUSDT",
            confidence=0.76,
            order_type="MARKET",
            entry_price=60021.0,
            sl_price=59500.0,
            tp_price=60640.0,
            margin_vst=700.0,
            leverage=5,
        )
        store.upsert_from_decision(decision_id="d-1", decision=dec1, snapshot_id="s-1", seq=1)
        block1 = store.render_append_only_block()

        dec2 = TradeDecisionV2(
            regime_read="1h demand bounce",
            primary_edge="SOL passive LIMIT @ 142.4",
            counter_thesis="BTC drag",
            invalidation_price=141.0,
            action="OPEN_LONG",
            symbol="SOLUSDT",
            confidence=0.68,
            order_type="LIMIT",
            entry_price=142.4,
            sl_price=140.9,
            tp_price=144.5,
            margin_vst=400.0,
            leverage=3,
        )
        store.upsert_from_decision(decision_id="d-2", decision=dec2, snapshot_id="s-2", seq=2)
        block2 = store.render_append_only_block()

        # Append-only invariant: block2 starts with exact bytes of block1
        assert block2.startswith(block1)
        assert "d-2|SOLUSDT|LONG|ACTIVE" in block2

        # compact_expired expires stale ACTIVE theses
        future_utc = datetime.now(timezone.utc) + timedelta(hours=13)
        expired_cnt = store.compact_expired(now_utc=future_utc)
        assert expired_cnt == 2
        block3 = store.render_append_only_block()
        assert "EXPIRED" in block3
    finally:
        store.close()


def test_snapshot_store_all_resync_transitions_and_eviction_monitor() -> None:
    ss = SnapshotStore(max_consecutive_misses=3)
    t0 = 7200.0
    assert ss.resolve_mode(now_ts=t0, current_seq=1, regime_signature="FLAT") == ResyncMode.RESYNC

    cp = ss.commit_checkpoint(
        snapshot_id="s1", now_ts=t0, seq=5, p1_text="P1_TEXT", regime_signature="FLAT"
    )
    assert ss.resolve_mode(now_ts=t0 + 10, current_seq=5, regime_signature="FLAT") == ResyncMode.CACHE_HIT
    assert ss.resolve_mode(now_ts=t0 + 10, current_seq=6, regime_signature="FLAT") == ResyncMode.LOCAL_RECONSTRUCT
    assert ss.resolve_mode(now_ts=t0 + 10, current_seq=4, regime_signature="FLAT") == ResyncMode.RESYNC  # seq rollback
    assert ss.resolve_mode(
        now_ts=t0 + 10, current_seq=5, regime_signature="FLAT", expected_base_hash="badhash"
    ) == ResyncMode.RESYNC

    # 3 consecutive Stage-2 misses trigger eviction alert -> RESYNC
    assert ss.observe_cache_result(0, prompt_tokens=8000, stage="stage2_drill") is False
    assert ss.observe_cache_result(0, prompt_tokens=8000, stage="stage2_drill") is False
    assert ss.observe_cache_result(0, prompt_tokens=8000, stage="stage2_drill") is True
    assert ss.resolve_mode(now_ts=t0 + 20, current_seq=5, regime_signature="FLAT") == ResyncMode.RESYNC

    # Commit resets misses
    ss.commit_checkpoint(
        snapshot_id="s2", now_ts=t0 + 25, seq=6, p1_text=cp.p1_text, regime_signature="FLAT"
    )
    assert ss.eviction_monitor.alert_active is False


def test_implicit_cache_eviction_monitor_metrics_and_explicit_rejection() -> None:
    mon = ImplicitCacheEvictionMonitor(max_consecutive_stage2_misses=3)
    mon.observe(10000, 8000, stage="stage2_drill")
    mon.observe(10000, 0, stage="stage2_drill")
    assert mon.consecutive_stage2_misses == 1
    assert mon.cache_token_fraction == 0.4  # 8000 / 20000
    assert mon.cache_request_hit_rate == 0.5  # 1 / 2

    with pytest.raises(ValueError, match="Explicit caching"):
        mon.reject_explicit_caching()


def test_gemini4_token_thresholds_on_real_btc_fixture(tmp_path: Path) -> None:
    store = ThesisMemoryStore(tmp_path / "thesis_tok.sqlite3")
    try:
        asm = PrefixContextAssembler(thesis_store=store, include_images=False)
        bundle = _make_4pair_bundle()
        s1 = asm.assemble_stage1(bundle=bundle, trigger_summary="THRESHOLD_CHECK")
        s2 = asm.assemble_stage2(
            bundle=bundle,
            candidate_symbol="BTCUSDT",
            stage1_summary={"action": "DRILL_DOWN", "candidate_symbol": "BTCUSDT"},
        )
        p0_tok = estimate_gemini_sp_tokens(s1.p0_static)
        p1_tok = estimate_gemini_sp_tokens(s1.p1_slow)
        p0_p1_tok = p0_tok + p1_tok
        p2_s2_tok = estimate_gemini_sp_tokens(s2.p2_dynamic)

        assert p0_tok >= 4_096
        assert 4_096 <= p0_p1_tok <= 9_500
        assert p2_s2_tok >= 2_050

        asm_pb = PrefixContextAssembler(
            thesis_store=store,
            include_images=False,
            extra_reference_playbook=QUANTUMEDGE_INTER_HOUR_PLAYBOOK,
        )
        p0_pb_tok = estimate_gemini_sp_tokens(asm_pb.p0_static)
        assert p0_pb_tok > p0_tok >= 4_096
    finally:
        store.close()


def test_orchestrator_with_prefix_assembler_updates_thesis_and_cache_monitor(
    tmp_path: Path, capsys: Any
) -> None:
    u_ledger = UsageLedger(tmp_path / "usage.sqlite3")
    o_ledger = OrderLedger(tmp_path / "orders.sqlite3")
    t_store = ThesisMemoryStore(tmp_path / "thesis.sqlite3")
    paper = PaperExchangeClient()
    bridge = HarnessBridge(LeanExecutor(paper, o_ledger, LeanRiskGuard()), tmp_path / "journal.jsonl")
    asm = PrefixContextAssembler(thesis_store=t_store, include_images=False)

    s1_reply = json.dumps(
        {
            "market_regime_summary": "BTC 1h constructive with SHORT_LIQ @ 60656.",
            "action": "DRILL_DOWN",
            "candidate_symbol": "BTCUSDT",
            "hold_blocker_code": "NONE",
            "candidate_rationale": "Strong composite +47 and tight spread.",
            "estimated_edge_bps": 90.0,
        }
    )
    s2_reply = json.dumps(
        {
            "regime_read": "1h/15m constructive range breakout toward upper liquidity",
            "primary_edge": "Composite +47, tight 0.28bps spread, SHORT_LIQ @ 60656",
            "counter_thesis": "5m pullback to 59880 before continuation",
            "invalidation_price": 59550.0,
            "action": "OPEN_LONG",
            "symbol": "BTCUSDT",
            "hold_blocker_code": "NONE",
            "thesis_status": "NEW",
            "confidence": 0.76,
            "evidence_refs": ["BTCUSDT:1h", "BTCUSDT:liqmap"],
            "order_type": "MARKET",
            "entry_price": 60021.0,
            "sl_price": 59500.0,
            "tp_price": 60630.0,
            "margin_vst": 650.0,
            "leverage": 5,
            "qty": None,
        }
    )
    transport = _ScriptedCacheTransport(u_ledger, [s1_reply, s2_reply], [0, 7200])
    orch = DeterministicTradingOrchestrator(
        transport=transport, ledger=u_ledger, bridge=bridge, assembler=asm
    )
    try:
        audit = asyncio.run(
            orch.run_decision_cycle(
                _make_4pair_bundle(),
                trigger_summary="PHASE4_E2E",
                dry_run=False,
                current_seq=1,
                regime_signature="FLAT_UP",
            )
        )
        assert audit.validator_passed is True
        assert len(paper.submitted_orders) == 1
        assert asm.snapshot_store.eviction_monitor.total_cache_read_tokens == 7200
        assert asm.snapshot_store.eviction_monitor.alert_active is False
        assert "BTCUSDT|LONG|ACTIVE" in t_store.render_append_only_block()

        # Verify CLI run-cycle defaults to PrefixContextAssembler in mock dry-run
        rc = harness_cli.main(
            [
                "run-cycle",
                "--transport",
                "mock",
                "--dry-run",
                "--ledger-db",
                str(tmp_path / "cli_u.sqlite3"),
                "--order-ledger-db",
                str(tmp_path / "cli_o.sqlite3"),
                "--journal-path",
                str(tmp_path / "cli_j.jsonl"),
                "--thesis-db",
                str(tmp_path / "cli_t.sqlite3"),
            ]
        )
        assert rc == 0
        cli_out = json.loads(capsys.readouterr().out.strip())
        assert cli_out["resync_mode"] == "RESYNC"
        assert cli_out["cache_eviction_alert"] is False
    finally:
        u_ledger.close()
        t_store.close()


def test_stage2_no_duplicate_ohlcv_and_intra_hour_thesis_delta_and_compact_forecast(
    tmp_path: Path,
) -> None:
    store = ThesisMemoryStore(tmp_path / "thesis_delta.sqlite3")
    try:
        asm = PrefixContextAssembler(thesis_store=store, include_images=False)
        bundle = _make_4pair_bundle()
        fc = ForecastBundleV1(
            symbol="BTCUSDT",
            horizon="15m",
            regime_probs={"UP": 0.65, "RANGE": 0.25, "DOWN": 0.10},
            regime_maturity="ESTABLISHED",
            regime_entropy=0.48,
            cond_vol_bps=22.4,
            vol_zscore=0.35,
            return_quantiles_bps={
                "q05": -35.0,
                "q10": -22.0,
                "q25": -8.0,
                "q50": 6.5,
                "q75": 21.0,
                "q90": 38.0,
                "q95": 49.0,
            },
            barrier_probs={"up_first": 0.61, "down_first": 0.21, "neither": 0.18},
            expected_mfe_bps={"q50": 31.0, "q90": 64.0},
            expected_mae_bps={"q50": -14.0, "q90": -28.0},
            tsfm_disagreement=0.12,
            disagreement_level="LOW",
            aci_alpha_t=0.19,
        )

        # Cycle 1 (RESYNC)
        s1 = asm.assemble_stage1(
            bundle=bundle,
            trigger_summary="HEARTBEAT_1H",
            forecasts_by_symbol={"BTCUSDT": fc},
            current_seq=1,
            now_ts=3_600_000.0,
        )
        s2 = asm.assemble_stage2(
            bundle=bundle,
            candidate_symbol="BTCUSDT",
            stage1_summary={"action": "DRILL_DOWN", "candidate_symbol": "BTCUSDT"},
            forecast=fc,
        )

        # 1. Stage 2 text-only has NO duplicate DELTA_BPS_V1 on 15m/5m/1m (or 4h/1h), keeps PROF + OHLCV_TICK_V1 + LIQMAP + L2
        for tf in ("4h", "1h", "15m", "5m", "1m"):
            assert f"DELTA_BPS_V1|BTCUSDT|{tf}|" not in s2.p2_dynamic
            assert f"PROF_{tf}:" in s2.p2_dynamic
        for tf in ("15m", "5m", "1m"):
            assert f"OHLCV_TICK_V1|BTCUSDT|{tf}|" in s2.p2_dynamic
        assert "LIQMAP|BTCUSDT|" in s2.p2_dynamic
        assert "L2_TOP15|" in s2.p2_dynamic
        assert estimate_gemini_sp_tokens(s2.p2_dynamic) >= 2_050

        # 2. ForecastBundleV1 renders via compact FORECAST_V1| line in both Stage 1 and Stage 2
        assert "FORECAST_V1|BTCUSDT|15m|" in s1.p2_dynamic
        assert "FORECAST_V1|BTCUSDT|15m|" in s2.p2_dynamic
        assert "return_quantiles_bps" not in s2.p2_dynamic
        assert "INTRA_HOUR_THESIS_DELTA" not in s1.p2_dynamic
        assert "INTRA_HOUR_THESIS_DELTA" not in s2.p2_dynamic

        # 3. Intra-hour thesis upsert must show up in P2 WITHOUT mutating P0+P1 prefix_hash!
        dec = TradeDecisionV2(
            regime_read="1h bullish trend continuation",
            primary_edge="BTC SHORT_LIQ @ 60656 with tight 0.28bps spread",
            counter_thesis="5m retest to 59850",
            invalidation_price=59550.0,
            action="OPEN_LONG",
            symbol="BTCUSDT",
            confidence=0.77,
            order_type="MARKET",
            entry_price=60021.0,
            sl_price=59500.0,
            tp_price=60640.0,
            margin_vst=700.0,
            leverage=5,
        )
        store.upsert_from_decision(decision_id="d-intra-1", decision=dec, snapshot_id="snap-1", seq=1)

        s1_intra = asm.assemble_stage1(
            bundle=bundle,
            trigger_summary="WAKEUP_5M",
            forecasts_by_symbol={"BTCUSDT": fc.model_dump()},
            current_seq=2,
            now_ts=3_600_300.0,
        )
        s2_intra = asm.assemble_stage2(
            bundle=bundle,
            candidate_symbol="BTCUSDT",
            stage1_summary={"action": "MANAGE_OPEN_POSITION", "candidate_symbol": "BTCUSDT"},
            forecast=fc.model_dump(),
        )
        assert s1_intra.resync_mode == ResyncMode.LOCAL_RECONSTRUCT
        assert s1_intra.prefix_hash == s1.prefix_hash
        assert s1_intra.p1_slow == s1.p1_slow
        assert "INTRA_HOUR_THESIS_DELTA|" in s1_intra.p2_dynamic
        assert "d-intra-1|BTCUSDT|LONG|ACTIVE" in s1_intra.p2_dynamic
        assert "INTRA_HOUR_THESIS_DELTA|" in s2_intra.p2_dynamic
        assert "d-intra-1|BTCUSDT|LONG|ACTIVE" in s2_intra.p2_dynamic
    finally:
        store.close()
