"""QuantumEdge Web Dashboard — Streamlit app styled like Binance Futures."""

from __future__ import annotations

import logging
import time
import json
import os
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import ccxt

from quantum_edge_core.dashboard.utils import (
    ProcessManager,
    clear_logs,
    fetch_data,
    force_apply_mode,
    get_mock_inventory,
    get_mock_llm_advice,
    get_mock_market_data,
    get_mock_orderbook,
    get_mock_trades,
    send_halt_command,
    tail_log,
    get_db_connection,
)

logger = logging.getLogger(__name__)

# Page Config
st.set_page_config(
    page_title="QuantumEdge Futures Terminal",
    layout="wide",
    page_icon="⚡",
)

# Dark styling to resemble Binance Futures
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
        background-color: #0B0E11;
        color: #EAECEF;
    }
    
    /* Hide top Streamlit decorations */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .terminal-header {
        background-color: #161A1E;
        border-bottom: 1px solid #2B3139;
        padding: 10px 20px;
        margin-bottom: 15px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-radius: 4px;
    }
    
    .ticker-metric {
        margin-right: 20px;
        display: inline-block;
    }
    .ticker-label {
        font-size: 11px;
        color: #848E9C;
        text-transform: uppercase;
        margin-bottom: 2px;
    }
    .ticker-value {
        font-size: 14px;
        font-weight: 600;
        font-family: 'Roboto Mono', monospace;
    }
    
    /* Table Styling */
    .binance-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
        font-family: 'Roboto Mono', monospace;
    }
    .binance-table th {
        color: #848E9C;
        text-align: left;
        padding: 4px 6px;
        font-weight: 500;
        border-bottom: 1px solid #2B3139;
    }
    .binance-table td {
        padding: 4px 6px;
        border-bottom: 1px solid rgba(43, 49, 57, 0.3);
    }
    
    /* Asks / Bids progress bar logic */
    .ask-row { color: #F6465D; position: relative; }
    .bid-row { color: #0ECB81; position: relative; }
    
    .ask-depth-bg {
        position: absolute;
        right: 0;
        top: 0;
        bottom: 0;
        background-color: rgba(246, 70, 93, 0.12);
        z-index: 0;
    }
    .bid-depth-bg {
        position: absolute;
        right: 0;
        top: 0;
        bottom: 0;
        background-color: rgba(14, 203, 129, 0.12);
        z-index: 0;
    }
    .row-content {
        position: relative;
        z-index: 1;
        display: flex;
        justify-content: space-between;
        width: 100%;
    }
    
    .whale-wall {
        border: 1px solid #F3BA2F;
        background-color: rgba(243, 186, 47, 0.15) !important;
        animation: pulse-whale 2s infinite;
        padding: 2px 4px;
        border-radius: 2px;
        font-weight: bold;
    }
    
    @keyframes pulse-whale {
        0% { box-shadow: 0 0 0 0 rgba(243, 186, 47, 0.4); }
        70% { box-shadow: 0 0 0 4px rgba(243, 186, 47, 0); }
        100% { box-shadow: 0 0 0 0 rgba(243, 186, 47, 0); }
    }
    
    /* Panel card */
    .panel-card {
        background-color: #161A1E;
        border: 1px solid #2B3139;
        border-radius: 8px;
        padding: 15px;
        margin-bottom: 15px;
    }
    
    /* Streamlit custom colors */
    div[data-testid="metric-container"] {
        background-color: #161A1E;
        border: 1px solid #2B3139;
        padding: 10px 15px;
        border-radius: 6px;
    }
    
    .stTabs [data-baseweb="tab-list"] {
        background-color: #161A1E;
        border-radius: 4px 4px 0 0;
        border: 1px solid #2B3139;
        padding: 0 10px;
    }
    .stTabs [data-baseweb="tab"] {
        color: #848E9C !important;
        font-size: 13px !important;
        font-weight: 500 !important;
    }
    .stTabs [aria-selected="true"] {
        color: #F3BA2F !important;
        border-bottom-color: #F3BA2F !important;
    }
    
    /* Buttons styling */
    .stButton>button {
        width: 100%;
        border-radius: 4px;
        background-color: #2B3139;
        color: #EAECEF;
        border: 1px solid #474F5A;
        font-size: 13px;
        font-weight: 500;
        padding: 6px 12px;
    }
    .stButton>button:hover {
        border-color: #F3BA2F;
        color: #F3BA2F;
        background-color: #2B3139;
    }
    .btn-danger>button {
        background-color: #F6465D !important;
        border-color: #F6465D !important;
        color: white !important;
    }
    .btn-danger>button:hover {
        background-color: #CF304A !important;
        border-color: #CF304A !important;
    }
    .btn-success>button {
        background-color: #0ECB81 !important;
        border-color: #0ECB81 !important;
        color: white !important;
    }
    .btn-success>button:hover {
        background-color: #0B9E64 !important;
        border-color: #0B9E64 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Refresh interval (5 seconds)
st_autorefresh(interval=5000, key="terminal_refresh")

# --- Helper functions for data fetching ---

@st.cache_resource
def get_ccxt_client():
    api_key = os.getenv("BINGX_TESTNET_API_KEY") or os.getenv("BINGX_API_KEY")
    secret = os.getenv("BINGX_TESTNET_SECRET") or os.getenv("BINGX_SECRET")
    use_testnet = os.getenv("USE_TESTNET", "true").lower() in {"1", "true", "t"}
    
    if not api_key or not secret:
        return None
    try:
        exchange = ccxt.bingx({
            "apiKey": api_key,
            "secret": secret,
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
        })
        if use_testnet:
            exchange.set_sandbox_mode(True)
        return exchange
    except Exception as e:
        logger.error(f"Failed to create CCXT client: {e}")
        return None

def fetch_exchange_telemetry(exchange):
    if not exchange:
        return {}
    try:
        balance = exchange.fetch_balance()
        use_testnet = os.getenv("USE_TESTNET", "true").lower() in {"1", "true", "t"}
        quote_asset = "VST" if use_testnet else "USDT"
        
        free_balance = float(balance.get(quote_asset, {}).get("free", 100000.0))
        total_balance = float(balance.get(quote_asset, {}).get("total", 100000.0))
        
        symbol = "BTC/USDT:USDT"
        positions = exchange.fetch_positions(symbols=[symbol])
        
        active_positions = []
        unrealized_pnl = 0.0
        position_qty = 0.0
        leverage = 20.0
        liq_price = 0.0
        
        for pos in positions:
            size = float(pos.get("contracts") or pos.get("size") or 0.0)
            if size > 0:
                side = pos.get("side", "").upper()
                pnl = float(pos.get("unrealizedPnl") or 0.0)
                entry_price = float(pos.get("entryPrice") or pos.get("averagePrice") or 0.0)
                mark_price = float(pos.get("markPrice") or 0.0)
                liq = float(pos.get("liquidationPrice") or 0.0)
                lev = float(pos.get("leverage") or 20.0)
                
                unrealized_pnl += pnl
                position_qty += size if side == "LONG" else -size
                leverage = lev
                liq_price = liq
                
                active_positions.append({
                    "Symbol": pos.get("symbol"),
                    "Side": side,
                    "Size": size,
                    "Entry Price": entry_price,
                    "Mark Price": mark_price,
                    "Liquidation Price": liq,
                    "Leverage": lev,
                    "Unrealized PnL": pnl,
                    "Margin": float(pos.get("initialMargin") or 0.0)
                })
                
        # Fetch open orders
        orders = exchange.fetch_open_orders(symbol=symbol)
        open_orders = []
        for o in orders:
            open_orders.append({
                "ID": o.get("id"),
                "Type": o.get("type", "").upper(),
                "Side": o.get("side", "").upper(),
                "Price": float(o.get("price") or 0.0),
                "Amount": float(o.get("amount") or 0.0),
                "Filled": float(o.get("filled") or 0.0),
                "Status": o.get("status", "").upper(),
                "Time": pd.to_datetime(o.get("timestamp"), unit="ms") if o.get("timestamp") else pd.Timestamp.now()
            })
            
        return {
            "balance": free_balance,
            "total_balance": total_balance,
            "unrealized_pnl": unrealized_pnl,
            "position_qty": position_qty,
            "leverage": leverage,
            "liq_price": liq_price,
            "positions": active_positions,
            "open_orders": open_orders
        }
    except Exception as e:
        logger.warning(f"CCXT telemetry fetch failed: {e}")
        return {}

def fetch_portfolio_state_fallback():
    conn = get_db_connection()
    if not conn:
        return {}
    try:
        query = """
        SELECT timestamp, equity, unrealized_pnl, position_qty, leverage, liquidation_price
        FROM portfolio_state
        ORDER BY timestamp DESC
        LIMIT 1
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        if not df.empty:
            row = df.iloc[0]
            return {
                "balance": float(row.get("equity", 100000.0)),
                "total_balance": float(row.get("equity", 100000.0)),
                "unrealized_pnl": float(row.get("unrealized_pnl", 0.0)),
                "position_qty": float(row.get("position_qty", 0.0)),
                "leverage": float(row.get("leverage", 20.0)),
                "liq_price": float(row.get("liquidation_price", 0.0)),
                "positions": [],
                "open_orders": []
            }
    except Exception as e:
        logger.warning(f"Failed to fetch portfolio state fallback: {e}")
        if conn:
            conn.close()
    return {}

def fetch_candles(timeframe):
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM klines_1m")
        count = cur.fetchone()[0]
        if count > 0:
            query = f"""
            SELECT ts AS timestamp, open, high, low, close, volume
            FROM klines_1m
            WHERE symbol = 'BTCUSDT'
            ORDER BY ts DESC
            LIMIT 500
            """
            df = pd.read_sql_query(query, conn)
            df = df.sort_values("timestamp")
            conn.close()
            return df
        else:
            query = """
            SELECT timestamp, open, high, low, close, volume
            FROM kline
            WHERE symbol = 'BTCUSDT'
            ORDER BY timestamp DESC
            LIMIT 500
            """
            df = pd.read_sql_query(query, conn)
            df = df.sort_values("timestamp")
            conn.close()
            return df
    except Exception as e:
        logger.warning(f"Failed to fetch klines: {e}")
        if conn:
            conn.close()
        return pd.DataFrame()

def fetch_real_orderbook():
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        # Get latest timestamp from orderbook_snapshots
        query = """
        SELECT price, qty, side, timestamp
        FROM orderbook_snapshots
        WHERE symbol = 'BTCUSDT' AND timestamp = (SELECT max(timestamp) FROM orderbook_snapshots)
        ORDER BY price DESC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        logger.warning(f"Failed to fetch orderbook snapshots: {e}")
        if conn:
            conn.close()
        return pd.DataFrame()

def is_localhost() -> bool:
    try:
        headers = st.context.headers
        if not headers:
            return True
        host = headers.get("Host", "")
        return host.startswith("localhost") or host.startswith("127.0.0.1")
    except Exception:
        return True

# --- Dialog Confirmation ---
@st.dialog("Confirm Action")
def confirm_process_action(action: str, name: str, cmd: str = ""):
    if not is_localhost():
        st.error("Action denied: Process controls are only allowed from localhost.")
        return
    st.write(f"Are you sure you want to {action} the **{name}** process?")
    if st.button(f"Yes, {action}"):
        if action == "Start":
            ProcessManager.start_process(name, cmd)
        elif action == "Stop":
            ProcessManager.stop_process(name)
        elif action == "Restart":
            ProcessManager.restart_process(name, cmd)
        st.rerun()

# --- Main Data Gathering ---
exchange = get_ccxt_client()
telemetry = fetch_exchange_telemetry(exchange)

# Fallback if CCXT fails
if not telemetry:
    telemetry = fetch_portfolio_state_fallback()
    
# Supply default values if everything is empty
if not telemetry:
    telemetry = {
        "balance": 100000.0,
        "total_balance": 100000.0,
        "unrealized_pnl": 0.0,
        "position_qty": 0.0,
        "leverage": 20.0,
        "liq_price": 0.0,
        "positions": [],
        "open_orders": []
    }

# Candles and price
df_candles = fetch_candles("1m")
last_price = 0.0
change_pct = 0.0
high_24h = 0.0
low_24h = 0.0
volume_24h = 0.0

if not df_candles.empty:
    last_price = float(df_candles["close"].iloc[-1])
    open_price = float(df_candles["open"].iloc[0])
    change_pct = ((last_price - open_price) / open_price) * 100 if open_price > 0 else 0.0
    high_24h = float(df_candles["high"].max())
    low_24h = float(df_candles["low"].min())
    volume_24h = float(df_candles["volume"].sum())

# Fetch orderbook snapshots
df_ob = fetch_real_orderbook()

# --- 1. Top Header Info Strip ---
header_html = f"""
<div class="terminal-header">
    <div style="display: flex; align-items: center;">
        <span style="font-size: 20px; font-weight: bold; color: #F3BA2F; margin-right: 15px;">⚡ QUANTUMEDGE TERMINAL</span>
        <span style="background-color: #2B3139; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 13px; color: #EAECEF;">BTCUSDT Perpetual</span>
    </div>
    <div style="display: flex; align-items: center;">
        <div class="ticker-metric">
            <div class="ticker-label">Mark Price</div>
            <div class="ticker-value" style="color: {'#0ECB81' if change_pct >= 0 else '#F6465D'}">${last_price:,.2f}</div>
        </div>
        <div class="ticker-metric">
            <div class="ticker-label">24h Change</div>
            <div class="ticker-value" style="color: {'#0ECB81' if change_pct >= 0 else '#F6465D'}">{change_pct:+.2f}%</div>
        </div>
        <div class="ticker-metric">
            <div class="ticker-label">24h High</div>
            <div class="ticker-value">${high_24h:,.2f}</div>
        </div>
        <div class="ticker-metric">
            <div class="ticker-label">24h Low</div>
            <div class="ticker-value">${low_24h:,.2f}</div>
        </div>
        <div class="ticker-metric">
            <div class="ticker-label">24h Volume (BTC)</div>
            <div class="ticker-value">{volume_24h:,.2f}</div>
        </div>
        <div class="ticker-metric">
            <div class="ticker-label">Funding / Countdown</div>
            <div class="ticker-value" style="color: #F3BA2F;">0.0100% / 07:44:12</div>
        </div>
    </div>
</div>
"""
st.markdown(header_html, unsafe_allow_html=True)

# --- 2. Three-Column Grid Layout ---
col_left, col_center, col_right = st.columns([1.1, 2.3, 1.1])

# --- COLUMN 1: Order Book & Market Trades ---
with col_left:
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.subheader("Order Book")
    
    # Process Orderbook snapshot
    if not df_ob.empty:
        asks = df_ob[df_ob["side"] == "SELL"].sort_values("price", ascending=False).head(10)
        bids = df_ob[df_ob["side"] == "BUY"].sort_values("price", ascending=False).head(10)
        
        # Calculate maximum quantity for depth background bars
        max_qty = max(df_ob["qty"].max(), 1.0)
        
        # Asks (Red)
        asks_html = ""
        for _, row in asks.iterrows():
            price = float(row["price"])
            qty = float(row["qty"])
            width_pct = min(100, (qty / max_qty) * 100)
            is_whale = "whale-wall" if qty >= 20.0 else ""
            
            asks_html += f"""
            <tr class="ask-row">
                <td style="position: relative; width: 100%; border: none; padding: 2px 6px;">
                    <div class="ask-depth-bg" style="width: {width_pct}%;"></div>
                    <div class="row-content">
                        <span class="{is_whale}">{price:,.1f}</span>
                        <span>{qty:.3f}</span>
                        <span>{price*qty:,.0f}</span>
                    </div>
                </td>
            </tr>
            """
            
        # Bids (Green)
        bids_html = ""
        for _, row in bids.iterrows():
            price = float(row["price"])
            qty = float(row["qty"])
            width_pct = min(100, (qty / max_qty) * 100)
            is_whale = "whale-wall" if qty >= 20.0 else ""
            
            bids_html += f"""
            <tr class="bid-row">
                <td style="position: relative; width: 100%; border: none; padding: 2px 6px;">
                    <div class="bid-depth-bg" style="width: {width_pct}%;"></div>
                    <div class="row-content">
                        <span class="{is_whale}">{price:,.1f}</span>
                        <span>{qty:.3f}</span>
                        <span>{price*qty:,.0f}</span>
                    </div>
                </td>
            </tr>
            """
            
        # Spread calculation
        best_ask = asks["price"].min() if not asks.empty else last_price
        best_bid = bids["price"].max() if not bids.empty else last_price
        spread = best_ask - best_bid
        
        orderbook_table = f"""
        <table class="binance-table" style="border: none;">
            <thead>
                <tr>
                    <th style="padding: 2px 6px;"><div class="row-content"><span>Price (USDT)</span><span>Size (BTC)</span><span>Total (USDT)</span></div></th>
                </tr>
            </thead>
            <tbody>
                {asks_html}
                <tr style="border-top: 1px solid #2B3139; border-bottom: 1px solid #2B3139;">
                    <td style="padding: 6px; font-weight: bold; text-align: center;">
                        <span style="color: {'#0ECB81' if change_pct >= 0 else '#F6465D'}; font-size: 15px;">${last_price:,.1f}</span>
                        <span style="color: #848E9C; font-size: 11px; margin-left: 10px;">Spread: ${spread:,.1f}</span>
                    </td>
                </tr>
                {bids_html}
            </tbody>
        </table>
        """
        st.markdown(orderbook_table, unsafe_allow_html=True)
    else:
        st.info("No live orderbook data in QuestDB.")
        
    st.markdown("</div>", unsafe_allow_html=True)

# --- COLUMN 2: Main Chart & Volatility ---
with col_center:
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    tf = st.selectbox("Timeframe", ["1m", "5m", "15m"], index=0, key="tf_select")
    
    if df_candles.empty or len(df_candles) < 2:
        st.info("Awaiting kline data from QuestDB...")
    else:
        # Calculate EMA Indicators
        df_candles["ema9"] = df_candles["close"].ewm(span=9, adjust=False).mean()
        df_candles["ema21"] = df_candles["close"].ewm(span=21, adjust=False).mean()
        
        # Subplot: Chart + Volume
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_width=[0.2, 0.8],
        )
        
        # Candlesticks
        fig.add_trace(
            go.Candlestick(
                x=df_candles["timestamp"],
                open=df_candles["open"],
                high=df_candles["high"],
                low=df_candles["low"],
                close=df_candles["close"],
                name="Price",
                increasing_line_color="#0ECB81",
                decreasing_line_color="#F6465D",
                increasing_fillcolor="#0ECB81",
                decreasing_fillcolor="#F6465D",
            ),
            row=1,
            col=1,
        )
        
        # EMA Lines
        fig.add_trace(
            go.Scatter(
                x=df_candles["timestamp"],
                y=df_candles["ema9"],
                line=dict(color="#F3BA2F", width=1),
                name="EMA 9",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df_candles["timestamp"],
                y=df_candles["ema21"],
                line=dict(color="#2196F3", width=1),
                name="EMA 21",
            ),
            row=1,
            col=1,
        )
        
        # Fetch realized trades from QuestDB to overlay execution points
        df_trades_history, _ = fetch_data(
            "SELECT timestamp, symbol, side, price, qty, realized_pnl "
            "FROM realized_trades WHERE symbol = 'BTCUSDT' "
            "ORDER BY timestamp DESC LIMIT 500",
            lambda: pd.DataFrame()
        )
        
        if not df_trades_history.empty:
            buys = df_trades_history[df_trades_history["side"].str.upper() == "BUY"]
            sells = df_trades_history[df_trades_history["side"].str.upper() == "SELL"]
            
            # Map timestamps to match datetime format in Plotly
            if not buys.empty:
                fig.add_trace(
                    go.Scatter(
                        x=buys["timestamp"],
                        y=buys["price"],
                        mode="markers",
                        marker=dict(color="#0ECB81", size=10, symbol="triangle-up", line=dict(color="white", width=1)),
                        name="BUY Execution",
                    ),
                    row=1,
                    col=1,
                )
            if not sells.empty:
                fig.add_trace(
                    go.Scatter(
                        x=sells["timestamp"],
                        y=sells["price"],
                        mode="markers",
                        marker=dict(color="#F6465D", size=10, symbol="triangle-down", line=dict(color="white", width=1)),
                        name="SELL Execution",
                    ),
                    row=1,
                    col=1,
                )
        
        # Volume
        volume_colors = [
            "#F6465D" if row["open"] - row["close"] >= 0 else "#0ECB81"
            for _, row in df_candles.iterrows()
        ]
        fig.add_trace(
            go.Bar(
                x=df_candles["timestamp"],
                y=df_candles["volume"],
                marker_color=volume_colors,
                name="Volume",
                opacity=0.7,
            ),
            row=2,
            col=1,
        )
        
        fig.update_layout(
            height=500,
            template="plotly_dark",
            xaxis_rangeslider_visible=False,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="#161A1E",
            plot_bgcolor="#161A1E",
            grid=dict(rows=2, columns=1),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(gridcolor="#2B3139", tickfont=dict(color="#848E9C")),
            xaxis=dict(gridcolor="#2B3139", tickfont=dict(color="#848E9C")),
            yaxis2=dict(gridcolor="#2B3139", tickfont=dict(color="#848E9C")),
        )
        st.plotly_chart(fig, use_container_width=True)
        
    st.markdown("</div>", unsafe_allow_html=True)

# --- COLUMN 3: Control Center & Risk Management ---
with col_right:
    # Service control cards
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.subheader("Process Controls")
    
    processes = {
        "Hub": "python3 -m quantum_edge_core.market_data.hub",
        "Supervisor": "python3 -m hermes.supervisor run-foreground",
        "Bot": "python3 -m quantum_edge_core.ai_scalper_bot.run_bot",
    }
    
    for name, cmd in processes.items():
        is_running = ProcessManager.is_running(name)
        pid = ProcessManager.get_pid(name)
        status_cls = "color: #0ECB81;" if is_running else "color: #F6465D;"
        status_text = f"RUNNING (PID {pid})" if is_running else "STOPPED"
        
        st.markdown(
            f"**{name}:** <span style='{status_cls} font-weight: bold;'>{status_text}</span>",
            unsafe_allow_html=True,
        )
        
        col1, col2 = st.columns(2)
        with col1:
            if not is_running:
                if st.button("Start", key=f"start_{name}"):
                    confirm_process_action("Start", name, cmd)
            else:
                if st.button("Stop", key=f"stop_{name}"):
                    confirm_process_action("Stop", name)
        with col2:
            if st.button("Restart", key=f"restart_{name}"):
                confirm_process_action("Restart", name, cmd)
        st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #2B3139;' />", unsafe_allow_html=True)
        
    # Cold Start Button
    if st.button("❄️ Cold Start Full System", key="cold_start"):
        if is_localhost():
            ProcessManager.cold_start_full_system()
            st.success("Cold start initiated!")
            time.sleep(1.5)
            st.rerun()
            
    # Emergency controls
    st.markdown('<div class="btn-danger">', unsafe_allow_html=True)
    if st.button("🛑 EMERGENCY HALT", key="halt_btn"):
        if is_localhost():
            if send_halt_command():
                st.error("HALT Command Broadcasted!")
            else:
                st.warning("Failed to broadcast HALT.")
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # Risk Metrics panel
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.subheader("Risk & Portfolio")
    
    bal = telemetry.get("balance", 100000.0)
    total_bal = telemetry.get("total_balance", 100000.0)
    upnl = telemetry.get("unrealized_pnl", 0.0)
    pos_qty = telemetry.get("position_qty", 0.0)
    lev = telemetry.get("leverage", 20.0)
    liq = telemetry.get("liq_price", 0.0)
    
    # Check if liq price is close to last price
    liq_alert_style = ""
    if liq > 0 and last_price > 0:
        dist = abs(last_price - liq) / last_price
        if dist < 0.05: # Close than 5%
            liq_alert_style = "color: #F6465D; font-weight: bold; animation: pulse-whale 1s infinite;"
            
    pnl_style = "color: #0ECB81;" if upnl >= 0 else "color: #F6465D;"
    qty_style = "color: #0ECB81;" if pos_qty > 0 else ("color: #F6465D;" if pos_qty < 0 else "")
    
    st.markdown(
        f"""
        <table class="binance-table" style="width:100%; border:none;">
            <tr><td>Account Balance</td><td style="text-align:right; font-weight:bold;">${total_bal:,.2f}</td></tr>
            <tr><td>Available Margin</td><td style="text-align:right; font-weight:bold;">${bal:,.2f}</td></tr>
            <tr><td>Unrealized PnL</td><td style="text-align:right; font-weight:bold; {pnl_style}">${upnl:+,.2f}</td></tr>
            <tr><td>Position Size</td><td style="text-align:right; font-weight:bold; {qty_style}">{pos_qty:+.4f} BTC</td></tr>
            <tr><td>Leverage</td><td style="text-align:right; font-weight:bold; color:#F3BA2F;">{lev:.0f}x</td></tr>
            <tr><td>Liquidation Price</td><td style="text-align:right; font-weight:bold; {liq_alert_style}">${liq:,.2f}</td></tr>
        </table>
        """,
        unsafe_allow_html=True,
    )
    
    # Manual Override Mode
    st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #2B3139;' />", unsafe_allow_html=True)
    st.markdown("**Manual Regime Override:**")
    override_mode = st.selectbox("Regime", ["SCALP", "DCA", "PASS", "NEUTRAL"], index=1, key="override_mode")
    if st.button("Apply Regime", key="apply_override"):
        if is_localhost():
            if force_apply_mode(override_mode):
                st.success(f"Mode {override_mode} forced!")
                st.rerun()
                
    st.markdown("</div>", unsafe_allow_html=True)

# --- 3. Bottom Panels / Tabs ---
st.markdown("### Terminal Details")
bot_tabs = st.tabs(["Positions", "Open Orders", "Trade History", "AI Supervisor Advice", "System Logs"])

# Tab 1: Positions
with bot_tabs[0]:
    st.markdown("#### Active Positions")
    positions = telemetry.get("positions", [])
    if not positions:
        st.info("No active positions.")
    else:
        df_p = pd.DataFrame(positions)
        st.dataframe(df_p, use_container_width=True)

# Tab 2: Open Orders
with bot_tabs[1]:
    st.markdown("#### Active DCA Grid Orders")
    open_orders = telemetry.get("open_orders", [])
    if not open_orders:
        st.info("No active open orders.")
    else:
        df_oo = pd.DataFrame(open_orders)
        st.dataframe(df_oo, use_container_width=True)

# Tab 3: Trade History
with bot_tabs[2]:
    st.markdown("#### Executed Trades Log")
    df_trades_history, _ = fetch_data(
        "SELECT timestamp, symbol, side, price, qty, realized_pnl "
        "FROM realized_trades WHERE symbol = 'BTCUSDT' "
        "ORDER BY timestamp DESC LIMIT 100",
        lambda: pd.DataFrame()
    )
    if df_trades_history.empty:
        st.info("No realized trades recorded in database.")
    else:
        st.dataframe(df_trades_history, use_container_width=True)

# Tab 4: AI Supervisor Advice
with bot_tabs[3]:
    st.markdown("#### AI Supervisor Brain Decisions")
    
    # Try fetching llm_advice table, fallback if not exists
    conn = get_db_connection()
    df_llm = pd.DataFrame()
    if conn:
        try:
            # Check if table exists
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM llm_advice")
            count = cur.fetchone()[0]
            if count > 0:
                df_llm = pd.read_sql_query("SELECT * FROM llm_advice ORDER BY time DESC LIMIT 50", conn)
        except Exception:
            pass
        finally:
            conn.close()
            
    if df_llm.empty:
        st.info("No LLM Supervisor decisions logged in database yet.")
    else:
        st.dataframe(df_llm, use_container_width=True)

# Tab 5: System Logs
with bot_tabs[4]:
    st.markdown("#### Real-time Log Stream")
    if st.button("Clean All System Logs", key="clear_logs_btn"):
        clear_logs()
        st.success("Logs wiped successfully.")
        st.rerun()
        
    col_log1, col_log2, col_log3 = st.columns(3)
    with col_log1:
        st.markdown("**MarketDataHub Log**")
        st.code(tail_log("hub.log", 30), language="log")
    with col_log2:
        st.markdown("**Supervisor Log**")
        st.code(tail_log("supervisor.log", 30), language="log")
    with col_log3:
        st.markdown("**Trading Bot Log**")
        st.code(tail_log("bot.log", 30), language="log")
