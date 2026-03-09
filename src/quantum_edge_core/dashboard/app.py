"""QuantumEdge Web Dashboard — Streamlit app."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from streamlit_autorefresh import st_autorefresh
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
    .awaiting-data {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px dashed rgba(0, 212, 170, 0.3);
        border-radius: 12px;
        padding: 40px 20px;
        text-align: center;
        margin: 20px 0;
    }
    .awaiting-data h3 { color: #00d4aa; margin-bottom: 8px; }
    .awaiting-data p { color: #8899aa; font-size: 0.9em; }
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


def is_localhost() -> bool:
    """Check if the request originates from localhost."""
    headers = _get_websocket_headers()
    if not headers:
        return True  # Default to allowing if headers can't be fetched locally
    host = headers.get("Host", "")
    return host.startswith("localhost") or host.startswith("127.0.0.1")


def show_awaiting_data(message: str = "Очікування перших даних...") -> None:
    """Display a graceful 'awaiting data' placeholder instead of crashing."""
    st.markdown(
        f"""
        <div class="awaiting-data">
            <h3>⏳ {message}</h3>
            <p>Система щойно запущена або таблиця ще порожня. Дані з'являться автоматично.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def safe_col(df: pd.DataFrame, col: str, default=None):
    """Safely get a column from a DataFrame, returning default if missing."""
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), name=col)


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
        "Bot": "python3 -m quantum_edge_core.ai_scalper_bot.run_bot",
    }

    for name, cmd in processes.items():
        is_running = ProcessManager.is_running(name)
        pid = ProcessManager.get_pid(name)
        status_cls = "status-up" if is_running else "status-down"
        status_text = f"RUNNING (PID {pid})" if is_running else "STOPPED"

        st.markdown(
            f"**{name}:** <span class='{status_cls}'>{status_text}</span>",
            unsafe_allow_html=True,
        )

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
    st.markdown("</div>", unsafe_allow_html=True)

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

    # ── Portfolio State from QuestDB (real telemetry) ──
    df_portfolio, is_portfolio_mock = fetch_data(
        "SELECT * FROM portfolio_state WHERE symbol = 'BTCUSDT' ORDER BY timestamp DESC LIMIT 2",
        get_mock_inventory,
    )

    if not is_portfolio_mock and not df_portfolio.empty and len(df_portfolio) >= 2:
        curr_eq = float(df_portfolio.iloc[0].get("equity", 0))
        prev_eq = float(df_portfolio.iloc[1].get("equity", 0))
        eq_pct = ((curr_eq - prev_eq) / prev_eq) * 100 if prev_eq > 0 else 0

        curr_pnl = float(df_portfolio.iloc[0].get("unrealized_pnl", 0))
        curr_qty = float(df_portfolio.iloc[0].get("position_qty", 0))

        st.metric("Equity", f"${curr_eq:,.2f}", f"{eq_pct:+.2f}%")
        st.metric("Unrealized PnL", f"${curr_pnl:,.2f}")
        st.metric("Position", f"{curr_qty:+.4f} BTC")
    else:
        # Fallback to inventory table or mock
        df_inv, _ = fetch_data(
            "SELECT * FROM inventory ORDER BY timestamp DESC LIMIT 2",
            get_mock_inventory,
        )
        if not df_inv.empty and len(df_inv) >= 2:
            curr_eq = df_inv.iloc[0]["equity"]
            prev_eq = df_inv.iloc[1]["equity"]
            eq_pct = ((curr_eq - prev_eq) / prev_eq) * 100 if prev_eq > 0 else 0
            st.metric("Equity", f"${curr_eq:,.2f}", f"{eq_pct:+.2f}%")

            if "drawdown" in df_inv.columns:
                curr_dd = df_inv.iloc[0]["drawdown"]
                prev_dd = df_inv.iloc[1]["drawdown"]
                dd_delta = curr_dd - prev_dd
                st.metric(
                    "Drawdown",
                    f"{curr_dd:.2f}%",
                    f"{dd_delta:+.2f}%",
                    delta_color="inverse",
                )
        else:
            st.metric("Equity", "—", "—")
            st.metric("Drawdown", "—", "—")

        st.metric("Unrealized PnL", "—", "—")

    df_llm_sidebar, _ = fetch_data(
        "SELECT * FROM llm_advice ORDER BY time DESC LIMIT 1", get_mock_llm_advice
    )

    if not df_llm_sidebar.empty and "multiplier" in df_llm_sidebar.columns:
        curr_mult = df_llm_sidebar.iloc[0]["multiplier"]
        st.metric("Risk Multiplier", f"{curr_mult}x")
    else:
        st.metric("Risk Multiplier", "—")

