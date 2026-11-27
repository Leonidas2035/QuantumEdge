"""Phase 1 unit tests: UsageLedger, LLMTransport, GeminiDirectClient, agy_openai_proxy, and A/B bench."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import tools.ab_transport_bench as ab_bench
import tools.agy_openai_proxy as agy_proxy
from quantum_edge_core.contracts.optimization import (
    RequestStatus,
    ThinkingLevel,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_ml.model_router.audit.events import AuditEvent
from quantum_edge_ml.model_router.backends.google_gemini import (
    GeminiDirectClient,
    clamp_thinking_level,
)
from quantum_edge_ml.model_router.backends.transport import (
    ImageAttachment,
    LLMRequest,
)
from quantum_edge_ml.model_router.router.budgets import BudgetConfig, TeacherBudgets
from quantum_edge_ml.model_router.usage_ledger import UsageLedger


def test_usage_ledger_unknown_on_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.sqlite3"
    ledger = UsageLedger(db_path=db_path)
    try:
        # Verify SQLite WAL + RAM temp_store pragmas on the active UsageLedger connection
        with ledger._lock:
            jm = ledger._conn.execute("PRAGMA journal_mode;").fetchone()[0]
            ts = ledger._conn.execute("PRAGMA temp_store;").fetchone()[0]
            sync = ledger._conn.execute("PRAGMA synchronous;").fetchone()[0]
        assert str(jm).lower() == "wal"
        assert int(ts) == 2  # MEMORY
        assert int(sync) == 1  # NORMAL

        for i in range(9):
            ledger.record_usage(
                UsageRecordV1(
                    record_id=f"ok-{i}",
                    decision_id=f"dec-{i}",
                    stage="single_shot",
                    transport=TransportChannel.GEMINI_INTERACTIONS,
                    model_provider_id="gemini-4-carbon",
                    thinking_level=ThinkingLevel.MEDIUM,
                    request_status=RequestStatus.OK,
                    provider_input_tokens=1000,
                    cache_read_tokens=500,
                    output_tokens=100,
                    thinking_tokens=200,
                    total_tokens=1300,
                    latency_ms=100.0 * (i + 1),
                    timeout_ms=180000.0,
                    is_censored=False,
                )
            )

        # 10% timeout -> right-censored Kaplan-Meier p95 must be None (> 5% survival at max OK)
        ledger.record_usage(
            UsageRecordV1(
                record_id="to-1",
                decision_id="dec-to",
                stage="single_shot",
                transport=TransportChannel.GEMINI_INTERACTIONS,
                model_provider_id="gemini-4-carbon",
                thinking_level=ThinkingLevel.MEDIUM,
                request_status=RequestStatus.TIMEOUT,
                error_code="TimeoutError",
                input_chars=5000,
                latency_ms=180000.0,
                timeout_ms=180000.0,
                is_censored=True,
            )
        )

        rows = ledger.fetch_usage_rows()
        to_row = [r for r in rows if r["record_id"] == "to-1"][0]
        assert int(to_row["is_censored"]) == 1
        assert to_row["provider_input_tokens"] is None
        assert to_row["iecu"] is None

        summary = ledger.summarize()
        assert summary["unknown_usage_records"] == 1
        assert summary["failure_rate"] == 0.1
        assert summary["latency_km_p50_ms"] is not None
        assert summary["latency_km_p95_ms"] is None
        assert summary["latency_imputed_p95_ms"] is not None
    finally:
        ledger.close()


def test_usage_ledger_retry_amplification_and_cache_metrics(tmp_path: Path) -> None:
    ledger = UsageLedger(db_path=tmp_path / "ledger.sqlite3")
    try:
        # First call: no cache hit
        ledger.record_usage(
            UsageRecordV1(
                record_id="r1",
                decision_id="d1",
                stage="stage1_screen",
                transport=TransportChannel.GEMINI_INTERACTIONS,
                model_provider_id="gemini-4-carbon",
                thinking_level=ThinkingLevel.LOW,
                request_status=RequestStatus.OK,
                provider_input_tokens=1000,
                cache_read_tokens=0,
                output_tokens=100,
                thinking_tokens=100,
                total_tokens=1200,
                latency_ms=500.0,
                timeout_ms=30000.0,
            )
        )
        # Retry call: 80% cached
        ledger.record_usage(
            UsageRecordV1(
                record_id="r2",
                decision_id="d1",
                stage="stage1_screen",
                transport=TransportChannel.GEMINI_INTERACTIONS,
                model_provider_id="gemini-4-carbon",
                thinking_level=ThinkingLevel.LOW,
                request_status=RequestStatus.OK,
                retry_of="r1",
                provider_input_tokens=1000,
                cache_read_tokens=800,
                output_tokens=100,
                thinking_tokens=100,
                total_tokens=1200,
                latency_ms=400.0,
                timeout_ms=30000.0,
            )
        )
        s = ledger.summarize()
        assert s["cache_token_fraction"] == 0.4  # 800 / 2000
        assert s["cache_request_hit_rate"] == 0.5  # 1 / 2
        assert s["retry_amplification"] > 1.0
        # r1 IECU = 1000 + 0 + 5*200 = 2000; r2 IECU = 200 + 0.05*800 + 5*200 = 1240
        assert s["sum_iecu"] == 3240.0
    finally:
        ledger.close()


def test_budgets_and_audit_event_consolidation(tmp_path: Path) -> None:
    ledger = UsageLedger(db_path=tmp_path / "ledger.sqlite3")
    try:
        budgets = TeacherBudgets(
            tmp_path / "budgets.json",
            BudgetConfig(max_requests_per_day=5, max_tokens_per_day=10000),
            ledger=ledger,
        )
        now = datetime.now(timezone.utc).isoformat()
        assert budgets.can_use(now, 500)
        budgets.record(now, 500)
        assert ledger.summarize()["total_requests"] == 1

        ev = AuditEvent(
            ts_utc=now,
            sym="BTCUSDT",
            backend_used="gemini_interactions",
            model_id="gemini-4-carbon",
            mode="direct",
            decision_json='{"action":"HOLD"}',
            confidence=0.8,
            risk="LOW",
            latency_ms=350.0,
            prompt_hash="abc123",
            ok=True,
            error_code=None,
        )
        rec = ev.to_usage_record()
        assert rec.provider_input_tokens is None
        roundtrip = AuditEvent.from_usage_record(rec)
        assert roundtrip.model_id == "gemini-4-carbon"
        assert roundtrip.ok is True
    finally:
        ledger.close()


def test_openai_compat_no_double_count_reasoning(tmp_path: Path) -> None:
    import asyncio

    ledger = UsageLedger(db_path=tmp_path / "ledger.sqlite3")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"action":"HOLD"}'}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 2500,
                    "total_tokens": 3500,
                    "prompt_tokens_details": {"cached_tokens": 400},
                    "completion_tokens_details": {"reasoning_tokens": 2000},
                },
            },
        )

    async def _run() -> None:
        mock_async = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        mock_sync = httpx.Client()
        client = GeminiDirectClient(
            ledger,
            api_key="DUMMY_KEY",
            mode="openai_compat",
            sdk_client=SimpleNamespace(),
            http2_async=mock_async,
            http2_sync=mock_sync,
        )
        try:
            req = LLMRequest(
                decision_id="dec-oai",
                stage="single_shot",
                model="gemini-4-carbon",
                system_instruction="sys",
                p2_dynamic_tail="user",
                thinking_level=ThinkingLevel.MEDIUM,
                max_output_tokens=16384,
            )
            resp = await client.generate(req)
            assert resp.usage.output_tokens == 500
            assert resp.usage.thinking_tokens == 2000
            assert resp.usage.cache_read_tokens == 400
            assert resp.usage.fresh_input_tokens == 600
        finally:
            await mock_async.aclose()
            mock_sync.close()
            await client.close()

    try:
        asyncio.run(_run())
    finally:
        ledger.close()


def test_interactions_places_images_strictly_after_p1_and_p2(tmp_path: Path) -> None:
    import asyncio

    ledger = UsageLedger(db_path=tmp_path / "ledger.sqlite3")
    captured_kwargs: dict[str, Any] = {}

    class _FakeInteractions:
        async def create(self, **kwargs: Any) -> Any:
            captured_kwargs.update(kwargs)
            return SimpleNamespace(
                id="int-123",
                status="completed",
                output_text='{"action":"HOLD"}',
                usage=SimpleNamespace(
                    total_input_tokens=3000,
                    total_cached_tokens=2000,
                    total_output_tokens=150,
                    total_thought_tokens=800,
                    total_tool_use_tokens=0,
                    total_tokens=3950,
                ),
            )

    fake_sdk = SimpleNamespace(aio=SimpleNamespace(interactions=_FakeInteractions()))

    async def _run() -> None:
        client = GeminiDirectClient(
            ledger,
            api_key="DUMMY_KEY",
            mode="interactions",
            sdk_client=fake_sdk,
        )
        try:
            req = LLMRequest(
                decision_id="dec-int",
                stage="stage2_drill",
                model="gemini-4-carbon",
                system_instruction="P0_SYS",
                p1_slow_prefix="P1_SLOW",
                p2_dynamic_tail="P2_TAIL",
                images=[ImageAttachment(data=b"webp-bytes", resolution="medium")],
                thinking_level=ThinkingLevel.HIGH,
                max_output_tokens=16384,
            )
            resp = await client.generate(req)
            assert resp.interaction_id == "int-123"
            assert resp.usage.provider_input_tokens == 3000
            assert resp.usage.cache_read_tokens == 2000
            assert resp.usage.output_tokens == 150
            assert resp.usage.thinking_tokens == 800
            assert resp.usage.total_tokens == 3950
        finally:
            await client.close()

    try:
        asyncio.run(_run())
    finally:
        ledger.close()

    parts = captured_kwargs["input"]
    assert [p["type"] for p in parts] == ["text", "text", "image"]
    assert parts[0]["text"] == "P1_SLOW"
    assert parts[1]["text"] == "P2_TAIL"
    assert parts[2]["resolution"] == "medium"

    gen_cfg = captured_kwargs["generation_config"]
    for forbidden in ("thinking_budget", "temperature", "top_p", "top_k"):
        assert forbidden not in gen_cfg
        assert forbidden not in captured_kwargs


def test_carbon_clamps_xhigh_to_high_and_neon_allows_xhigh() -> None:
    assert clamp_thinking_level("gemini-4-carbon", ThinkingLevel.XHIGH) == ThinkingLevel.HIGH
    assert clamp_thinking_level("gemini-4-argon", ThinkingLevel.XHIGH) == ThinkingLevel.HIGH
    assert clamp_thinking_level("gemini-4-neon", ThinkingLevel.XHIGH) == ThinkingLevel.XHIGH
    assert clamp_thinking_level("gemini-4-hydrogen", ThinkingLevel.XHIGH) == ThinkingLevel.XHIGH


def test_rejects_max_output_below_8192(tmp_path: Path) -> None:
    import asyncio

    ledger = UsageLedger(db_path=tmp_path / "ledger.sqlite3")
    client = GeminiDirectClient(
        ledger,
        api_key="DUMMY_KEY",
        mode="interactions",
        sdk_client=SimpleNamespace(),
    )
    req = LLMRequest(
        decision_id="dec-bad",
        stage="single_shot",
        model="gemini-4-carbon",
        system_instruction="sys",
        p2_dynamic_tail="user",
        max_output_tokens=4096,
    )
    try:
        with pytest.raises(ValueError, match="8192"):
            asyncio.run(client.generate(req))
    finally:
        asyncio.run(client.close())
        ledger.close()


def test_agy_proxy_logs_five_native_usage_fields_and_unknown_on_error(tmp_path: Path) -> None:
    log_path = tmp_path / "proxy.log"
    ledger = UsageLedger(db_path=tmp_path / "proxy_ledger.sqlite3")
    call_idx = {"n": 0}

    def _runner(prompt: str, **kwargs: Any) -> dict[str, Any]:
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            return {
                "status": "SUCCESS",
                "response": "HOLD",
                "num_turns": 1,
                "duration_seconds": 3.42,
                "usage": {
                    "input_tokens": 12000,
                    "output_tokens": 150,
                    "thinking_tokens": 600,
                    "cache_read_tokens": 2000,
                    "total_tokens": 12750,
                },
            }
        raise subprocess.TimeoutExpired(cmd="agy", timeout=30.0)

    engine = agy_proxy.AgyProxyEngine(
        timeout_sec=30.0,
        log_path=log_path,
        runner=_runner,
        ledger=ledger,
    )
    try:
        out = engine.create_chat_completion(
            {
                "model": "gemini-4-carbon-medium",
                "messages": [{"role": "user", "content": "status?"}],
            }
        )
        assert out["usage"]["prompt_tokens"] == 12000
        assert out["usage"]["completion_tokens"] == 750
        assert out["usage"]["prompt_tokens_details"]["cached_tokens"] == 2000
        assert out["usage"]["completion_tokens_details"]["reasoning_tokens"] == 600

        with pytest.raises(subprocess.TimeoutExpired):
            engine.create_chat_completion(
                {
                    "model": "gemini-4-carbon-medium",
                    "messages": [{"role": "user", "content": "timeout check"}],
                }
            )

        lines = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 2
        ok_ev, err_ev = lines[0], lines[1]

        assert ok_ev["status"] == "ok"
        assert ok_ev["input_tokens"] == 12000
        assert ok_ev["output_tokens"] == 150
        assert ok_ev["thinking_tokens"] == 600
        assert ok_ev["cache_read_tokens"] == 2000
        assert ok_ev["total_tokens"] == 12750
        assert ok_ev["num_turns"] == 1
        assert ok_ev["duration_seconds"] == 3.42

        assert err_ev["status"] == "timeout"
        assert err_ev["is_censored"] is True
        assert err_ev["input_chars"] > 0
        assert err_ev["latency_ms"] >= 0.0
        for tok_field in (
            "input_tokens",
            "output_tokens",
            "thinking_tokens",
            "cache_read_tokens",
            "total_tokens",
        ):
            assert err_ev[tok_field] is None

        s = ledger.summarize()
        assert s["total_requests"] == 2
        assert s["unknown_usage_records"] == 1
        assert s["failure_rate"] == 0.5
    finally:
        ledger.close()


def test_ab_transport_bench_dry_run_end_to_end(tmp_path: Path) -> None:
    out_json = tmp_path / "ab_results.json"
    rc = ab_bench.main(["--mode", "dry-run", "--runs", "6", "--out-json", str(out_json)])
    assert rc == 0
    assert out_json.is_file()

    data = json.loads(out_json.read_text(encoding="utf-8"))
    branches = data["branches"]
    assert set(branches.keys()) == {
        "A_agy_proxy",
        "B_gemini_interactions",
        "C_gemini_openai_compat",
    }
    assert branches["A_agy_proxy"]["unknown_usage_records"] == 1
    assert branches["B_gemini_interactions"]["failure_rate"] == 0.0
    assert branches["B_gemini_interactions"]["sum_iecu"] < branches["A_agy_proxy"]["sum_iecu"]
    assert branches["B_gemini_interactions"]["sum_provider_input_tokens"] < branches["A_agy_proxy"]["sum_provider_input_tokens"]


def test_cold_start_subprocess_imports_no_circular_import() -> None:
    import os
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    p1 = subprocess.run(
        [
            sys.executable,
            "-c",
            "from quantum_edge_ml.model_router.usage_ledger import UsageLedger; print(UsageLedger.__name__)",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert p1.returncode == 0, f"UsageLedger cold import failed: {p1.stderr}"
    assert "UsageLedger" in p1.stdout

    p2 = subprocess.run(
        [sys.executable, "tools/agy_openai_proxy.py", "--help"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert p2.returncode == 0, f"agy_openai_proxy.py --help failed: {p2.stderr}"

    p3 = subprocess.run(
        [sys.executable, "tools/qe_harness_cli.py", "--help"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert p3.returncode == 0, f"qe_harness_cli.py --help failed: {p3.stderr}"
