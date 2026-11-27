#!/usr/bin/env python3
"""Interactive Operator & AGY TUI Briefing CLI (Development #2 Stage QE-04).

Reads the RAM-backed live session state (`/dev/shm/quantumedge/sessions/latest_session.json`),
`UsageLedger` (`decision_audits` + `usage_records`), `ThesisMemoryStore`, and
`lean_trade_journal.jsonl` without polluting the trading orchestrator's stateless
prompt history or burning OAuth quota.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quantum_edge_core.execution.storage_policy import (
    CANONICAL_SSD_DURABLE_PATHS,
    get_ram_tmp_dir,
)
from quantum_edge_ml.model_router.usage_ledger import UsageLedger


def default_latest_session_path() -> Path:
    return get_ram_tmp_dir("sessions") / "latest_session.json"


def _discover_latest_ledger_db() -> Path:
    runtime_dir = ROOT / "runtime"
    if runtime_dir.is_dir():
        candidates = sorted(
            runtime_dir.glob("usage_ledger*.sqlite3"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    return ROOT / CANONICAL_SSD_DURABLE_PATHS["usage_ledger"]


def _read_json_if_exists(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _fetch_recent_audits(ledger_path: Path | None, limit: int) -> list[dict[str, Any]]:
    if ledger_path is None or not ledger_path.is_file():
        return []
    try:
        conn = sqlite3.connect(str(ledger_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM decision_audits ORDER BY ts_utc DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []

    audits: list[dict[str, Any]] = []
    for r in reversed(rows):
        try:
            dp = json.loads(r["decision_json"] or "{}")
        except Exception:
            dp = {}
        try:
            ex = json.loads(r["execution_json"] or "{}") if r["execution_json"] else {}
        except Exception:
            ex = {}
        audits.append(
            {
                "decision_id": r["decision_id"],
                "ts_utc": r["ts_utc"],
                "thinking_level": r["thinking_level"],
                "complexity_score": r["complexity_score"],
                "validator_passed": bool(r["validator_passed"]),
                "validator_reason": r["validator_reason"],
                "action": dp.get("action"),
                "symbol": dp.get("symbol"),
                "order_type": dp.get("order_type"),
                "entry_price": dp.get("entry_price"),
                "sl_price": dp.get("sl_price"),
                "tp_price": dp.get("tp_price"),
                "confidence": dp.get("confidence"),
                "hold_blocker_code": dp.get("hold_blocker_code"),
                "regime_read": dp.get("regime_read"),
                "primary_edge": dp.get("primary_edge"),
                "counter_thesis": dp.get("counter_thesis"),
                "sizing": dp.get("sizing"),
                "execution_status": ex.get("status") if isinstance(ex, dict) else None,
            }
        )
    return audits


def _fetch_active_theses(thesis_db_path: Path | None) -> list[dict[str, Any]]:
    if thesis_db_path is None or not thesis_db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(str(thesis_db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT thesis_id, symbol, direction, position_state, confidence, "
                "invalidation_json, target_zones_json, summary_text, opened_at_utc "
                "FROM theses WHERE position_state='ACTIVE' ORDER BY opened_at_utc DESC LIMIT 10"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _read_journal_tail(journal_path: Path | None, limit: int = 5) -> list[dict[str, Any]]:
    if journal_path is None or not journal_path.is_file():
        return []
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-max(1, int(limit)) :]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _estimate_elapsed_hours(session_data: dict[str, Any], ledger_path: Path | None) -> float:
    elapsed_sec = float(session_data.get("elapsed_sec") or 0.0)
    if elapsed_sec <= 0.0:
        started = session_data.get("started_utc")
        updated = session_data.get("updated_utc") or session_data.get("finished_utc")
        if started and updated:
            try:
                t0 = datetime.fromisoformat(str(started))
                t1 = datetime.fromisoformat(str(updated))
                elapsed_sec = max(0.0, (t1 - t0).total_seconds())
            except Exception:
                elapsed_sec = 0.0
    if elapsed_sec <= 0.0 and ledger_path is not None and ledger_path.is_file():
        try:
            conn = sqlite3.connect(str(ledger_path), timeout=5.0)
            try:
                row = conn.execute("SELECT MIN(ts_utc), MAX(ts_utc) FROM usage_records").fetchone()
                if row and row[0] and row[1]:
                    t0 = datetime.fromisoformat(str(row[0]))
                    t1 = datetime.fromisoformat(str(row[1]))
                    elapsed_sec = max(0.0, (t1 - t0).total_seconds())
            finally:
                conn.close()
        except Exception:
            pass
    # Floor at 60s so sub-minute dry runs extrapolate bounded hourly rates cleanly
    return max(elapsed_sec, 60.0) / 3600.0


def build_briefing_payload(
    *,
    session_json: Path | None = None,
    ledger_db: Path | None = None,
    thesis_db: Path | None = None,
    journal_path: Path | None = None,
    last_n: int = 5,
) -> dict[str, Any]:
    sess_path = session_json or default_latest_session_path()
    sess = _read_json_if_exists(sess_path)
    eff_ledger = ledger_db or _discover_latest_ledger_db()
    eff_thesis = thesis_db or (ROOT / CANONICAL_SSD_DURABLE_PATHS["thesis_memory"])
    eff_journal = journal_path or (ROOT / CANONICAL_SSD_DURABLE_PATHS["lean_trade_journal"])

    usage_summary: dict[str, Any] = dict(sess.get("usage_summary") or {})
    if eff_ledger.is_file():
        ledger = UsageLedger(eff_ledger)
        try:
            db_summary = ledger.summarize(since_utc=sess.get("started_utc"))
            if db_summary.get("total_requests", 0) > 0 or not usage_summary:
                usage_summary = db_summary
        finally:
            ledger.close()

    audits = _fetch_recent_audits(eff_ledger, last_n)
    if not audits and sess.get("cycles"):
        for c in (sess.get("cycles") or [])[-max(1, int(last_n)) :]:
            audits.append(dict(c))

    active_theses = _fetch_active_theses(eff_thesis)
    if not active_theses and sess.get("active_theses"):
        active_theses = list(sess.get("active_theses") or [])

    elapsed_h = _estimate_elapsed_hours(sess, eff_ledger)
    sum_iecu = float(usage_summary.get("sum_iecu") or 0.0)
    sum_in = int(usage_summary.get("sum_provider_input_tokens") or 0)
    iecu_per_1h = round(sum_iecu / elapsed_h, 1) if sum_iecu > 0 else 0.0
    iecu_per_5h = round(iecu_per_1h * 5.0, 1)
    in_tok_per_1h = int(round(sum_in / elapsed_h)) if sum_in > 0 else 0

    start_eq = sess.get("start_equity_vst")
    cur_eq = sess.get("current_equity_vst", sess.get("end_equity_vst"))
    realized_delta = (
        round(float(cur_eq) - float(start_eq), 4)
        if (start_eq is not None and cur_eq is not None)
        else None
    )

    api_stats = dict(sess.get("api_stats") or {})
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "session_path": str(sess_path),
        "session_found": bool(sess),
        "status": sess.get("status", "UNKNOWN"),
        "mode": "DRY_RUN" if sess.get("dry_run", True) else "LIVE_VST",
        "transport": sess.get("transport"),
        "model": sess.get("model"),
        "reuse_session": sess.get("reuse_session", False),
        "symbols": sess.get("symbols") or [],
        "cycles_count": sess.get("cycles_count", len(sess.get("cycles") or [])),
        "cycle_errors_count": sess.get("cycle_errors_count", 0),
        "start_equity_vst": start_eq,
        "current_equity_vst": cur_eq,
        "equity_delta_vst": realized_delta,
        "unrealized_pnl_vst": sess.get("unrealized_pnl_vst", 0.0),
        "open_positions": sess.get("open_positions") or [],
        "open_orders": sess.get("open_orders") or [],
        "active_theses": active_theses,
        "recent_decisions": audits[-max(1, int(last_n)) :],
        "recent_journal_trades": _read_journal_tail(eff_journal, limit=last_n),
        "usage_summary": usage_summary,
        "projections": {
            "elapsed_hours_basis": round(elapsed_h, 4),
            "iecu_1h_proj": iecu_per_1h,
            "iecu_5h_proj": iecu_per_5h,
            "input_tokens_1h_proj": in_tok_per_1h,
            "input_tokens_5h_proj": in_tok_per_1h * 5,
        },
        "api_stats": {
            "rest_calls_total": int(api_stats.get("rest_calls_total", 0)),
            "rest_cache_hits": int(api_stats.get("rest_cache_hits", 0)),
            "ws_updates_applied": int(api_stats.get("ws_updates_applied", 0)),
            "rate_limit_retries": int(api_stats.get("rate_limit_retries", 0)),
            "watcher_mode": sess.get("watcher_mode", api_stats.get("watcher_mode", "IDLE")),
            "watcher_runs": int(sess.get("watcher_runs", 0)),
        },
    }


def render_human_briefing(data: dict[str, Any]) -> str:
    u = data.get("usage_summary") or {}
    proj = data.get("projections") or {}
    api = data.get("api_stats") or {}
    lines: list[str] = [
        "# QuantumEdge Hybrid — Оперативний Бріфінг Сесії (QE-04)",
        "",
        "## 1. Статус сесії, Баланс/PnL, Позиції та Активні Тези",
        f"- **Статус / Режим:** `{data.get('status')}` / `{data.get('mode')}` | **Модель:** `{data.get('model')}` (`{data.get('transport')}`, reuse_session=`{data.get('reuse_session')}`)",
        f"- **Всесвіт пар:** `{','.join(data.get('symbols') or []) or 'N/A'}` | **Циклів:** `{data.get('cycles_count')}` (помилок=`{data.get('cycle_errors_count')}`)",
        f"- **Баланс (VST):** старт `{data.get('start_equity_vst')}` -> поточний `{data.get('current_equity_vst')}` (Δ=`{data.get('equity_delta_vst')}` VST, uPnL=`{data.get('unrealized_pnl_vst')}` VST)",
    ]

    positions = data.get("open_positions") or []
    if positions:
        lines.append(f"- **Відкриті позиції ({len(positions)}):**")
        for p in positions:
            lines.append(
                f"  - `{p.get('symbol')}` `{p.get('positionSide') or p.get('side')}` qty=`{p.get('contracts')}` "
                f"entry=`{p.get('entry_price')}` mark=`{p.get('mark_price')}` uPnL=`{p.get('unrealized_pnl')}` VST"
            )
    else:
        lines.append("- **Відкриті позиції:** `0 (FLAT)`")

    theses = data.get("active_theses") or []
    if theses:
        lines.append(f"- **Активні тези ThesisMemory ({len(theses)}):**")
        for t in theses:
            lines.append(
                f"  - `{t.get('symbol')}` `{t.get('direction')}` (conf=`{t.get('confidence')}`): "
                f"inv=`{t.get('invalidation_json')}` | {t.get('summary_text')}"
            )
    else:
        lines.append("- **Активні тези ThesisMemory:** немає активних записів")

    decs = data.get("recent_decisions") or []
    lines.extend(["", f"## 2. Останні рішення агента ({len(decs)})"])
    if not decs:
        lines.append("- Рішення відсутні.")
    for idx, d in enumerate(decs, start=1):
        sz = d.get("sizing") if isinstance(d.get("sizing"), dict) else None
        sz_str = (
            f"risk=`{sz.get('risk_score')}` conv=`{sz.get('conviction')}` notional=`{sz.get('notional_vst')}` VST (`{sz.get('margin_vst')}` x `{sz.get('leverage')}x`)"
            if sz
            else "N/A (HOLD/CLOSE)"
        )
        lines.extend(
            [
                f"### #{idx} `{d.get('decision_id')}` — `{d.get('action')}` `{d.get('symbol')}` (think=`{d.get('thinking_level')}`, exec=`{d.get('execution_status')}`)",
                f"- **Режим ринку:** {d.get('regime_read') or 'N/A'}",
                f"- **Перевага (Edge):** {d.get('primary_edge') or 'N/A'}",
                f"- **Контр-теза:** {d.get('counter_thesis') or 'N/A'}",
                f"- **Ордер / Сайзинг:** type=`{d.get('order_type')}` entry=`{d.get('entry_price')}` sl=`{d.get('sl_price')}` tp=`{d.get('tp_price')}` | {sz_str}",
            ]
        )

    lines.extend(
        [
            "",
            "## 3. Токен-економіка (UsageLedger) та Навантаження BingX API",
            f"- **Запити LLM:** `{u.get('total_requests', 0)}` (fail_rate=`{u.get('failure_rate', 0.0)}`, p95=`{u.get('latency_imputed_p95_ms')}` ms)",
            f"- **Токени:** in=`{u.get('sum_provider_input_tokens', 0)}` | cache=`{u.get('sum_cache_read_tokens', 0)}` | visible_out=`{u.get('sum_output_tokens', 0)}` | think=`{u.get('sum_thinking_tokens', 0)}`",
            f"- **Сумарний IECU:** `{u.get('sum_iecu', 0.0)}` | **Прогноз 1 год:** `~{proj.get('iecu_1h_proj', 0.0)}` IECU (`~{proj.get('input_tokens_1h_proj', 0)}` in tok) | **Прогноз 5 год:** `~{proj.get('iecu_5h_proj', 0.0)}` IECU",
            f"- **BingX API Телеметрія:** REST calls=`{api.get('rest_calls_total', 0)}` | RAM cache hits=`{api.get('rest_cache_hits', 0)}` | WS updates=`{api.get('ws_updates_applied', 0)}` | Rate-limit retries=`{api.get('rate_limit_retries', 0)}` | Watcher=`{api.get('watcher_mode')}` (runs=`{api.get('watcher_runs', 0)}`)",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qe_briefing",
        description="Human & AGY TUI live session briefing reader (RAM-First, 0 LLM tokens)",
    )
    p.add_argument("--session-json", type=Path, default=None, help="Path to RAM latest_session.json")
    p.add_argument("--ledger-db", type=Path, default=None, help="Path to UsageLedger SQLite DB")
    p.add_argument("--thesis-db", type=Path, default=None, help="Path to ThesisMemoryStore SQLite DB")
    p.add_argument("--journal", type=Path, default=None, help="Path to lean_trade_journal.jsonl")
    p.add_argument("--last", type=int, default=5, help="Number of recent decisions to render (default: 5)")
    p.add_argument("--json", action="store_true", default=False, help="Output structured JSON instead of Markdown")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_briefing_payload(
        session_json=args.session_json,
        ledger_db=args.ledger_db,
        thesis_db=args.thesis_db,
        journal_path=args.journal,
        last_n=args.last,
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False), flush=True)
    else:
        print(render_human_briefing(payload), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
