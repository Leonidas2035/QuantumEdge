# QuantumEdge Persistent VST Session Report

## 1. Session Summary
- **Started UTC:** `2026-11-27T15:43:09.794570+00:00`
- **Finished UTC:** `2026-11-27T17:43:14.062081+00:00`
- **Mode:** `LIVE_VST`
- **Transport / Model:** `agy-direct` / `gemini-4-carbon` (reuse_session=`True`)
- **Universe (1d/4h + 1h/15m/5m):** `BNBUSDT,LINKUSDT,SUIUSDT`
- **Start Equity (VST):** `268172.3813` -> **End Equity (VST):** `267908.1275`
- **Cycles Executed:** `18` (cycle_errors=`1`, quota_stopped=`False`) | **Prefix Hash Stable:** `False` (`['04989b42b2859415', '67057a10cdfb3a9d', '747d9aff8111d03b', '96fbf5f846ca1305', 'b9b5109124ae19d9']`)
- **PositionProtectionWatcher Runs:** `1265`

## 2. UsageLedger Telemetry
- Total Requests: `34` (failures=`0.0588`)
- Provider Input Tokens: `1597934` | Cache Read Tokens: `637213`
- Output Tokens: `166926` | Thinking Tokens: `148636`
- Sum IECU: `2575268.65` | Latency p95 (ms): `106901.97`

## 3. Cycle Log
| # | Decision ID | Resync | Prefix Hash | Action | Symbol | Thinking | Validator | Exec Status |
|---:|---|---|---|---|---|---|---|---|
| 1 | `qe-1795794197-3fe2117a` | `RESYNC` | `67057a10cdfb3a9d` | `OPEN_LONG` | `SUIUSDT` | `medium` | `True` | `SUBMITTED` |
| 2 | `qe-1795794564-deca18ed` | `LOCAL_RECONSTRUCT` | `67057a10cdfb3a9d` | `OPEN_LONG` | `BNBUSDT` | `medium` | `True` | `SUBMITTED` |
| 3 | `qe-1795794965-735f34ea` | `LOCAL_RECONSTRUCT` | `67057a10cdfb3a9d` | `HOLD` | `LINKUSDT` | `medium` | `True` | `SKIPPED_HOLD` |
| 4 | `qe-1795795400-bafe0a48` | `RESYNC` | `96fbf5f846ca1305` | `OPEN_SHORT` | `LINKUSDT` | `medium` | `True` | `SUBMITTED` |
| 5 | `qe-1795795818-4b34bd07` | `LOCAL_RECONSTRUCT` | `96fbf5f846ca1305` | `HOLD` | `BNBUSDT` | `medium` | `True` | `SKIPPED_HOLD` |
| 6 | `qe-1795796180-fd67600c` | `LOCAL_RECONSTRUCT` | `96fbf5f846ca1305` | `HOLD` | `LINKUSDT` | `medium` | `True` | `SKIPPED_HOLD` |
| 7 | `qe-1795796544-ae6fe0e2` | `LOCAL_RECONSTRUCT` | `96fbf5f846ca1305` | `HOLD` | `BNBUSDT` | `medium` | `True` | `SKIPPED_HOLD` |
| 8 | `qe-1795797106-026a7833` | `LOCAL_RECONSTRUCT` | `96fbf5f846ca1305` | `OPEN_SHORT` | `SUIUSDT` | `medium` | `True` | `SUBMITTED` |
| 9 | `qe-1795797523-35434008` | `RESYNC` | `04989b42b2859415` | `OPEN_LONG` | `LINKUSDT` | `medium` | `True` | `SUBMITTED` |
| 10 | `qe-1795797926-25ecfcdb` | `LOCAL_RECONSTRUCT` | `04989b42b2859415` | `HOLD` | `SUIUSDT` | `medium` | `True` | `SKIPPED_HOLD` |
| 11 | `qe-1795798510-66d27239` | `LOCAL_RECONSTRUCT` | `04989b42b2859415` | `HOLD` | `LINKUSDT` | `medium` | `True` | `SKIPPED_HOLD` |
| 12 | `qe-1795798986-4c6576a9` | `RESYNC` | `b9b5109124ae19d9` | `HOLD` | `LINKUSDT` | `medium` | `True` | `SKIPPED_HOLD` |
| 13 | `None` | `ERROR` | `` | `CYCLE_ERROR` | `None` | `N/A` | `False` | `ERROR` |
| 14 | `qe-1795799724-45f1be61` | `LOCAL_RECONSTRUCT` | `b9b5109124ae19d9` | `HOLD` | `BNBUSDT` | `low` | `True` | `SKIPPED_HOLD` |
| 15 | `qe-1795800038-c3c194bd` | `LOCAL_RECONSTRUCT` | `b9b5109124ae19d9` | `HOLD` | `BNBUSDT` | `low` | `True` | `SKIPPED_HOLD` |
| 16 | `qe-1795800387-c166adfc` | `LOCAL_RECONSTRUCT` | `b9b5109124ae19d9` | `OPEN_LONG` | `LINKUSDT` | `medium` | `True` | `SUBMITTED` |
| 17 | `qe-1795800768-87b6dea5` | `RESYNC` | `747d9aff8111d03b` | `HOLD` | `BNBUSDT` | `high` | `True` | `SKIPPED_HOLD` |
| 18 | `qe-1795801165-88cff671` | `LOCAL_RECONSTRUCT` | `747d9aff8111d03b` | `HOLD` | `BNBUSDT` | `high` | `True` | `SKIPPED_HOLD` |
