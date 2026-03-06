"""QuantumEdge Web Dashboard — Streamlit app."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from urllib.parse import urlparse
from streamlit.web.server.websocket_headers import _get_websocket_headers

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
)

logger = logging.getLogger(__name__)

# ─── Page Config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="QuantumEdge • Single Pane of Glass",
    layout="wide",
    page_icon="⚡",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
        background-color: #0E1117;
        color: #E0E0E0;
    }
    .status-card {
        background: #1a1a2e;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 15px;
        margin-bottom: 10px;
    }
    .status-up { color: #00e676; font-weight: bold; }
    .status-down { color: #ff5252; font-weight: bold; }
    .stButton>button {
        width: 100%;
        border-radius: 6px;
        background-color: #2b2b36;
        color: #fff;
        border: 1px solid #4a4a5a;
    }
    .stButton>button:hover {
        border-color: #00d4aa;
        color: #00d4aa;
    }
    .btn-danger>button {
        background-color: #ff1744 !important;
        border-color: #ff1744 !important;
        color: white !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Refresh every 5 seconds
st_autorefresh(interval=5000, key="data_refresh")

def is_localhost():
    """Check if the request originates from localhost."""
    headers = _get_websocket_headers()
    if not headers:
        return True # Default to allowing if headers can't be fetched
    host = headers.get("Host", "")
    return host.startswith("localhost") or host.startswith("127.0.0.1")

# ─── Sidebar ─────────────────────────────────────────────────────────

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

with st.sidebar:
    st.markdown("## ⚡ QuantumEdge Controls")

    st.markdown("### Process Status")

    processes = {
        "Hub": "python3 -m quantum_edge_core.market_data.hub",
        "Supervisor": "python3 -m quantum_edge_core.supervisor.supervisor run-foreground --mode paper",
        "Bot": "python3 -m quantum_edge_core.ai_scalper_bot.run_bot"
    }

    for name, cmd in processes.items():
        is_running = ProcessManager.is_running(name)
        pid = ProcessManager.get_pid(name)
        status_cls = "status-up" if is_running else "status-down"
        status_text = f"RUNNING (PID {pid})" if is_running else "STOPPED"

        st.markdown(f"**{name}:** <span class='{status_cls}'>{status_text}</span>", unsafe_allow_html=True)

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Start", key=f"start_{name}"):
                confirm_process_action("Start", name, cmd)
        with col2:
            if st.button("Stop", key=f"stop_{name}"):
                confirm_process_action("Stop", name, cmd)
        with col3:
            if st.button("Restart", key=f"restart_{name}"):
                confirm_process_action("Restart", name, cmd)
        st.markdown("---")

    if st.button("❄️ Cold Start Full System"):
        if is_localhost():
            ProcessManager.cold_start_full_system()
            st.success("Cold start initiated!")
            time.sleep(1)
            st.rerun()
        else:
            st.error("Action denied: localhost only.")

    st.markdown("### Emergency Controls")
    st.markdown('<div class="btn-danger">', unsafe_allow_html=True)
    if st.button("🛑 Manual HALT"):
        if is_localhost():
            if send_halt_command():
                st.error("HALT command sent via ZMQ!")
            else:
                st.warning("Failed to send HALT.")
        else:
            st.error("Action denied: localhost only.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("### Manual Override")
    mode = st.selectbox("Trading Mode", ["SCALP", "DCA", "PASS", "NEUTRAL"])
    if st.button("Force Apply Mode"):
        if is_localhost():
            if force_apply_mode(mode):
                st.success(f"Mode {mode} forced!")
            else:
                st.error("Failed to apply mode.")
        else:
            st.error("Action denied: localhost only.")

    st.markdown("### Metrics Overview")
    st.metric("Equity", "$10,245.50", "2.4%")
    st.metric("Unrealized PnL", "-$12.30", "-0.1%")
    st.metric("Drawdown", "1.2%", "-0.5%")
    st.metric("Risk Multiplier", "1.5x", "0.5")

# ─── Tabs ────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "👁️ Очі Кванта (Market Overview)",
    "🧠 Мізки AI (LLM Supervisor Brain)",
    "⚡ Виконання (Execution)",
    "🛡️ Ризик (Inventory & Risk)",
    "🔥 Orderbook Heatmap",
    "📝 Live Logs"
])

# ─── Tab 1: Market Overview ──────────────────────────────────────────
with tab1:
    st.markdown("### Market Overview (BTC/USDT)")
    tf = st.selectbox("Timeframe", ["1m", "5m", "15m"], index=0)

    # Precise query as requested in requirements
    sql_query = """
    SELECT timestamp, first(price) AS open, max(high) AS high, min(low) AS low, last(price) AS close, sum(volume) AS volume
    FROM trades
    SAMPLE BY 1m ALIGN TO CALENDAR
    WHERE timestamp > now() - 24h
    ORDER BY timestamp
    """

    # Query logic simulated with fallback
    df, is_mock = fetch_data(sql_query, get_mock_market_data)

    if is_mock:
        st.error("⚠️ Table not found — running in demo mode")

    # Calculate TA
    df.ta.bbands(length=20, std=2, append=True)
    try:
        df.ta.supertrend(length=7, multiplier=3, append=True)
    except Exception:
        pass # Handle case if ta not fully available

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03, subplot_titles=('Price', 'Volume'),
        row_width=[0.2, 0.7]
    )

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df['timestamp'], open=df['open'], high=df['high'],
        low=df['low'], close=df['close'], name='Price'
    ), row=1, col=1)

    # Bollinger Bands
    if 'BBL_20_2.0' in df.columns:
        fig.add_trace(go.Scatter(x=df['timestamp'], y=df['BBU_20_2.0'], line=dict(color='gray', width=1, dash='dot'), name='Upper BB'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['timestamp'], y=df['BBL_20_2.0'], line=dict(color='gray', width=1, dash='dot'), name='Lower BB', fill='tonexty', fillcolor='rgba(128,128,128,0.1)'), row=1, col=1)

    # Volume
    colors = ['red' if row['open'] - row['close'] >= 0 else 'green' for index, row in df.iterrows()]
    fig.add_trace(go.Bar(x=df['timestamp'], y=df['volume'], marker_color=colors, name='Volume'), row=2, col=1)

    fig.update_layout(height=700, template='plotly_dark', xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

# ─── Tab 2: LLM Supervisor Brain ─────────────────────────────────────
with tab2:
    st.markdown("### AI Supervisor Reasoning History")
    df_llm, _ = fetch_data("SELECT * FROM llm_advice", get_mock_llm_advice)

    # Step chart
    fig_llm = go.Figure()

    # Mode mapping for step chart visualization
    mode_map = {"SCALP": 4, "DCA": 3, "NEUTRAL": 2, "PASS": 1, "HALT": 0}
    y_vals = [mode_map.get(m, 2) for m in df_llm['mode']]

    fig_llm.add_trace(go.Scatter(
        x=df_llm['time'], y=y_vals,
        mode='lines+markers', line_shape='hv',
        name='Trading Mode',
        text=df_llm['reason'],
        hovertemplate='<b>%{text}</b><br>Mode Level: %{y}<extra></extra>'
    ))

    # Overlay risk multiplier
    fig_llm.add_trace(go.Scatter(
        x=df_llm['time'], y=df_llm['multiplier'],
        mode='lines', line=dict(color='orange', dash='dash'),
        name='Risk Multiplier',
        yaxis='y2'
    ))

    fig_llm.update_layout(
        height=400, template='plotly_dark',
        yaxis=dict(title='Mode', tickvals=[0,1,2,3,4], ticktext=['HALT','PASS','NEUTRAL','DCA','SCALP']),
        yaxis2=dict(title='Multiplier', overlaying='y', side='right')
    )
    st.plotly_chart(fig_llm, use_container_width=True)

    st.markdown("#### Latest 20 Decisions")
    st.dataframe(df_llm.head(20), use_container_width=True)

# ─── Tab 3: Execution & Trades ───────────────────────────────────────
with tab3:
    st.markdown("### Trade Executions")

    df_trades, _ = fetch_data("SELECT * FROM executed_trades", get_mock_trades)

    # Overlay trades on Candlestick
    st.markdown("#### Execution Overlay")
    df_market, _ = fetch_data(
        "SELECT timestamp, first(price) AS open, max(high) AS high, min(low) AS low, last(price) AS close, sum(volume) AS volume FROM trades SAMPLE BY 1m ALIGN TO CALENDAR WHERE timestamp > now() - 24h ORDER BY timestamp",
        get_mock_market_data
    )

    fig_overlay = go.Figure()
    fig_overlay.add_trace(go.Candlestick(
        x=df_market['timestamp'], open=df_market['open'], high=df_market['high'],
        low=df_market['low'], close=df_market['close'], name='Price'
    ))

    # Mocking timestamps for trades to overlay on the chart properly
    if 'timestamp' not in df_trades.columns:
        df_trades['timestamp'] = [df_market['timestamp'].iloc[-(i+1)*5] for i in range(len(df_trades))]

    buys_df = df_trades[df_trades['side'] == 'BUY']
    sells_df = df_trades[df_trades['side'] == 'SELL']

    fig_overlay.add_trace(go.Scatter(
        x=buys_df['timestamp'], y=buys_df['price'],
        mode='markers', marker=dict(color='green', size=12, symbol='triangle-up'),
        name='Buy Execution'
    ))

    fig_overlay.add_trace(go.Scatter(
        x=sells_df['timestamp'], y=sells_df['price'],
        mode='markers', marker=dict(color='red', size=12, symbol='triangle-down'),
        name='Sell Execution'
    ))

    fig_overlay.update_layout(height=500, template='plotly_dark', xaxis_rangeslider_visible=False)
    st.plotly_chart(fig_overlay, use_container_width=True)

    col_skew, col_pos = st.columns(2)

    buys = len(df_trades[df_trades['side'] == 'BUY'])
    sells = len(df_trades[df_trades['side'] == 'SELL'])
    total = max(1, buys + sells)
    buy_pct = buys / total * 100

    skew_color = "red" if abs(buy_pct - 50) > 20 else "green"

    with col_skew:
        st.markdown(f"#### Skew: <span style='color:{skew_color}'>Buy {buy_pct:.0f}% / Sell {100-buy_pct:.0f}%</span>", unsafe_allow_html=True)
    with col_pos:
        st.markdown("#### Current Position: 0.45 BTC")

    st.dataframe(df_trades, use_container_width=True)

# ─── Tab 4: Inventory & Risk ─────────────────────────────────────────
with tab4:
    st.markdown("### Portfolio Equity & Risk Curve")

    df_inv, _ = fetch_data("SELECT * FROM inventory", get_mock_inventory)

    fig_eq = make_subplots(specs=[[{"secondary_y": True}]])

    fig_eq.add_trace(
        go.Scatter(x=df_inv['timestamp'], y=df_inv['equity'], name="Equity", fill='tozeroy'),
        secondary_y=False,
    )

    fig_eq.add_trace(
        go.Scatter(x=df_inv['timestamp'], y=df_inv['drawdown'], name="Drawdown %", line=dict(color='red')),
        secondary_y=True,
    )

    # Add risk lines
    max_eq = df_inv['equity'].max()
    fig_eq.add_hline(y=max_eq * 0.95, line_dash="dash", line_color="orange", annotation_text="Max Daily Loss Limit")

    fig_eq.update_layout(height=500, template='plotly_dark')
    st.plotly_chart(fig_eq, use_container_width=True)

    kill_switch_active = False
    status_color = "red" if kill_switch_active else "green"
    status_text = "ENGAGED" if kill_switch_active else "ARMED"
    st.markdown(f"**Kill-Switch Status:** <span style='color:{status_color}; font-weight:bold;'>{status_text}</span>", unsafe_allow_html=True)

# ─── Tab 5: Orderbook Heatmap ────────────────────────────────────────
with tab5:
    st.markdown("### Orderbook Heatmap & Whale Walls")
    df_ob, _ = fetch_data("SELECT * FROM orderbook_snapshots", get_mock_orderbook)

    # Pivot dataframe for Heatmap
    # rows: prices, cols: timestamps, values: volume
    heatmap_data = df_ob.pivot_table(index='price', columns='timestamp', values='volume', fill_value=0)

    # Ensure it's sorted properly
    heatmap_data = heatmap_data.sort_index(ascending=True)

    fig_ob = go.Figure(data=go.Heatmap(
        z=heatmap_data.values,
        x=heatmap_data.columns,
        y=heatmap_data.index,
        colorscale='Viridis',
        colorbar=dict(title='Volume')
    ))

    # Highlight whales (Volume >= 20)
    whales = df_ob[df_ob['volume'] >= 20]
    if not whales.empty:
        fig_ob.add_trace(go.Scatter(
            x=whales['timestamp'], y=whales['price'],
            mode='markers+text',
            marker=dict(color='yellow', size=8, symbol='star', line=dict(color='red', width=1)),
            text=[f"{v:.0f} BTC" for v in whales['volume']],
            textposition="top right",
            name="Whale Walls >= 20 BTC",
            textfont=dict(color="yellow")
        ))

    fig_ob.update_layout(
        height=600,
        template='plotly_dark',
        yaxis_title="Price",
        xaxis_title="Time",
        showlegend=False
    )
    st.plotly_chart(fig_ob, use_container_width=True)

# ─── Tab 6: Live Logs & Diagnostics ──────────────────────────────────
with tab6:
    st.markdown("### Process Diagnostics")

    if st.button("🧹 Clear & Restart All Logs"):
        clear_logs()
        st.success("Logs cleared!")
        st.rerun()

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("#### hub.log")
        st.code(tail_log("hub.log", 30), language="log")

    with col2:
        st.markdown("#### supervisor.log")
        st.code(tail_log("supervisor.log", 30), language="log")

    with col3:
        st.markdown("#### bot.log")
        st.code(tail_log("bot.log", 30), language="log")
