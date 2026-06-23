import pytest
from quantum_edge_core.market_data.tsdb.query_builder import QuestDBQueryBuilder

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

    def get(self, url):
        return MockResponse(self.json_data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

@pytest.mark.asyncio
async def test_get_microstructure_mock(monkeypatch):
    mock_response_data = {
        "columns": [
            {"name": "ts", "type": "TIMESTAMP"},
            {"name": "mid_price", "type": "DOUBLE"},
            {"name": "spread", "type": "DOUBLE"},
            {"name": "ofi_raw", "type": "DOUBLE"},
            {"name": "volume_delta", "type": "DOUBLE"}
        ],
        "dataset": [
            [1672531200000000, 16500.0, 1.5, 120.5, 0.5]
        ]
    }

    monkeypatch.setattr("aiohttp.ClientSession", lambda: MockSession(mock_response_data))

    qb = QuestDBQueryBuilder()
    res = await qb.get_microstructure("BTCUSDT", minutes=15)

    assert len(res) == 1
    assert res[0]["mid_price"] == 16500.0
    assert res[0]["spread"] == 1.5
    assert res[0]["ofi_raw"] == 120.5
    assert res[0]["volume_delta"] == 0.5

@pytest.mark.asyncio
async def test_get_volatility_profile_mock(monkeypatch):
    mock_response_data = {
        "columns": [
            {"name": "ts", "type": "TIMESTAMP"},
            {"name": "atr_14", "type": "DOUBLE"},
            {"name": "mid_price", "type": "DOUBLE"}
        ],
        "dataset": [
            [1672531200000000, 25.4, 16501.0]
        ]
    }

    monkeypatch.setattr("aiohttp.ClientSession", lambda: MockSession(mock_response_data))

    qb = QuestDBQueryBuilder()
    res = await qb.get_volatility_profile("BTCUSDT", hours=4)

    assert len(res) == 1
    assert res[0]["atr_14"] == 25.4
    assert res[0]["mid_price"] == 16501.0

@pytest.mark.asyncio
async def test_get_vwap_bands_mock(monkeypatch):
    mock_response_data = {
        "columns": [
            {"name": "ts", "type": "TIMESTAMP"},
            {"name": "close", "type": "DOUBLE"},
            {"name": "volume", "type": "DOUBLE"}
        ],
        "dataset": [
            [1672531200000000, 16502.0, 10.0]
        ]
    }

    monkeypatch.setattr("aiohttp.ClientSession", lambda: MockSession(mock_response_data))

    qb = QuestDBQueryBuilder()
    res = await qb.get_vwap_bands("BTCUSDT", days=1)

    assert len(res) == 1
    assert res[0]["close"] == 16502.0
    assert res[0]["volume"] == 10.0
