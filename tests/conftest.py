import sys
import os
from pathlib import Path
import asyncio
import pytest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
src_path = ROOT / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

BOT_ROOT = ROOT / "ai_scalper_bot"
if BOT_ROOT.exists() and str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

if sys.platform.startswith("win") and hasattr(
    asyncio, "WindowsSelectorEventLoopPolicy"
):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def mock_offline_env(monkeypatch):
    """Automatically mock network calls if QE_OFFLINE is set."""
    if os.environ.get("QE_OFFLINE") == "1":
        # Mock generic socket connect to fail fast or pass silently depending on need
        # For now, let's mock specific libraries used in the stack

        # Mock Google Generative AI
        mock_genai = MagicMock()
        monkeypatch.setattr("google.generativeai.GenerativeModel", MagicMock())
        monkeypatch.setattr("google.generativeai.configure", MagicMock())

        # Mock requests
        monkeypatch.setattr(
            "requests.get",
            MagicMock(return_value=MagicMock(status_code=200, json=lambda: {})),
        )
        monkeypatch.setattr(
            "requests.post",
            MagicMock(return_value=MagicMock(status_code=200, json=lambda: {})),
        )

        # Mock ZMQ context to avoid binding real ports if not needed,
        # though integration tests might need loopback.
        # Keeping ZMQ real for local loopback is usually fine if ports don't conflict.
