import argparse
import json
import time
import zmq
import sys
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal

# --- Pydantic Schemas ---

class StatusEnvelope(BaseModel):
    source: str
    timestamp: float
    status: str
    pnl_session: float
    drawdown_pct: float
    min_equity_intraday: Optional[float] = None
    halt_reason: Optional[str] = None
    metrics: Dict[str, Any] = {}
    errors: List[Any] = []
    equity: float
    trading_mode: str
    buy_zone_max: Optional[float] = None
    risk_multiplier: Optional[float] = None
    atr: Optional[float] = None
    volume_delta_1m: Optional[float] = None
    liquidations_1m: Optional[int] = None


class TradeCommandEnvelope(BaseModel):
    action: Literal["PAUSE_ENTRIES", "RESUME_ENTRIES", "ADJUST_RISK", "STOP_LOSS"]
    risk_multiplier: Optional[float] = Field(None, ge=0.0, le=2.0)
    buy_zone_max: Optional[float] = None
    sell_zone_min: Optional[float] = None
    trading_mode: Optional[str] = None
    ttl: int = Field(default=60, ge=10)
    timestamp: float = Field(default_factory=time.time)

    @field_validator("action")
    @classmethod
    def enforce_no_loss_experiment_rules(cls, v: str) -> str:
        """Safety Guardrail: Blocks any STOP_LOSS command to enforce the No-Loss Experiment rules."""
        if v == "STOP_LOSS":
            raise ValueError("No-Loss safety rule violation: Direct STOP_LOSS command from agent is strictly blocked.")
        return v


# --- Bridge Logic ---

def get_status():
    context = zmq.Context()
    
    # Setup SUB socket for AI Scalper (5557)
    ai_scalper_sub = context.socket(zmq.SUB)
    ai_scalper_sub.connect("tcp://127.0.0.1:5557")
    ai_scalper_sub.setsockopt_string(zmq.SUBSCRIBE, "")
    
    # Setup SUB socket for DynDCA (5567)
    dyndca_sub = context.socket(zmq.SUB)
    dyndca_sub.connect("tcp://127.0.0.1:5567")
    dyndca_sub.setsockopt_string(zmq.SUBSCRIBE, "")
    
    poller = zmq.Poller()
    poller.register(ai_scalper_sub, zmq.POLLIN)
    poller.register(dyndca_sub, zmq.POLLIN)
    
    timeout_ms = 3000
    start_time = time.time()
    
    status_report = {
        "ai_scalper": {"status": "offline"},
        "dyndca": {"status": "offline"}
    }
    
    while time.time() - start_time < (timeout_ms / 1000.0):
        remaining_time = max(0, timeout_ms - int((time.time() - start_time) * 1000))
        if remaining_time == 0:
            break
            
        socks = dict(poller.poll(remaining_time))
        
        if ai_scalper_sub in socks and socks[ai_scalper_sub] == zmq.POLLIN:
            try:
                frames = ai_scalper_sub.recv_multipart(flags=zmq.NOBLOCK)
                if len(frames) >= 2:
                    payload = frames[1].decode("utf-8")
                    data = json.loads(payload)
                    # Validate via Pydantic StatusEnvelope
                    envelope = StatusEnvelope(**data)
                    status_report["ai_scalper"] = envelope.model_dump()
            except zmq.Again:
                pass
            except Exception as e:
                status_report["ai_scalper"] = {"status": "error", "message": f"Validation/parse error: {e}"}
                
        if dyndca_sub in socks and socks[dyndca_sub] == zmq.POLLIN:
            try:
                frames = dyndca_sub.recv_multipart(flags=zmq.NOBLOCK)
                if len(frames) >= 2:
                    payload = frames[1].decode("utf-8")
                    data = json.loads(payload)
                    # Validate via Pydantic StatusEnvelope (or generic dict if custom structure)
                    # DynDCA uses a slightly different structure, so parse safely
                    try:
                        envelope = StatusEnvelope(**data)
                        status_report["dyndca"] = envelope.model_dump()
                    except Exception:
                        status_report["dyndca"] = data
            except zmq.Again:
                pass
            except Exception as e:
                status_report["dyndca"] = {"status": "error", "message": f"Validation/parse error: {e}"}
                
        if status_report["ai_scalper"].get("status") != "offline" and status_report["dyndca"].get("status") != "offline":
            break
            
    ai_scalper_sub.close()
    dyndca_sub.close()
    context.term()
    
    print(json.dumps(status_report, indent=2))


def send_policy(bot_id, action, ttl, buy_zone_max=None, sell_zone_min=None, risk_multiplier=None, trading_mode=None):
    try:
        # Construct and validate the command using TradeCommandEnvelope
        envelope = TradeCommandEnvelope(
            action=action,
            risk_multiplier=risk_multiplier,
            buy_zone_max=buy_zone_max,
            sell_zone_min=sell_zone_min,
            trading_mode=trading_mode,
            ttl=int(ttl)
        )
    except Exception as ve:
        result = {"success": False, "error": f"Schema Validation Failed: {ve}"}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    context = zmq.Context()
    pub_socket = context.socket(zmq.PUB)
    pub_socket.connect("tcp://127.0.0.1:5559")
    
    time.sleep(0.2)
    
    topic = f"command.{bot_id}".encode("utf-8")
    payload = envelope.model_dump()
    
    try:
        pub_socket.send_multipart([topic, json.dumps(payload).encode("utf-8")])
        time.sleep(0.1)
        result = {"success": True, "message": "Policy sent successfully", "payload": payload}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    finally:
        pub_socket.close()
        context.term()
        
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description="ZMQ MCP Bridge for Hermes Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Get latest status from bots")
    
    # Policy command
    policy_parser = subparsers.add_parser("policy", help="Publish policy directive to bots")
    policy_parser.add_argument("--bot", required=True, help="Target bot ID (e.g. ai_scalper)")
    policy_parser.add_argument("--action", required=True, help="Action to enforce (e.g. PAUSE_ENTRIES, ADJUST_RISK)")
    policy_parser.add_argument("--ttl", type=int, required=True, help="Time to live in seconds")
    policy_parser.add_argument("--buy-zone-max", type=float, default=None, help="Max price for BUY entries")
    policy_parser.add_argument("--sell-zone-min", type=float, default=None, help="Min price for SELL / TP")
    policy_parser.add_argument("--risk-multiplier", type=float, default=None, help="Risk multiplier (0.0 - 1.0)")
    policy_parser.add_argument("--trading-mode", type=str, default=None, help="Trading mode (SCALP, DCA, etc.)")
    
    try:
        args = parser.parse_args()
        
        if args.command == "status":
            get_status()
        elif args.command == "policy":
            send_policy(
                args.bot, 
                args.action, 
                args.ttl,
                buy_zone_max=args.buy_zone_max,
                sell_zone_min=args.sell_zone_min,
                risk_multiplier=args.risk_multiplier,
                trading_mode=args.trading_mode
            )
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
