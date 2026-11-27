"""Phase 5 tests: Hawkes O(1), CUSUM, LiqMagnet spread filter, 700ms coalescing, RAM ShadowLogger, ZMQ SUB, PositionProtectionWatcher, and 72h shadow replay."""

from __future__ import annotations

import ast
import asyncio
import json
import math
from pathlib import Path
from typing import Any

import msgspec
import numpy as np
import pytest
import zmq
import zmq.asyncio

import tools.eval_wakeup_shadow as shadow_eval
import tools.qe_harness_cli as harness_cli
from quantum_edge_core.contracts import DecisionEventV1
from quantum_edge_core.execution import (
    HarnessBridge,
    LeanExecutor,
    LeanRiskGuard,
    OrderLedger,
    PaperExchangeClient,
    PositionProtectionWatcher,
)
from quantum_edge_core.market_data.wakeup_gate import (
    EventDrivenWakeupGate,
    HawkesTracker,
    ShadowWakeupLogger,
    SymmetricCusumFilter,
)


def test_hawkes_o1_matches_exact_on_sum() -> None:
    with pytest.raises(ValueError, match="Non-stationary"):
        HawkesTracker(mu=2.0, alpha=1.6, beta=1.5)

    rng = np.random.default_rng(2026)
    dts = rng.exponential(scale=0.35, size=200)
    times = np.cumsum(dts) + 1000.0

    mu, alpha, beta = 2.0, 0.9, 1.5
    tracker = HawkesTracker(mu=mu, alpha=alpha, beta=beta)
    max_err = 0.0
    for k in range(len(times)):
        t_k = float(times[k])
        o1_ratio = tracker.on_trade(t_k)
        # Exact O(N) sum over all arrivals i = 0..k
        exact_sum = float(np.sum(np.exp(-beta * (t_k - times[: k + 1]))))
        exact_ratio = (mu + alpha * exact_sum) / mu
        max_err = max(max_err, abs(o1_ratio - exact_ratio))

    assert max_err < 1e-12


def test_symmetric_cusum_detects_up_and_down_and_resets() -> None:
    filt = SymmetricCusumFilter(k_mult=3.0, min_sigma=0.0005)
    assert filt.update(100.0) is None
    # Upward jump > 15 bps triggers CUSUM_UP and resets s_pos to 0
    assert filt.update(100.30) == "CUSUM_UP"
    assert filt.s_pos == 0.0

    # Downward jump triggers CUSUM_DOWN and resets s_neg to 0
    filt2 = SymmetricCusumFilter(k_mult=3.0, min_sigma=0.0005)
    assert filt2.update(100.0) is None
    assert filt2.update(99.70) == "CUSUM_DOWN"
    assert filt2.s_neg == 0.0


def test_liq_magnet_filters_alt_spread_noise_and_passes_btc_and_large_alt() -> None:
    # SOLUSDT spread=26 bps, dist=28 bps (< 2.5 * 26 = 65 bps) -> rejected as spread noise
    assert (
        EventDrivenWakeupGate.check_liq_magnet(
            dist_bps=28.0, spread_bps=26.0, atr_15m_bps=65.0, cluster_usd=750_000.0
        )
        is False
    )
    # SOLUSDT spread=26 bps, dist=72 bps (<= 1.2 * 65 = 78 bps and >= 65 bps) -> passes
    assert (
        EventDrivenWakeupGate.check_liq_magnet(
            dist_bps=72.0, spread_bps=26.0, atr_15m_bps=65.0, cluster_usd=750_000.0
        )
        is True
    )
    # BTCUSDT spread=0.25 bps, dist=28 bps -> passes
    assert (
        EventDrivenWakeupGate.check_liq_magnet(
            dist_bps=28.0, spread_bps=0.25, atr_15m_bps=35.0, cluster_usd=800_000.0
        )
        is True
    )
    # Cluster < $500k -> rejected
    assert (
        EventDrivenWakeupGate.check_liq_magnet(
            dist_bps=20.0, spread_bps=0.25, atr_15m_bps=35.0, cluster_usd=300_000.0
        )
        is False
    )


def test_700ms_coalescing_merges_four_simultaneous_triggers_into_one_event(
    tmp_path: Path,
) -> None:
    events: list[DecisionEventV1] = []

    async def _on_ev(ev: DecisionEventV1) -> None:
        events.append(ev)

    logger = ShadowWakeupLogger(tmp_path / "shadow.jsonl", flush_max_items=50)
    gate = EventDrivenWakeupGate(
        _on_ev,
        coalesce_ms=80,  # Shortened wall-clock sleep for fast deterministic unit test
        per_symbol_cooldown_sec=90.0,
        shadow_logger=logger,
    )

    async def _run() -> None:
        t0 = 1_795_644_050.0  # Not on hour boundary
        for idx, sym in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")):
            await gate.ingest_tick(
                symbol=sym,
                price=100.0 + idx,
                ts_sec=t0 + idx * 0.01,
                cvd_zscore_5m=3.1,
            )
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.15)
        logger.close()

    asyncio.run(_run())
    assert len(events) == 1
    assert events[0].symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    assert len(gate.get_recent_ticks("BTCUSDT")) == 1


