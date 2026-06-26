import subprocess, json, datetime, os

cmds = [
    "PYTHONPATH=src .venv/bin/python /home/korben/QuantumEdge-main/hermes_agent/zmq_mcp_bridge.py status",
    "PYTHONPATH=src .venv/bin/python /home/korben/QuantumEdge-main/src/quantum_edge_infra/automation/hermes_agent/data_mcp_bridge.py query_market_trend --symbol BTCUSDT",
    "PYTHONPATH=src .venv/bin/python /home/korben/QuantumEdge-main/src/quantum_edge_infra/automation/hermes_agent/data_mcp_bridge.py market_snapshot --symbol BTCUSDT",
]

results = []
for c in cmds:
    r = subprocess.run(c, shell=True, cwd="/home/korben/QuantumEdge-main", capture_output=True, text=True)
    results.append(r.stdout)

status_data = json.loads(results[0])
trend_data = json.loads(results[1])
snapshot_data = json.loads(results[2])

# ai_scalper
ai = status_data.get("ai_scalper", {})
ai_status = "RUNNING"
ai_pnl = ai.get("pnl_session", "N/A")
ai_equity = ai.get("equity", "N/A")
ai_atr = ai.get("atr", "N/A")
ai_pos = ai.get("active_positions_count", "N/A")
ai_mode = ai.get("trading_mode", "N/A")

# dyndca
dy = status_data.get("dyndca", {})
dy_status = dy.get("status", "UNKNOWN")
m = dy.get("metrics", {})
dy_pos = m.get("active_positions_count", "N/A")
dy_size = m.get("position_size", "N/A")
dy_avg = m.get("average_entry_price", "N/A")
dy_pnl = m.get("unrealized_pnl", "N/A")

# market snapshot
price = snapshot_data.get("current_price", "N/A")
spread = snapshot_data.get("spread", "N/A")

# latest trend
latest = trend_data[-1]
rsi = latest.get("rsi", "N/A")
macd = latest.get("macd", "N/A")
atr_val = latest.get("atr", "N/A")

# simple atr trend: compare last to previous
if len(trend_data) >= 2:
    prev_atr = trend_data[-2].get("atr")
    if atr_val < prev_atr:
        atr_trend = "DOWN"
    else:
        atr_trend = "UP"
else:
    atr_trend = "UNKNOWN"

now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

log_line = (
    f"{now_utc} | "
    f"ai_scalper status='{ai_status}', pnl_session={ai_pnl}, equity={ai_equity}, "
    f"atr={ai_atr}, active_positions_count={ai_pos}, trading_mode={ai_mode}; "
    f"dyndca status={dy_status}, active_positions_count={dy_pos}, position_size={dy_size}, "
    f"average_entry_price={dy_avg}, unrealized_pnl={dy_pnl}; "
    f"market_snapshot price={price}, spread={spread}; "
    f"RSI={rsi}, MACD={macd}, ATR_trend={atr_trend}"
)

log_path = "/home/korben/QuantumEdge-main/nightly_status.log"
os.makedirs(os.path.dirname(log_path), exist_ok=True)
with open(log_path, "a") as f:
    f.write(log_line + "\n")

print(log_line)
