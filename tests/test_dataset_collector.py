import json
import os
import pytest
from pathlib import Path
from hermes.supervisor.utils.dataset_collector import collect_llm_sample


def test_collect_llm_sample_success(tmp_path):
    dataset_file = tmp_path / "test_dataset.jsonl"
    
    messages = [
        {"role": "system", "content": "You are a trader."},
        {"role": "user", "content": "RSI is 30, should I buy?"}
    ]
    response = '{"action": "BUY", "reason": "RSI is oversold"}'
    model_name = "test-model"
    
    # Execute the collection
    collect_llm_sample(messages, response, model_name, str(dataset_file))
    
    # Assert file exists
    assert dataset_file.exists()
    
    # Read and parse JSONL
    with open(dataset_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    assert len(lines) == 1
    data = json.loads(lines[0])
    
    # Verify contents
    assert "messages" in data
    assert len(data["messages"]) == 3
    assert data["messages"][0] == {"role": "system", "content": "You are a trader."}
    assert data["messages"][1] == {"role": "user", "content": "RSI is 30, should I buy?"}
    assert data["messages"][2] == {"role": "assistant", "content": response}
    
    assert "metadata" in data
    assert data["metadata"]["model"] == "test-model"
    assert "timestamp" in data["metadata"]


def test_collect_llm_sample_robustness():
    # If the file path is invalid, it should log a warning but NOT raise an exception
    messages = [{"role": "user", "content": "hello"}]
    
    # This path is intentionally invalid/non-writable on Unix systems
    invalid_path = "/non_existent_directory_XYZ/dataset.jsonl"
    
    # Calling should succeed without exception
    collect_llm_sample(messages, "world", "test-model", invalid_path)


def test_chat_completions_client_integration(tmp_path, monkeypatch):
    import asyncio
    import httpx
    from unittest.mock import AsyncMock
    from hermes.supervisor.llm_supervisor import ChatCompletionsClient
    
    dataset_file = tmp_path / "gemma_dataset.jsonl"
    
    # Configure environment
    monkeypatch.setenv("GOOGLE_API_KEY", "mock_key")
    
    # Mock httpx.AsyncClient.post
    mock_response = httpx.Response(
        status_code=200,
        content=json.dumps({
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"market_regime": "ranging"}'}
                        ]
                    }
                }
            ]
        }).encode("utf-8")
    )
    
    async def mock_post(*args, **kwargs):
        return mock_response
        
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    
    # Monkeypatch the target dataset file path inside dataset_collector.py
    import hermes.supervisor.utils.dataset_collector as dc
    monkeypatch.setattr(dc, "collect_llm_sample", lambda messages, res, model, path=None: collect_llm_sample(messages, res, model, str(dataset_file)))
    
    client = ChatCompletionsClient(
        api_url="https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent",
        api_key_env="GOOGLE_API_KEY"
    )
    
    messages = [{"role": "user", "content": "hi"}]
    
    async def run_completion():
        return await client.complete_async("gemini-3-flash-preview", messages, 0.0, 10.0)
        
    res = asyncio.run(run_completion())
    
    assert res == '{"market_regime": "ranging"}'
    
    # Assert JSONL got updated
    assert dataset_file.exists()
    with open(dataset_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["messages"][-1]["content"] == res
    assert data["metadata"]["model"] == "gemini-3-flash-preview"


def test_chat_completions_client_hermes_oneshot(tmp_path, monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from hermes.supervisor.llm_supervisor import ChatCompletionsClient
    
    dataset_file = tmp_path / "gemma_dataset.jsonl"
    
    # Mock asyncio.create_subprocess_exec
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b'{"market_regime": "ranging"}', b"")
    
    async def mock_create_subprocess_exec(*args, **kwargs):
        return mock_proc
        
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
    
    # Monkeypatch the target dataset file path inside dataset_collector.py
    import hermes.supervisor.utils.dataset_collector as dc
    monkeypatch.setattr(dc, "collect_llm_sample", lambda messages, res, model, path=None: collect_llm_sample(messages, res, model, str(dataset_file)))
    
    client = ChatCompletionsClient(
        api_url="hermes",
        api_key_env="HERMES"
    )
    
    messages = [{"role": "user", "content": "hi"}]
    
    async def run_completion():
        return await client.complete_async("hermes", messages, 0.0, 10.0)
        
    res = asyncio.run(run_completion())
    
    assert res == '{"market_regime": "ranging"}'
    
    # Assert JSONL got updated
    assert dataset_file.exists()
    with open(dataset_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["messages"][-1]["content"] == res
    assert data["metadata"]["model"] == "hermes"


