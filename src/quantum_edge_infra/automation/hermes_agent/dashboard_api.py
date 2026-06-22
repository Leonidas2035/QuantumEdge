import json
import subprocess
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
