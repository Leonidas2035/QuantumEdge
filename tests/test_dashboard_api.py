import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

# Mock classes to mock aiohttp context managers for dashboard_api
class MockResponse:
    def __init__(self, json_data, status=200):
        self._json_data = json_data
        self.status = status
        
    async def json(self):
        return self._json_data
        
    async def text(self):
        return str(self._json_data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

class MockSession:
    def __init__(self, json_data):
        self.json_data = json_data

    def get(self, url, timeout=None):
        return MockResponse(self.json_data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

def test_data_mcp_bridge_commands(monkeypatch):
    import urllib.request
    from unittest.mock import mock_open
    
    mock_response_data = {
        "columns": [
            {"name": "ts", "type": "TIMESTAMP"},
            {"name": "pnl_session", "type": "DOUBLE"},
            {"name": "pnl", "type": "DOUBLE"},
            {"name": "max_dd", "type": "DOUBLE"},
            {"name": "rsi_14", "type": "DOUBLE"},
            {"name": "rsi", "type": "DOUBLE"},
            {"name": "macd", "type": "DOUBLE"},
            {"name": "atr", "type": "DOUBLE"}
        ],
        "dataset": [
            [1672531200000000, 1.5, 1.5, 0.1, 55.0, 55.0, 0.2, 12.3]
        ]
    }
    
    # Mock urllib.request.urlopen for the CLI module
    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.return_value = bytes(
        import_json_data := bytes(str(mock_response_data).replace("'", '"'), "utf-8")
    )
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    from quantum_edge_infra.automation.hermes_agent.data_mcp_bridge import (
        cmd_query_telemetry,
        cmd_query_market_trend
    )

    # Test cmd_query_telemetry
    res_tel = cmd_query_telemetry("scalper_v1", hours=1)
    assert len(res_tel) == 1
    assert res_tel[0]["pnl"] == 1.5
    assert res_tel[0]["max_dd"] == 0.1

    # Test cmd_query_market_trend
    res_trend = cmd_query_market_trend("BTCUSDT", hours=4)
    assert len(res_trend) == 1
    assert res_trend[0]["rsi"] == 55.0
    assert res_trend[0]["macd"] == 0.2
    assert res_trend[0]["atr"] == 12.3

def test_dashboard_api_endpoints(monkeypatch):
    mock_response_data = {
        "columns": [
            {"name": "time", "type": "LONG"},
            {"name": "price", "type": "DOUBLE"},
            {"name": "rsi", "type": "DOUBLE"},
            {"name": "macd", "type": "DOUBLE"},
            {"name": "value", "type": "DOUBLE"}
        ],
        "dataset": [
            [1672531200, 65000.0, 55.0, 0.2, 1.5]
        ]
    }

    # Patch aiohttp.ClientSession in dashboard_api module
    monkeypatch.setattr(
        "quantum_edge_infra.automation.hermes_agent.dashboard_api.aiohttp.ClientSession",
        lambda: MockSession(mock_response_data)
    )

    from quantum_edge_infra.automation.hermes_agent.dashboard_api import app
    client = TestClient(app)

    # Test features endpoint
    response = client.get("/api/v1/charts/features?symbol=BTCUSDT&hours=4")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["time"] == 1672531200
    assert data[0]["value"] == 65000.0
    assert data[0]["price"] == 65000.0
    assert data[0]["rsi"] == 55.0
    assert data[0]["macd"] == 0.2

    # Test pnl endpoint
    response = client.get("/api/v1/charts/pnl?bot_id=scalper_v1&hours=12")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["time"] == 1672531200
    assert data[0]["value"] == 1.5