def test_cooldown_90s_suppresses_normal_but_allows_critical_invalidation_and_minus_05r(
    tmp_path: Path,
) -> None:
    events: list[DecisionEventV1] = []

    async def _on_ev(ev: DecisionEventV1) -> None:
        events.append(ev)

    logger = ShadowWakeupLogger(tmp_path / "shadow_cd.jsonl", flush_max_items=50)
    gate = EventDrivenWakeupGate(
        _on_ev,
        coalesce_ms=700,
        per_symbol_cooldown_sec=90.0,
        virtual_clock=True,
        shadow_logger=logger,
    )

    async def _run() -> None:
        t0 = 1_795_644_050.0
        # 1. Initial normal wakeup
        await gate.ingest_tick(symbol="BTCUSDT", price=60000.0, ts_sec=t0, cvd_zscore_5m=3.0)
        await gate.flush_pending()
        assert len(events) == 1

        # 2. Normal trigger 20s later (< 90s cooldown) -> suppressed!
        await gate.ingest_tick(symbol="BTCUSDT", price=60010.0, ts_sec=t0 + 20.0, hmm_regime_delta=0.40)
        await gate.flush_pending()
        assert len(events) == 1

        # 3. CRITICAL thesis_invalidated 30s later -> bypasses 90s cooldown!
        await gate.ingest_tick(
            symbol="BTCUSDT", price=59500.0, ts_sec=t0 + 30.0, thesis_invalidated=True
        )
        await gate.flush_pending()
        assert len(events) == 2
        assert events[-1].priority == "CRITICAL"

        # 4. CRITICAL position_r_multiple <= -0.5 40s later -> bypasses cooldown!
        await gate.ingest_tick(
            symbol="BTCUSDT", price=59400.0, ts_sec=t0 + 40.0, position_r_multiple=-0.55
        )
        await gate.flush_pending()
        assert len(events) == 3
        assert events[-1].priority == "CRITICAL"
        logger.close()

    asyncio.run(_run())


