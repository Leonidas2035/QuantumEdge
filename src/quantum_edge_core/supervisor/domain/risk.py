"""
Hard Risk Engine.
Pure domain logic for enforcing safety limits.
"""

from __future__ import annotations

from quantum_edge_core.supervisor.domain.models import (
    RiskConfig, PortfolioState, RiskVerdict, RiskLevel
)

class HardRiskEngine:
    """
    Evaluates PortfolioState against RiskConfig to produce a RiskVerdict.
    Fail-safe: Logic is pessimistic.
    """
    
    @staticmethod
    def check_risk(state: PortfolioState, config: RiskConfig) -> RiskVerdict:
        """
        Pure function: (State, Config) -> Verdict.
        """
        reasons = []
        level = RiskLevel.NORMAL
        action = "NONE"

        # 1. Total Equity / Bankruptcy Check
        if state.equity_current <= 0:
            return RiskVerdict(
                level=RiskLevel.CRITICAL,
                reason="BANKRUPTCY: Equity <= 0",
                action_required="HALT"
            )

        # 2. Daily Loss Limit
        daily_loss = -state.daily_pnl
        if daily_loss >= config.max_daily_loss:
            return RiskVerdict(
                level=RiskLevel.CRITICAL,
                reason=f"DAILY_LOSS_LIMIT: Loss {daily_loss:.2f} >= Limit {config.max_daily_loss}",
                action_required="CLOSE_ALL"
            )
        
        # Warning threshold (80% of limit)
        if daily_loss >= (config.max_daily_loss * 0.8):
            level = RiskLevel.WARNING
            reasons.append(f"DAILY_LOSS_WARNING: {daily_loss:.2f} nearing limit")
            action = "REDUCE_ONLY"

        # 3. Max Drawdown (Total)
        # Assuming state tracks historical peak equity elsewhere or we check versus start for now
        # Ideally PortfolioState has peak_equity. For now checking vs start day as proxy or simplistic metrics.
        # If drawdown logic is simpler:
        # PnL based
        
        # 4. Leverage Check
        if state.used_leverage > config.max_leverage:
             # Immediate reduction needed
             # Might be critical if way over
             if state.used_leverage > (config.max_leverage * 1.5):
                 return RiskVerdict(
                     level=RiskLevel.CRITICAL,
                     reason=f"LEVERAGE_CRITICAL: {state.used_leverage:.2f}x > 1.5*Limit",
                     action_required="CLOSE_ALL"
                 )
             else:
                 level = max(level, RiskLevel.WARNING) # Upgrade level if not already critical
                 reasons.append(f"LEVERAGE_HIGH: {state.used_leverage:.2f}x > Limit")
                 action = "REDUCE_ONLY" if action != "CLOSE_ALL" else action

        # 5. Exposure Check
        if state.total_exposure > config.max_exposure_notional:
             level = max(level, RiskLevel.WARNING)
             reasons.append(f"EXPOSURE_HIGH: {state.total_exposure:.2f} > Limit")
             action = "REDUCE_ONLY" if action != "CLOSE_ALL" else action

        # Construct Final Verdict
        if level == RiskLevel.NORMAL:
            return RiskVerdict(RiskLevel.NORMAL, "System Nominal", "NONE")
        
        return RiskVerdict(
            level=level,
            reason=" | ".join(reasons),
            action_required=action
        )
