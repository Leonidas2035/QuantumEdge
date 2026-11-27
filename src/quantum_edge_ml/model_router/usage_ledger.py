"""Unified SQLite WAL telemetry ledger and decision audit store for QuantumEdge."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import numpy as np

from quantum_edge_core.contracts.optimization import (
    DecisionAuditV1,
    RequestStatus,
    UsageRecordV1,
)
from quantum_edge_core.execution.storage_policy import (
    CANONICAL_SSD_DURABLE_PATHS,
    apply_sqlite_wal_pragmas,
)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usage_records (
    record_id              TEXT PRIMARY KEY,
    ts_utc                 TEXT NOT NULL,
    decision_id            TEXT NOT NULL,
    stage                  TEXT NOT NULL,
    transport              TEXT NOT NULL,
    model_provider_id      TEXT NOT NULL,
    agy_alias              TEXT,
    thinking_level         TEXT,
    request_status         TEXT NOT NULL,
    error_code             TEXT,
    retry_of               TEXT,
    logical_input_tokens   INTEGER, -- NULL = UNKNOWN
    provider_input_tokens  INTEGER, -- NULL = UNKNOWN
    cache_read_tokens      INTEGER, -- NULL = UNKNOWN
    output_tokens          INTEGER, -- NULL = UNKNOWN
    thinking_tokens        INTEGER, -- NULL = UNKNOWN
    tool_use_tokens        INTEGER, -- NULL = UNKNOWN
    total_tokens           INTEGER, -- NULL = UNKNOWN
    iecu                   REAL,    -- NULL = UNKNOWN
    input_chars            INTEGER NOT NULL DEFAULT 0,
    output_chars           INTEGER NOT NULL DEFAULT 0,
    latency_ms             REAL NOT NULL,
    timeout_ms             REAL NOT NULL,
    is_censored            INTEGER NOT NULL DEFAULT 0,
    num_turns              INTEGER NOT NULL DEFAULT 1,
    prefix_hash            TEXT,
    context_hash           TEXT,
    snapshot_id            TEXT,
    interaction_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_records(ts_utc);
CREATE INDEX IF NOT EXISTS idx_usage_decision ON usage_records(decision_id);
CREATE INDEX IF NOT EXISTS idx_usage_transport_model ON usage_records(transport, model_provider_id);

CREATE TABLE IF NOT EXISTS decision_audits (
    decision_id         TEXT PRIMARY KEY,
    ts_utc              TEXT NOT NULL,
    snapshot_id         TEXT NOT NULL,
    context_hash        TEXT NOT NULL,
    prefix_hash         TEXT NOT NULL,
    model_provider_id   TEXT NOT NULL,
    agy_alias           TEXT,
    thinking_level      TEXT NOT NULL,
    complexity_score    REAL,
    usage_record_ids    TEXT NOT NULL,
    decision_json       TEXT NOT NULL,
    validator_passed    INTEGER NOT NULL,
    validator_reason    TEXT,
    execution_json      TEXT,
    outcome_pnl_vst     REAL,
    outcome_mfe_bps     REAL,
    outcome_mae_bps     REAL
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON decision_audits(ts_utc);
CREATE INDEX IF NOT EXISTS idx_audit_snapshot ON decision_audits(snapshot_id);
"""


