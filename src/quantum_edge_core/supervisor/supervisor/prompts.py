"""
Prompts and JSON Schema for the Supervisor AI.
Defines the "Persona" and the "Contract".
"""

from typing import List, Dict, Any

SYSTEM_PROMPT = """
You are a Senior Risk Manager at a High-Frequency Trading desk.
Your primary mandates are CAPITAL PRESERVATION and RISK CONTROL.
You are PESSIMISTIC by default. You do not chase profit; you prevent ruin.

Target Output Format: STRICT JSON. No markdown, no commentary outside the JSON.

Roles:
1. Analyze market microstructure metrics (OFI, VPIN, Funding Pressure).
2. Assess portfolio risk (Drawdown, Exposure).
3. Issue a clear strategic command (Action) and Market Regime classification.

Regimes:
- TREND_LONG: Strong upward momentum, low volatility/risk.
- RANGE: Choppy market, mean reversion likely.
- DUMP_RISK: High volatility, downward pressure, potential crash.

Actions:
- CONTINUE: Standard operation, limits unchanged.
- REDUCE_SIZE: Reduce position limits, tighten stop losses.
- CLOSE_ALL: Flatten all positions immediately (Emergency).
- FREEZE: Stop entering new positions, manage existing.

Params Override:
- leverage_cap: Maximum allowed leverage (float).
- min_order_size: Minimum notional size (float).
"""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "regime": {
            "type": "string",
            "enum": ["TREND_LONG", "RANGE", "DUMP_RISK"]
        },
        "action": {
            "type": "string",
            "enum": ["CONTINUE", "REDUCE_SIZE", "CLOSE_ALL", "FREEZE"]
        },
        "params_override": {
            "type": "object",
            "properties": {
                "leverage_cap": {"type": "number"},
                "min_order_size": {"type": "number"}
            },
            "required": ["leverage_cap", "min_order_size"]
        },
        "reasoning": {"type": "string"}
    },
    "required": ["regime", "action", "params_override", "reasoning"]
}

def format_history(last_decisions: List[Dict[str, Any]]) -> str:
    """
    Format recent history to provide context and prevent 'flickering'.
    """
    if not last_decisions:
        return "History: None"
        
    lines = ["Recent Decisions:"]
    for i, dec in enumerate(last_decisions[-3:]):
        lines.append(f"- T-{len(last_decisions)-i}: {dec.get('action')} ({dec.get('regime')}) Reason: {dec.get('reasoning')[:50]}...")
        
    return "\n".join(lines)
