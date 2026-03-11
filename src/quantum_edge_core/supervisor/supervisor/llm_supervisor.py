"""LLM-based supervisor to suggest high-level risk actions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, Literal
from pydantic import BaseModel, Field, ValidationError
from quantum_edge_core.supervisor.supervisor.audit_report import load_events_for_date
from quantum_edge_core.supervisor.supervisor.config import (
    LlmSupervisorConfig,
    RiskConfig,
)
from quantum_edge_core.supervisor.supervisor.events import (
    BaseEvent,
    EventType,
    EventLogger,
)
from quantum_edge_core.supervisor.supervisor.llm.chat_client import (
    ChatCompletionsClient,
)
from quantum_edge_core.supervisor.supervisor.state import RiskStateSnapshot
from quantum_edge_core.supervisor.domain.models import LlmGridPolicy

ZmqPolicyPublisher = Any


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that LLMs often add."""
    stripped = text.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```$", stripped, re.DOTALL)
    return m.group(1).strip() if m else stripped


class TradingMode(str, Enum):
    SCALP = "scalp"
    DCA = "dca"
    PASS = "pass"
    NEUTRAL = "neutral"
    SPOT_GRID = "spot_grid"


# Keep legacy LlmAction for backward compat with EventLogger
class LlmAction(str, Enum):
    OK = "OK"
    LOWER_RISK = "LOWER_RISK"
    PAUSE = "PAUSE"
    SWITCH_TO_PAPER = "SWITCH_TO_PAPER"
    UNSPECIFIED = "UNSPECIFIED"


def _trading_mode_to_action(mode: TradingMode) -> LlmAction:
    """Map TradingMode to legacy LlmAction for event logging."""
    return {
        TradingMode.SCALP: LlmAction.OK,
        TradingMode.DCA: LlmAction.OK,
        TradingMode.NEUTRAL: LlmAction.OK,
        TradingMode.PASS: LlmAction.PAUSE,
    }.get(mode, LlmAction.UNSPECIFIED)


@dataclass
class LlmSupervisorAdvice:
    trading_mode: TradingMode
    risk_multiplier: Optional[float]
    reasoning: str
    raw_response: str
    market_regime: str = "ranging"
    grid_bias: str = "neutral"
    recommended_grid_top: float = 0.0
    recommended_grid_bottom: float = 0.0
    capital_exposure_pct: float = 1.0
    grid_spacing_multiplier: float = 1.0
    # Legacy compat
    action: LlmAction = LlmAction.UNSPECIFIED
    comment: str = ""


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
        policy_publisher: Optional["ZmqPolicyPublisher"] = None,
    ) -> None:
        self._config = config
        self._risk_config = risk_config
        self._events_dir = events_dir
        self._logger = logger
        self._event_logger = event_logger
        self._chat_client = chat_client or ChatCompletionsClient(
            config.api_url, config.api_key_env, logger
        )
        self._policy_publisher = policy_publisher
        self._logger.debug("LlmSupervisor initialized. enabled=%s", config.enabled)

    def run_check(
        self,
        today: date,
        snapshot: RiskStateSnapshot,
        mode: str = "unknown",
        situation_text: str = "",
    ) -> Optional[LlmSupervisorAdvice]:
        self._logger.debug(
            "LlmSupervisor.run_check triggered. enabled=%s", self._config.enabled
        )
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
            summary = build_summary(
                snapshot, self._risk_config, events, self._config, mode
            )
            system_prompt, user_prompt = build_prompts(
                summary,
                self._risk_config,
                self._config,
                situation_text=situation_text,
            )
        except Exception as e:
            self._logger.error("Error building LLM prompts: %s", e)
            return None

        try:
            self._logger.info(
                f"Sending prompt to Gemini (length: {len(user_prompt)}):\n{user_prompt[:500]}..."
            )
            raw = self.call_llm(system_prompt, user_prompt)
        except Exception as exc:
            self._logger.error("LLM call failed: %s", exc)
            return None

        advice = self.parse_advice(raw)
        if self._event_logger:
            self._event_logger.log_llm_advice(
                advice.action.value,
                advice.risk_multiplier,
                advice.reasoning,
                self._config.dry_run,
            )

        # ── Beautiful console output ─────────────────────────────────
        self._logger.info(
            "\n┌─── LLM DECISION ───────────────────────────────────────┐"
            "\n│ Trading Mode : %-12s │ Risk Multiplier : %.2f │"
            "\n├─── LLM REASONING ──────────────────────────────────────┤"
            "\n│ %s"
            "\n└────────────────────────────────────────────────────────┘",
            advice.trading_mode.value,
            advice.risk_multiplier if advice.risk_multiplier is not None else 1.0,
            advice.reasoning,
        )

        # ── Publish directive via ZMQ PUB → LockBot ──────────────────
        if self._policy_publisher is not None:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(
                        self._policy_publisher.publish_directive(
                            mode=advice.trading_mode.value,
                            risk_multiplier=(
                                advice.risk_multiplier
                                if advice.risk_multiplier is not None
                                else 1.0
                            ),
                            reasoning=advice.reasoning,
                            market_regime=advice.market_regime,
                            grid_bias=advice.grid_bias,
                            recommended_grid_top=advice.recommended_grid_top,
                            recommended_grid_bottom=advice.recommended_grid_bottom,
                            capital_exposure_pct=advice.capital_exposure_pct,
                            grid_spacing_multiplier=advice.grid_spacing_multiplier,
                        )
                    )
                else:
                    loop.run_until_complete(
                        self._policy_publisher.publish_directive(
                            mode=advice.trading_mode.value,
                            risk_multiplier=(
                                advice.risk_multiplier
                                if advice.risk_multiplier is not None
                                else 1.0
                            ),
                            reasoning=advice.reasoning,
                            market_regime=advice.market_regime,
                            grid_bias=advice.grid_bias,
                            recommended_grid_top=advice.recommended_grid_top,
                            recommended_grid_bottom=advice.recommended_grid_bottom,
                            capital_exposure_pct=advice.capital_exposure_pct,
                            grid_spacing_multiplier=advice.grid_spacing_multiplier,
                        )
                    )
            except Exception as exc:
                self._logger.warning("Failed to publish ZMQ directive: %s", exc)

        return advice

    def call_llm(self, system_prompt: str, user_prompt: str) -> Any:
        # Gemini models are slower than OpenAI — enforce a 60s minimum.
        timeout = max(self._config.timeout_seconds, 60)
        return self._chat_client.complete(
            model=self._config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            timeout_seconds=timeout,
            response_schema=LlmGridPolicy,
        )

    def parse_advice(self, payload: Any) -> LlmSupervisorAdvice:
        raw_payload_text = (
            repr(payload.text) if hasattr(payload, "text") else repr(payload)
        )
        self._logger.info(f"RAW LLM RESPONSE (full):\n{raw_payload_text}")
        self._logger.info(f"RAW LLM RESPONSE type: {type(payload)}")

        try:
            self._logger.debug("Attempting to parse LLM response as JSON...")
            if isinstance(payload, str):
                cleaned = _strip_markdown_fences(payload)
                raw_dict = json.loads(cleaned)
                policy = LlmGridPolicy(**raw_dict)
            elif isinstance(payload, LlmGridPolicy):
                policy = payload
            else:
                # Dict or other mapping returned
                policy = LlmGridPolicy(**payload)

            self._logger.info("Policy parsed successfully")

            trading_mode = TradingMode.SPOT_GRID

            risk_multiplier = 1.0

            reasoning = "LLM Grid Policy parsed"

            # Legacy action mapping
            action = _trading_mode_to_action(trading_mode)

            return LlmSupervisorAdvice(
                trading_mode=trading_mode,
                risk_multiplier=risk_multiplier,
                reasoning=reasoning,
                raw_response=str(
                    policy.model_dump_json()
                    if hasattr(policy, "model_dump_json")
                    else {"reasoning": reasoning}
                ),
                market_regime=policy.market_regime,
                grid_bias=policy.grid_bias,
                recommended_grid_top=float(policy.recommended_grid_top),
                recommended_grid_bottom=float(policy.recommended_grid_bottom),
                capital_exposure_pct=float(policy.capital_exposure_pct),
                grid_spacing_multiplier=float(policy.grid_spacing_multiplier),
                action=action,
                comment=reasoning,
            )
        except json.JSONDecodeError as exc:
            self._logger.error(
                f"JSON DECODE ERROR: {str(exc)}\nRaw string:\n{raw_payload_text}",
                exc_info=True,
            )
            reasoning = f"Failed to parse LLM response (JSONDecodeError): {exc}"
        except ValidationError as exc:
            self._logger.error(
                f"Pydantic VALIDATION ERROR: {str(exc)}\nRaw:\n{raw_payload_text}",
                exc_info=True,
            )
            reasoning = f"Failed to parse LLM response (ValidationError): {exc}"
        except Exception as exc:
            self._logger.error(
                f"UNEXPECTED PARSE ERROR: {str(exc)}\nRaw response:\n{repr(payload)}\nRaw json string:\n{raw_payload_text}",
                exc_info=True,
            )
            reasoning = f"Failed to parse LLM response: {exc}"

        return LlmSupervisorAdvice(
            trading_mode=TradingMode.SPOT_GRID,
            risk_multiplier=1.0,
            reasoning=reasoning,
            raw_response=raw_payload_text,
            market_regime="ranging",
            grid_bias="neutral",
            recommended_grid_top=0.0,
            recommended_grid_bottom=0.0,
            capital_exposure_pct=1.0,
            grid_spacing_multiplier=1.0,
            action=LlmAction.UNSPECIFIED,
            comment=reasoning,
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
    summary: LlmSupervisorSummary,
    limits: RiskConfig,
    config: LlmSupervisorConfig,
    situation_text: str = "",
) -> Tuple[str, str]:
    system_prompt = (
        "You are an elite High-Frequency Trading (HFT) AI Supervisor.\n"
        "Your primary task is to analyze real-time market data, technical indicators (TA), and microstructure to dictate the trading strategy for an execution bot.\n\n"
        "DATA INTERPRETATION RULES:\n"
        "1. Indicators (ta_1h, ta_5m): You will receive RSI (14), Bollinger Bands (20,2) position, and Trend (SMA50 vs SMA20).\n"
        '   - "Cold Start": If TA values are `null`, it means the database is building history. Do not panic. Rely on microstructure (orderbook walls) and use NEUTRAL mode with risk_multiplier = 0.5.\n'
        "2. Derivatives: High `funding_rate` (>0.0001) means market is over-leveraged long (risk of squeeze).\n\n"
        "TRADING MODE DIRECTIVES:\n"
        "Choose strictly from ['scalp', 'dca', 'pass', 'neutral'].\n\n"
        "- PASS:\n"
        "  * TRIGGER: Extreme volatility, massive liquidations, or conflicting macro trends.\n"
        "  * ACTION: Emergency stop. Sets risk_multiplier=0.0.\n\n"
        "- DCA (Smart Accumulation):\n"
        "  * TRIGGER: 1H/4H trend is UP, but 5m RSI shows oversold (< 35). Price is approaching a strong BID wall.\n"
        "  * ACTION: Set buy_zone_max near the support wall. Bot will place grid limit orders.\n\n"
        "- SCALP (Range Trading):\n"
        "  * TRIGGER: Market is consolidating. Bollinger Bands are flat, and strong liquidity walls exist on both sides.\n"
        "  * ACTION: Set buy_zone_max just above the BID wall, and sell_zone_min just below the ASK wall.\n\n"
        "- NEUTRAL (Market Making):\n"
        "  * TRIGGER: Normal conditions, low volatility, TA indicators are near neutral (RSI ~50).\n"
        "  * ACTION: Bot will collect spread on both sides.\n\n"
        "OUTPUT CONSTRAINTS:\n"
        "You must strictly follow the Pydantic JSON schema requested by the API. `trading_mode` must be exact match, lowercase. Calculate realistic `buy_zone_max` and `sell_zone_min` based on the provided liquidity walls.\n\n"
        "IMPORTANT: For testing purposes, YOU MUST ALWAYS output trading_mode='SCALP' and risk_multiplier=1.0. Do not use NEUTRAL or PASS."
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
    ofi_str = (
        f"{summary.ofi_1s:.2f}"
        if getattr(summary, "ofi_1s", None) is not None
        else "0.00"
    )
    wall_str = (
        f"{summary.closest_wall_dist_pct:.4f}"
        if getattr(summary, "closest_wall_dist_pct", None) is not None
        else "0.0"
    )
    atr_str = (
        f"{summary.atr:.2f}" if getattr(summary, "atr", None) is not None else "0.0"
    )
    vd_str = (
        f"{summary.volume_delta_1m:.2f}"
        if getattr(summary, "volume_delta_1m", None) is not None
        else "0.0"
    )
    liqs = getattr(summary, "liquidations_1m", 0) or 0
    sig_str = getattr(summary, "active_signal", "HOLD") or "HOLD"
    market_context = f"Ctx: OFI={ofi_str}, WallDist={wall_str}, Sig={sig_str} | ATR={atr_str}, VolDelta={vd_str}BTC, Liqs(1m)={liqs}."

    # State Analysis block (Phase 1: equity, margin, leverage)
    state_block = (
        f"STATE ANALYSIS: "
        f"total_equity={summary.equity_now}, "
        f"realized_pnl_today={summary.realized_pnl_today}, "
        f"daily_loss={summary.daily_loss}, drawdown={summary.drawdown}. "
    )

    # Situation Analysis block (Phase 2: multi-timeframe OHLCV)
    situation_block = f"\n{situation_text}\n" if situation_text else ""

    user_prompt = (
        f"Mode: {summary.mode}, halted: {summary.halted}, llm_paused: {summary.llm_paused}, "
        f"llm_risk_multiplier: {summary.llm_risk_multiplier}. "
        f"{state_block}"
        f"{situation_block}"
        f"Limits: max_daily_loss_abs={limits.max_daily_loss_abs}, max_daily_loss_pct={limits.max_daily_loss_pct}, "
        f"max_drawdown_abs={limits.max_drawdown_abs}, max_drawdown_pct={limits.max_drawdown_pct}, "
        f"max_notional_per_symbol={limits.max_notional_per_symbol}, max_leverage={limits.max_leverage}. "
        f"Orders: allowed={summary.allowed_orders}, denied={summary.denied_orders}, deny_codes={deny_breakdown}. "
        f"{market_context}\n"
        f"Recent trades:\n{trades_block}\n"
        "Respond ONLY with valid JSON."
    )

    return system_prompt, user_prompt


def _parse_cli_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for standalone LLM supervisor execution."""
    parser = argparse.ArgumentParser(
        description="LLM Supervisor — standalone risk-check runner.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run-foreground",
        choices=["run-foreground", "check-once"],
        help="Command to execute (default: run-foreground).",
    )
    parser.add_argument(
        "--mode",
        default="demo",
        choices=["demo", "paper", "live"],
        help="Execution mode (default: demo).",
    )
    parser.add_argument(
        "--config-dir",
        dest="config_dir",
        default="config",
        help="Path to supervisor config directory.",
    )
    return parser.parse_args(argv)


def _run_standalone(args: argparse.Namespace) -> None:
    """Run a single LLM risk check from the command line."""
    from datetime import date as _date

    config_dir = Path(args.config_dir)

    # ── Pre-flight: API key ──────────────────────────────────────────
    api_key_env = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key_env:
        print(
            "[!] ПОМИЛКА: Не встановлено GOOGLE_API_KEY або GEMINI_API_KEY.\n"
            "    Встановіть змінну середовища і спробуйте знову:\n"
            "    export GOOGLE_API_KEY=<your-key>",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Load configs ─────────────────────────────────────────────────
    llm_config_path = config_dir / "llm_supervisor.yaml"
    risk_config_path = config_dir / "risk.yaml"

    if not llm_config_path.exists():
        print(
            f"[!] ПОМИЛКА: Конфіг LLM Supervisor не знайдено: {llm_config_path}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not risk_config_path.exists():
        print(
            f"[!] ПОМИЛКА: Конфіг Risk не знайдено: {risk_config_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    from quantum_edge_core.supervisor.supervisor.config import (
        load_llm_supervisor_config,
        load_risk_config,
    )

    llm_cfg = load_llm_supervisor_config(llm_config_path)
    risk_cfg = load_risk_config(risk_config_path)

    # ── Build supervisor ─────────────────────────────────────────────
    _logger = logging.getLogger("LlmSupervisor")
    events_dir = Path("runtime") / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    supervisor = LlmSupervisor(
        config=llm_cfg,
        risk_config=risk_cfg,
        events_dir=events_dir,
        logger=_logger,
    )

    # ── Build a realistic test snapshot ────────────────────────────────
    snapshot = RiskStateSnapshot(
        trading_day=_date.today(),
        equity_start=10000.0,
        equity_now=9985.0,
        realized_pnl_today=-15.5,
        max_equity_intraday=10050.0,
        min_equity_intraday=9970.0,
        halted=False,
        halt_reason=None,
        llm_risk_multiplier=1.0,
        llm_paused=False,
        total_equity=10000.0,
        free_margin=9500.0,
        unrealized_pnl=-15.5,
        open_positions_count=1,
        portfolio_skew=0.5,
        current_leverage=1.2,
    )

    _logger.info(
        "Starting LLM Supervisor in mode=%s, command=%s …",
        args.mode,
        args.command,
    )

    # ── Phase 2: Import market data fetchers ─────────────────────────
    from quantum_edge_core.supervisor.supervisor.market_client import (
        fetch_situation_summary,
        mock_situation_summary,
    )

    # ── Analysis loop ────────────────────────────────────────────────
    import time as _time

    is_continuous = args.command == "run-foreground"
    cycle = 0

    while True:
        cycle += 1
        _logger.info("═" * 50)
        _logger.info("Analysis cycle #%d started.", cycle)

        # Refresh snapshot date (handles midnight rollover)
        snapshot.trading_day = _date.today()

        # Fetch market data
        if args.mode == "demo":
            situation_text = mock_situation_summary()
        else:
            try:
                situation_text = fetch_situation_summary()
                _logger.info("Fetched live OHLCV from Binance.")
            except Exception as exc:
                _logger.critical(
                    "CRITICAL: Failed to fetch live market data from Binance: %s. "
                    "Skipping this cycle — REFUSING to feed fake data to LLM.",
                    exc,
                )
                if not is_continuous:
                    raise
                _time.sleep(10)
                continue

        _logger.info("Situation text:\n%s", situation_text)

        advice = supervisor.run_check(
            today=_date.today(),
            snapshot=snapshot,
            mode=args.mode,
            situation_text=situation_text,
        )
        if advice:
            print("\n" + "=" * 60)
            print(
                f"  [LLM DECISION] Mode: {advice.trading_mode.value} | Risk: {advice.risk_multiplier}"
            )
            print(f'  [LLM REASONING] "{advice.reasoning}"')
            print("=" * 60 + "\n")
            print(
                json.dumps(
                    {
                        "trading_mode": advice.trading_mode.value,
                        "risk_multiplier": advice.risk_multiplier,
                        "reasoning": advice.reasoning,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print("[LlmSupervisor] No advice returned (disabled or insufficient data).")

        # ── Exit or sleep ────────────────────────────────────────────
        if not is_continuous:
            _logger.info("Single check complete (check-once mode). Exiting.")
            break

        sleep_sec = llm_cfg.check_interval_minutes * 60
        _logger.info(
            "[INFO] Sleeping for %d minutes (%d seconds) until next analysis...",
            llm_cfg.check_interval_minutes,
            sleep_sec,
        )
        _time.sleep(sleep_sec)


if __name__ == "__main__":
    # ── Configure logging first ──────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        cli_args = _parse_cli_args()
        _run_standalone(cli_args)
    except KeyboardInterrupt:
        print("\n[LlmSupervisor] Перервано користувачем.", file=sys.stderr)
        sys.exit(130)
    except SystemExit:
        raise
    except Exception:
        # ── НІКОЛИ не мовчимо: повний stacktrace ─────────────────────
        print(
            "\n[!] КРИТИЧНА ПОМИЛКА під час ініціалізації LLM Supervisor:\n",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