class UsageLedger:
    """Thread-safe SQLite WAL registry for native LLM usage telemetry and decision audits."""

    def __init__(
        self,
        db_path: Path | str | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else CANONICAL_SSD_DURABLE_PATHS["usage_ledger"]
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            apply_sqlite_wal_pragmas(self._conn)
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> UsageLedger:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def record_usage(self, rec: UsageRecordV1) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO usage_records (
                    record_id, ts_utc, decision_id, stage, transport, model_provider_id,
                    agy_alias, thinking_level, request_status, error_code, retry_of,
                    logical_input_tokens, provider_input_tokens, cache_read_tokens,
                    output_tokens, thinking_tokens, tool_use_tokens, total_tokens, iecu,
                    input_chars, output_chars, latency_ms, timeout_ms, is_censored,
                    num_turns, prefix_hash, context_hash, snapshot_id, interaction_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec.record_id,
                    rec.ts_utc.isoformat(),
                    rec.decision_id,
                    rec.stage,
                    rec.transport.value,
                    rec.model_provider_id,
                    rec.agy_alias,
                    rec.thinking_level.value if rec.thinking_level else None,
                    rec.request_status.value,
                    rec.error_code,
                    rec.retry_of,
                    rec.logical_input_tokens,
                    rec.provider_input_tokens,
                    rec.cache_read_tokens,
                    rec.output_tokens,
                    rec.thinking_tokens,
                    rec.tool_use_tokens,
                    rec.total_tokens,
                    rec.iecu,
                    rec.input_chars,
                    rec.output_chars,
                    rec.latency_ms,
                    rec.timeout_ms,
                    1 if rec.is_censored else 0,
                    rec.num_turns,
                    rec.prefix_hash,
                    rec.context_hash,
                    rec.snapshot_id,
                    rec.interaction_id,
                ),
            )
            self._conn.commit()

    def record_decision_audit(self, audit: DecisionAuditV1) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO decision_audits (
                    decision_id, ts_utc, snapshot_id, context_hash, prefix_hash,
                    model_provider_id, agy_alias, thinking_level, complexity_score,
                    usage_record_ids, decision_json, validator_passed, validator_reason,
                    execution_json, outcome_pnl_vst, outcome_mfe_bps, outcome_mae_bps
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    audit.decision_id,
                    audit.ts_utc.isoformat(),
                    audit.snapshot_id,
                    audit.context_hash,
                    audit.prefix_hash,
                    audit.model_provider_id,
                    audit.agy_alias,
                    audit.thinking_level.value,
                    audit.complexity_score,
                    json.dumps(audit.usage_record_ids),
                    json.dumps(audit.decision_payload, ensure_ascii=False, sort_keys=True),
                    1 if audit.validator_passed else 0,
                    audit.validator_reason,
                    (
                        json.dumps(audit.execution_result, ensure_ascii=False)
                        if audit.execution_result is not None
                        else None
                    ),
                    audit.outcome_pnl_vst,
                    audit.outcome_mfe_bps,
                    audit.outcome_mae_bps,
                ),
            )
            self._conn.commit()

    def fetch_usage_rows(
        self,
        *,
        since_utc: str | None = None,
        transport: str | None = None,
        model_provider_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if since_utc:
            clauses.append("ts_utc >= ?")
            params.append(since_utc)
        if transport:
            clauses.append("transport = ?")
            params.append(transport)
        if model_provider_id:
            clauses.append("model_provider_id = ?")
            params.append(model_provider_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            return self._conn.execute(
                f"SELECT * FROM usage_records {where_sql} ORDER BY ts_utc ASC",
                params,
            ).fetchall()

    def summarize(
        self,
        *,
        since_utc: str | None = None,
        transport: str | None = None,
        model_provider_id: str | None = None,
    ) -> dict[str, Any]:
        rows = self.fetch_usage_rows(
            since_utc=since_utc,
            transport=transport,
            model_provider_id=model_provider_id,
        )
        total_reqs = len(rows)
        if total_reqs == 0:
            return {"total_requests": 0}

        ok_rows = [r for r in rows if r["request_status"] == RequestStatus.OK.value]
        err_rows = [r for r in rows if r["request_status"] != RequestStatus.OK.value]
        unknown_usage_rows = [r for r in rows if r["total_tokens"] is None]
        native_ok = [r for r in ok_rows if r["provider_input_tokens"] is not None]

        sum_in = sum(int(r["provider_input_tokens"]) for r in native_ok)
        sum_cache = sum(int(r["cache_read_tokens"] or 0) for r in native_ok)
        sum_out = sum(int(r["output_tokens"] or 0) for r in native_ok)
        sum_think = sum(int(r["thinking_tokens"] or 0) for r in native_ok)
        sum_iecu = sum(float(r["iecu"]) for r in native_ok if r["iecu"] is not None)

        cache_hits = sum(1 for r in native_ok if int(r["cache_read_tokens"] or 0) > 0)
        cache_token_fraction = (sum_cache / sum_in) if sum_in > 0 else 0.0
        cache_req_hit_rate = (cache_hits / len(native_ok)) if native_ok else 0.0

        lat_rows = [
            r
            for r in rows
            if r["request_status"] in (RequestStatus.OK.value, RequestStatus.TIMEOUT.value)
        ]
        km_p50, km_p95, imputed_p95 = self._compute_censored_latency(lat_rows)

        first_iecu = sum(
            float(r["iecu"])
            for r in native_ok
            if r["retry_of"] is None and r["iecu"] is not None
        )
        retry_amp = (sum_iecu / first_iecu) if first_iecu > 0 else 1.0

        return {
            "total_requests": total_reqs,
            "ok_requests": len(ok_rows),
            "failed_requests": len(err_rows),
            "failure_rate": round(len(err_rows) / total_reqs, 4),
            "native_usage_coverage": round(len(native_ok) / len(ok_rows), 4) if ok_rows else 0.0,
            "unknown_usage_records": len(unknown_usage_rows),
            "sum_provider_input_tokens": sum_in,
            "sum_cache_read_tokens": sum_cache,
            "sum_fresh_input_tokens": max(0, sum_in - sum_cache),
            "sum_output_tokens": sum_out,
            "sum_thinking_tokens": sum_think,
            "sum_iecu": round(sum_iecu, 2),
            "cache_token_fraction": round(cache_token_fraction, 4),
            "cache_request_hit_rate": round(cache_req_hit_rate, 4),
            "retry_amplification": round(retry_amp, 4),
            "latency_imputed_p95_ms": imputed_p95,
            "latency_km_p50_ms": km_p50,
            "latency_km_p95_ms": km_p95,
        }

    @staticmethod
    def _compute_censored_latency(
        rows: list[sqlite3.Row],
    ) -> tuple[float | None, float | None, float | None]:
        if not rows:
            return None, None, None
        observed = np.array(
            [
                min(float(r["latency_ms"]), float(r["timeout_ms"]))
                if int(r["is_censored"])
                else float(r["latency_ms"])
                for r in rows
            ],
            dtype=np.float64,
        )
        censored = np.array([bool(r["is_censored"]) for r in rows], dtype=bool)
        imputed_p95 = round(float(np.percentile(observed, 95)), 2)

        ok_times = np.sort(np.unique(observed[~censored]))
        survival = 1.0
        km_p50: float | None = None
        km_p95: float | None = None
        for t in ok_times:
            n_at_risk = int(np.sum(observed >= t))
            d_events = int(np.sum((~censored) & (observed == t)))
            if n_at_risk > 0:
                survival *= 1.0 - (d_events / n_at_risk)
            if km_p50 is None and survival <= 0.50:
                km_p50 = round(float(t), 2)
            if km_p95 is None and survival <= 0.05:
                km_p95 = round(float(t), 2)
        return km_p50, km_p95, imputed_p95


__all__ = ["SCHEMA_SQL", "UsageLedger"]
