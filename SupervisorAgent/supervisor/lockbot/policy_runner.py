"""LockBotBTC policy runner (regime + strategy orchestration)."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from supervisor.lockbot.hub_subscriber import LockbotHubSubscriber, MarketDataCache
from supervisor.lockbot.models import (
    BotStatusSnapshot,
    MarketSnapshot,
    PolicyIntent,
    PolicyRunnerConfig,
    StrategyDecision,
)
from supervisor.lockbot.regime_detector import RegimeDetector, RegimeHysteresis
from supervisor.lockbot.strategy_range import evaluate_range
from supervisor.lockbot.strategy_trend import evaluate_trend


class PolicyAuditLogger:
    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self._path = path
        self._logger = logger
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=200)

    def append(self, record: Dict[str, Any]) -> None:
        self._recent.append(record)
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError as exc:
            self._logger.warning("Policy audit log write failed: %s", exc)

    def recent(self, limit: int = 20) -> list[Dict[str, Any]]:
        if limit <= 0:
            return []
        return list(self._recent)[-limit:]


class LockbotPolicyRunner:
    def __init__(
        self,
        cfg: PolicyRunnerConfig,
        control_client: Any,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._cfg = cfg
        self._control = control_client
        self._logger = logger or logging.getLogger(__name__)
        self._market_cache = MarketDataCache(cfg.symbol)
        self._hub_sub = LockbotHubSubscriber(cfg.hub_sub_endpoint, cfg.hub_topics, self._market_cache)
        self._regime_detector = RegimeDetector(cfg.regime)
        self._hysteresis = RegimeHysteresis(cfg.regime)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._enabled = cfg.enabled
        self._manual_hold = False
        self._manual_hold_sent = False
        self._last_cmd_window_ms = 0
        self._cmds_in_window = 0
        self._exec_step_ts: Deque[int] = deque()
        self._cooldown_until_ms = 0
        self._last_target_sent_ms = 0
        self._last_regime_sent: Optional[str] = None
        self._last_target_payload: Optional[Dict[str, Any]] = None
        self._last_ddn_verdict: Optional[str] = None
        self._reject_count = 0
        self._pending_cmds: Dict[str, Dict[str, Any]] = {}
        self._audit = PolicyAuditLogger(Path(cfg.audit_log_path), self._logger)
        self._last_decision: Optional[Dict[str, Any]] = None

    def start(self) -> None:
        if self._thread:
            return
        self._hub_sub.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._hub_sub.stop()
        self._thread = None

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if enabled:
            self._manual_hold = False
            self._manual_hold_sent = False
            self._reject_count = 0

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "manual_hold": self._manual_hold,
            "last_decision": self._last_decision,
            "current_regime": self._hysteresis.current(),
        }

    def decisions(self, limit: int = 20) -> list[Dict[str, Any]]:
        return self._audit.recent(limit)

    def run_once(self, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if not self._enabled:
            return None
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        market = self._market_cache.snapshot()
        status = self._snapshot_status()
        if status is None:
            return None

        market_lag = _lag_ms(now_ms, market.mark_ts)
        account_lag = status.account_lag_ms
        is_stale = _is_stale(market_lag, account_lag, self._cfg.max_market_lag_ms, self._cfg.max_account_lag_ms)
        lock_present = status.long_qty >= self._cfg.min_leg_qty and status.short_qty >= self._cfg.min_leg_qty

        if status.ddn_verdict == "REJECT" and self._last_ddn_verdict != "REJECT":
            self._cooldown_until_ms = max(self._cooldown_until_ms, now_ms + self._cfg.cooldown_after_reject_ms)
            self._reject_count += 1
        elif status.ddn_verdict in {"ALLOW", "MODIFY", "PANIC_ONLY"}:
            self._reject_count = 0
        self._last_ddn_verdict = status.ddn_verdict

        if self._reject_count >= self._cfg.reject_pause_threshold:
            if not self._manual_hold:
                self._manual_hold = True
                self._manual_hold_sent = False

        regime_decision = self._regime_detector.evaluate(market)
        current_regime, changed = self._hysteresis.update(regime_decision.candidate, now_ms)
        intent_sent: Optional[PolicyIntent] = None
        reason = "noop"
        if is_stale:
            intent_sent = self._stale_intent()
            reason = "stale_data"
            self._send_intents([intent_sent], now_ms, lock_present)
        elif self._manual_hold:
            if not self._manual_hold_sent:
                pause_intent = PolicyIntent(cmd="PAUSE", payload={"reason": "ddn_rejects"}, reason="manual_hold", priority=5)
                self._send_intents([pause_intent], now_ms, lock_present)
                self._manual_hold_sent = True
            reason = "manual_hold"
        else:
            intents = []
            if changed or self._last_regime_sent != current_regime:
                intents.append(_intent_set_regime(current_regime))
            target_intent = self._target_intent(current_regime, now_ms)
            if target_intent:
                intents.append(target_intent)
            if current_regime == "RANGE":
                decision = evaluate_range(market, status, self._cfg.range_policy)
                if decision.intent:
                    intents.append(decision.intent)
                    reason = decision.reason
            elif current_regime in {"TREND_UP", "TREND_DOWN"}:
                decision = evaluate_trend(market, status, self._cfg.trend_policy, current_regime)
                if decision.intent:
                    intents.append(decision.intent)
                    reason = decision.reason
            elif current_regime == "CHAOS":
                intents.append(self._stale_intent(reason="chaos"))
                reason = "chaos"
            intent_sent = self._send_intents(intents, now_ms, lock_present)

        record = self._build_record(
            now_ms,
            market,
            status,
            current_regime,
            regime_decision,
            is_stale,
            intent_sent,
            reason,
        )
        self._audit.append(record)
        self._last_decision = record
        return record

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("Lockbot policy runner tick failed: %s", exc)
            time.sleep(max(self._cfg.tick_interval_ms, 200) / 1000.0)

    def _snapshot_status(self) -> Optional[BotStatusSnapshot]:
        raw = self._control.status()
        if not raw:
            return None
        payload = raw.get("payload") if isinstance(raw, dict) else None
        if not isinstance(payload, dict):
            return None
        status_payload = payload.get("payload") if "payload" in payload else payload
        if not isinstance(status_payload, dict):
            return None
        positions = status_payload.get("positions") if isinstance(status_payload.get("positions"), dict) else {}
        lags = status_payload.get("lags") if isinstance(status_payload.get("lags"), dict) else {}
        ddn = status_payload.get("ddn") if isinstance(status_payload.get("ddn"), dict) else {}
        policy = status_payload.get("policy") if isinstance(status_payload.get("policy"), dict) else {}
        return BotStatusSnapshot(
            mode=str(status_payload.get("mode", "UNKNOWN")),
            regime=str(status_payload.get("regime", "UNKNOWN")),
            net_delta=float(status_payload.get("net_delta_est") or 0.0),
            long_qty=float(positions.get("long_qty") or 0.0),
            short_qty=float(positions.get("short_qty") or 0.0),
            market_lag_ms=_safe_int(lags.get("market_lag_ms")),
            account_lag_ms=_safe_int(lags.get("account_lag_ms")),
            ddn_verdict=str(ddn.get("last_verdict")) if ddn.get("last_verdict") is not None else None,
            ddn_reasons=list(ddn.get("last_reasons") or []),
            last_cmd_type=str(policy.get("last_cmd_type")) if policy.get("last_cmd_type") is not None else None,
            last_cmd_id=str(policy.get("last_cmd_id")) if policy.get("last_cmd_id") is not None else None,
            last_cmd_ts=_safe_int(policy.get("last_cmd_ts")),
        )

    def _stale_intent(self, reason: str = "stale_data") -> PolicyIntent:
        cmd = "PANIC_LOCK" if self._cfg.stale_action == "PANIC_LOCK" else "PAUSE"
        payload: Dict[str, Any] = {"reason": reason}
        if cmd == "PANIC_LOCK":
            payload["force_1to1"] = True
        return PolicyIntent(cmd=cmd, payload=payload, reason=reason, priority=10)

    def _target_intent(self, regime: str, now_ms: int) -> Optional[PolicyIntent]:
        if regime == "RANGE":
            target = self._cfg.range_policy.target
            band_low = self._cfg.range_policy.band_low
            band_high = self._cfg.range_policy.band_high
            refresh_s = self._cfg.trend_policy.target_refresh_s
        elif regime == "TREND_UP":
            target = self._cfg.trend_policy.target_up
            band_low = self._cfg.trend_policy.band_low
            band_high = self._cfg.trend_policy.band_high
            refresh_s = self._cfg.trend_policy.target_refresh_s
        elif regime == "TREND_DOWN":
            target = self._cfg.trend_policy.target_down
            band_low = self._cfg.trend_policy.band_low
            band_high = self._cfg.trend_policy.band_high
            refresh_s = self._cfg.trend_policy.target_refresh_s
        else:
            return None
        payload = {
            "target": target,
            "band_low": band_low,
            "band_high": band_high,
            "reason": f"policy:{regime}",
        }
        if self._last_target_payload == payload:
            if now_ms - self._last_target_sent_ms < refresh_s * 1000:
                return None
        self._last_target_payload = dict(payload)
        return PolicyIntent(cmd="SET_DELTA_TARGET", payload=payload, reason="set_target", priority=30)

    def _send_intents(self, intents: list[PolicyIntent], now_ms: int, lock_present: bool) -> Optional[PolicyIntent]:
        if not intents:
            return None
        sent = None
        intents_sorted = sorted(intents, key=lambda item: item.priority)
        for intent in intents_sorted:
            if sent and self._cfg.max_cmds_per_tick <= 1:
                break
            if intent.cmd == "EXEC_STEP" and not self._cfg.execution_enabled:
                continue
            if intent.cmd == "EXEC_STEP" and not lock_present:
                continue
            if intent.cmd == "EXEC_STEP":
                if now_ms < self._cooldown_until_ms:
                    continue
                if self._is_exec_rate_limited(now_ms):
                    continue
            if not self._can_send_cmd(now_ms):
                continue
            try:
                cmd_id = self._control.send_command(intent.cmd, intent.payload)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Policy cmd failed: %s", exc)
                continue
            self._pending_cmds[cmd_id] = {"cmd": intent.cmd, "payload": intent.payload}
            self._record_cmd(intent, now_ms)
            sent = intent
            if intent.cmd == "SET_REGIME":
                self._last_regime_sent = intent.payload.get("regime")
            if intent.cmd == "SET_DELTA_TARGET":
                self._last_target_sent_ms = now_ms
        return sent

    def _record_cmd(self, intent: PolicyIntent, now_ms: int) -> None:
        if now_ms - self._last_cmd_window_ms >= 1000:
            self._last_cmd_window_ms = now_ms
            self._cmds_in_window = 0
        self._cmds_in_window += 1
        if intent.cmd == "EXEC_STEP":
            self._exec_step_ts.append(now_ms)
            while self._exec_step_ts and now_ms - self._exec_step_ts[0] > 60_000:
                self._exec_step_ts.popleft()

    def _can_send_cmd(self, now_ms: int) -> bool:
        if now_ms - self._last_cmd_window_ms >= 1000:
            self._last_cmd_window_ms = now_ms
            self._cmds_in_window = 0
        return self._cmds_in_window < max(self._cfg.max_cmds_per_sec, 1)

    def _is_exec_rate_limited(self, now_ms: int) -> bool:
        while self._exec_step_ts and now_ms - self._exec_step_ts[0] > 60_000:
            self._exec_step_ts.popleft()
        return len(self._exec_step_ts) >= max(self._cfg.max_exec_steps_per_minute, 1)

    def _build_record(
        self,
        now_ms: int,
        market: MarketSnapshot,
        status: BotStatusSnapshot,
        current_regime: str,
        regime_decision: Any,
        is_stale: bool,
        intent: Optional[PolicyIntent],
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "ts_ms": now_ms,
            "regime": current_regime,
            "candidate": regime_decision.candidate,
            "signals": {
                "adx": regime_decision.signals.adx,
                "atr": regime_decision.signals.atr,
                "atr_baseline": regime_decision.signals.atr_baseline,
                "slope_bps": regime_decision.signals.slope_bps,
                "chaos": regime_decision.signals.chaos,
                "chaos_reasons": regime_decision.signals.chaos_reasons,
            },
            "market": {
                "mark_price": market.mark_price,
                "vwap": market.vwap,
                "band_1u": market.band_1u,
                "band_1l": market.band_1l,
                "band_2u": market.band_2u,
                "band_2l": market.band_2l,
                "avwap": market.avwap,
                "avwap_anchor": market.avwap_anchor,
                "liq_above": market.liq.intensity_above,
                "liq_below": market.liq.intensity_below,
            },
            "status": {
                "mode": status.mode,
                "regime": status.regime,
                "net_delta": status.net_delta,
                "long_qty": status.long_qty,
                "short_qty": status.short_qty,
                "ddn_verdict": status.ddn_verdict,
                "ddn_reasons": list(status.ddn_reasons),
            },
            "intent": {
                "cmd": intent.cmd,
                "payload": intent.payload,
                "reason": intent.reason,
            }
            if intent
            else None,
            "reason": reason,
            "stale": is_stale,
            "lock_present": status.long_qty >= self._cfg.min_leg_qty and status.short_qty >= self._cfg.min_leg_qty,
            "manual_hold": self._manual_hold,
        }


def _intent_set_regime(regime: str) -> PolicyIntent:
    return PolicyIntent(cmd="SET_REGIME", payload={"regime": regime, "reason": "policy_regime"}, reason="set_regime", priority=20)


def _safe_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lag_ms(now_ms: int, last_ts_ms: Optional[int]) -> Optional[int]:
    if last_ts_ms is None:
        return None
    return max(0, now_ms - last_ts_ms)


def _is_stale(
    market_lag_ms: Optional[int],
    account_lag_ms: Optional[int],
    max_market_lag_ms: int,
    max_account_lag_ms: int,
) -> bool:
    if market_lag_ms is None:
        return True
    if market_lag_ms > max_market_lag_ms:
        return True
    if account_lag_ms is None:
        return False
    return account_lag_ms > max_account_lag_ms
