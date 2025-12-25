import asyncio
import os
import tempfile
import time
from pathlib import Path

from bot.core.config_loader import Config, config as global_config
from bot.market_data.data_manager import DataManager


def _override_config(tmpdir: Path):
    settings = {
        "app": {"data_path": str(tmpdir)},
        "storage": {
            "save_trades": True,
            "save_orderbook_json": True,
            "max_jsonl_size_mb": 0.0005,
            "max_jsonl_minutes": 60,
            "retention_days": 1,
            "flush_batch": 1,
            "flush_interval_seconds": 0.1,
        },
    }
    cfg_path = tmpdir / "settings.yaml"
    import yaml

    cfg_path.write_text(yaml.safe_dump(settings))
    os.environ["QE_CONFIG_PATH"] = str(cfg_path)
    global_config.__dict__.update(Config(str(cfg_path)).__dict__)
    return cfg_path


def test_data_manager_writes_and_rotates():
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        _override_config(tmpdir)
        dm = DataManager()
        async def _push():
            for i in range(5):
                await dm.save_trade({"p": 100 + i, "q": 0.01, "s": "BTCUSDT", "T": int(time.time()*1000)+i})
        asyncio.run(_push())
        time.sleep(0.5)
        dm.close()
        trades_dir = tmpdir / "trades"
        files = list(trades_dir.glob("*.jsonl"))
        assert files, "no trade files written"
