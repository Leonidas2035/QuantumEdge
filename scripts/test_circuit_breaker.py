import asyncio
from unittest.mock import MagicMock, patch
from quantum_edge_core.supervisor.gemini_client import GeminiClient


async def test_circuit_breaker_logic():
    print("Initializing Client...")
    # Mock API key not needed since we mock httpx
    client = GeminiClient(api_key="dummy")

    # Override backoff to be instant for test
    client.circuit.recovery_timeout = 0.5

    print("Testing Normal Operation (Closed State)...")
    # Simulate Success
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Risk Low"}]}}]}
        mock_post.return_value = mock_response

        result = await client.safe_analyze_risk("Status?")
        assert result == "Risk Low"
        print("[PASS] Normal call succeeded")

    print("Testing Failure Threshold (Transition to Open)...")
    # Simulate Failures
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        # Threshold is 3
        await client.safe_analyze_risk("1")
        await client.safe_analyze_risk("2")
        await client.safe_analyze_risk("3")

        assert client.circuit.failures == 3
        assert client.circuit.state.value == "OPEN"
        print("[PASS] Circuit transitioned to OPEN after 3 failures")

        # Next call should be skipped immediately
        result = await client.safe_analyze_risk("4")
        assert result is None
        print("[PASS] Call skipped while OPEN")

    print("Testing Recovery (Half-Open)...")
    # Wait for timeout
    await asyncio.sleep(0.6)

    # Next call should be allowed (Probe)
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Recovered"}]}}]}
        mock_post.return_value = mock_response

        result = await client.safe_analyze_risk("Probe")
        assert result == "Recovered"
        assert client.circuit.state.value == "CLOSED"
        assert client.circuit.failures == 0
        print("[PASS] Circuit recovered to CLOSED")


if __name__ == "__main__":
    from quantum_edge_core.logging_setup import setup_logging

    setup_logging()
    asyncio.run(test_circuit_breaker_logic())
