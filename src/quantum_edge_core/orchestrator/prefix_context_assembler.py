"""3-Layer PrefixContextAssembler for Gemini 4 Implicit Caching, SQLite WAL ThesisMemoryStore, and RAM SnapshotStore."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from quantum_edge_core.contracts.optimization import ForecastBundleV1, TradeDecisionV2
from quantum_edge_core.execution.market_context_serializer import MarketContextSerializer
from quantum_edge_core.execution.risk_guard import RiskConfig
from quantum_edge_core.execution.storage_policy import (
    CANONICAL_SSD_DURABLE_PATHS,
    apply_sqlite_wal_pragmas,
)
from quantum_edge_core.orchestrator.deterministic_orchestrator import (
    extract_account_risk_limits,
    format_forecast_for_prompt,
    serialize_stage2_candidate,
)
from quantum_edge_core.orchestrator.prompts import build_p0_static_prefix
from quantum_edge_ml.model_router.backends.transport import ImageAttachment
from quantum_edge_ml.model_router.router.cache import ImplicitCacheEvictionMonitor

THESIS_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS theses (
    thesis_id               TEXT PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    direction               TEXT NOT NULL,
    opened_at_utc           TEXT NOT NULL,
    evidence_refs_json      TEXT NOT NULL,
    invalidation_json       TEXT NOT NULL,
    target_zones_json       TEXT NOT NULL,
    confidence              REAL NOT NULL,
    position_state          TEXT NOT NULL, -- ACTIVE | CLOSED | INVALIDATED | EXPIRED
    last_confirmed_seq      INTEGER NOT NULL,
    expires_at_utc          TEXT NOT NULL,
    summary_text            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_theses_state ON theses(position_state, opened_at_utc);
"""


class ResyncMode(str, Enum):
    """Stateful checkpoint/delta synchronization mode."""

    CACHE_HIT = "CACHE_HIT"
    LOCAL_RECONSTRUCT = "LOCAL_RECONSTRUCT"
    RESYNC = "RESYNC"


class ThesisMemoryStore:
    """Append-only SQLite WAL store for active trading theses (<= 800 tokens in P1)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else CANONICAL_SSD_DURABLE_PATHS["thesis_memory"]
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            apply_sqlite_wal_pragmas(self._conn)
            self._conn.executescript(THESIS_SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> ThesisMemoryStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def upsert_from_decision(
        self,
        *,
        decision_id: str,
        decision: TradeDecisionV2,
        snapshot_id: str,
        seq: int = 0,
        ttl_hours: int = 12,
    ) -> None:
        _ = snapshot_id
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=ttl_hours)
        state = "ACTIVE"
        if decision.action in ("CLOSE_LONG", "CLOSE_SHORT"):
            state = "CLOSED"
        elif decision.thesis_status == "INVALIDATED":
            state = "INVALIDATED"
        direction = (
            "LONG"
            if "LONG" in decision.action
            else ("SHORT" if "SHORT" in decision.action else "FLAT")
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO theses (
                    thesis_id, symbol, direction, opened_at_utc, evidence_refs_json,
                    invalidation_json, target_zones_json, confidence, position_state,
                    last_confirmed_seq, expires_at_utc, summary_text
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    decision_id,
                    decision.symbol,
                    direction,
                    now.isoformat(),
                    json.dumps(decision.evidence_refs, sort_keys=True),
                    json.dumps(
                        {"invalidation_price": decision.invalidation_price, "sl": decision.sl_price},
                        sort_keys=True,
                    ),
                    json.dumps(
                        {"tp": decision.tp_price, "entry": decision.entry_price},
                        sort_keys=True,
                    ),
                    float(decision.confidence),
                    state,
                    seq,
                    expires.isoformat(),
                    f"{decision.primary_edge} | CT:{decision.counter_thesis}"[:260],
                ),
            )
            self._conn.commit()

    def compact_expired(self, now_utc: datetime | None = None) -> int:
        """Expire stale ACTIVE theses during hourly RESYNC without breaking intra-hour append-only."""
        now_iso = (now_utc or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE theses SET position_state = 'EXPIRED' WHERE position_state = 'ACTIVE' AND expires_at_utc <= ?",
                (now_iso,),
            )
            self._conn.commit()
            return int(cur.rowcount)

    def render_append_only_block(self, max_active: int = 4, max_closed: int = 3) -> str:
        """Render deterministic append-only block (new active entries strictly at the tail)."""
        with self._lock:
            active = self._conn.execute(
                "SELECT * FROM theses WHERE position_state='ACTIVE' ORDER BY opened_at_utc ASC LIMIT ?",
                (max_active,),
            ).fetchall()
            closed = self._conn.execute(
                "SELECT * FROM theses WHERE position_state!='ACTIVE' ORDER BY opened_at_utc DESC LIMIT ?",
                (max_closed,),
            ).fetchall()
        rows = list(reversed(closed)) + list(active)
        if not rows:
            return "THESIS_MEMORY_V1:EMPTY"
        lines = ["THESIS_MEMORY_V1|id,sym,dir,state,conf,inv,tgt,summary"]
        for r in rows:
            lines.append(
                f"{r['thesis_id']}|{r['symbol']}|{r['direction']}|{r['position_state']}|"
                f"{r['confidence']:.2f}|{r['invalidation_json']}|{r['target_zones_json']}|{r['summary_text']}"
            )
        return "\n".join(lines)

    def max_rowid(self) -> int:
        """Return monotonic SQLite rowid watermark for checkpointing P1 without clock drift."""
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM theses").fetchone()
            return int(row[0]) if row is not None else 0

    def render_intra_hour_delta_block(self, *, since_rowid: int, max_rows: int = 12) -> str:
        """Render theses inserted/updated AFTER the current P1 checkpoint for P2 injection."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM theses WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
                (int(since_rowid), int(max_rows)),
            ).fetchall()
        if not rows:
            return ""
        lines = ["INTRA_HOUR_THESIS_DELTA|id,sym,dir,state,conf,inv,tgt,summary"]
        for r in rows:
            lines.append(
                f"{r['thesis_id']}|{r['symbol']}|{r['direction']}|{r['position_state']}|"
                f"{r['confidence']:.2f}|{r['invalidation_json']}|{r['target_zones_json']}|{r['summary_text']}"
            )
        return "\n".join(lines)


