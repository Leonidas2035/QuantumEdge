"""QuantumEdge Web Dashboard — Streamlit app.

Launch:
    streamlit run src/quantum_edge_core/dashboard/app.py --server.port 8501

Sections:
    1. AI Supervisor Directives  (parses supervisor.log)
    2. Market State              (QuestDB REST)
    3. Bot Health                (ZMQ port probe)
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import streamlit as st

# ─── Page Config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="QuantumEdge Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Styling ─────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    .status-card {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 16px;
        color: #e0e0e0;
    }
    .status-card h3 {
        margin: 0 0 12px 0;
        color: #00d4aa;
        font-size: 1.1rem;
        font-weight: 600;
    }
    .metric-row {
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
    }
    .metric-box {
        background: rgba(255,255,255,0.04);
        border-radius: 8px;
        padding: 12px 18px;
        flex: 1;
        min-width: 140px;
        text-align: center;
    }
    .metric-box .label {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #888;
        margin-bottom: 4px;
    }
    .metric-box .value {
        font-size: 1.4rem;
        font-weight: 700;
        color: #fff;
    }
    .mode-long { color: #00e676 !important; }
    .mode-short { color: #ff5252 !important; }
    .mode-neutral { color: #40c4ff !important; }
    .mode-risk-off { color: #ffa726 !important; }
    .mode-halt { color: #ff1744 !important; }
    .reasoning-box {
        background: rgba(0,212,170,0.06);
        border-left: 3px solid #00d4aa;
        padding: 12px 16px;
        border-radius: 0 8px 8px 0;
        font-style: italic;
        color: #ccc;
        margin-top: 12px;
    }
    .port-ok { color: #00e676; font-weight: 600; }
    .port-fail { color: #ff5252; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─── Header ──────────────────────────────────────────────────────────
st.markdown("# ⚡ QuantumEdge Dashboard")
st.markdown("*Real-time monitoring of AI Supervisor, Market Data & Bot Health*")
st.markdown("---")


# ═══════════════════════════════════════════════════════════════════
# Section 1: AI Supervisor Directives
# ═══════════════════════════════════════════════════════════════════

def _parse_supervisor_log(log_path: str, max_lines: int = 50) -> List[Dict[str, Any]]:
    """Read last N lines of supervisor log and extract JSON directives."""
    path = Path(log_path)
    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    entries: List[Dict[str, Any]] = []
    for line in lines[-max_lines:]:
        # Try to find JSON in the line
        for start_char in ("{",):
            idx = line.find(start_char)
            if idx == -1:
                continue
            candidate = line[idx:]
            try:
                obj = json.loads(candidate)
                if "trading_mode" in obj or "mode" in obj:
                    entries.append(obj)
            except (json.JSONDecodeError, ValueError):
                pass
    return entries


def _mode_css_class(mode: str) -> str:
    return {
        "LONG_ONLY": "mode-long",
        "SHORT_ONLY": "mode-short",
        "NEUTRAL": "mode-neutral",
        "RISK_OFF": "mode-risk-off",
        "HALT": "mode-halt",
        "PANIC_LOCK": "mode-halt",
    }.get(mode, "mode-neutral")


def render_supervisor_section() -> None:
    st.markdown("## 🧠 AI Supervisor Directives")

    LOG_PATHS = [
        "logs/supervisor.log",
        "src/quantum_edge_core/supervisor/runtime/logs/supervisor.log",
    ]

    entries: List[Dict[str, Any]] = []
    for lp in LOG_PATHS:
        entries = _parse_supervisor_log(lp)
        if entries:
            break

    if not entries:
        st.info(
            "No directive entries found in supervisor logs. "
            "Run the Supervisor with `run-foreground` to generate directives."
        )
        return

    latest = entries[-1]
    mode = latest.get("trading_mode") or latest.get("mode", "UNKNOWN")
    risk = latest.get("risk_multiplier", 1.0)
    reasoning = latest.get("reasoning", "—")
    css_class = _mode_css_class(mode)

    st.markdown(
        f"""
        <div class="status-card">
            <h3>Latest LLM Decision</h3>
            <div class="metric-row">
                <div class="metric-box">
                    <div class="label">Trading Mode</div>
                    <div class="value {css_class}">{mode}</div>
                </div>
                <div class="metric-box">
                    <div class="label">Risk Multiplier</div>
                    <div class="value">{risk if risk is not None else 1.0:.2f}</div>
                </div>
                <div class="metric-box">
                    <div class="label">Directives Parsed</div>
                    <div class="value">{len(entries)}</div>
                </div>
            </div>
            <div class="reasoning-box">
                "{reasoning}"
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # History table
    if len(entries) > 1:
        with st.expander("📜 Directive History", expanded=False):
            history_data = []
            for e in reversed(entries[-10:]):
                history_data.append({
                    "Mode": e.get("trading_mode") or e.get("mode", "?"),
                    "Risk": e.get("risk_multiplier", "—"),
                    "Reasoning": (e.get("reasoning") or "")[:80],
                })
            st.dataframe(pd.DataFrame(history_data), use_container_width=True)


