"""Async Supervisor Service Entrypoint."""

import asyncio
import copy
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# Try to use uvloop for performance
try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

from quantum_edge_core.logging.audit_logger import AuditLogger
from quantum_edge_core.supervisor.context.builder import ContextBuilder
# Domain Imports
from quantum_edge_core.supervisor.domain.models import (PortfolioState,
                                                        RiskConfig, RiskLevel,
                                                        RiskVerdict)
from quantum_edge_core.supervisor.domain.policy import PolicyManager
from quantum_edge_core.supervisor.domain.risk import HardRiskEngine
from quantum_edge_core.supervisor.state_manager import ThreadSafeStateManager
from quantum_edge_core.supervisor.supervisor.ai_bridge import (
    AiBridge, MalformedResponseError)
from quantum_edge_core.supervisor.supervisor.api import DashboardServer
from quantum_edge_core.supervisor.supervisor.config import \
    load_llm_supervisor_config
from quantum_edge_core.supervisor.supervisor.data_ingest import ZmqListener
from quantum_edge_core.supervisor.supervisor.gemini_client import GeminiClient
from quantum_edge_core.supervisor.supervisor.ipc import PolicyPublisher
from quantum_edge_core.supervisor.supervisor.prompts import (SYSTEM_PROMPT,
                                                             format_history)

# Assuming we have a ZMQ wrapper or will verify connectivity briefly.
# For this stage, we implement the loop logic.


