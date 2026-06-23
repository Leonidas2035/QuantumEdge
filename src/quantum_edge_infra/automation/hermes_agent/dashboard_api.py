import json
import subprocess
import aiohttp
import urllib.parse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DashboardAPI")

app = FastAPI(title="QuantumEdge Dashboard API Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/v1/health")
def health_check():
    return {"status": "ok", "service": "hermes_api_bridge"}

import sys

@app.get("/api/v1/dashboard/status")
def get_dashboard_status():
    try:
        # Execute the zmq_mcp_bridge script to get the aggregated status
        # This isolates the ZMQ blocking poller from the FastAPI async event loop
        result = subprocess.run(
            [sys.executable, "/home/korben/QuantumEdge-main/hermes_agent/zmq_mcp_bridge.py", "status"],
            capture_output=True,
            text=True,
            timeout=5.0
        )
        
        if result.returncode == 0 and result.stdout.strip():
            # Parse the JSON string printed by the bridge
            try:
                status_data = json.loads(result.stdout.strip())
                return status_data
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse bridge output: {e} - Output: {result.stdout}")
                return {
                    "ai_scalper": {"status": "offline", "error": "Invalid JSON from bridge"},
                    "dyndca": {"status": "offline", "error": "Invalid JSON from bridge"}
                }
        else:
            logger.error(f"ZMQ Bridge error: {result.stderr}")
            # Return an offline status instead of crashing
            return {
                "ai_scalper": {"status": "offline", "error": result.stderr.strip() or "Unknown error"},
                "dyndca": {"status": "offline", "error": result.stderr.strip() or "Unknown error"}
            }
            
    except subprocess.TimeoutExpired:
        logger.warning("ZMQ Bridge execution timed out.")
        return {
            "ai_scalper": {"status": "offline", "error": "timeout"},
            "dyndca": {"status": "offline", "error": "timeout"}
        }
    except Exception as e:
        logger.error(f"Exception while executing ZMQ Bridge: {e}")
        return {
            "ai_scalper": {"status": "offline", "error": str(e)},
            "dyndca": {"status": "offline", "error": str(e)}
        }

async def query_questdb(sql: str):
    url = f"http://127.0.0.1:9000/exec?query={urllib.parse.quote(sql)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5.0) as response:
                if response.status == 200:
                    data = await response.json()
                    columns = [c["name"] for c in data.get("columns", [])]
                    dataset = data.get("dataset", [])
                    results = []
                    for row in dataset:
                        results.append(dict(zip(columns, row)))
                    return results
                else:
                    logger.error(f"QuestDB query failed with status {response.status}")
                    return []
    except Exception as e:
        logger.error(f"Error querying QuestDB: {e}")
        return []

@app.get("/api/v1/charts/features")
async def get_charts_features(symbol: str, interval: str = '1m', hours: int = 4):
    sql = f"""SELECT 
    to_unix_timestamp(ts) / 1000000 as time, 
    last(mid_price) as price, 
    last(rsi_14) as rsi,
    last(macd_line) as macd
FROM market_features
WHERE symbol = '{symbol.upper()}' AND ts > dateadd('h', -{hours}, now())
SAMPLE BY {interval} ALIGN TO CALENDAR;"""
    
    results = await query_questdb(sql)
    formatted = []
    for row in results:
        t = int(row.get("time", 0))
        price = row.get("price")
        rsi = row.get("rsi")
        macd = row.get("macd")
        formatted.append({
            "time": t,
            "value": price,
            "price": price,
            "rsi": rsi,
            "macd": macd
        })
    return formatted

@app.get("/api/v1/charts/pnl")
async def get_charts_pnl(bot_id: str, interval: str = '1m', hours: int = 12):
    sql = f"""SELECT 
    to_unix_timestamp(ts) / 1000000 as time, 
    last(pnl_session) as value
FROM bot_telemetry
WHERE bot_id = '{bot_id}' AND ts > dateadd('h', -{hours}, now())
SAMPLE BY {interval} ALIGN TO CALENDAR;"""
    
    results = await query_questdb(sql)
    formatted = []
    for row in results:
        t = int(row.get("time", 0))
        val = row.get("value")
        formatted.append({
            "time": t,
            "value": val
        })
    return formatted

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