# ─── Tabs ────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "👁️ Очі Кванта (Market Overview)",
        "🧠 Мізки AI (LLM Supervisor Brain)",
        "⚡ Виконання (Execution)",
        "🛡️ Ризик (Inventory & Risk)",
        "🔥 Orderbook Heatmap",
        "📝 Live Logs",
    ]
)

# ─── Tab 1: Market Overview ──────────────────────────────────────────
with tab1:
    st.markdown("### Market Overview (BTC/USDT)")
    tf = st.selectbox("Timeframe", ["1m", "5m", "15m"], index=0)

    sql_query = f"""
    SELECT timestamp, first(price) AS open, max(price) AS high, min(price) AS low, last(price) AS close, sum(qty) AS volume
    FROM trades
    WHERE timestamp > now() - 24h AND symbol = 'BTCUSDT'
    SAMPLE BY {tf} ALIGN TO CALENDAR
    ORDER BY timestamp
    LIMIT -1000
    """

    df, is_mock = fetch_data(sql_query, get_mock_market_data)

    if is_mock:
        st.warning("⚠️ Дані з QuestDB недоступні — демо-режим")

    if df.empty or len(df) < 2:
        show_awaiting_data("Очікування ринкових даних...")
    else:
        # Calculate TA
        try:
            df.ta.bbands(length=20, std=2, append=True)
        except Exception:
            pass
        try:
            df.ta.supertrend(length=7, multiplier=3, append=True)
        except Exception:
            pass

        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            subplot_titles=("Price", "Volume"),
            row_width=[0.2, 0.7],
        )

        # Candlesticks
        fig.add_trace(
            go.Candlestick(
                x=df["timestamp"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="Price",
            ),
            row=1,
            col=1,
        )

        # Bollinger Bands
        if "BBL_20_2.0" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["timestamp"],
                    y=df["BBU_20_2.0"],
                    line=dict(color="gray", width=1, dash="dot"),
                    name="Upper BB",
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=df["timestamp"],
                    y=df["BBL_20_2.0"],
                    line=dict(color="gray", width=1, dash="dot"),
                    name="Lower BB",
                    fill="tonexty",
                    fillcolor="rgba(128,128,128,0.1)",
                ),
                row=1,
                col=1,
            )

        # Volume
        colors = [
            "red" if row["open"] - row["close"] >= 0 else "green"
            for index, row in df.iterrows()
        ]
        fig.add_trace(
            go.Bar(
                x=df["timestamp"], y=df["volume"], marker_color=colors, name="Volume"
            ),
            row=2,
            col=1,
        )

        fig.update_layout(
            height=700, template="plotly_dark", xaxis_rangeslider_visible=False
        )
        st.plotly_chart(fig, use_container_width=True)

# ─── Tab 2: LLM Supervisor Brain ─────────────────────────────────────
with tab2:
    st.markdown("### AI Supervisor Reasoning History")
    df_llm, is_llm_mock = fetch_data(
        "SELECT * FROM llm_advice LIMIT -100", get_mock_llm_advice
    )

    if df_llm.empty or "trading_mode" not in df_llm.columns:
        show_awaiting_data("Очікування перших рішень LLM Supervisor...")
    else:
        # Step chart
        fig_llm = go.Figure()

        # Mode mapping for step chart visualization
        mode_map = {
            "SCALP": 4,
            "DCA": 3,
            "NEUTRAL": 2,
            "PASS": 1,
            "HALT": 0,
            "scalp": 4,
            "dca": 3,
            "neutral": 2,
            "pass": 1,
        }
        y_vals = [mode_map.get(str(m).strip(), 2) for m in df_llm["trading_mode"]]

        fig_llm.add_trace(
            go.Scatter(
                x=safe_col(df_llm, "time"),
                y=y_vals,
                mode="lines+markers",
                line_shape="hv",
                name="Trading Mode",
                text=safe_col(df_llm, "reason", ""),
                hovertemplate="<b>%{text}</b><br>Mode Level: %{y}<extra></extra>",
            )
        )

        # Overlay risk multiplier
        if "multiplier" in df_llm.columns:
            fig_llm.add_trace(
                go.Scatter(
                    x=safe_col(df_llm, "time"),
                    y=df_llm["multiplier"],
                    mode="lines",
                    line=dict(color="orange", dash="dash"),
                    name="Risk Multiplier",
                    yaxis="y2",
                )
            )

        fig_llm.update_layout(
            height=400,
            template="plotly_dark",
            yaxis=dict(
                title="Mode",
                tickvals=[0, 1, 2, 3, 4],
                ticktext=["HALT", "PASS", "NEUTRAL", "DCA", "SCALP"],
            ),
            yaxis2=dict(title="Multiplier", overlaying="y", side="right"),
        )
        st.plotly_chart(fig_llm, use_container_width=True)

        st.markdown("#### Latest 20 Decisions")
        st.dataframe(df_llm.head(20), use_container_width=True)

