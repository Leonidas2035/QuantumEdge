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
        "mid": None,
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

        if top_bid is not None:
            snapshot["top_bid"] = top_bid
        if top_ask is not None:
            snapshot["top_ask"] = top_ask

        # Enforce Single Source of Truth (SSOT) from Hub
        mid_price = best_depth.get("mid_price")
        spread = best_depth.get("spread")

        if mid_price is not None:
            snapshot["mid_price"] = round(float(mid_price), 4)
            snapshot["mid"] = snapshot["mid_price"]
            snapshot["current_price"] = snapshot["mid_price"] # SSOT dictates current_price is mid_price
        elif top_bid and top_ask:
            # Fallback only if SSOT is unavailable
            snapshot["mid_price"] = round((top_bid + top_ask) / 2.0, 4)
            snapshot["mid"] = snapshot["mid_price"]
            if snapshot["current_price"] is None:
                snapshot["current_price"] = snapshot["mid_price"]

        if spread is not None:
            snapshot["spread"] = round(float(spread), 4)
        elif top_bid and top_ask:
            snapshot["spread"] = round(top_ask - top_bid, 4)
        
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

def cmd_query_telemetry(bot_id: str, hours: int = 1):
    sql = f"""SELECT ts, last(pnl_session) as pnl, max(drawdown_pct) as max_dd
FROM bot_telemetry 
WHERE bot_id = '{bot_id}' AND ts > dateadd('h', -{hours}, now()) 
SAMPLE BY 5m ALIGN TO CALENDAR;"""
    res = cmd_query_db(sql)
    if "error" in res:
        return {"error": "No data yet", "details": res.get("details", "")}
    return res.get("results", [])

def cmd_query_market_trend(symbol: str, hours: int = 4):
    sql = f"""SELECT ts, avg(rsi_14) as rsi, avg(macd_line) as macd, avg(atr_14) as atr
FROM market_features 
WHERE symbol = '{symbol}' AND ts > dateadd('h', -{hours}, now()) 
SAMPLE BY 15m ALIGN TO CALENDAR;"""
    res = cmd_query_db(sql)
    results = res.get("results", []) if "error" not in res else []
    
    # Check if we have valid non-zero indicators
    needs_fallback = False
    if not results:
        needs_fallback = True
    else:
        all_zero = True
        for row in results:
            if float(row.get("rsi") or 0.0) != 0.0 or float(row.get("macd") or 0.0) != 0.0:
                all_zero = False
                break
        if all_zero:
            needs_fallback = True
            
    if needs_fallback:
        # Fetch raw 1m klines from DB to calculate indicators locally
        # Add extra hours lookback to allow indicators to warm up
        kline_sql = f"""SELECT ts, open, high, low, close, volume FROM klines_1m 
WHERE symbol = '{symbol}' AND ts > dateadd('h', -{hours + 6}, now()) 
ORDER BY ts ASC;"""
        kline_res = cmd_query_db(kline_sql)
        klines = kline_res.get("results", [])
        if klines:
            try:
                import pandas as pd
                import numpy as np
                
                df = pd.DataFrame(klines)
                df['ts'] = pd.to_datetime(df['ts'])
                df['close'] = df['close'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                
                # Compute ATR
                tr1 = df['high'] - df['low']
                tr2 = (df['high'] - df['close'].shift(1)).abs()
                tr3 = (df['low'] - df['close'].shift(1)).abs()
                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                df['atr'] = tr.rolling(14).mean()
                
                # Compute RSI
                delta = df['close'].diff()
                gain = delta.clip(lower=0).rolling(window=14).mean()
                loss = (-delta.clip(upper=0)).rolling(window=14).mean()
                rs = gain / (loss + 1e-10)
                df['rsi'] = 100.0 - (100.0 / (1.0 + rs))
                
                # Compute MACD
                ema12 = df['close'].ewm(span=12, adjust=False).mean()
                ema26 = df['close'].ewm(span=26, adjust=False).mean()
                df['macd'] = ema12 - ema26
                
                df = df.dropna(subset=['rsi', 'macd', 'atr']).copy()
                
                # Resample to 15m intervals
                df.set_index('ts', inplace=True)
                resampled = df.resample('15Min').agg({
                    'rsi': 'mean',
                    'macd': 'mean',
                    'atr': 'mean'
                }).interpolate(method='linear').fillna(0.0).reset_index()
                
                # Cutoff for requested hours
                cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)
                
                fallback_results = []
                for _, row in resampled.iterrows():
                    ts_val = row['ts']
                    if ts_val.tzinfo is None:
                        ts_val = ts_val.tz_localize('UTC')
                    if ts_val >= cutoff:
                        fallback_results.append({
                            "ts": row['ts'].strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                            "rsi": round(float(row['rsi']), 4),
                            "macd": round(float(row['macd']), 4),
                            "atr": round(float(row['atr']), 4)
                        })
                if fallback_results:
                    return fallback_results
            except Exception as e:
                # Fallback failure logs, return original empty/zero results
                pass
                
    return results

def main():
    parser = argparse.ArgumentParser(description="Data Plane MCP Bridge for Hermes Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # market_snapshot
    parser_snap = subparsers.add_parser("market_snapshot", help="Get a real-time market snapshot")
    parser_snap.add_argument("--symbol", type=str, required=True, help="Symbol to snapshot (e.g. BTCUSDT)")

    # query_db
    parser_db = subparsers.add_parser("query_db", help="Execute SQL against QuestDB")
    parser_db.add_argument("--sql", type=str, required=True, help="SQL query string")

    # query_telemetry
    parser_tel = subparsers.add_parser("query_telemetry", help="Query bot telemetry analytics")
    parser_tel.add_argument("--bot", type=str, required=True, help="Bot ID (e.g. scalper_v1)")
    parser_tel.add_argument("--hours", type=int, default=1, help="Lookback hours (default: 1)")

    # query_market_trend
    parser_trend = subparsers.add_parser("query_market_trend", help="Query market trend analytics")
    parser_trend.add_argument("--symbol", type=str, required=True, help="Symbol (e.g. BTCUSDT)")
    parser_trend.add_argument("--hours", type=int, default=4, help="Lookback hours (default: 4)")

    args = parser.parse_args()

    result = {}
    if args.command == "market_snapshot":
        result = cmd_market_snapshot(args.symbol.upper())
    elif args.command == "query_db":
        result = cmd_query_db(args.sql)
    elif args.command == "query_telemetry":
        result = cmd_query_telemetry(args.bot, args.hours)
    elif args.command == "query_market_trend":
        result = cmd_query_market_trend(args.symbol.upper(), args.hours)

    # Output pure JSON
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
