# QuantumEdge System Context

This document serves as the **Single Source of Truth** for the QuantumEdge project. It outlines the file structure, module architecture, data flow, technical stack, and implementation status.

## 1. File System Structure

### Core Layout (`src/`)

The project is organized into a `src/` layout to separate core logic, infrastructure, and machine learning components.

*   **`src/quantum_edge_core/`**: Core trading services and logic.
    *   **`market_data/`**: High-performance market data ingestion and distribution service.
        *   `hub.py`: Service entry point.
        *   `feeds/`: Connectors to exchanges (Binance Spot, Futures).
        *   `ipc/`: ZeroMQ publisher and snapshot server.
        *   `tsdb/`: Time-series database writers (QuestDB).
    *   **`supervisor/`**: The system's central nervous system. Monitors health, evaluates risk, and enforces policies.
        *   `supervisor.py`: Main entry point.
        *   `policy/`: Policy engine and contracts.
        *   `dashboard_web/`: Web interface components.
    *   **`ai_scalper_bot/`**: The active trading bot (High-Frequency/Scalping).
        *   `run_bot.py`: Bot entry point.
        *   `bot/`: Trading logic, strategies, and execution.
    *   **`strategies/`**: Additional trading strategies and legacy reference implementations (e.g., `LockBotBTC`).

*   **`src/quantum_edge_infra/`**: Infrastructure, automation, and tooling.
    *   **`automation/meta_agent/`**: The "DevOps" agent orchestrating code changes and safety gates.
        *   `meta_agent.py`: Core logic.
    *   **`tools/`**: Shared utility scripts for HFT environments.

*   **`src/quantum_edge_ml/`**: Machine Learning and LLM ecosystem.
    *   **`inference_engine/`**: Infrastructure for hosting local Large Language Models (TensorRT-LLM).
        *   `scripts/`: Build and serving scripts.
    *   **`model_router/`**: API Gateway and Router for LLM services.
        *   `router/`: Logic to route requests between local engine and cloud providers.

### Root-Level Files & Wrappers

*   **`QuantumEdge.py`**: The main orchestrator script. Manages the lifecycle of Supervisor, Bot, and Meta-Agent processes.
*   **`meta_agent.py`**: A root-level wrapper that invokes the Meta-Agent logic in `src/quantum_edge_infra/`.
*   **`config/`**: Shared configuration files (system-wide).
*   **`docs/`**: Project documentation (consolidated).
*   **`tests/`**: Suite of integration and unit tests.

## 2. Module Architecture

### **2.1. MarketDataHub**
*   **Path**: `src/quantum_edge_core/market_data/`
*   **Responsibility**: Ingest raw market data from WebSocket feeds (Binance), normalize it, and broadcast it to the rest of the system via IPC (ZeroMQ). It also persists data to the Time Series Database.
*   **Key Components**: `MockLiveFeed`, `LiquidationFeed`, `ZmqPublisher`, `QuestILPWriter`.

### **2.2. SupervisorAgent**
*   **Path**: `src/quantum_edge_core/supervisor/`
*   **Responsibility**: Operates as the "Manager". It monitors the "Worker" (Bot), evaluates market conditions, checks system health, and calculates risk metrics. It enforces policies and can stop/restart components.
*   **Key Components**: `RiskEngine`, `ProcessManager`, `PolicyEngine`, `DashboardService`.

### **2.3. ai_scalper_bot**
*   **Path**: `src/quantum_edge_core/ai_scalper_bot/`
*   **Responsibility**: The active execution unit. Listens to market data, applies trading strategies, and sends orders to the exchange.
*   **Interaction**: Managed by Supervisor; Consumes data from MarketDataHub.

### **2.4. Meta-Agent**
*   **Path**: `src/quantum_edge_infra/automation/meta_agent/`
*   **Responsibility**: Specialized agent for maintaining the codebase. It reads tasks, plans changes, runs "shadow" tests, and applies approved changes through safety gates.

### **2.5. LLM Ecosystem (inference_engine + model_router)**
*   **Path**: `src/quantum_edge_ml/`
*   **Responsibility**: Provides intelligence. `inference_engine` runs the raw model (TensorRT-LLM optimized). `model_router` adds a layer of structured control and routing to make the LLM output usable for programmatic decisions.

## 3. Data Flow & Logic

### **Market Data Pipeline**
1.  **Ingest**: `MarketDataHub` (`src/quantum_edge_core/market_data/hub.py`) connects to feeds.
2.  **Broadcast**: Data is normalized and published via **ZeroMQ (PUB/SUB)**.
3.  **Consumption**: Bots subscribe to these ZMQ topics to react instantly. `SupervisorAgent` also subscribes for risk calculations.
4.  **Storage**: `MarketDataHub` flushes data to **QuestDB** (via ILP) for historical analysis.

### **Control Loop**
1.  `QuantumEdge.py` reads configurations and starts `SupervisorAgent`.
2.  `SupervisorAgent` (if configured) starts `ai_scalper_bot`.
3.  `SupervisorAgent` continuously monitors heartbeats and risk.
4.  Violations lead to STOP signals or process termination.

## 4. Technical Stack

*   **Core**: Python 3.11+ (Targeting Ubuntu 24.04).
*   **Orchestration**: `QuantumEdge.py` (Subprocess management).
*   **Communication**: ZeroMQ (IPC), HTTP (REST API).
*   **Database**:
    *   **Time Series**: QuestDB (Primary).
    *   **JSON/State**: `ujson` for high-throughput decoding.
*   **AI/ML**:
    *   **Inference**: TensorRT-LLM.
    *   **Models**: Gemma 3 4B.
*   **Environment**: Google Antigravity Agentic platform.

## 5. Implementation Status

| Component | Status | Notes |
| :--- | :--- | :--- |
| **MarketDataHub** | ✅ **Ready** | Integrated in `src/quantum_edge_core/market_data/`. |
| **SupervisorAgent** | ✅ **Ready** | Integrated in `src/quantum_edge_core/supervisor/`. |
| **ai_scalper_bot** | ✅ **Active** | Integrated in `src/quantum_edge_core/ai_scalper_bot/`. |
| **Meta-Agent** | ✅ **Active** | Integrated in `src/quantum_edge_infra/automation/meta_agent/`. |
| **LLM Inference** | ⚙️ **Ready** | Located in `src/quantum_edge_ml/inference_engine/`. |
| **Model Router** | ⚙️ **Ready** | Located in `src/quantum_edge_ml/model_router/`. |

---
**Last Updated**: 2026-02-11
**Context**: Migration to Src-Layout Completed