# ─── Tab 3: Execution & Trades ───────────────────────────────────────
with tab3:
    st.markdown("### Trade Executions")

    df_trades, is_trades_mock = fetch_data(
        "SELECT * FROM realized_trades WHERE symbol = 'BTCUSDT' ORDER BY timestamp DESC LIMIT -100",
        get_mock_trades,
    )

    if df_trades.empty or len(df_trades) < 1:
        show_awaiting_data("Очікування перших виконаних угод...")
    else:
        # Overlay trades on Candlestick
        st.markdown("#### Execution Overlay")
        df_market, _ = fetch_data(
            "SELECT timestamp, first(price) AS open, max(price) AS high, min(price) AS low, last(price) AS close, sum(qty) AS volume FROM trades WHERE timestamp > now() - 24h AND symbol = 'BTCUSDT' SAMPLE BY 1m ALIGN TO CALENDAR ORDER BY timestamp LIMIT -1000",
            get_mock_market_data,
        )

        fig_overlay = go.Figure()

        if not df_market.empty:
            fig_overlay.add_trace(
                go.Candlestick(
                    x=df_market["timestamp"],
                    open=df_market["open"],
                    high=df_market["high"],
                    low=df_market["low"],
                    close=df_market["close"],
                    name="Price",
                )
            )

        # Mocking timestamps for trades to overlay on the chart properly
        if "timestamp" not in df_trades.columns:
            market_len = len(df_market)
            df_trades["timestamp"] = [
                (
                    df_market["timestamp"].iloc[-min((i + 1) * 5, market_len)]
                    if market_len > 0
                    else pd.Timestamp.now()
                )
                for i in range(len(df_trades))
            ]

        buys_df = df_trades[df_trades["side"] == "BUY"]
        sells_df = df_trades[df_trades["side"] == "SELL"]

        fig_overlay.add_trace(
            go.Scatter(
                x=buys_df["timestamp"] if "timestamp" in buys_df.columns else [],
                y=buys_df["price"],
                mode="markers",
                marker=dict(color="green", size=12, symbol="triangle-up"),
                name="Buy Execution",
            )
        )

        fig_overlay.add_trace(
            go.Scatter(
                x=sells_df["timestamp"] if "timestamp" in sells_df.columns else [],
                y=sells_df["price"],
                mode="markers",
                marker=dict(color="red", size=12, symbol="triangle-down"),
                name="Sell Execution",
            )
        )

        fig_overlay.update_layout(
            height=500, template="plotly_dark", xaxis_rangeslider_visible=False
        )
        st.plotly_chart(fig_overlay, use_container_width=True)

        col_skew, col_pos = st.columns(2)

        buys = len(buys_df)
        sells = len(sells_df)
        total = max(1, buys + sells)
        buy_pct = buys / total * 100

        skew_color = "red" if abs(buy_pct - 50) > 20 else "green"

        with col_skew:
            st.markdown(
                f"#### Skew: <span style='color:{skew_color}'>Buy {buy_pct:.0f}% / Sell {100-buy_pct:.0f}%</span>",
                unsafe_allow_html=True,
            )
        with col_pos:
            # Show real position from portfolio_state if available
            df_pos, is_pos_mock = fetch_data(
                "SELECT position_qty FROM portfolio_state WHERE symbol = 'BTCUSDT' ORDER BY timestamp DESC LIMIT 1",
                lambda: pd.DataFrame({"position_qty": [0.0]}),
            )
            pos_qty = float(df_pos.iloc[0]["position_qty"]) if not df_pos.empty else 0.0
            pos_color = "green" if pos_qty > 0 else ("red" if pos_qty < 0 else "gray")
            st.markdown(
                f"#### Position: <span style='color:{pos_color}'>{pos_qty:+.4f} BTC</span>",
                unsafe_allow_html=True,
            )

        # Show realized PnL summary
        if "realized_pnl" in df_trades.columns:
            total_pnl = df_trades["realized_pnl"].sum()
            pnl_color = "green" if total_pnl >= 0 else "red"
            st.markdown(
                f"**Total Realized PnL:** <span style='color:{pnl_color};font-size:1.2em;font-weight:bold'>${total_pnl:+,.2f}</span>",
                unsafe_allow_html=True,
            )

        st.dataframe(df_trades, use_container_width=True)

