#!/usr/bin/env python3
"""
Data Plane MCP Bridge for Hermes Agent.

Provides Hermes with:
1. Real-time Market Snapshots (via ZMQ)
2. Historical Data Queries (via QuestDB REST API)
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from urllib.error import URLError, HTTPError
import zmq

def cmd_market_snapshot(symbol: str, timeout_sec: float = 4.0):
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    
    # The Hub Publisher binds to 5555, so we connect
    socket.connect("tcp://127.0.0.1:5555")
    
    # Subscribe to all events, we will filter by symbol in the loop
    socket.setsockopt_string(zmq.SUBSCRIBE, "")
    
    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)
    
    start_time = time.time()
    
    best_trade = None
    best_depth = None
    best_walls = None
    
    # Listen for up to timeout_sec
    while time.time() - start_time < timeout_sec:
        socks = dict(poller.poll(100)) # 100ms
        if socket in socks:
            try:
                parts = socket.recv_multipart(flags=zmq.NOBLOCK)
                if len(parts) >= 2:
                    payload_bytes = parts[-1]
                    payload = json.loads(payload_bytes.decode("utf-8"))
                    
                    if payload.get("symbol") == symbol:
                        ev_type = payload.get("event_type")
                        if ev_type in ("trade", "TradeEvent"):
                            best_trade = payload
                        elif ev_type in ("depth", "depth_l2", "OrderBookUpdate"):
                            best_depth = payload
                        elif ev_type == "walls":
                            best_walls = payload
                            
                        if best_depth and best_walls:
                            break
            except zmq.Again:
                continue
            except Exception:
                continue

    socket.close()
    context.term()

    if not best_trade and not best_depth and not best_walls:
        return {"error": f"No data received for symbol {symbol} within {timeout_sec}s"}

    # Format the snapshot
    snapshot = {
        "symbol": symbol,
        "timestamp": time.time(),
        "current_price": None,
        "mid_price": None,
        "spread": None,
        "top_bid": None,
        "top_ask": None,
        "whale_walls": best_walls.get("whale_walls", []) if best_walls else []
    }

    if best_trade:
        snapshot["current_price"] = best_trade.get("price")

    if best_depth:
        bids = best_depth.get("bids", [])
        asks = best_depth.get("asks", [])
        
        top_bid = None
        top_ask = None
        
        try:
            if bids:
                top_bid = float(bids[0].get("price") if isinstance(bids[0], dict) else bids[0][0])
            if asks:
                top_ask = float(asks[0].get("price") if isinstance(asks[0], dict) else asks[0][0])
        except (KeyError, IndexError, ValueError):
            pass

        if top_bid and top_ask:
            mid = (top_bid + top_ask) / 2.0
            spread = top_ask - top_bid
            snapshot["top_bid"] = top_bid
            snapshot["top_ask"] = top_ask
            snapshot["mid_price"] = round(mid, 4)
            snapshot["spread"] = round(spread, 4)
            if snapshot["current_price"] is None:
                snapshot["current_price"] = snapshot["mid_price"]
        
        # Fallback if whale_walls are bundled inside depth payload
        if not snapshot["whale_walls"] and "whale_walls" in best_depth:
            snapshot["whale_walls"] = best_depth.get("whale_walls", [])
    
    return snapshot

def cmd_query_db(sql: str):
    base_url = "http://127.0.0.1:9000/exec"
    params = urllib.parse.urlencode({"query": sql})
    url = f"{base_url}?{params}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            # QuestDB returns {"columns": [{"name": "..."}], "dataset": [[...]], "count": ...}
            columns = [c["name"] for c in data.get("columns", [])]
            dataset = data.get("dataset", [])
            
            # Map columns to dataset rows to create a clean list of dicts
            results = []
            for row in dataset:
                results.append(dict(zip(columns, row)))
            
            return {"results": results, "count": len(results)}
            
    except HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(error_body)
            error_msg = err_json.get("error", str(e))
        except json.JSONDecodeError:
            error_msg = error_body or str(e)
        return {"error": "Query failed", "details": error_msg}
    except URLError as e:
        return {"error": "Connection failed", "details": str(e.reason)}
    except Exception as e:
        return {"error": "Unexpected error", "details": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Data Plane MCP Bridge for Hermes Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # market_snapshot
    parser_snap = subparsers.add_parser("market_snapshot", help="Get a real-time market snapshot")
    parser_snap.add_argument("--symbol", type=str, required=True, help="Symbol to snapshot (e.g. BTCUSDT)")

    # query_db
    parser_db = subparsers.add_parser("query_db", help="Execute SQL against QuestDB")
    parser_db.add_argument("--sql", type=str, required=True, help="SQL query string")

    args = parser.parse_args()

    result = {}
    if args.command == "market_snapshot":
        result = cmd_market_snapshot(args.symbol.upper())
    elif args.command == "query_db":
        result = cmd_query_db(args.sql)

    # Output pure JSON
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