@dataclass(slots=True)
class CheckpointState:
    """In-RAM checkpoint state for hourly P1 macro prefix stability."""

    snapshot_id: str
    hour_bucket: int
    base_seq: int
    last_seq: int
    base_hash: str
    p1_text: str
    regime_signature: str
    thesis_watermark: int = 0


class SnapshotStore:
    """RAM-only Checkpoint/Delta state machine + ImplicitCacheEvictionMonitor."""

    def __init__(
        self,
        max_consecutive_misses: int = 3,
        eviction_monitor: ImplicitCacheEvictionMonitor | None = None,
    ) -> None:
        self._checkpoint: CheckpointState | None = None
        self.eviction_monitor = eviction_monitor or ImplicitCacheEvictionMonitor(
            max_consecutive_stage2_misses=max_consecutive_misses
        )

    @property
    def max_consecutive_misses(self) -> int:
        return self.eviction_monitor.max_consecutive_stage2_misses

    def resolve_mode(
        self,
        *,
        now_ts: float,
        current_seq: int,
        regime_signature: str,
        expected_base_hash: str | None = None,
    ) -> ResyncMode:
        hour_bucket = int(now_ts // 3600)
        cp = self._checkpoint
        if cp is None:
            return ResyncMode.RESYNC
        if cp.hour_bucket != hour_bucket or cp.regime_signature != regime_signature:
            return ResyncMode.RESYNC
        if current_seq < cp.last_seq:
            return ResyncMode.RESYNC
        if expected_base_hash and expected_base_hash != cp.base_hash:
            return ResyncMode.RESYNC
        if self.eviction_monitor.alert_active:
            return ResyncMode.RESYNC
        if current_seq > cp.last_seq:
            return ResyncMode.LOCAL_RECONSTRUCT
        return ResyncMode.CACHE_HIT

    def commit_checkpoint(
        self,
        *,
        snapshot_id: str,
        now_ts: float,
        seq: int,
        p1_text: str,
        regime_signature: str,
        thesis_watermark: int = 0,
    ) -> CheckpointState:
        b_hash = hashlib.sha256(p1_text.encode("utf-8")).hexdigest()[:16]
        self._checkpoint = CheckpointState(
            snapshot_id=snapshot_id,
            hour_bucket=int(now_ts // 3600),
            base_seq=seq,
            last_seq=seq,
            base_hash=b_hash,
            p1_text=p1_text,
            regime_signature=regime_signature,
            thesis_watermark=int(thesis_watermark),
        )
        self.eviction_monitor.reset_misses()
        return self._checkpoint

    def advance_seq(self, seq: int) -> None:
        if self._checkpoint is not None:
            self._checkpoint.last_seq = max(self._checkpoint.last_seq, seq)

    def observe_cache_result(
        self,
        cache_read_tokens: int | None,
        *,
        prompt_tokens: int | None = None,
        stage: str = "stage2_drill",
    ) -> bool:
        """Delegate cache observation to the single ImplicitCacheEvictionMonitor."""
        if self._checkpoint is None:
            return False
        return self.eviction_monitor.observe(
            prompt_tokens, cache_read_tokens, stage=stage
        )

    @property
    def current(self) -> CheckpointState | None:
        return self._checkpoint


@dataclass(slots=True)
class AssembledPrompt:
    """3-layer assembled prompt with byte-stable P0+P1 prefix hashes."""

    p0_static: str
    p1_slow: str
    p2_dynamic: str
    images: list[ImageAttachment] = field(default_factory=list)
    prefix_hash: str = ""
    context_hash: str = ""
    resync_mode: ResyncMode = ResyncMode.RESYNC
    estimated_p2_tokens: int = 0


class PrefixContextAssembler:
    """3-layer (P0/P1/P2) prompt assembler optimized for Gemini 4 Implicit Caching."""

    MIN_P0_TOKENS = 4_096
    MIN_P2_S2_TOKENS = 2_050

    def __init__(
        self,
        serializer: MarketContextSerializer | None = None,
        thesis_store: ThesisMemoryStore | None = None,
        snapshot_store: SnapshotStore | None = None,
        *,
        include_images: bool = False,
        extra_reference_playbook: str = "",
        risk_limits: RiskConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.serializer = serializer or MarketContextSerializer()
        self.thesis_store = thesis_store or ThesisMemoryStore()
        self.snapshot_store = snapshot_store or SnapshotStore()
        self.include_images = include_images
        self.risk_limits = risk_limits
        self.last_resync_mode: ResyncMode | None = None
        self.p0_static = build_p0_static_prefix(
            extra_reference_playbook=extra_reference_playbook
        )

    def _build_p1_slow(self, contexts_by_symbol: dict[str, dict[str, Any]]) -> str:
        parts: list[str] = ["=== P1_HOURLY_MACRO_LAYER ==="]
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
                    parts.append(
                        self.serializer.encode_ohlcv_delta_bps(sym, tf, bars, tail=30)
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
        parts.append("=== P1_APPEND_ONLY_THESIS_MEMORY ===")
        parts.append(self.thesis_store.render_append_only_block())
        return "\n".join(parts)

    def _ensure_p1(
        self,
        bundle: Any,
        *,
        current_seq: int = 0,
        regime_signature: str = "DEFAULT",
        now_ts: float | None = None,
    ) -> tuple[str, ResyncMode]:
        ts_val = time.time() if now_ts is None else now_ts
        mode = self.snapshot_store.resolve_mode(
            now_ts=ts_val,
            current_seq=current_seq,
            regime_signature=regime_signature,
        )
        if mode == ResyncMode.RESYNC or self.snapshot_store.current is None:
            self.thesis_store.compact_expired()
            watermark = (
                self.thesis_store.max_rowid()
                if hasattr(self.thesis_store, "max_rowid")
                else 0
            )
            p1_text = self._build_p1_slow(bundle.contexts_by_symbol)
            self.snapshot_store.commit_checkpoint(
                snapshot_id=bundle.snapshot_id,
                now_ts=ts_val,
                seq=current_seq,
                p1_text=p1_text,
                regime_signature=regime_signature,
                thesis_watermark=watermark,
            )
            return p1_text, ResyncMode.RESYNC
        if mode == ResyncMode.LOCAL_RECONSTRUCT:
            self.snapshot_store.advance_seq(current_seq)
        return self.snapshot_store.current.p1_text, mode

    def _intra_hour_thesis_delta(self) -> str:
        cp = self.snapshot_store.current
        if cp is None or not hasattr(self.thesis_store, "render_intra_hour_delta_block"):
            return ""
        return self.thesis_store.render_intra_hour_delta_block(
            since_rowid=cp.thesis_watermark
        )

    def assemble_stage1(
        self,
        *,
        bundle: Any,
        trigger_summary: str,
        forecasts_by_symbol: dict[str, dict[str, Any] | ForecastBundleV1] | None = None,
        current_seq: int = 0,
        regime_signature: str = "DEFAULT",
        now_ts: float | None = None,
    ) -> AssembledPrompt:
        p1_slow, resync_mode = self._ensure_p1(
            bundle,
            current_seq=current_seq,
            regime_signature=regime_signature,
            now_ts=now_ts,
        )
        self.last_resync_mode = resync_mode
        p2_parts: list[str] = [
            "=== P2_STAGE1_DYNAMIC_SCREENING ===",
            json.dumps(
                {
                    "snapshot_id": bundle.snapshot_id,
                    "resync_mode": resync_mode.value,
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
        intra_delta = self._intra_hour_thesis_delta()
        if intra_delta:
            p2_parts.append(intra_delta)
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
        return self._finalize(p1_slow, "\n".join(p2_parts), images, resync_mode)

    def assemble_stage2(
        self,
        *,
        bundle: Any,
        candidate_symbol: str,
        stage1_summary: dict[str, Any],
        forecast: dict[str, Any] | ForecastBundleV1 | None = None,
    ) -> AssembledPrompt:
        p1_slow = (
            self.snapshot_store.current.p1_text
            if self.snapshot_store.current is not None
            else self._build_p1_slow(bundle.contexts_by_symbol)
        )
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
        intra_delta = self._intra_hour_thesis_delta()
        if intra_delta:
            p2_parts.append(intra_delta)
        if isinstance(fc_fmt, str):
            p2_parts.append(fc_fmt)
        p2_parts.append(cand_text)
        p2_parts.append("OUTPUT_CONTRACT: Return ONLY valid JSON matching STAGE2_SCHEMA.")
        return self._finalize(p1_slow, "\n".join(p2_parts), images, ResyncMode.CACHE_HIT)

    def observe_cache_result(
        self,
        cache_read_tokens: int | None,
        *,
        prompt_tokens: int | None = None,
        stage: str = "stage2_drill",
    ) -> bool:
        return self.snapshot_store.observe_cache_result(
            cache_read_tokens, prompt_tokens=prompt_tokens, stage=stage
        )

    def upsert_thesis(
        self,
        *,
        decision_id: str,
        decision: TradeDecisionV2,
        snapshot_id: str,
    ) -> None:
        self.thesis_store.upsert_from_decision(
            decision_id=decision_id,
            decision=decision,
            snapshot_id=snapshot_id,
        )

    def _finalize(
        self,
        p1_slow: str,
        p2_dynamic: str,
        images: list[ImageAttachment],
        resync_mode: ResyncMode,
    ) -> AssembledPrompt:
        prefix_hash = hashlib.sha256(
            (self.p0_static + "\n" + p1_slow).encode("utf-8")
        ).hexdigest()[:16]
        context_hash = hashlib.sha256(
            (prefix_hash + "\n" + p2_dynamic).encode("utf-8")
        ).hexdigest()[:16]
        est_img_toks = sum(im.token_budget for im in images)
        est_p2_toks = len(p2_dynamic) // 3 + est_img_toks
        return AssembledPrompt(
            p0_static=self.p0_static,
            p1_slow=p1_slow,
            p2_dynamic=p2_dynamic,
            images=images,
            prefix_hash=prefix_hash,
            context_hash=context_hash,
            resync_mode=resync_mode,
            estimated_p2_tokens=est_p2_toks,
        )


# Compatible alias per Phase 4 specification
PrefixPromptAssembler = PrefixContextAssembler

__all__ = [
    "AssembledPrompt",
    "CheckpointState",
    "PrefixContextAssembler",
    "PrefixPromptAssembler",
    "ResyncMode",
    "SnapshotStore",
    "THESIS_SCHEMA_SQL",
    "ThesisMemoryStore",
]
