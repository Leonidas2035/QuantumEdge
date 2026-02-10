# QuantumEdge System Context

This document serves as the **Single Source of Truth** for the QuantumEdge project. It outlines the file structure, module architecture, data flow, technical stack, and implementation status.

## 1. File System Structure

### Top-Level Directories

*   **`/QuantumEdge.py`**: The main orchestrator script. Manages the lifecycle of Supervisor, Bot, and Meta-Agent processes.
*   **`/MarketDataHub/`**: High-performance market data ingestion and distribution service.
    *   `hub.py`: Service entry point.
    *   `feeds/`: Connectors to exchanges (Binance Spot, Futures).
    *   `ipc/`: ZeroMQ publisher and snapshot server.
    *   `tsdb/`: Time-series database writers (QuestDB).
*   **`/SupervisorAgent/`**: The system's central nervous system. Monitors health, evaluates risk, and enforces policies.
    *   `supervisor.py`: Main entry point.
    *   `supervisor/`: Core logic (risk engine, process manager, dashboards).
    *   `policy/`: Policy engine and contracts.
    *   `dashboard_web/`: Web interface components.
*   **`/ai_scalper_bot/`**: The active trading bot (High-Frequency/Scalping).
    *   `run_bot.py`: Bot entry point.
    *   `bot/`: Trading logic, strategies, and execution.
*   **`/LockBotBTC/`**: **legacy** trading bot components (Bitcoin specific). Reference implementation.
*   **`/llm_engine/`**: Infrastructure for hosting local Large Language Models (TensorRT-LLM).
    *   `scripts/`: Build and serving scripts for Gemma 3 4B.
    *   `artifacts/`: Stores compiled engines and checkpoints.
*   **`/supervisor_llm/`**: API Gateway and Router for LLM services.
    *   `api/`: FastAPI application.
    *   `router/`: Logic to route requests between local engine and cloud providers (OpenAI).
    *   `context/`: Handling of prompt context and history.
*   **`/meta_agent/`**: The "DevOps" agent. Orchestrates code changes, runs tasks, and ensures safety gates.
    *   `meta_agent.py`: Entry point.
    *   `safety_policy.py`: logic for approving/rejecting file system changes.
*   **`/config/`**: Shared configuration files (`quantumedge.yaml`, `ports.yaml`, etc).
*   **`/tools/`**: Shared utility scripts.
*   **`/docs/`**: Project documentation.

## 2. Module Architecture

### **2.1. MarketDataHub**
*   **Responsibility**: Ingest raw market data from WebSocket feeds (Binance), normalize it, and broadcast it to the rest of the system via IPC (Inter-Process Communication). It also persists data to the Time Series Database.
*   **Key Components**: `BinanceSpotFeed`, `BinanceFuturesFeed`, `ZmqPublisher`, `QuestILPWriter`.

### **2.2. SupervisorAgent**
*   **Responsibility**: Operates as the "Manager". It does not execute trades directly but monitors the "Worker" (Bot). It evaluates market conditions, checks system health (heartbeats), and calculates risk metrics. It can kill/restart the bot if policies are violated.
*   **Key Components**: `RiskEngine`, `ProcessManager`, `PolicyEngine`, `DashboardService`.

### **2.3. ai_scalper_bot**
*   **Responsibility**: The active execution unit. Listens to market data, applies trading strategies (e.g., scalping), and sends orders to the exchange.
*   **Interaction**: Managed by Supervisor; Consumes data from MarketDataHub.

### **2.4. Meta-Agent**
*   **Responsibility**: A specialized agent for maintaining the codebase itself. It reads tasks, plans changes, runs "shadow" tests, and applies approved changes. It is the "safety valve" for autonomous coding.

### **2.5. LLM Ecosystem (llm_engine + supervisor_llm)**
*   **Responsibility**: Provides intelligence. `llm_engine` runs the raw model (TensorRT-LLM optimized). `supervisor_llm` adds a layer of structured control (JSON schema enforcement) and routing (Cloud fallback) to make the LLM output usable for programmatic decisions (e.g., "HOLD", "BUY").

## 3. Data Flow & Logic

### **Market Data Pipeline**
1.  **Ingest**: `MarketDataHub` connects to Binance WebSockets.
2.  **Broadcast**: Data is normalized and published via **ZeroMQ (PUB/SUB)** under specific topics (e.g., `market.trade.btc_usdt`).
3.  **Consumption**: `ai_scalper_bot` subscribes to these ZMQ topics to react instantly. `SupervisorAgent` also subscribes to calculate real-time risk metrics.
4.  **Storage**: `MarketDataHub` flushes data to **QuestDB** (via ILP) for historical analysis.

### **Control Loop**
1.  `SupervisorAgent` reads `config/supervisor.yaml` and starts `ai_scalper_bot` (if configured to manage it).
2.  `SupervisorAgent` continuously pings components (Heartbeat).
3.  If `ai_scalper_bot` violates risk limits (e.g., drawdown > 4%) or fails heartbeats, `SupervisorAgent` sends a STOP signal or kills the process.

### **Decision Making (LLM)**
1.  `SupervisorAgent` (or Bot) encounters a complex scenario.
2.  It sends a JSON request to `supervisor_llm` API (`http://127.0.0.1:8010`).
3.  `supervisor_llm` constructs a prompt and forwards it to `llm_engine` (served locally) or OpenAI (Teacher).
4.  The response is validated against a strict schema (DecisionV1).
5.  A structured decision (`{"v":1, "s":"HOLD", ...}`) is returned to the requester.

## 4. Technical Stack

*   **Core**: Python 3.10+
*   **Orchestration**: Custom Python Scripts (`QuantumEdge.py`), leveraging `subprocess` and `signal`.
*   **Web/API Framework**: FastAPI, Uvicorn.
*   **Communication**: ZeroMQ (Internal Low Latency), HTTP (API).
*   **Database**:
    *   **Time Series**: QuestDB (Primary), ClickHouse (Supported).
    *   **State/Cache**: SQLite.
*   **AI/ML**:
    *   **Inference**: TensorRT-LLM (NVIDIA optimized).
    *   **Libraries**: PyTorch, Transformers, Accelerate.
    *   **Models**: Gemma 3 4B (Text-only).
*   **External APIs**: Binance (Data/Execution), OpenAI (Optional Teacher).

## 5. Implementation Status

| Component | Status | Notes |
| :--- | :--- | :--- |
| **MarketDataHub** | ✅ **Ready** | Full feed integration, ZMQ publishing, QuestDB logging operational. |
| **SupervisorAgent** | ✅ **Ready** | Advanced. Includes Risk Engine, Policy Engine, Dashboard, and Process Management. |
| **ai_scalper_bot** | ✅ **Active** | Main trading bot. Active development. |
| **LockBotBTC** | ⚠️ **Legacy** | Reference implementation. Superseded by `ai_scalper_bot`. |
| **Meta-Agent** | ✅ **Active** | Fully functional for safe code modifications. |
| **llm_engine** | ⚙️ **Ready** | Scripts for building/quantizing TensorRT-LLM engines are present. |
| **supervisor_llm** | ⚙️ **Ready** | Router and API functional. JSON schema enforcement active. |
| **UI/Control Center** | 🚧 **Partial** | CLI is dominant. Web Dashboard exists in Supervisor but is secondary. |

---
**Last Updated**: 2026-02-02
**Context**: Full Repo Audit
