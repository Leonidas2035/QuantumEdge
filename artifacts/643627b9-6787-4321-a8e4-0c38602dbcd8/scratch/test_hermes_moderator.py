import asyncio
import json
import subprocess
import os

async def test_suggest():
    signals = {
        "drawdown_pct": 0.08,
        "spread_bps": 12.5,
        "volatility_index": 2.5
    }
    base_policy = {
        "allow_trading": True,
        "mode": "normal",
        "size_multiplier": 1.0,
        "reason": "OK"
    }

    prompt_str = (
        "You are the risk management supervisor for QuantumEdge. "
        "Allowed keys in output JSON: allow_trading, mode, size_multiplier, cooldown_sec, "
        "spread_max_bps, max_daily_loss, reason. Do not include extra keys. "
        "If no changes are needed, return an empty JSON object {}.\n\n"
        "Return JSON only. Do not wrap in markdown or any other text.\n\n"
        "Current Signals:\n" + json.dumps(signals, indent=2) + "\n\n"
        "Base Policy:\n" + json.dumps(base_policy, indent=2) + "\n"
    )

    print("Sending prompt to Hermes Oneshot...")
    
    # Run hermes CLI command
    cmd = ["/home/korben/.local/bin/hermes", "-z", prompt_str]
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    output = stdout.decode().strip()
    error_output = stderr.decode().strip()
    
    print("--- Hermes Output ---")
    print(output)
    print("--- Hermes Stderr ---")
    print(error_output)
    print("--- End ---")
    
    # Try parsing
    try:
        data = json.loads(output)
        print("Parsed successfully:", data)
    except json.JSONDecodeError as e:
        print("JSON parse failed:", e)

if __name__ == "__main__":
    asyncio.run(test_suggest())