# ═══════════════════════════════════════════════════════════════════
# Section 2: Market State (QuestDB)
# ═══════════════════════════════════════════════════════════════════

def render_market_section() -> None:
    st.markdown("## 📊 Market State (QuestDB)")

    QUESTDB_URL = "http://127.0.0.1:9000/exec"
    QUERY = "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10"

    try:
        resp = requests.get(
            QUESTDB_URL,
            params={"query": QUERY},
            timeout=3,
        )
        resp.raise_for_status()
        data = resp.json()

        columns = [col["name"] for col in data.get("columns", [])]
        rows = data.get("dataset", [])

        if rows:
            df = pd.DataFrame(rows, columns=columns)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"Showing {len(rows)} most recent trades from QuestDB")
        else:
            st.info("No trades found in QuestDB `trades` table.")

    except requests.ConnectionError:
        st.warning(
            "⚠️ Cannot connect to QuestDB at `127.0.0.1:9000`. "
            "Ensure QuestDB is running."
        )
    except requests.Timeout:
        st.warning("⚠️ QuestDB request timed out.")
    except Exception as exc:
        st.error(f"QuestDB query failed: {exc}")


# ═══════════════════════════════════════════════════════════════════
# Section 3: Bot Health (ZMQ Port Check)
# ═══════════════════════════════════════════════════════════════════

def _check_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """Check if a TCP port is open (accepting connections)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


def render_health_section() -> None:
    st.markdown("## 🏥 Bot Health — ZMQ Ports")

    PORTS = {
        5555: ("MarketDataHub", "PUB → SUB (ticks)"),
        5556: ("Supervisor Policy", "PUB → SUB (directives)"),
        5557: ("LockBot Telemetry", "PUB → SUB (status/acks)"),
        9000: ("QuestDB HTTP", "REST API"),
    }

    cols = st.columns(len(PORTS))
    for col, (port, (name, desc)) in zip(cols, PORTS.items()):
        alive = _check_port("127.0.0.1", port)
        status_html = (
            '<span class="port-ok">● LIVE</span>'
            if alive
            else '<span class="port-fail">● DOWN</span>'
        )
        col.markdown(
            f"""
            <div class="status-card" style="text-align:center;">
                <h3>:{port}</h3>
                <div style="font-size:0.85rem;color:#aaa;">{name}</div>
                <div style="font-size:0.75rem;color:#666;margin-bottom:8px;">{desc}</div>
                <div style="font-size:1.1rem;">{status_html}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════════════════════════
# Layout
# ═══════════════════════════════════════════════════════════════════

render_supervisor_section()
st.markdown("---")
render_market_section()
st.markdown("---")
render_health_section()

# ─── Sidebar ─────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Controls")
    if st.button("🔄 Refresh Data"):
        st.rerun()

    st.markdown("---")
    st.markdown("### 📡 System Info")
    st.markdown(
        """
        | Component | Port |
        |---|---|
        | MarketDataHub | `5555` |
        | Supervisor | `5556` |
        | LockBot PUB | `5557` |
        | QuestDB | `9000` |
        """
    )
    st.markdown("---")
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
    if auto_refresh:
        time.sleep(30)
        st.rerun()

    st.markdown(
        "<div style='text-align:center;color:#555;font-size:0.7rem;margin-top:40px;'>"
        "QuantumEdge Dashboard v1.0<br>© 2026 Leonidas2035"
        "</div>",
        unsafe_allow_html=True,
    )