class AsyncSupervisor:
    def __init__(self):
        self.logger = logging.getLogger("Supervisor")
        self.running = True
        self.last_heartbeat_time = time.time()
        self.bot_state = {
            "equity_start": 10000.0,  # Default/Placeholder until first heartbeat
            "equity_current": 10000.0,
            "max_equity_intraday": 10000.0,
            "current_exposure": 0.0,
        }

        # Load configs (Synchronous load at startup is fine)
        # Load configs (Synchronous load at startup is fine)
        # We might need to mock these for the test script if files don't exist,
        # but in production they should exist.
        # For now, we hardcode defaults if load fails or assume the environment is set up.
        self.risk_config = RiskConfig(
            max_daily_loss=500.0,  # Example
            max_drawdown_total=1000.0,
            max_leverage=20.0,
        )
        self.policy_manager = PolicyManager()
        self.policy_publisher = PolicyPublisher(pub_port=5556)

        # In a real app, these would come from config files
        # self.config = load_supervisor_config(...)

        # Initialize Gemini Client (will be set up in run)
        self.gemini_client: Optional[GeminiClient] = None

        # Data & Context
        # Data & Context
        self.zmq_listener = ZmqListener()
        # New ContextBuilder does not need store init, it maintains its own accumulators
        self.context_builder = ContextBuilder()

        # Fail-Safe State
        self.last_ai_contact_ts = time.time()
        self.emergency_mode_triggered = False
        self.active_policy = {
            "action": "CONTINUE",
            "regime": "RANGE",
            "reasoning": "Initial State",
        }
        self.decision_history = []

        # Logging & API & State
        self.audit_logger = AuditLogger()
        self.state_manager = ThreadSafeStateManager()
        self.api_server = DashboardServer(self.state_manager, port=8000)

    async def _setup(self):
        """Async setup."""
        llm_config = load_llm_supervisor_config(Path("config/llm_supervisor.yaml"))
        # Assuming defaults are safe or env vars are present.

        # Start ZMQ
        await self.zmq_listener.start()

        self.gemini_client = GeminiClient(llm_config, logger=self.logger)
        self.logger.info("Supervisor AI Client initialized.")

        # Start API (in background thread)
        # Start API (in background thread)
        self.api_server.start()

        # Start Policy Publisher
        await self.policy_publisher.start()

    async def monitor_loop(self):
        """Fast loop: ZMQ, Heartbeat, Hard Risk."""
        self.logger.info("Starting Monitor Loop (Fast)")

        while self.running:
            start_time = time.time()

            # 1. Read ZMQ (Non-blocking)
            msg = await self.zmq_listener.get_message_nowait()
            if msg:
                # self.logger.debug(f"Received ZMQ: {msg['topic']}")
                # Pass to ContextBuilder
                # msg format from ZmqListener might be {"topic": ..., "payload": ...} or similar
                # Assuming ZmqListener returns parsed dict or we parse it
                # If msg is just the payload dict with topic injected:
                topic = msg.get("topic", "unknown")
                self.context_builder.on_market_data(topic, msg)

            # Update internal bot_state for HardRiskEngine from DataStore
            # This syncs the ingestion state to the risk engine's expected format
            # HardRiskEngine expects specific keys.

            # Simplified mapping:
            # We assume total_unrealized_pnl and total_exposure from context builder logic
            # or we calculate it here quickly.
            # For efficiency, maybe ContextBuilder handles it, or we access store directly.

            ctx = self.context_builder.build_snapshot()
            # Inject Risk State from ZmqListener's store if it has it (Position/Balance updates)
            # The new ContextBuilder focuses on Market Data.
            # We still need Portfolio Data.
            # ZmqListener.store still exists and gets account updates.
            # We bridge them here.

            risk_info = {
                "total_exposure": 0.0,  # Placeholder or calc from store
                "total_unrealized_pnl": 0.0,
            }
            if hasattr(self.zmq_listener, "store"):
                # Manually aggregate from store if needed
                # For now, let's keep the old logic but apply to bot_state
                pass

            # self.bot_state["current_exposure"] = risk_info.get("total_exposure", 0.0)
            # self.bot_state["equity_current"] = self.bot_state["equity_start"] + risk_info.get("total_unrealized_pnl", 0.0)

            # Inject into ctx for AI
            ctx["risk_metrics"]["equity"] = self.bot_state["equity_current"]
            ctx["risk_metrics"]["exposure"] = self.bot_state["current_exposure"]

            # 2. Check Heartbeat
            hb_age = time.time() - self.last_heartbeat_time

            # Sync to State Manager
            self.state_manager.update(
                {
                    "last_heartbeat_time": self.last_heartbeat_time,
                    "heartbeat_age_s": hb_age,
                    "emergency_mode": self.emergency_mode_triggered,
                    "status": "RUNNING" if self.running else "STOPPED",
                }
            )

            if hb_age > 3.0:
                self.logger.warning(
                    "HEARTBEAT LOST! (Time since last: %.2fs)",
                    time.time() - self.last_heartbeat_time,
                )
                # In real code: await self.kill_bot()

            # 3. Emergency Brain Dead Check
            if not self.emergency_mode_triggered and (
                time.time() - self.last_ai_contact_ts > 600.0
            ):
                self.logger.critical(
                    "AI BRAIN DEAD (Last Contact > 10m). TRIGGERING EMERGENCY LIQUIDATION."
                )
                self.emergency_mode_triggered = True
                self.bot_state["current_exposure"] = (
                    0.0  # Simulating the effect or triggering action
                )

            # 4. Hard Risk Check (New Domain Logic)
            # Create PortfolioState snapshot
            p_state = PortfolioState(
                equity_start_day=self.bot_state["equity_start"],
                equity_current=self.bot_state["equity_current"],
                unrealized_pnl=self.bot_state["equity_current"]
                - self.bot_state["equity_start"],
                total_exposure=self.bot_state["current_exposure"],
                open_order_count=0,  # Need to track
                used_leverage=(
                    self.bot_state["current_exposure"]
                    / self.bot_state["equity_current"]
                    if self.bot_state["equity_current"] > 0
                    else 0.0
                ),
            )

            verdict = HardRiskEngine.check_risk(p_state, self.risk_config)

            if self.emergency_mode_triggered:
                verdict = RiskVerdict(
                    RiskLevel.CRITICAL, "Emergency Mode Active", "CLOSE_ALL"
                )

            if verdict.level != RiskLevel.NORMAL:
                if verdict.level == RiskLevel.CRITICAL:
                    self.logger.critical(f"HARD RISK: {verdict.reason}")
                    self.audit_logger.log_kill_event(verdict.reason, "HardRisk", 0.0)
                else:
                    self.logger.warning(f"RISK WARNING: {verdict.reason}")

            # 5. Enforce on Policy & Broadcast
            # Apply override on a COPY to detect changes vs current active
            # We don't want to mutate active_policy directly without validation/broadcasting
            current_policy = self.policy_manager.active_policy

            # enforce_hard_risk modifies the passed policy, so we pass a copy
            safe_policy_proposal = self.policy_manager.enforce_hard_risk(
                verdict, copy.copy(current_policy)
            )

            # Broadcast if changed
            if safe_policy_proposal != current_policy:
                self.policy_manager.active_policy = safe_policy_proposal
                await self.policy_publisher.publish_policy(safe_policy_proposal)
                self.logger.info(
                    f"Policy Enforced by Risk: {safe_policy_proposal.mode} (Mult: {safe_policy_proposal.risk_multiplier})"
                )

            # Maintain ~10Hz (100ms)
            elapsed = time.time() - start_time
            sleep_time = max(0.0, 0.1 - elapsed)
            await asyncio.sleep(sleep_time)

    async def strategy_loop(self):
        """Slow loop: AI/Strategy Analysis with Fail-Safe."""
        self.logger.info("Starting Strategy Loop (Slow)")

        while self.running:
            start_ts = time.time()
            try:
                if self.gemini_client:
                    # Provide context from ContextBuilder
                    context = self.context_builder.build_snapshot()

                    # Inject History to Context for AI
                    context["history_summary"] = format_history(self.decision_history)
                    context["system_prompt_hint"] = (
                        SYSTEM_PROMPT  # Or passed differently depending on GeminiClient impl
                    )

                    self.logger.info("Requesting Strategy Analysis...")
                    # Note: We probably need to update GeminiClient to accept system prompt or handle it.
                    # For now passing context.

                    raw_result = await self.gemini_client.safe_analyze_risk(context)

                    # Circuit Breaker / Error fallback result usually is "HOLD" but might not match strict schema?
                    # GeminiClient.safe_analyze_risk returns a dict.
                    # We need to ensure it matches our schema or validate it.
                    # The GeminiClient skeleton returned: {"action": "HOLD", ...}
                    # We need to validate strictly.

                    validated_policy = AiBridge.validate_response(raw_result)

                    # Log Event
                    latency_ms = (time.time() - start_ts) * 1000.0
                    self.audit_logger.log_ai_event(
                        context, validated_policy, latency_ms
                    )

                    # Success
                    # AI Bridge returns a dict matching JSON Schema.
                    # We need to map it to PolicyContract via PolicyManager

                    # Update Policy Manager
                    new_policy = self.policy_manager.apply_ai_decision(validated_policy)

                    # Publish New Policy
                    await self.policy_publisher.publish_policy(new_policy)

                    self.last_ai_contact_ts = time.time()

                    # Update State Manager
                    self.state_manager.update(
                        {
                            "active_policy": new_policy.to_dict(),
                            "regime": validated_policy.get("regime", "UNKNOWN"),
                        }
                    )

                    # Update History
                    self.decision_history.append(validated_policy)
                    if len(self.decision_history) > 10:
                        self.decision_history.pop(0)

                    self.logger.info(
                        "New Strategy Policy Applied: %s (Reason: %s)",
                        new_policy.mode,
                        new_policy.ai_reasoning[:50],
                    )

            except (MalformedResponseError, Exception) as e:
                self.logger.error("Strategy Loop Error (entering DEGRADED MODE): %s", e)
                # In Degraded mode, we keep self.active_policy as old one.
                # If last_ai_contact_ts ages too much -> Emergency Mode in Monitor Loop triggers.

            # Wait for next cycle (5s)
            elapsed = time.time() - start_ts
            sleep_time = max(0.0, 5.0 - elapsed)
            await asyncio.sleep(sleep_time)

    async def run(self):
        """Main entry point."""
        # Setup signals
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        await self._setup()

        # Run loops concurrently
        monitor_task = asyncio.create_task(self.monitor_loop())
        strategy_task = asyncio.create_task(self.strategy_loop())

        await asyncio.gather(monitor_task, strategy_task)

    def stop(self):
        self.logger.info("Stopping Supervisor...")
        self.running = False
        # Ideally await zmq close, but stop is sync here.
        # We rely on loop clean up or added async cleanup method.


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    supervisor = AsyncSupervisor()
    try:
        asyncio.run(supervisor.run())
    except KeyboardInterrupt:
        pass
