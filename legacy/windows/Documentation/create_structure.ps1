$root = "C:\ai_scalper_bot"

$folders = @(
    "$root\config",
    "$root\data",
    "$root\data\ohlcv",
    "$root\data\orderbooks",
    "$root\data\trades",
    "$root\data\funding",
    "$root\data\oi",
    "$root\data\liquidations",
    "$root\data\logs",
    "$root\storage",
    "$root\storage\db",
    "$root\storage\cache",
    "$root\bot",
    "$root\bot\core",
    "$root\bot\market_data",
    "$root\bot\indicators",
    "$root\bot\ml",
    "$root\bot\ml\signal_model",
    "$root\bot\ml\execution_model",
    "$root\bot\ml\confidence_model",
    "$root\bot\ai_llm",
    "$root\bot\backtester",
    "$root\bot\trading",
    "$root\bot\monitoring"
)

foreach ($folder in $folders) {
    if (!(Test-Path $folder)) {
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        Write-Host "Created folder: $folder"
    }
}

# Create empty files
$files = @(
    "$root\config\settings.yaml",
    "$root\config\secrets_template.env",
    "$root\config\pairs.yaml",
    "$root\bot\core\config_loader.py",
    "$root\bot\core\state.py",
    "$root\bot\core\utils.py",
    "$root\bot\core\constants.py",
    "$root\bot\core\exceptions.py",

    "$root\bot\market_data\ws_manager.py",
    "$root\bot\market_data\binance_ws.py",
    "$root\bot\market_data\bingx_ws.py",
    "$root\bot\market_data\data_manager.py",

    "$root\bot\indicators\ohlcv_indicators.py",
    "$root\bot\indicators\orderflow.py",
    "$root\bot\indicators\volatility.py",
    "$root\bot\indicators\feature_builder.py",

    "$root\bot\ml\signal_model\model.py",
    "$root\bot\ml\signal_model\train.py",
    "$root\bot\ml\signal_model\dataset.py",

    "$root\bot\ml\execution_model\__init__.py",
    "$root\bot\ml\confidence_model\__init__.py",

    "$root\bot\ai_llm\llm_client.py",
    "$root\bot\ai_llm\llm_prompt_builder.py",
    "$root\bot\ai_llm\llm_cache.py",

    "$root\bot\backtester\tick_replay.py",
    "$root\bot\backtester\backtest_model.py",
    "$root\bot\backtester\simulator.py",
    "$root\bot\backtester\metrics.py",

    "$root\bot\trading\order_manager.py",
    "$root\bot\trading\risk_engine.py",
    "$root\bot\trading\position_manager.py",
    "$root\bot\trading\hedging.py",
    "$root\bot\trading\executor.py",

    "$root\bot\main.py",
    "$root\bot\run_dry.py",
    "$root\requirements.txt",
    "$root\README.md"
)

foreach ($f in $files) {
    if (!(Test-Path $f)) {
        New-Item -ItemType File -Path $f -Force | Out-Null
        Write-Host "Created file: $f"
    }
}

Write-Host "`n🎉 Project structure created successfully in $root"
