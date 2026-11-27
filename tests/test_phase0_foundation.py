"""Phase 0 foundation tests: optimization contracts, lean re-exports, and RAM-First storage policy."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from quantum_edge_core.contracts import (
    DecisionAuditV1,
    RequestStatus,
    ThinkingLevel,
    TradeDecision,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.contracts.lean import (
    DecisionAuditV1 as LeanDecisionAuditV1,
    UsageRecordV1 as LeanUsageRecordV1,
)
from quantum_edge_core.execution.storage_policy import (
    BufferedJsonlArchiver,
    CANONICAL_SSD_DURABLE_PATHS,
    RingMarketBuffer,
    StorageTier,
    apply_sqlite_wal_pragmas,
    get_ram_tmp_dir,
)


def test_usage_record_v1_unknown_on_error_and_extra_ignore() -> None:
    rec = UsageRecordV1(
        record_id="rec-err-1",
        decision_id="dec-1",
        stage="single_shot",
        transport=TransportChannel.GEMINI_INTERACTIONS,
        model_provider_id="gemini-4-carbon",
        thinking_level=ThinkingLevel.MEDIUM,
        request_status=RequestStatus.TIMEOUT,
        error_code="timeout_180s",
        latency_ms=180000.0,
        timeout_ms=180000.0,
        is_censored=True,
        unexpected_extra_field="ignored",
    )

    assert rec.logical_input_tokens is None
    assert rec.provider_input_tokens is None
    assert rec.cache_read_tokens is None
    assert rec.output_tokens is None
    assert rec.thinking_tokens is None
    assert rec.total_tokens is None
    assert rec.fresh_input_tokens is None
    assert rec.iecu is None
    assert not hasattr(rec, "unexpected_extra_field")


def test_usage_record_v1_iecu_calculation() -> None:
    rec = UsageRecordV1(
        record_id="rec-ok-1",
        decision_id="dec-2",
        stage="stage2_drill",
        transport=TransportChannel.GEMINI_INTERACTIONS,
        model_provider_id="gemini-4-carbon",
        thinking_level=ThinkingLevel.HIGH,
        request_status=RequestStatus.OK,
        provider_input_tokens=10000,
        cache_read_tokens=8000,
        output_tokens=200,
        thinking_tokens=1000,
        total_tokens=11200,
        latency_ms=1250.0,
        timeout_ms=30000.0,
    )

    assert rec.fresh_input_tokens == 2000
    # 2000 + 0.05 * 8000 + 5.0 * (200 + 1000) = 2000 + 400 + 6000 = 8400.0
    assert rec.iecu == 8400.0


def test_decision_audit_v1_and_lean_reexport() -> None:
    assert LeanUsageRecordV1 is UsageRecordV1
    assert LeanDecisionAuditV1 is DecisionAuditV1

    audit = DecisionAuditV1(
        decision_id="dec-3",
        snapshot_id="snap-1",
        context_hash="ctx-abc",
        prefix_hash="pfx-xyz",
        model_provider_id="gemini-4-carbon",
        thinking_level=ThinkingLevel.LOW,
        usage_record_ids=["rec-1"],
        decision_payload={"action": "HOLD"},
        validator_passed=True,
        unexpected_field=123,
    )
    assert audit.decision_id == "dec-3"
    assert audit.validator_passed is True
    assert not hasattr(audit, "unexpected_field")

    # Existing lean.py contract remains untouched
    td = TradeDecision(
        decision_id="td-1",
        symbol="BTC-USDT",
        action="HOLD",
        reason="ok",
    )
    assert td.margin_vst == 0.0


def test_get_ram_tmp_dir_uses_dev_shm_when_available() -> None:
    tmp_dir = get_ram_tmp_dir("phase0_test")
    assert tmp_dir.exists()
    assert tmp_dir.is_dir()
    if Path("/dev/shm").is_dir():
        assert str(tmp_dir).startswith("/dev/shm/quantumedge")


def test_ring_market_buffer_fixed_maxlen_in_ram() -> None:
    buf: RingMarketBuffer[int] = RingMarketBuffer(maxlen=3)
    buf.extend([1, 2, 3, 4, 5])
    assert len(buf) == 3
    assert buf.snapshot() == [3, 4, 5]
    assert buf.latest() == 5
    buf.clear()
    assert len(buf) == 0
    assert buf.latest() is None


def test_buffered_jsonl_archiver_defers_ssd_write_until_threshold_or_flush(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "journal.jsonl"
    archiver = BufferedJsonlArchiver(target, flush_max_items=3, flush_interval_sec=3600.0)

    assert archiver.append({"idx": 1}) is False
    assert archiver.append({"idx": 2}) is False
    assert archiver.pending_count == 2
    assert not target.exists()

    # 3rd append hits flush_max_items=3
    assert archiver.append({"idx": 3}) is True
    assert archiver.pending_count == 0
    assert target.exists()
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    # Additional append stays in RAM until explicit flush/close
    assert archiver.append({"idx": 4}) is False
    assert archiver.pending_count == 1
    assert archiver.close() == 1
    assert len(target.read_text(encoding="utf-8").strip().splitlines()) == 4


def test_apply_sqlite_wal_pragmas_and_storage_tiers(tmp_path: Path) -> None:
    assert StorageTier.RAM_EPHEMERAL.value == "ram_ephemeral"
    assert "usage_ledger" in CANONICAL_SSD_DURABLE_PATHS

    db_path = tmp_path / "test_wal.sqlite3"
    with sqlite3.connect(db_path) as conn:
        apply_sqlite_wal_pragmas(conn)
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
        temp_store = conn.execute("PRAGMA temp_store;").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(synchronous) == 1  # NORMAL
    assert int(temp_store) == 2  # MEMORY
