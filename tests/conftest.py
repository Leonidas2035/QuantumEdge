import sys
from pathlib import Path
import asyncio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BOT_ROOT = ROOT / "ai_scalper_bot"
if BOT_ROOT.exists() and str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
