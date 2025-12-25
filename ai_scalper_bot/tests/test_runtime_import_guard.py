import sys
from pathlib import Path
import types


def test_runtime_import_guard():
    repo_root = Path(__file__).resolve().parents[2]
    bot_root = repo_root / "ai_scalper_bot"
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if bot_root.exists() and str(bot_root) not in sys.path:
        sys.path.insert(0, str(bot_root))
    if "xgboost" not in sys.modules:
        stub = types.ModuleType("xgboost")
        stub.XGBClassifier = object
        sys.modules["xgboost"] = stub
    # Import runtime entrypoint; this should not pull offline/backtest tooling.
    import bot.run_bot  # noqa: F401

    # Heavy deps that should not appear in runtime import graph.
    forbidden_prefixes = ("sklearn", "matplotlib", "torch", "tensorflow")
    offenders = [name for name in sys.modules if name.startswith(forbidden_prefixes)]
    assert not offenders, f"Unexpected heavy imports: {offenders}"

    # Offline/backtest modules should stay out of runtime imports.
    forbidden_modules = [
        "bot.sandbox.offline_loop",
        "bot.sandbox.generate_synthetic_ticks",
        "bot.backtester.backtest_model",
        "bot.backtester.metrics",
        "bot.backtester.simulator",
        "bot.backtester.tick_replay",
        "bot.ml.signal_model.dataset",
        "bot.ml.signal_model.dataset_builder",
        "bot.ml.signal_model.train",
        "bot.ml.signal_model.train_all",
        "bot.ml.signal_model.test_inference",
        "bot.market_data.offline_simulator",
    ]
    for name in forbidden_modules:
        assert name not in sys.modules, f"Offline module imported at runtime: {name}"
