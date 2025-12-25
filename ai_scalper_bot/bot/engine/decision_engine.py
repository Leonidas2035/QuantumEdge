import time
from typing import Dict, List, Optional

from bot.core.config_loader import config
from bot.engine.decision_types import Decision, DecisionAction, DecisionDirection, HorizonDecision
from bot.engine.regime_policy import RegimePolicy
from bot.ml.ensemble import EnsembleOutput
from bot.ml.signal_model.model import SignalOutput
from bot.trading.trade_stats import TradeStats


class DecisionEngine:
    """Layer 2: signal filtering, horizon agreement, confidence gating, loss-streak/over-trading, regime-aware."""

    def __init__(self):
        self.cfg = config.get("decision", {}) or {}
        filters = self.cfg.get("filters", {}) or {}
        self.min_confidence = filters.get("min_confidence", 0.55)
        self.min_edge = filters.get("min_edge", 0.02)
        self.hz_cfg = self.cfg.get("horizons", {}) or {"primary": [1, 5], "anchor": 30}
        self.thresholds = self.cfg.get("thresholds", {}) or {
            "min_conf_primary": 0.52,
            "strong_conf_anchor": 0.6,
            "min_conf_long": 0.55,
            "min_conf_short": 0.55,
        }
        self.loss_cfg = self.cfg.get("loss_streak", {}) or {
            "window_trades": 10,
            "max_losses": 3,
            "cooldown_seconds": 600,
        }
        self.over_cfg = self.cfg.get("overtrading", {}) or {"max_trades_per_hour": 60}
        risk_cfg = config.get("risk", {}) or {}
        session_cfg = risk_cfg.get("session", {}) or {}
        self.max_daily_loss_abs = session_cfg.get("max_daily_loss_abs", risk_cfg.get("max_daily_loss"))
        self.max_drawdown_abs = session_cfg.get("max_drawdown_abs", risk_cfg.get("max_drawdown_abs"))
        self.max_trades_per_hour = session_cfg.get("max_trades_per_hour", self.over_cfg.get("max_trades_per_hour", 60))
        self.regime_policy = RegimePolicy(self.cfg.get("regimes", {}))
        self.cooldown_until: Dict[str, float] = {}
        self.trade_stats: Dict[str, TradeStats] = {}
        self.last_risk_state: Dict[str, str] = {}

    def _direction_from_signal(self, sig: SignalOutput, min_conf: float) -> str:
        if sig.p_up >= min_conf:
            return DecisionDirection.LONG
        if sig.p_down >= min_conf:
            return DecisionDirection.SHORT
        return DecisionDirection.NONE

    def _horizon_agreement(self, outputs: Dict[int, SignalOutput], reasons: List[str]) -> (str, List[int]):
        primary = self.hz_cfg.get("primary", [1, 5])
        anchor = self.hz_cfg.get("anchor", 30)
        min_conf_primary = self.thresholds.get("min_conf_primary", 0.52)
        strong_anchor = self.thresholds.get("strong_conf_anchor", 0.6)

        dirs = {}
        for h in primary + [anchor]:
            if h in outputs:
                dirs[h] = self._direction_from_signal(outputs[h], min_conf_primary)

        prim_dirs = [dirs.get(h, DecisionDirection.NONE) for h in primary]
        if not prim_dirs or any(d == DecisionDirection.NONE for d in prim_dirs):
            reasons.append("HORIZON_DISAGREE")
            return DecisionDirection.NONE, []
        if len(set(prim_dirs)) > 1:
            reasons.append("HORIZON_DISAGREE")
            return DecisionDirection.NONE, []

        primary_dir = prim_dirs[0]
        used = [h for h in primary if h in outputs]

        if anchor in outputs:
            anchor_sig = outputs[anchor]
            if primary_dir == DecisionDirection.LONG and anchor_sig.p_down >= strong_anchor:
                reasons.append("ANCHOR_OPPOSE")
                return DecisionDirection.NONE, used + [anchor]
            if primary_dir == DecisionDirection.SHORT and anchor_sig.p_up >= strong_anchor:
                reasons.append("ANCHOR_OPPOSE")
                return DecisionDirection.NONE, used + [anchor]
            used.append(anchor)

        return primary_dir, used

    def _confidence_gate(self, direction: str, ensemble: EnsembleOutput, reasons: List[str]) -> bool:
        min_conf_long = self.thresholds.get("min_conf_long", self.min_confidence)
        min_conf_short = self.thresholds.get("min_conf_short", self.min_confidence)
        if direction == DecisionDirection.LONG:
            if ensemble.meta_edge < self.min_edge or (0.5 + ensemble.meta_edge) < min_conf_long:
                reasons.append("LOW_CONFIDENCE")
                return False
        elif direction == DecisionDirection.SHORT:
            if ensemble.meta_edge > -self.min_edge or (0.5 - ensemble.meta_edge) < min_conf_short:
                reasons.append("LOW_CONFIDENCE")
                return False
        return True

    def _check_loss_streak(self, symbol: str, reasons: List[str]) -> bool:
        cfg = self.loss_cfg
        stats = self.trade_stats.setdefault(symbol, TradeStats())
        now = time.time()
        if self.cooldown_until.get(symbol, 0) > now:
            reasons.append("LOSS_STREAK_COOLDOWN")
            return False
        losses = stats.loss_streak(cfg.get("window_trades", 10), cfg.get("max_losses", 3))
        if losses >= cfg.get("max_losses", 3):
            self.cooldown_until[symbol] = now + cfg.get("cooldown_seconds", 600)
            reasons.append("LOSS_STREAK_COOLDOWN")
            return False
        return True

    def _check_overtrading(self, symbol: str, reasons: List[str]) -> bool:
        cfg = self.over_cfg
        stats = self.trade_stats.setdefault(symbol, TradeStats())
        limit = self.max_trades_per_hour or cfg.get("max_trades_per_hour", 60)
        if stats.trades_last_hour() >= limit:
            reasons.append("OVERTRADING_LIMIT")
            return False
        return True

    def _check_drawdown_loss(self, symbol: str, reasons: List[str]) -> bool:
        stats = self.trade_stats.setdefault(symbol, TradeStats())
        if self.max_daily_loss_abs is not None:
            if stats.total_pnl_window(86400) <= -abs(self.max_daily_loss_abs):
                reasons.append("DAILY_LOSS_LIMIT")
                return False
        if self.max_drawdown_abs is not None:
            if stats.max_drawdown_abs() >= abs(self.max_drawdown_abs):
                reasons.append("DRAWDOWN_LIMIT")
                return False
        return True

    def decide(
        self,
        symbol: str,
        ensemble: EnsembleOutput,
        features,
        position: int,
        approved: bool = True,
        warmup_ready: bool = True,
    ) -> Decision:
        reasons: List[str] = []
        if not approved:
            reasons.append("RISK_MOD_REJECT")
            return Decision(action=DecisionAction.NO_TRADE, reasons=reasons)
        if not warmup_ready:
            reasons.append("WARMUP")
            return Decision(action=DecisionAction.NO_TRADE, reasons=reasons)

        direction, used_hz = self._horizon_agreement(ensemble.components, reasons)
        if direction == DecisionDirection.NONE:
            return Decision(action=DecisionAction.NO_TRADE, reasons=reasons, horizons_used=used_hz)

        if not self._confidence_gate(direction, ensemble, reasons):
            return Decision(action=DecisionAction.NO_TRADE, reasons=reasons, horizons_used=used_hz)

        if position == 0:
            if not self._check_loss_streak(symbol, reasons):
                self.last_risk_state[symbol] = reasons[-1] if reasons else "LOSS_STREAK_COOLDOWN"
                return Decision(action=DecisionAction.NO_TRADE, reasons=reasons, horizons_used=used_hz)
            if not self._check_overtrading(symbol, reasons):
                self.last_risk_state[symbol] = reasons[-1] if reasons else "OVERTRADING_LIMIT"
                return Decision(action=DecisionAction.NO_TRADE, reasons=reasons, horizons_used=used_hz)
            if not self._check_drawdown_loss(symbol, reasons):
                self.last_risk_state[symbol] = reasons[-1] if reasons else "RISK_LIMIT"
                return Decision(action=DecisionAction.NO_TRADE, reasons=reasons, horizons_used=used_hz)
        else:
            self.last_risk_state[symbol] = ""

        regime_tag = None
        trade_style = None
        if features is not None and len(features) > 0:
            try:
                regime_tag = int(features[-1])
            except Exception:
                regime_tag = None

        regime_name = {
            0: "flat",
            1: "trending_up",
            -1: "trending_down",
            2: "high_vol",
        }.get(regime_tag, "flat")

        allow, min_conf_override, style = self.regime_policy.allow(regime_name, direction, self.min_confidence)
        trade_style = style
        if not allow:
            reasons.append("REGIME_BLOCK")
            return Decision(action=DecisionAction.NO_TRADE, reasons=reasons, horizons_used=used_hz, regime=regime_name)

        horizon_details = {
            h: HorizonDecision(
                horizon=h,
                direction=self._direction_from_signal(sig, self.thresholds.get("min_conf_primary", 0.52)),
                confidence=max(sig.p_up, sig.p_down),
                edge=sig.edge,
            )
            for h, sig in ensemble.components.items()
        }

        action = DecisionAction.HOLD
        if position == 0:
            action = DecisionAction.ENTER
        elif position > 0 and direction == DecisionDirection.SHORT:
            action = DecisionAction.EXIT
        elif position < 0 and direction == DecisionDirection.LONG:
            action = DecisionAction.EXIT
        else:
            action = DecisionAction.HOLD

        return Decision(
            action=action,
            direction=direction,
            confidence=0.5 + ensemble.meta_edge if direction == DecisionDirection.LONG else 0.5 - ensemble.meta_edge,
            edge=abs(ensemble.meta_edge),
            regime=regime_name,
            trade_style=trade_style,
            horizons_used=used_hz,
            horizon_details=horizon_details,
            reasons=reasons,
        )
