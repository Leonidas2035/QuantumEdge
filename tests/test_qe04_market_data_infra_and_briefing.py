"""Development #2 Stage QE-04 tests: BingX API RateLimiter, OhlcvRamTtlCache, Adaptive Watcher, WS-First HubSnapshotBridge, RAM ReplayStore, 15m HMM + Macro-Beta Sizer, and qe_briefing CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import tools.qe_briefing as qe_briefing
import tools.run_vst_session as session_runner
from quantum_edge_core.contracts import (
    DecisionAuditV1,
    ForecastBundleV1,
    RequestStatus,
    ThinkingLevel,
    TradeDecisionV2,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution import (
    ConvictionPositionSizer,
    HarnessBridge,
    LeanExecutor,
    LeanRiskGuard,
    OrderLedger,
    PositionProtectionWatcher,
    RiskConfig,
)
from quantum_edge_core.execution.bingx_vst_adapter import (
    ApiBudgetStats,
    BingXRestRateLimiter,
    OhlcvRamTtlCache,
    fetch_full_vst_context,
    invalidate_vst_status_cache,
)
from quantum_edge_core.market_data.hub_bridge import HubSnapshotBridge
from quantum_edge_core.market_data.replay_store import (
    MarketReplayReader,
    MarketSnapshotRecorder,
)
from quantum_edge_core.orchestrator import (
    PreFetchCoordinator,
    PreFetchedBundle,
    ThesisMemoryStore,
    build_p0_static_prefix,
)
from quantum_edge_core.orchestrator.deterministic_orchestrator import (
    LocalForecastProvider,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger
from tools.benchmark_count_tokens import estimate_gemini_sp_tokens


def _make_raw_ohlcv(base: float, n: int = 100, step_ms: int = 900_000, drift: float = 0.0003) -> list[list[float]]:
    t0 = 1_795_600_000_000
    rows: list[list[float]] = []
    px = base
    for i in range(n):
        nxt = px * (1.0 + drift + (0.0012 if i % 2 == 0 else -0.0009))
        hi = max(px, nxt) * 1.002
        lo = min(px, nxt) * 0.998
        rows.append([float(t0 + i * step_ms), round(px, 4), round(hi, 4), round(lo, 4), round(nxt, 4), 100.0 + i])
        px = nxt
    return rows


def test_ohlcv_ram_ttl_cache_prevents_repeated_1d_4h_rest_fetches() -> None:
    class _CountingExchange:
        _qe_sandbox_verified = True

        def __init__(self) -> None:
            self.ohlcv_calls_by_tf: dict[str, int] = {}

        def fetch_balance(self) -> dict[str, Any]:
            return {"VST": {"free": 1000.0, "total": 1000.0, "used": 0.0}}

        def fetch_positions(self) -> list[dict[str, Any]]:
            return []

        def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            _ = symbol
            return []

        def fetch_ticker(self, symbol: str) -> dict[str, Any]:
            _ = symbol
            return {"bid": 609.9, "ask": 610.1, "last": 610.0}

        def fetch_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any]:
            _ = (symbol, limit)
            return {
                "bids": [[609.9 - i * 0.1, 3.0] for i in range(15)],
                "asks": [[610.1 + i * 0.1, 3.0] for i in range(15)],
                "timestamp": 1_795_641_840_000,
            }

        def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> list[list[float]]:
            _ = symbol
            self.ohlcv_calls_by_tf[timeframe] = self.ohlcv_calls_by_tf.get(timeframe, 0) + 1
            return _make_raw_ohlcv(610.0, n=limit)

    client = _CountingExchange()
    stats = ApiBudgetStats()
    cache = OhlcvRamTtlCache()
    coord = PreFetchCoordinator(
        client,
        symbols=("BNBUSDT",),
        include_btc_leader=True,
        ohlcv_cache=cache,
        api_stats=stats,
    )

    b1 = asyncio.run(coord.fetch_all(include_analytics=False))
    calls_after_1 = stats.snapshot()["rest_calls_total"]
    assert client.ohlcv_calls_by_tf["1d"] == 1
    assert client.ohlcv_calls_by_tf["4h"] == 1

    # Immediate second cycle within TTL: 1d, 4h, 1h, 15m, 5m (+ BTC 15m/1h) served straight from RAM
    b2 = asyncio.run(coord.fetch_all(include_analytics=False))
    snap = stats.snapshot()
    assert b1.contexts_by_symbol["BNBUSDT"]["ohlcv_by_tf"]["1d"] == b2.contexts_by_symbol["BNBUSDT"]["ohlcv_by_tf"]["1d"]
    assert client.ohlcv_calls_by_tf["1d"] == 1
    assert client.ohlcv_calls_by_tf["4h"] == 1
    assert snap["rest_cache_hits"] >= 5
    # Second cycle makes far fewer REST calls than first cycle
    calls_in_cycle_2 = snap["rest_calls_total"] - calls_after_1
    assert calls_in_cycle_2 < calls_after_1


def test_rate_limiter_retries_bingx_100410_and_tracks_stats() -> None:
    slept: list[float] = []
    stats = ApiBudgetStats()
    limiter = BingXRestRateLimiter(
        rate_per_sec=100.0,
        burst=4,
        stats=stats,
        sleep_fn=lambda d: slept.append(float(d)),
        base_backoff_sec=0.20,
    )
    state = {"n": 0}

    def _flaky_endpoint() -> dict[str, Any]:
        state["n"] += 1
        if state["n"] <= 2:
            raise RuntimeError("bingx error code=100410 Too Many Requests frequency limit")
        return {"ok": True}

    out = limiter.call_with_rate_limit(_flaky_endpoint, max_retries=2)
    assert out == {"ok": True}
    snap = stats.snapshot()
    assert snap["rest_calls_total"] == 3
    assert snap["rate_limit_retries"] == 2
    assert len(slept) == 2 and all(s > 0 for s in slept)


def test_position_protection_uses_lightweight_fetch_and_adaptive_idle_interval(
    tmp_path: Path,
) -> None:
    class _WatchClient:
        _qe_sandbox_verified = True

        def __init__(self) -> None:
            self.balance_calls = 0
            self.pos_calls = 0
            self.positions: list[dict[str, Any]] = []
            self.orders: list[dict[str, Any]] = []

        def fetch_balance(self) -> dict[str, Any]:
            self.balance_calls += 1
            return {"VST": {"free": 1000.0, "total": 1000.0, "used": 0.0}}

        def fetch_positions(self) -> list[dict[str, Any]]:
            self.pos_calls += 1
            return list(self.positions)

        def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            _ = symbol
            return list(self.orders)

    invalidate_vst_status_cache()
    client = _WatchClient()
    ledger = OrderLedger(tmp_path / "prot_qe04.sqlite3")
    executor = LeanExecutor(client, ledger, LeanRiskGuard(RiskConfig.for_qe_isolated_50x()))
    bridge = HarnessBridge(executor, journal_path=tmp_path / "prot_qe04.jsonl")
    watcher = PositionProtectionWatcher(
        client,
        bridge,
        allowed_symbols=("BNBUSDT", "LINKUSDT", "SUIUSDT"),
        interval_sec=5.0,
        idle_interval_sec=25.0,
        dry_run=False,
    )
    watcher.touch_feed_heartbeat()

    # 1. FLAT state -> 0 fetch_balance calls, IDLE mode, 25s sleep
    r_idle = asyncio.run(watcher.check_once())
    assert client.balance_calls == 0
    assert client.pos_calls == 1
    assert r_idle["watcher_mode"] == "IDLE"
    assert r_idle["next_sleep_sec"] == 25.0

    # 2. Active position -> 0 fetch_balance calls, ACTIVE mode, 5s sleep
    client.positions.append(
        {
            "symbol": "SUI/USDT:USDT",
            "side": "long",
            "contracts": 50.0,
            "entryPrice": 1.2000,
            "markPrice": 1.2010,
            "liquidationPrice": 0.9000,
            "info": {"positionSide": "LONG"},
        }
    )
    r_act = asyncio.run(watcher.check_once())
    assert client.balance_calls == 0
    assert r_act["watcher_mode"] == "ACTIVE"
    assert r_act["next_sleep_sec"] == 5.0


def test_hub_snapshot_bridge_ingests_zmq_payload_and_serves_fresh_ram_snapshot_with_rest_fallback() -> None:
    class _RestFallbackClient:
        _qe_sandbox_verified = True

        def __init__(self) -> None:
            self.ob_calls = 0
            self.ticker_calls = 0

        def fetch_balance(self) -> dict[str, Any]:
            return {"VST": {"free": 1000.0, "total": 1000.0, "used": 0.0}}

        def fetch_positions(self) -> list[dict[str, Any]]:
            return []

        def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            _ = symbol
            return []

        def fetch_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any]:
            _ = (symbol, limit)
            self.ob_calls += 1
            return {
                "bids": [[15.0, 20.0], [14.99, 20.0]],
                "asks": [[15.02, 20.0], [15.03, 20.0]],
                "timestamp": 1_795_641_840_000,
            }

        def fetch_ticker(self, symbol: str) -> dict[str, Any]:
            _ = symbol
            self.ticker_calls += 1
            return {"bid": 15.0, "ask": 15.02, "last": 15.01}

        def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> list[list[float]]:
            _ = symbol
            return _make_raw_ohlcv(15.0, n=limit)

    bridge = HubSnapshotBridge(default_max_age_sec=10.0)
    bridge.ingest_message(
        "orderbook.LINKUSDT",
        {
            "symbol": "LINK-USDT",
            "bids": [[15.10, 50.0], [15.09, 30.0]],
            "asks": [[15.12, 40.0], [15.13, 25.0]],
        },
        now_ts=1000.0,
    )
    bridge.ingest_message(
        "trade.LINKUSDT",
        {"symbol": "LINKUSDT", "price": 15.115},
        now_ts=1001.0,
    )
    fresh = bridge.get_fresh_snapshot("LINKUSDT", now_ts=1005.0)
    assert fresh is not None
    assert fresh["last_price"] == 15.115
    assert fresh["bid"] == 15.10
    assert fresh["ask"] == 15.12
    assert len(bridge.get_ring_history("LINKUSDT")) == 2

    # Stale snapshot (> 10s old) returns None cleanly
    assert bridge.get_fresh_snapshot("LINKUSDT", now_ts=1020.0) is None

    # Live now_ts ingest -> fetch_full_vst_context reads OB & ticker from RAM (0 REST ob/ticker calls!)
    bridge.ingest_message(
        "orderbook.LINKUSDT",
        {
            "symbol": "LINKUSDT",
            "price": 15.25,
            "bids": [[15.24, 50.0], [15.23, 40.0]],
            "asks": [[15.26, 45.0], [15.27, 35.0]],
        },
    )
    client = _RestFallbackClient()
    stats = ApiBudgetStats()
    cache = OhlcvRamTtlCache()
    ctx_ws = fetch_full_vst_context(
        client,
        "LINKUSDT",
        include_analytics=False,
        ohlcv_cache=cache,
        market_bridge=bridge,
        shared_vst_status={"balance_vst": {"free": 1000.0, "total": 1000.0, "used": 0.0}, "open_positions": [], "open_orders": []},
        api_stats=stats,
    )
    assert ctx_ws.market_snapshot.source == "hub_ws_ram"
    assert ctx_ws.market_snapshot.last_price == 15.25
    assert client.ob_calls == 0
    assert client.ticker_calls == 0
    assert stats.snapshot()["ws_updates_applied"] == 1

    # Unpopulated symbol SUIUSDT seamlessly falls back to REST
    ctx_rest = fetch_full_vst_context(
        client,
        "SUIUSDT",
        include_analytics=False,
        ohlcv_cache=cache,
        market_bridge=bridge,
        shared_vst_status={"balance_vst": {"free": 1000.0, "total": 1000.0, "used": 0.0}, "open_positions": [], "open_orders": []},
        api_stats=stats,
    )
    assert ctx_rest.market_snapshot.source == "bingx_vst_sandbox"
    assert client.ob_calls == 1
    assert client.ticker_calls == 1


def test_replay_store_ram_buffered_roundtrip_zero_immediate_ssd_write(
    tmp_path: Path,
) -> None:
    replay_file = tmp_path / "replay_test.jsonl"
    rec = MarketSnapshotRecorder(path=replay_file, flush_max_items=10, flush_interval_sec=300.0)
    b1 = PreFetchedBundle(
        snapshot_id="snap-rep-1",
        vst_status={"balance_vst": {"free": 1000.0}},
        contexts_by_symbol={"BNBUSDT": {"market_snapshot": {"last_price": 612.5}}},
        fetch_ms=4.2,
        market_leader={"symbol": "BTCUSDT", "px": 95000.0, "chg_4h_bps": -180.0},
    )
    b2 = PreFetchedBundle(
        snapshot_id="snap-rep-2",
        vst_status={"balance_vst": {"free": 995.0}},
        contexts_by_symbol={"BNBUSDT": {"market_snapshot": {"last_price": 614.0}}},
        fetch_ms=1.1,
        market_leader={"symbol": "BTCUSDT", "px": 95120.0, "chg_4h_bps": -160.0},
    )
    assert rec.record_bundle(b1) is False
    assert rec.record_bundle(b2) is False
    # Zero immediate disk writes before flush/close!
    assert not replay_file.exists()
    assert rec.pending_count == 2

    flushed = rec.close()
    assert flushed == 2
    assert replay_file.is_file()

    loaded = MarketReplayReader(replay_file).load_bundles()
    assert [b.snapshot_id for b in loaded] == ["snap-rep-1", "snap-rep-2"]
    assert loaded[0].market_leader == {"symbol": "BTCUSDT", "px": 95000.0, "chg_4h_bps": -180.0}
    assert loaded[1].contexts_by_symbol["BNBUSDT"]["market_snapshot"]["last_price"] == 614.0


def test_local_forecast_provider_prefers_15m_over_5m_and_macro_beta_penalizes_countertrend_long_on_btc_dump() -> None:
    p0_text = build_p0_static_prefix()
    assert "Macro-Beta Alignment" in p0_text
    assert estimate_gemini_sp_tokens(p0_text) >= 4_096

    # 1. LocalForecastProvider defaults to 15m and picks 15m when both 5m and 15m are present
    provider = LocalForecastProvider()
    assert provider.timeframe == "15m"
    bars_5m = [
        {"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
        for r in _make_raw_ohlcv(15.0, n=80, step_ms=300_000, drift=0.0005)
    ]
    bars_15m = [
        {"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
        for r in _make_raw_ohlcv(15.0, n=80, step_ms=900_000, drift=-0.0008)
    ]
    bundle = PreFetchedBundle(
        snapshot_id="snap-15m-pref",
        vst_status={"balance_vst": {"free": 1000.0}, "open_positions": [], "open_orders": []},
        contexts_by_symbol={"LINKUSDT": {"ohlcv_by_tf": {"5m": bars_5m, "15m": bars_15m}}},
        fetch_ms=1.0,
    )
    fcs = provider.build_forecasts(bundle, now_monotonic=10.0)
    assert "LINKUSDT" in fcs
    assert provider.last_used_tf_by_symbol["LINKUSDT"] == "15m"

    # 2. Macro-Beta sizer filter: BTC 4h = -250 bps & 1d di_diff = -20 penalizes OPEN_LONG and boosts OPEN_SHORT
    sizer = ConvictionPositionSizer()
    risk_cfg = RiskConfig.for_qe_isolated_50x()
    ctx = {
        "market_snapshot": {"symbol": "LINKUSDT", "last_price": 10.0, "spread_bps": 10.0},
        "indicators": {"1d_di_diff": -20.0},
        "microstructure_and_liquidity": {
            "orderflow": {"imbalance": 0.0},
            "composite_signal": {"score": 0.0},
            "liqmap": {"status": "NONE"},
        },
    }
    fc_neutral = {
        "regime_probs": {"UP": 0.34, "RANGE": 0.32, "DOWN": 0.34},
        "regime_maturity": "ESTABLISHED",
        "vol_zscore": 0.5,
        "expected_mae_bps": {"q90": -60.0},
        "barrier_probs": {"up_first": 0.45, "down_first": 0.45, "neither": 0.10},
    }
    btc_dump = {"symbol": "BTCUSDT", "px": 89000.0, "chg_4h_bps": -250.0, "trend": "BEAR"}

    dec_long = TradeDecisionV2(
        regime_read="Testing countertrend long under BTC dump.",
        primary_edge="Local bounce attempt against HTF downtrend.",
        counter_thesis="BTC 4h dump continues.",
        invalidation_price=9.85,
        action="OPEN_LONG",
        symbol="LINKUSDT",
        confidence=0.70,
        order_type="LIMIT",
        entry_price=10.0,
        sl_price=9.85,
        tp_price=10.30,
        margin_vst=500.0,
        leverage=20,
    )
    dec_short = TradeDecisionV2(
        regime_read="Testing trend-aligned short under BTC dump.",
        primary_edge="BTC -250bps 4h impulse + bearish 1d DI.",
        counter_thesis="Short-term oversold squeeze.",
        invalidation_price=10.15,
        action="OPEN_SHORT",
        symbol="LINKUSDT",
        confidence=0.70,
        order_type="LIMIT",
        entry_price=10.0,
        sl_price=10.15,
        tp_price=9.70,
        margin_vst=500.0,
        leverage=20,
    )

    sz_long_no_btc = sizer.size_order(decision=dec_long, ctx=ctx, forecast=fc_neutral, risk_config=risk_cfg)
    sz_long_dump = sizer.size_order(
        decision=dec_long, ctx=ctx, forecast=fc_neutral, risk_config=risk_cfg, market_leader=btc_dump
    )
    sz_short_dump = sizer.size_order(
        decision=dec_short, ctx=ctx, forecast=fc_neutral, risk_config=risk_cfg, market_leader=btc_dump
    )

    assert sz_long_dump.risk_score > sz_long_no_btc.risk_score
    assert sz_long_dump.notional_vst < sz_long_no_btc.notional_vst
    assert sz_short_dump.notional_vst > sz_long_dump.notional_vst


def test_qe_briefing_cli_renders_human_and_json_briefing_from_ram_session_and_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    latest_ram = tmp_path / "latest_session.json"
    ledger_db = tmp_path / "brief_usage.sqlite3"
    thesis_db = tmp_path / "brief_thesis.sqlite3"
    journal_path = tmp_path / "brief_journal.jsonl"

    args = session_runner.build_parser().parse_args(
        [
            "--transport",
            "mock",
            "--dry-run",
            "--duration-minutes",
            "0.02",
            "--cycle-interval-sec",
            "0.20",
            "--ledger-db",
            str(ledger_db),
            "--order-ledger-db",
            str(tmp_path / "brief_orders.sqlite3"),
            "--thesis-db",
            str(thesis_db),
            "--journal-path",
            str(journal_path),
            "--session-summary-ram",
            str(tmp_path / "sess_out.json"),
            "--latest-session-ram",
            str(latest_ram),
        ]
    )
    summary = asyncio.run(session_runner.run_persistent_session(args))
    assert latest_ram.is_file()
    assert "api_stats" in summary
    capsys.readouterr()  # clear session stdout

    # 1. Human-readable Ukrainian Markdown briefing
    rc_md = qe_briefing.main(
        [
            "--session-json",
            str(latest_ram),
            "--ledger-db",
            str(ledger_db),
            "--thesis-db",
            str(thesis_db),
            "--journal",
            str(journal_path),
            "--last",
            "3",
        ]
    )
    assert rc_md == 0
    md_out = capsys.readouterr().out
    assert "# QuantumEdge Hybrid — Оперативний Бріфінг Сесії" in md_out
    assert "## 2. Останні рішення агента" in md_out
    assert "## 3. Токен-економіка (UsageLedger) та Навантаження BingX API" in md_out
    assert "Прогноз 1 год:" in md_out

    # 2. Structured --json mode for programmatic / AGY TUI consumption
    rc_json = qe_briefing.main(
        [
            "--session-json",
            str(latest_ram),
            "--ledger-db",
            str(ledger_db),
            "--thesis-db",
            str(thesis_db),
            "--journal",
            str(journal_path),
            "--json",
        ]
    )
    assert rc_json == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["session_found"] is True
    assert parsed["status"] == "FINISHED"
    assert len(parsed["recent_decisions"]) >= 1
    assert parsed["projections"]["iecu_1h_proj"] >= 0.0
    assert "rest_calls_total" in parsed["api_stats"]
