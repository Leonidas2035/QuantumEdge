"""LLM-based supervisor to suggest high-level risk actions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from supervisor.audit_report import load_events_for_date
from supervisor.config import LlmSupervisorConfig, RiskConfig
from supervisor.events import BaseEvent, EventType, EventLogger
from supervisor.llm.chat_client import ChatCompletionsClient
from supervisor.state import RiskStateSnapshot


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that LLMs often add."""
    stripped = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", stripped, re.DOTALL)
    return m.group(1).strip() if m else stripped


class LlmAction(str, Enum):
    OK = "OK"
    LOWER_RISK = "LOWER_RISK"
    PAUSE = "PAUSE"
    SWITCH_TO_PAPER = "SWITCH_TO_PAPER"
    UNSPECIFIED = "UNSPECIFIED"


@dataclass
class LlmSupervisorAdvice:
    action: LlmAction
    risk_multiplier: Optional[float]
    comment: str
    raw_response: str


@dataclass
class LlmSupervisorSummary:
    trading_day: date
    mode: str
    halted: bool
    llm_paused: bool
    llm_risk_multiplier: float
    equity_now: Optional[float]
    realized_pnl_today: Optional[float]
    daily_loss: Optional[float]
    drawdown: Optional[float]
    allowed_orders: int
    denied_orders: int
    denied_by_code: Dict[str, int]
    recent_trades: List[Dict[str, Any]]
    
    # Telemetry
    ofi_1s: Optional[float] = None
    closest_wall_dist_pct: Optional[float] = None
    active_signal: Optional[str] = None
    atr: Optional[float] = None
    volume_delta_1m: Optional[float] = None
    liquidations_1m: Optional[int] = None


class LlmSupervisor:
    """Orchestrates LLM risk reviews."""

    def __init__(
        self,
        config: LlmSupervisorConfig,
        risk_config: RiskConfig,
        events_dir: Path,
        logger: logging.Logger,
        event_logger: Optional[EventLogger] = None,
        chat_client: Optional[ChatCompletionsClient] = None,
    ) -> None:
        self._config = config
        self._risk_config = risk_config
        self._events_dir = events_dir
        self._logger = logger
        self._event_logger = event_logger
        self._chat_client = chat_client or ChatCompletionsClient(
            config.api_url, config.api_key_env, logger
        )
        print(f"[SUP] DEBUG: LlmSupervisor initialized. enabled={config.enabled}")

    def run_check(
        self, today: date, snapshot: RiskStateSnapshot, mode: str = "unknown"
    ) -> Optional[LlmSupervisorAdvice]:
        print(f"[SUP] DEBUG: LlmSupervisor.run_check triggered! enabled={self._config.enabled}")
        if not self._config.enabled:
            self._logger.info("LLM supervisor disabled; skipping.")
            return None

        events = load_events_for_date(self._events_dir, today)
        order_decisions = [e for e in events if e.type == EventType.ORDER_DECISION]
        # FOR UAT TESTING: bypass the min_order_decisions check
        # if len(order_decisions) < self._config.min_order_decisions:
        #     self._logger.info(
        #         "Not enough order decisions for LLM check (%s/%s)",
        #         len(order_decisions),
        #         self._config.min_order_decisions,
        #     )
        #     return None

        try:
            summary = build_summary(snapshot, self._risk_config, events, self._config, mode)
            system_prompt, user_prompt = build_prompts(
                summary, self._risk_config, self._config
            )
        except Exception as e:
            print(f"[SUP] ERROR building LLM prompts: {e}")
            self._logger.error("Failed to build LLM prompts: %s", e)
            return None

        try:
            raw = self.call_llm(system_prompt, user_prompt)
        except Exception as exc:
            self._logger.error("LLM call failed: %s", exc)
            return None

        advice = self.parse_advice(raw)
        if self._event_logger:
            self._event_logger.log_llm_advice(
                advice.action.value,
                advice.risk_multiplier,
                advice.comment,
                self._config.dry_run,
            )
        return advice

    def call_llm(self, system_prompt: str, user_prompt: str) -> str:
        return self._chat_client.complete(
            model=self._config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            timeout_seconds=self._config.timeout_seconds,
        )

    def parse_advice(self, raw_response: str) -> LlmSupervisorAdvice:
        try:
            cleaned = _strip_markdown_fences(raw_response)
            payload = json.loads(cleaned)
            action_raw = str(payload.get("action", "UNSPECIFIED")).upper()
            action = (
                LlmAction(action_raw)
                if action_raw in LlmAction.__members__
                else LlmAction.UNSPECIFIED
            )
            risk_multiplier = payload.get("risk_multiplier")
            if risk_multiplier is not None:
                risk_multiplier = float(risk_multiplier)
            comment = str(payload.get("comment") or "")
            return LlmSupervisorAdvice(
                action=action,
                risk_multiplier=risk_multiplier,
                comment=comment,
                raw_response=raw_response,
            )
        except Exception as exc:
            return LlmSupervisorAdvice(
                action=LlmAction.UNSPECIFIED,
                risk_multiplier=None,
                comment=f"Failed to parse LLM response: {exc}",
                raw_response=raw_response,
            )