# ─── Tab 4: Inventory & Risk ─────────────────────────────────────────
with tab4:
    st.markdown("### Portfolio Equity & Risk Curve")

    # Prefer portfolio_state (real telemetry), fallback to inventory mock
    df_eq, is_eq_mock = fetch_data(
        "SELECT * FROM portfolio_state WHERE symbol = 'BTCUSDT' ORDER BY timestamp LIMIT -100",
        get_mock_inventory,
    )

    if df_eq.empty or len(df_eq) < 2:
        show_awaiting_data("Очікування даних портфеля...")
    else:
        fig_eq = make_subplots(specs=[[{"secondary_y": True}]])

        eq_col = "equity" if "equity" in df_eq.columns else "close"
        ts_col = "timestamp"

        fig_eq.add_trace(
            go.Scatter(x=df_eq[ts_col], y=df_eq[eq_col], name="Equity", fill="tozeroy"),
            secondary_y=False,
        )

        # Unrealized PnL as a secondary axis
        if "unrealized_pnl" in df_eq.columns:
            fig_eq.add_trace(
                go.Scatter(
                    x=df_eq[ts_col],
                    y=df_eq["unrealized_pnl"],
                    name="Unrealized PnL",
                    line=dict(color="cyan", dash="dot"),
                ),
                secondary_y=True,
            )

        # Drawdown if available (from inventory)
        if "drawdown" in df_eq.columns:
            fig_eq.add_trace(
                go.Scatter(
                    x=df_eq[ts_col],
                    y=df_eq["drawdown"],
                    name="Drawdown %",
                    line=dict(color="red"),
                ),
                secondary_y=True,
            )

        # Add risk lines
        max_eq = df_eq[eq_col].max()
        if max_eq and max_eq > 0:
            fig_eq.add_hline(
                y=max_eq * 0.95,
                line_dash="dash",
                line_color="orange",
                annotation_text="Max Daily Loss Limit",
            )

        fig_eq.update_layout(height=500, template="plotly_dark")
        st.plotly_chart(fig_eq, use_container_width=True)

    kill_switch_active = False
    status_color = "red" if kill_switch_active else "green"
    status_text = "ENGAGED" if kill_switch_active else "ARMED"
    st.markdown(
        f"**Kill-Switch Status:** <span style='color:{status_color}; font-weight:bold;'>{status_text}</span>",
        unsafe_allow_html=True,
    )

# ─── Tab 5: Orderbook Heatmap ────────────────────────────────────────
with tab5:
    st.markdown("### Orderbook Heatmap & Whale Walls")
    df_ob, is_ob_mock = fetch_data(
        "SELECT * FROM orderbook_snapshots LIMIT -1000", get_mock_orderbook
    )

    if df_ob.empty or "price" not in df_ob.columns or "volume" not in df_ob.columns:
        show_awaiting_data("Очікування даних OrderBook...")
    else:
        # Pivot dataframe for Heatmap
        heatmap_data = df_ob.pivot_table(
            index="price", columns="timestamp", values="volume", fill_value=0
        )
        heatmap_data = heatmap_data.sort_index(ascending=True)

        fig_ob = go.Figure(
            data=go.Heatmap(
                z=heatmap_data.values,
                x=heatmap_data.columns,
                y=heatmap_data.index,
                colorscale="Viridis",
                colorbar=dict(title="Volume"),
            )
        )

        # Highlight whales (Volume >= 20)
        whales = df_ob[df_ob["volume"] >= 20]
        if not whales.empty:
            fig_ob.add_trace(
                go.Scatter(
                    x=whales["timestamp"],
                    y=whales["price"],
                    mode="markers+text",
                    marker=dict(
                        color="yellow",
                        size=8,
                        symbol="star",
                        line=dict(color="red", width=1),
                    ),
                    text=[f"{v:.0f} BTC" for v in whales["volume"]],
                    textposition="top right",
                    name="Whale Walls >= 20 BTC",
                    textfont=dict(color="yellow"),
                )
            )

        fig_ob.update_layout(
            height=600,
            template="plotly_dark",
            yaxis_title="Price",
            xaxis_title="Time",
            showlegend=False,
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