def test_shadow_wakeup_logger_ram_buffered_no_immediate_ssd_write(tmp_path: Path) -> None:
    log_path = tmp_path / "ram_shadow.jsonl"
    logger = ShadowWakeupLogger(log_path, flush_max_items=5, flush_interval_sec=3600.0)

    logger.log(should_wake=True, did_wake=False, reasons=["R1"], symbols=["BTCUSDT"], metrics={})
    logger.log(should_wake=True, did_wake=False, reasons=["R2"], symbols=["ETHUSDT"], metrics={})
    assert logger.pending_count == 2
    assert not log_path.exists()  # Zero SSD writes before threshold/flush!

    flushed = logger.flush()
    assert flushed == 2
    assert logger.pending_count == 0
    assert log_path.is_file()
    assert len(log_path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_zmq_subscriber_ingests_inproc_or_tcp_pub_messages_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    events: list[DecisionEventV1] = []
    heartbeats: list[float] = []

    async def _on_ev(ev: DecisionEventV1) -> None:
        events.append(ev)

    logger = ShadowWakeupLogger(tmp_path / "zmq_shadow.jsonl", flush_max_items=50)
    gate = EventDrivenWakeupGate(
        _on_ev,
        coalesce_ms=30,
        shadow_logger=logger,
    )

    async def _run() -> None:
        ctx = zmq.asyncio.Context()
        pub = ctx.socket(zmq.PUB)
        port = pub.bind_to_random_port("tcp://127.0.0.1")
        endpoint = f"tcp://127.0.0.1:{port}"

        sub_task = asyncio.create_task(
            gate.run_zmq_subscriber(
                endpoint=endpoint,
                on_heartbeat_touch=heartbeats.append,
                zmq_context=ctx,
                poll_timeout_ms=30,
            )
        )
        try:
            await asyncio.sleep(0.08)  # Allow ZMQ SUB slow-joiner handshake
            # 1. Test real Hub depth.btcusdt JSON frame (OrderBookUpdate with imbalance_top10 >= 0.75)
            json_frame = json.dumps(
                {
                    "type": "OrderBookUpdate",
                    "symbol": "BTCUSDT",
                    "mid_price": 60100.0,
                    "spread": 1.5,
                    "imbalance_top10": 0.88,
                    "timestamp": 1795644055.0,
                }
            ).encode("utf-8")
            await pub.send_multipart([b"depth.btcusdt", json_frame])

            # 2. Test MessagePack frame on market.depth.ethusdt
            mp_frame = msgspec.msgpack.encode(
                {
                    "symbol": "ETHUSDT",
                    "mid": 2420.0,
                    "spread": 6.0,
                    "imbalance_top10": -0.82,
                    "ts_ns": 1_795_644_055_010_000_000,
                }
            )
            await pub.send_multipart([b"market.depth.ethusdt", mp_frame])

            await asyncio.sleep(0.12)
            gate.stop()
            await asyncio.wait_for(sub_task, timeout=1.0)
        finally:
            pub.close(linger=0)
            ctx.term()

    asyncio.run(_run())
    assert len(heartbeats) >= 2
    assert len(events) >= 1
    all_syms = {s for ev in events for s in ev.symbols}
    assert {"BTCUSDT", "ETHUSDT"}.issubset(all_syms)


def test_position_protection_zero_llm_orphan_cleanup_and_emergency_close(
    tmp_path: Path,
) -> None:
    # 1. Verify AST of position_protection.py has ZERO imports from LLM / model_router
    prot_file = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "quantum_edge_core"
        / "execution"
        / "position_protection.py"
    )
    tree = ast.parse(prot_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", "") or ""
            names = [a.name for a in node.names]
            combined = " ".join([mod, *names])
            for forbidden in ("quantum_edge_ml", "model_router", "genai", "agy_openai_proxy"):
                assert forbidden not in combined

    # 2. Orphan cleanup in FLAT state
    class _FakeProtClient(PaperExchangeClient):
        _qe_sandbox_verified = True

        def __init__(self, positions: list[dict[str, Any]], orders: list[dict[str, Any]]) -> None:
            super().__init__()
            self._positions = positions
            self._orders = orders
            self.cancelled: list[tuple[str, str]] = []

        def fetch_balance(self) -> dict[str, Any]:
            return {"VST": {"free": 9000.0, "total": 10000.0, "used": 1000.0}}

        def fetch_positions(self) -> list[dict[str, Any]]:
            return self._positions

        def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            return self._orders

        def cancel_order(self, oid: str, sym: str) -> dict[str, Any]:
            self.cancelled.append((oid, sym))
            return {"id": oid, "status": "canceled"}

    flat_client = _FakeProtClient(
        positions=[],
        orders=[
            {
                "id": "ord-orphan-1",
                "symbol": "BTC/USDT:USDT",
                "type": "stop_market",
                "reduceOnly": True,
                "info": {"type": "STOP_MARKET", "reduceOnly": True},
            }
        ],
    )
    o_ledger = OrderLedger(tmp_path / "orders.sqlite3")
    bridge = HarnessBridge(LeanExecutor(flat_client, o_ledger, LeanRiskGuard()), tmp_path / "j.jsonl")

    # Dry-run does NOT cancel on exchange
    w_dry = PositionProtectionWatcher(flat_client, bridge, dry_run=True)
    r_dry = asyncio.run(w_dry.check_once())
    assert "CANCELLED_ORPHANS:1" in r_dry["actions"]
    assert flat_client.cancelled == []

    # Live mode cancels orphan via cancel_vst_open_orders
    w_live = PositionProtectionWatcher(flat_client, bridge, dry_run=False)
    r_live = asyncio.run(w_live.check_once())
    assert "CANCELLED_ORPHANS:1" in r_live["actions"]
    assert len(flat_client.cancelled) == 1

    # 3. Emergency close on near_liq AND missing SL + stale feed via HarnessBridge
    danger_client = _FakeProtClient(
        positions=[
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 0.05,
                "entryPrice": 60000.0,
                "markPrice": 58900.0,
                "liquidationPrice": 58200.0,  # 1.19% distance <= 2.0%!
                "info": {"positionSide": "LONG"},
            },
            {
                "symbol": "ETH/USDT:USDT",
                "side": "short",
                "contracts": 0.5,
                "entryPrice": 2400.0,
                "markPrice": 2410.0,
                "liquidationPrice": 2900.0,  # Not near liq, but NO SL + stale feed!
                "info": {"positionSide": "SHORT"},
            },
        ],
        orders=[],  # Missing SL on both!
    )
    bridge2 = HarnessBridge(LeanExecutor(danger_client, o_ledger, LeanRiskGuard()), tmp_path / "j2.jsonl")
    w_emerg = PositionProtectionWatcher(
        danger_client, bridge2, stale_feed_limit_sec=10.0, max_liq_distance_pct=2.0, dry_run=False
    )
    w_emerg.touch_feed_heartbeat(ts_sec=100.0)  # Stale feed
    res_emerg = asyncio.run(w_emerg.check_once())
    assert "EMERGENCY_CLOSE_LONG:BTCUSDT" in res_emerg["actions"]
    assert "EMERGENCY_CLOSE_SHORT:ETHUSDT" in res_emerg["actions"]
    assert len(danger_client.submitted_orders) == 2
    assert all(o["params"]["reduceOnly"] is True for o in danger_client.submitted_orders)