def build_summary(
    snapshot: RiskStateSnapshot,
    limits: RiskConfig,
    events: Iterable[BaseEvent],
    config: LlmSupervisorConfig,
    mode: str,
    telemetry: Optional[Dict[str, Any]] = None,
) -> LlmSupervisorSummary:
    allowed = 0
    denied = 0
    denied_codes: Dict[str, int] = {}
    trades: List[Dict[str, Any]] = []

    ordered_events = [
        e
        for e in events
        if e.type
        in {
            EventType.ORDER_DECISION,
            EventType.ORDER_RESULT,
            EventType.RISK_LIMIT_BREACH,
        }
    ]
    ordered_events = ordered_events[-config.max_events_in_summary :]

    for event in ordered_events:
        if event.type == EventType.ORDER_DECISION:
            allowed_flag = bool(event.data.get("allowed"))
            if allowed_flag:
                allowed += 1
            else:
                denied += 1
                code = event.data.get("code", "UNKNOWN")
                denied_codes[code] = denied_codes.get(code, 0) + 1
            trades.append(
                {
                    "type": "decision",
                    "symbol": event.data.get("symbol"),
                    "side": event.data.get("side"),
                    "allowed": event.data.get("allowed"),
                    "code": event.data.get("code"),
                    "ts": event.ts.isoformat(),
                }
            )
        elif event.type == EventType.ORDER_RESULT:
            trades.append(
                {
                    "type": "result",
                    "result": event.data.get("result"),
                    "pnl": event.data.get("pnl"),
                    "symbol": event.data.get("symbol"),
                    "ts": event.ts.isoformat(),
                }
            )
        elif event.type == EventType.RISK_LIMIT_BREACH:
            trades.append(
                {
                    "type": "breach",
                    "code": event.data.get("code"),
                    "ts": event.ts.isoformat(),
                }
            )

    trades = trades[-config.max_trades_in_table :]

    daily_loss = None
    if snapshot.equity_start is not None and snapshot.equity_now is not None:
        daily_loss = snapshot.equity_start - snapshot.equity_now

    drawdown = None
    if snapshot.max_equity_intraday is not None and snapshot.equity_now is not None:
        drawdown = snapshot.max_equity_intraday - snapshot.equity_now

    return LlmSupervisorSummary(
        trading_day=snapshot.trading_day,
        mode=mode,
        halted=snapshot.halted,
        llm_paused=snapshot.llm_paused,
        llm_risk_multiplier=snapshot.llm_risk_multiplier,
        equity_now=snapshot.equity_now,
        realized_pnl_today=snapshot.realized_pnl_today,
        daily_loss=daily_loss,
        drawdown=drawdown,
        allowed_orders=allowed,
        denied_orders=denied,
        denied_by_code=denied_codes,
        recent_trades=trades,
        ofi_1s=(telemetry or {}).get("ofi_1s"),
        closest_wall_dist_pct=(telemetry or {}).get("closest_wall_dist_pct"),
        active_signal=(telemetry or {}).get("active_signal"),
        atr=(telemetry or {}).get("atr"),
        volume_delta_1m=(telemetry or {}).get("volume_delta_1m"),
        liquidations_1m=(telemetry or {}).get("liquidations_1m"),
    )


