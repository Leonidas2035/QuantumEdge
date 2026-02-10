"""
src/quantum_edge_core/supervisor/gemini_client.py

Async Gemini Client with Circuit Breaker.
Wraps Google GenAI API calls to prevent blocking the event loop and handle failures gracefully.
"""

import time
import structlog
import httpx
from enum import Enum
from typing import Optional


logger = structlog.get_logger()

class CircuitState(Enum):
    CLOSED = "CLOSED"     # Normal operation
    OPEN = "OPEN"         # Fast fail
    HALF_OPEN = "HALF_OPEN" # Testing recovery

class CircuitBreakerOpenException(Exception):
    """Raised when the circuit is open."""
    pass

class AsyncCircuitBreaker:
    """
    Manages failure state to protect external APIs.
    """
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0):
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0.0
        self.logger = logger.bind(component="CircuitBreaker")

    def _check_state(self):
        now = time.time()
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.logger.info("Circuit HALF_OPEN - Probing service")
            else:
                raise CircuitBreakerOpenException(f"Circuit OPEN. Retry in {self.recovery_timeout - (now - self.last_failure_time):.1f}s")
    
    def record_success(self):
        if self.state != CircuitState.CLOSED:
            self.state = CircuitState.CLOSED
            self.failures = 0
            self.logger.info("Circuit CLOSED - Service recovered")
    
    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.threshold:
            self.state = CircuitState.OPEN
            self.logger.warning("Circuit OPEN - Threshold reached", failures=self.failures)

class GeminiClient:
    """
    Async Wrapper for Gemini API with resilience.
    """
    def __init__(self, api_key: str, model_name: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model_name = model_name
        self.circuit = AsyncCircuitBreaker()
        self.logger = logger.bind(component="GeminiClient", model=model_name)
        
        # Configure GenAI (still needed for some helpers, but we might use raw HTTP or the lib if async supported)
        # The prompt says "Using httpx ...". The official lib is synchronous mostly.
        # We will implement a lightweight REST wrapper or wrap the sync call in to_thread, 
        # BUT the prompt specifically said "using httpx.AsyncClient". 
        # So we will implement a direct REST call or use the async support if available.
        # Given "Paste the EXACT code provided", I'll infer the implementation based on standard patterns 
        # if the explicit code wasn't provided in the prompt text (it was referenced as [cite: 664-687]).
        # I will build a robust implementation assuming REST via httpx.
        
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

    async def safe_analyze_risk(self, prompt: str) -> Optional[str]:
        """
        Executes analysis protected by Circuit Breaker.
        Returns: Response text or None if failed/open.
        """
        try:
            self.circuit._check_state()
            
            # Using httpx for true async I/O
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}?key={self.api_key}",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}]
                    },
                    timeout=10.0
                )
                
                if response.status_code != 200:
                    self.logger.error("API Error", status=response.status_code, body=response.text)
                    raise Exception(f"HTTP {response.status_code}")
                
                data = response.json()
                # Parse Gemini response structure
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    self.circuit.record_success()
                    return text
                except KeyError:
                    self.logger.error("Malformed response", data=data)
                    raise Exception("Malformed response")

        except CircuitBreakerOpenException:
            self.logger.warning("Skipping call (Circuit OPEN)")
            return None
        except Exception as e:
            self.logger.error("Call failed", error=str(e))
            self.circuit.record_failure()
            return None