def test_eval_wakeup_shadow_72h_meets_acceptance_gates(tmp_path: Path) -> None:
    out_json = tmp_path / "eval_72h.json"
    summary = asyncio.run(shadow_eval.run_shadow_evaluation(hours=72, seed=42, out_json=out_json))
    assert out_json.is_file()
    assert summary["material_event_recall"] >= 0.90
    assert summary["false_wake_rate"] <= 0.25
    assert summary["missed_opportunity_rate"] <= 0.10
    assert summary["mixed_wakes_per_day"] < 144.0


def test_cli_protect_once_mock_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mock_status = json.dumps(
        {
            "positions": [
                {
                    "symbol": "BTC/USDT:USDT",
                    "side": "long",
                    "contracts": 0.02,
                    "entryPrice": 60000.0,
                    "markPrice": 59000.0,
                    "liquidationPrice": 58500.0,
                    "info": {"positionSide": "LONG"},
                }
            ],
            "orders": [],
        }
    )
    rc = harness_cli.main(
        [
            "protect-once",
            "--dry-run",
            "--order-ledger-db",
            str(tmp_path / "prot_o.sqlite3"),
            "--journal-path",
            str(tmp_path / "prot_j.jsonl"),
            "--mock-status-json",
            mock_status,
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["dry_run"] is True
    assert out["source"] == "mock_status"
    assert "EMERGENCY_CLOSE_LONG:BTCUSDT" in out["actions"]


def test_position_protection_ignores_foreign_symbols_and_never_cancels_all(
    tmp_path: Path,
) -> None:
    class _ForeignScopeClient:
        _qe_sandbox_verified = True

        def __init__(self) -> None:
            self.cancelled_symbols: list[str] = []
            self.submitted_orders: list[dict[str, Any]] = []

        def fetch_balance(self) -> dict[str, Any]:
            return {"VST": {"free": 10000.0, "total": 10000.0, "used": 0.0}}

        def fetch_positions(self) -> list[dict[str, Any]]:
            return [
                {
                    "symbol": "BNB/USDT:USDT",
                    "side": "long",
                    "contracts": 522.67,
                    "entryPrice": 548.18,
                    "markPrice": 563.87,
                    "liquidationPrice": 550.0,
                    "info": {"positionSide": "LONG"},
                }
            ]

        def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
            orders = [
                {
                    "id": "bnb-sl-1",
                    "symbol": "BNB/USDT:USDT",
                    "type": "market",
                    "side": "sell",
                    "reduceOnly": True,
                    "stopPrice": 551.03,
                    "info": {"rawType": "STOP_MARKET", "positionSide": "LONG"},
                },
                {
                    "id": "btc-orphan-1",
                    "symbol": "BTC/USDT:USDT",
                    "type": "market",
                    "side": "sell",
                    "reduceOnly": True,
                    "stopPrice": 59000.0,
                    "info": {"rawType": "STOP_MARKET", "positionSide": "LONG"},
                },
            ]
            if symbol is None:
                return orders
            return [o for o in orders if o["symbol"] == symbol]

        def cancel_order(self, oid: str, sym: str) -> dict[str, Any]:
            self.cancelled_symbols.append(sym)
            return {"id": oid, "status": "canceled"}

        def create_order(
            self,
            symbol: str,
            type: str,
            side: str,
            amount: float,
            price: float | None = None,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.submitted_orders.append({"symbol": symbol, "params": params or {}})
            return {"id": "emerg-1", "status": "closed"}

    from quantum_edge_core.execution.risk_guard import RiskConfig

    client = _ForeignScopeClient()
    ledger = OrderLedger(tmp_path / "scope_orders.sqlite3")
    executor = LeanExecutor(client, ledger, LeanRiskGuard(RiskConfig()))
    bridge = HarnessBridge(executor, journal_path=tmp_path / "scope_journal.jsonl")
    watcher = PositionProtectionWatcher(
        client,
        bridge,
        allowed_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
        stale_feed_limit_sec=1.0,
        dry_run=False,
    )
    watcher.touch_feed_heartbeat(ts_sec=1.0)
    res = asyncio.run(watcher.check_once())

    assert res["open_positions"] == 0
    assert res["open_orders"] == 1
    assert res["ignored_foreign_positions"] == 1
    assert res["ignored_foreign_orders"] == 1
    assert res["actions"] == ["CANCELLED_ORPHANS:1"]
    assert client.cancelled_symbols == ["BTC/USDT:USDT"]
    assert "ALL" not in client.cancelled_symbols
    assert "BNB/USDT:USDT" not in client.cancelled_symbols
    assert client.submitted_orders == []
