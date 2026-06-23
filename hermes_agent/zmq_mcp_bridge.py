import argparse
import json
import time
import zmq
import sys

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
    
    # We will wait up to 3 seconds to catch the telemetry
    timeout_ms = 3000
    start_time = time.time()
    
    status_report = {
        "ai_scalper": {"status": "offline"},
        "dyndca": {"status": "offline"}
    }
    
    # Keep polling until we have both or timeout is reached
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
                    status_report["ai_scalper"] = data
            except zmq.Again:
                pass
            except Exception as e:
                status_report["ai_scalper"] = {"status": "error", "message": str(e)}
                
        if dyndca_sub in socks and socks[dyndca_sub] == zmq.POLLIN:
            try:
                frames = dyndca_sub.recv_multipart(flags=zmq.NOBLOCK)
                if len(frames) >= 2:
                    payload = frames[1].decode("utf-8")
                    data = json.loads(payload)
                    status_report["dyndca"] = data
            except zmq.Again:
                pass
            except Exception as e:
                status_report["dyndca"] = {"status": "error", "message": str(e)}
                
        # If we got data for both, we can break early
        if status_report["ai_scalper"].get("status") != "offline" and status_report["dyndca"].get("status") != "offline":
            break
            
    ai_scalper_sub.close()
    dyndca_sub.close()
    context.term()
    
    print(json.dumps(status_report, indent=2))

def send_policy(bot_id, action, ttl):
    context = zmq.Context()
    pub_socket = context.socket(zmq.PUB)
    pub_socket.connect("tcp://127.0.0.1:5558")
    
    # Allow some time for the PUB socket to establish connections
    time.sleep(0.2)
    
    # Use the correct topic required by the bots' subscriber filters
    topic = f"command.{bot_id}".encode("utf-8")
    
    payload = {
        "action": action,
        "ttl": int(ttl),
        "timestamp": time.time()
    }
    
    try:
        # Send as a multipart message [topic, JSON payload]
        pub_socket.send_multipart([topic, json.dumps(payload).encode("utf-8")])
        # Brief pause to ensure message is flushed
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
    policy_parser.add_argument("--action", required=True, help="Action to enforce (e.g. PAUSE, STOP)")
    policy_parser.add_argument("--ttl", type=int, required=True, help="Time to live in seconds")
    
    try:
        args = parser.parse_args()
        
        if args.command == "status":
            get_status()
        elif args.command == "policy":
            send_policy(args.bot, args.action, args.ttl)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
