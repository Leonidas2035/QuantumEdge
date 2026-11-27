# QE_D2_04B_OVERNIGHT_CRASH_PROOF_FORENSIC_REPORTING_AND_QUOTA_COOLDOWN_V1

## 1. Key-Value Summary

```text
TASK=QE_D2_04B_OVERNIGHT_CRASH_PROOF_FORENSIC_REPORTING_AND_QUOTA_COOLDOWN_V1
FINAL_VERDICT=PASS

BASE_BRANCH=feat/qe-d2-04-market-data-infra
BASE_SHA=0974d542fc3399b9a4ff02789325ac042cc44a40
TARGET_BRANCH=feat/qe-d2-04-market-data-infra

INCREMENTAL_REPORT_AND_DURABLE_EVENT_LOG_READY=true
FORENSIC_REPORT_6_SECTIONS_WITH_TRACEBACK_READY=true
QUOTA_COOLDOWN_RESUME_READY=true
QE_BRIEFING_EVENT_LOG_FALLBACK_READY=true
ZERO_DUPLICATION_GUARDS_PASS=true

COMPILEALL_STATUS=PASS
PYTEST_QE04_SUITE=10 passed
PYTEST_FULL_SUITE_NEW_CORE=167 passed, 1 skipped in 37.67s
TOTAL_FAILED=0

EXTERNAL_REPOS_TOUCHED=false
GLOBAL_DOT_HERMES_MODIFIED=false
SECRETS_READ_OR_MODIFIED=false
LIVE_READS=0
LIVE_WRITES=0
PROVIDER_GETS=0
PROVIDER_MUTATIONS=0
SERVICE_RESTARTS=0
DEPLOYED=false
REAL_TRADING=false

NEXT_GATE=LAUNCH_8H_OVERNIGHT_LIVE_VST_SESSION
```

## 2. Hardening Changes

- `tools/run_vst_session.py` (modified):
  - Added `faulthandler.enable()` and atomic SSD report writer `_atomic_write_text()` (`.tmp -> flush -> os.fsync -> os.replace`) updating `--report-out` after every cycle (`SESSION_STATUS=IN_PROGRESS`) and finalizing in `finally:` as `COMPLETED`, `STOPPED_BY_SIGNAL`, `STOPPED_BY_QUOTA`, or `CRASHED`.
  - Added `--gdrive-sync-every-cycles` (default `6`) for periodic fail-soft Google Drive synchronization plus final exit sync.
  - Added `--event-log-path` (`_append_durable_event()` with `flush()` + `os.fsync()`) persisting `SESSION_START`, `WATCHER_ACTION`, `CYCLE_COMPLETE`, `CYCLE_ERROR` (with `traceback`), `QUOTA_COOLDOWN_WAIT`, `FATAL_CRASH`, and `SESSION_END`.
  - Upgraded `_render_session_report()` (`_render_session_markdown` alias) to 6 forensic sections: `KEY=VALUE` summary, `UsageLedger` + `1h/5h/24h` IECU projections + `ApiBudgetStats`, full cycle table (`BTC_15m/4h`, `Entry/SL/TP`, `Risk/Conv`, `Notional`, `OpenPos`, `uPnL`, `IECU_cum`), full thesis/sizing chronology, watcher actions & `exit_cleanup`, and errors/`rate_limit_retries`/tracebacks.
  - Added `--quota-cooldown-sec` (default `900.0`s): on `QUOTA_REACHED` when `remaining > quota_cooldown_sec`, logs `QUOTA_COOLDOWN_WAIT`, keeps the 5-second `PositionProtectionWatcher` active, awaits `stop_requested` up to `quota_cooldown_sec` (preserving instant `Ctrl+C`), and resumes trading cleanly.
- `tools/qe_briefing.py` (modified):
  - Added `--event-log` and automatic fallback discovery (`runtime/sessions/*.jsonl`) + `_reconstruct_session_from_event_log()` when `/dev/shm/quantumedge/sessions/latest_session.json` is absent.
- `tests/test_qe04_market_data_infra_and_briefing.py` (modified):
  - Added 3 offline regression tests verifying incremental `IN_PROGRESS -> COMPLETED` report + `fsync` durable event log + traceback capture, `QUOTA_COOLDOWN_WAIT` resume without aborting, and `qe_briefing.py` event-log fallback.

## 3. Overnight 8H Live VST Launch Command

```bash
cd /home/layman/QuantumEdge
.venv/bin/python tools/run_vst_session.py \
  --live-vst \
  --transport agy-direct \
  --model gemini-4-carbon \
  --symbols BNBUSDT,LINKUSDT,SUIUSDT \
  --duration-minutes 480.0 \
  --cycle-interval-sec 300.0 \
  --protection-interval-sec 5.0 \
  --protection-idle-interval-sec 25.0 \
  --be-trigger-pct 0.50 \
  --be-offset-pct 0.20 \
  --quota-cooldown-sec 900.0 \
  --gdrive-sync-every-cycles 6 \
  --event-log-path runtime/sessions/live_8h_events.jsonl \
  --record-replay \
  --close-on-exit \
  --report-out reports/tasks/QE_LIVE_8H_OVERNIGHT_SESSION_REPORT.md \
  --sync-gdrive
```