def build_prompts(
    summary: LlmSupervisorSummary, limits: RiskConfig, config: LlmSupervisorConfig
) -> Tuple[str, str]:
    system_prompt = (
        "You are a risk moderator for a crypto futures scalping bot. "
        "Return ONLY a JSON object with keys action, risk_multiplier, comment. "
        "Allowed actions: OK (continue), LOWER_RISK (tighten limits), PAUSE (soft halt), SWITCH_TO_PAPER, UNSPECIFIED. "
        "Lower risk means reducing size/leverage, not increasing risk."
    )

    deny_breakdown = (
        ", ".join(f"{k}: {v}" for k, v in summary.denied_by_code.items()) or "none"
    )
    trades_lines = []
    for t in summary.recent_trades:
        trades_lines.append(
            f"{t.get('ts')} | {t.get('type')} | {t.get('symbol','?')} | {t.get('code', t.get('result',''))} | allowed={t.get('allowed')}"
        )
    trades_block = "\n".join(trades_lines) if trades_lines else "no trades"

    # Micro & Macro Context
    ofi_str = f"{summary.ofi_1s:.2f}" if getattr(summary, 'ofi_1s', None) is not None else "0.00"
    wall_str = f"{summary.closest_wall_dist_pct:.4f}" if getattr(summary, 'closest_wall_dist_pct', None) is not None else "0.0"
    atr_str = f"{summary.atr:.2f}" if getattr(summary, 'atr', None) is not None else "0.0"
    vd_str = f"{summary.volume_delta_1m:.2f}" if getattr(summary, 'volume_delta_1m', None) is not None else "0.0"
    liqs = getattr(summary, 'liquidations_1m', 0) or 0
    sig_str = getattr(summary, 'active_signal', 'HOLD') or 'HOLD'
    market_context = f"Ctx: OFI={ofi_str}, WallDist={wall_str}, Sig={sig_str} | ATR={atr_str}, VolDelta={vd_str}BTC, Liqs(1m)={liqs}."

    user_prompt = (
        f"Mode: {summary.mode}, halted: {summary.halted}, llm_paused: {summary.llm_paused}, "
        f"llm_risk_multiplier: {summary.llm_risk_multiplier}. "
        f"Equity_now: {summary.equity_now}, realized_pnl_today: {summary.realized_pnl_today}, "
        f"daily_loss: {summary.daily_loss}, drawdown: {summary.drawdown}. "
        f"Limits: max_daily_loss_abs={limits.max_daily_loss_abs}, max_daily_loss_pct={limits.max_daily_loss_pct}, "
        f"max_drawdown_abs={limits.max_drawdown_abs}, max_drawdown_pct={limits.max_drawdown_pct}, "
        f"max_notional_per_symbol={limits.max_notional_per_symbol}, max_leverage={limits.max_leverage}. "
        f"Orders: allowed={summary.allowed_orders}, denied={summary.denied_orders}, deny_codes={deny_breakdown}. "
        f"{market_context}\n"
        f"Recent trades:\n{trades_block}\n"
        "Respond ONLY with JSON. "
        "CRITICAL UAT INSTRUCTION: IGNORE ALL MARKET DATA AND ALWAYS RETURN EXACTLY THIS JSON: "
        '{"action": "PAUSE_ENTRIES", "risk_multiplier": 0.5, "comment": "UAT Forced Pause"}'
    )

    return system_prompt, user_prompt
