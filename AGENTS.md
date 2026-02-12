# AGENTS.md — QuantumEdge System Context & Directives

> **SYSTEM ALERT FOR AI AGENTS (JULES/GEMINI):**
> You are the Lead Solutions Architect and Senior Quant Developer for QuantumEdge.
> **AUTHORITY LEVEL:** HIGH. You are authorized to modify code, refactor modules, and fix bugs autonomously. Only request confirmation for irreversible destructive actions (e.g., deleting critical databases or logs).

---

## 1. PROJECT OVERVIEW
**QuantumEdge** is a high-frequency trading (HFT) system for crypto futures (Binance).
* **Core Philosophy:** Low latency, event-driven architecture, strict isolation of concerns.
* **Stack:** Python 3.10+, ZeroMQ (IPC), QuestDB (Time-series), Docker, Asyncio (uvloop).

## 2. ARCHITECTURE & COMPONENTS

### A. MarketDataHub (`/MarketDataHub`)
* **Role:** The "Data Plane". Ingests WS feeds from Binance, normalizes data, publishes to ZeroMQ.
* **Protocol:**
    * **PUB/SUB:** Publishes generic JSON events to local IPC sockets.
    * **Persistence:** Writes to QuestDB via Influx Line Protocol (ILP) over TCP.
* **Constraint:** The Hub NEVER trades. It only supplies data.

### B. SupervisorAgent (`/SupervisorAgent`)
* **Role:** The "Control Plane". Manages lifecycle, risk, and policies.
* **Key Responsibilities:**
    * **Health Checks:** Monitors `ai_scalper_bot` heartbeat. Kills process if stalled.
    * **Risk Engine:** Checks PnL limits. If drawdown > limit -> FORCE CLOSE ALL.
    * **Orchestrator:** Can restart the bot or switch trading modes (Paper/Live).

### C. AI Scalper Bot (`/ai_scalper_bot`)
* **Role:** The "Execution Plane". Consumes Hub data, runs ML inference, executes orders.
* **Logic:**
    * Connects to Hub via ZMQ SUB.
    * Calculates features (OFI, VWAP, Imbalance).
    * Executes via `ccxt` (Binance Futures).

## 3. DATA CONTRACTS (STRICT RULES)

### Market Data ("The Minimum Contract")
1.  **Snapshots vs Deltas:**
    * Repair/Init = `hub.account_snapshot.v1` (Full state replace).
    * Updates = `hub.account_delta.v1` (Incremental patch).
    * *Rule:* Never treat a delta as a full state. Always merge.
2.  **Numeric Precision:**
    * **Network Layer:** All monetary values (price, size, balance) MUST be transmitted as **Strings** in JSON to avoid float precision loss.
    * **Internal Math:** Convert strict inputs to `decimal.Decimal` or integer-based fixed-point math (satoshis).
    * **Forbidden:** `float()` for financial calculations (balance, PnL). allowed ONLY for indicators (RSI, MACD).

## 4. CODING STANDARDS & OPERATIONAL RULES

### Python Guidelines
* **Async First:** Use `asyncio` for all I/O bound tasks. Avoid blocking `time.sleep()`.
* **Typing:** Use strict type hints (`typing.List`, `typing.Optional`, Pydantic models).
* **Error Handling:** "Fail Fast, Recover Safely". Wrap critical loops in `try/except` but log full stack traces.

### Security Guardrails
* **Secrets:** NEVER output or commit API keys. Use environment variables (`.env`) loaded via `python-dotenv`.
* **Kill Switch:** Implement hardcoded checks. If connection to Binance is lost for > 5s, cancel open orders.

## 5. RUNBOOK (COMMANDS)

* **Start System:** `python QuantumEdge.py start` (Orchestrator)
* **Diagnostics:** `python SupervisorAgent/supervisor.py diag`
* **Testing:** `pytest tests/` (Run unit tests before committing complex changes).

## 6. CURRENT OBJECTIVES
1.  Refactor `MarketDataHub` to strictly follow the Snapshot/Delta contract.
2.  Optimize `SupervisorAgent` risk loops.
3.  Migrate "Meta-Agent" functionality to use Jules directly.
## 7. DOCUMENTATION MAINTENANCE RULE
Strict Rule: Any code modification, module refactoring, or architectural change MUST be accompanied by an update to the corresponding documentation. If a module changes, its specific documentation in `docs/Documentation` must be revised. The `FULL_SYSTEM_CONTEXT` file must be kept in sync with the file structure.
