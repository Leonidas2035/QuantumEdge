"""Google AI Client for concise risk assessment queries."""
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class GoogleClient:
    """
    Client for interacting with Google AI (Gemini) with concise prompts.
    """
    def __init__(self, api_key_env: str = "GOOGLE_API_KEY", logger: Optional[logging.Logger] = None):
        self.api_key_env = api_key_env
        self.logger = logger or logging.getLogger(__name__)

    def generate_risk_query(self, context: Dict[str, Any]) -> str:
        """
        Generates a compressed prompt for risk assessment.
        Format: "SYS:HFT_SUPERVISOR. MODE:SCALP. PNL:-1.2%. DD:0.5%. VOL:HIGH. SPREAD:25bps. Q: RISK_ASSESSMENT? OUTPUT: JSON {verdict: 'CONTINUE'|'REDUCE'|'HALT', reason: '...'}"
        """
        # Extract metrics with defaults
        pnl = context.get("pnl_pct", 0.0)
        dd = context.get("drawdown_pct", 0.0)
        vol = context.get("volatility", "MEDIUM")
        spread = context.get("spread_bps", 0)
        mode = context.get("mode", "SCALP").upper()

        # Format values
        pnl_str = f"{pnl:.1f}%" if isinstance(pnl, (int, float)) else str(pnl)
        dd_str = f"{dd:.1f}%" if isinstance(dd, (int, float)) else str(dd)
        spread_str = f"{spread}bps"

        prompt = (
            f"SYS:HFT_SUPERVISOR. MODE:{mode}. "
            f"PNL:{pnl_str}. DD:{dd_str}. VOL:{vol}. SPREAD:{spread_str}. "
            "Q: RISK_ASSESSMENT? OUTPUT: JSON {verdict: 'CONTINUE'|'REDUCE'|'HALT', reason: '...'}"
        )
        return prompt
