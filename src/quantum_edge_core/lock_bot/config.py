"""Config loader for quantum_edge_core.lock_bot."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

from quantum_edge_core.lock_bot.ddn.config import (
    DDNConfig,
    DDNProfile,
)
from quantum_edge_core.lock_bot.execution.base import (
    ExecutionConfig,
    ExecutionMode,
)


@dataclass
class LockbotConfig:
    bot_id: str = "LockBotBTC"
    symbol: str = "BTCUSDT"
    hub_sub_endpoint: str = os.getenv(
        "LOCKBOT_HUB_SUB_ENDPOINT", "ipc:///tmp/quantum_market_data.ipc"
    )
    supervisor_cmd_sub_endpoint: str = os.getenv(
        "LOCKBOT_CMD_SUB_ENDPOINT", "ipc:///tmp/lockbot_cmd.ipc"
    )
    bot_pub_endpoint: str = os.getenv(
        "LOCKBOT_PUB_ENDPOINT", "ipc:///tmp/lockbot_status.ipc"
    )
    market_topics: List[str] = field(
        default_factory=lambda: [
            "BTCUSDT:mark_price_1s",
            "BTCUSDT:vwap_d",
            "BTCUSDT:vwap_bands_d",
            "BTCUSDT:avwap",
            "BTCUSDT:liq_heatmap",
        ]
    )
    account_topics: List[str] = field(default_factory=list)
    heartbeat_interval_ms: int = int(os.getenv("LOCKBOT_HEARTBEAT_MS", "1000"))
    cmd_ttl_ms: int = int(os.getenv("LOCKBOT_CMD_TTL_MS", "2000"))
    cmd_cache_size: int = int(os.getenv("LOCKBOT_CMD_CACHE_SIZE", "256"))
    log_level: str = os.getenv("LOCKBOT_LOG_LEVEL", "INFO")
    log_path: str = os.getenv("LOCKBOT_LOG_PATH", "runtime/lockbot_btc.log")
    ddn: DDNConfig = field(default_factory=DDNConfig.default)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    @staticmethod
    def load(path: Path | None = None) -> "LockbotConfig":
        if path is None or not path.exists():
            return LockbotConfig()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = LockbotConfig()
        for key, value in (data or {}).items():
            if key in {"ddn", "execution"}:
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        ddn_cfg = data.get("ddn", {}) if isinstance(data, dict) else {}
        if isinstance(ddn_cfg, dict):
            profiles = {}
            for name, prof in (ddn_cfg.get("profiles", {}) or {}).items():
                if not isinstance(prof, dict):
                    continue
                profiles[name] = DDNProfile(
                    name=str(name),
                    target=float(prof.get("target", 0.0)),
                    band_low=float(prof.get("band_low", -0.1)),
                    band_high=float(prof.get("band_high", 0.1)),
                    force_hedge=bool(prof.get("force_hedge", False)),
                )
            cfg.ddn = DDNConfig(
                profiles=profiles or DDNConfig.default().profiles,
                max_band_abs=float(ddn_cfg.get("max_band_abs", cfg.ddn.max_band_abs)),
                max_margin_usage=float(
                    ddn_cfg.get("max_margin_usage", cfg.ddn.max_margin_usage)
                ),
                min_distance_to_liq_bps=float(
                    ddn_cfg.get(
                        "min_distance_to_liq_bps", cfg.ddn.min_distance_to_liq_bps
                    )
                ),
                max_step_notional_usd=float(
                    ddn_cfg.get("max_step_notional_usd", cfg.ddn.max_step_notional_usd)
                ),
                min_step_notional_usd=float(
                    ddn_cfg.get("min_step_notional_usd", cfg.ddn.min_step_notional_usd)
                ),
                max_steps_per_minute=int(
                    ddn_cfg.get("max_steps_per_minute", cfg.ddn.max_steps_per_minute)
                ),
                cooldown_ms_after_reject=int(
                    ddn_cfg.get(
                        "cooldown_ms_after_reject", cfg.ddn.cooldown_ms_after_reject
                    )
                ),
                panic_on_lag_ms=int(
                    ddn_cfg.get("panic_on_lag_ms", cfg.ddn.panic_on_lag_ms)
                ),
                taker_fee_bps=float(
                    ddn_cfg.get("taker_fee_bps", cfg.ddn.taker_fee_bps)
                ),
                maker_fee_bps=float(
                    ddn_cfg.get("maker_fee_bps", cfg.ddn.maker_fee_bps)
                ),
                expected_slippage_bps_market=float(
                    ddn_cfg.get(
                        "expected_slippage_bps_market",
                        cfg.ddn.expected_slippage_bps_market,
                    )
                ),
                funding_weight=float(
                    ddn_cfg.get("funding_weight", cfg.ddn.funding_weight)
                ),
                min_expected_edge_bps=float(
                    ddn_cfg.get("min_expected_edge_bps", cfg.ddn.min_expected_edge_bps)
                ),
                max_cost_bps_per_step=float(
                    ddn_cfg.get("max_cost_bps_per_step", cfg.ddn.max_cost_bps_per_step)
                ),
                volatility_window=int(
                    ddn_cfg.get("volatility_window", cfg.ddn.volatility_window)
                ),
                step_volatility_scale=float(
                    ddn_cfg.get("step_volatility_scale", cfg.ddn.step_volatility_scale)
                ),
                max_volatility_bps_atr=float(
                    ddn_cfg.get(
                        "max_volatility_bps_atr", cfg.ddn.max_volatility_bps_atr
                    )
                ),
            )
        exec_cfg = data.get("execution", {}) if isinstance(data, dict) else {}
        if isinstance(exec_cfg, dict):
            mode_raw = exec_cfg.get("mode", cfg.execution.mode.value)
            try:
                mode = ExecutionMode(str(mode_raw))
            except ValueError:
                mode = ExecutionMode.DRY_RUN
            cfg.execution = ExecutionConfig(
                mode=mode,
                auto_submit_on_allow=bool(
                    exec_cfg.get(
                        "auto_submit_on_allow", cfg.execution.auto_submit_on_allow
                    )
                ),
                allow_live_mainnet=bool(
                    exec_cfg.get("allow_live_mainnet", cfg.execution.allow_live_mainnet)
                ),
                symbol_whitelist=list(
                    exec_cfg.get("symbol_whitelist", cfg.execution.symbol_whitelist)
                ),
                max_open_orders=int(
                    exec_cfg.get("max_open_orders", cfg.execution.max_open_orders)
                ),
                ack_timeout_ms=int(
                    exec_cfg.get("ack_timeout_ms", cfg.execution.ack_timeout_ms)
                ),
                stale_account_ms=int(
                    exec_cfg.get("stale_account_ms", cfg.execution.stale_account_ms)
                ),
                error_threshold=int(
                    exec_cfg.get("error_threshold", cfg.execution.error_threshold)
                ),
                allow_reduce_only_in_panic=bool(
                    exec_cfg.get(
                        "allow_reduce_only_in_panic",
                        cfg.execution.allow_reduce_only_in_panic,
                    )
                ),
                ledger_path=str(exec_cfg.get("ledger_path", cfg.execution.ledger_path)),
                api_key_env=str(exec_cfg.get("api_key_env", cfg.execution.api_key_env)),
                api_secret_env=str(
                    exec_cfg.get("api_secret_env", cfg.execution.api_secret_env)
                ),
                base_url=str(exec_cfg.get("base_url", cfg.execution.base_url)),
                recv_window=int(exec_cfg.get("recv_window", cfg.execution.recv_window)),
            )
        # ── Security: validate API keys from ENV ─────────────────────
        if cfg.execution.mode != ExecutionMode.DRY_RUN:
            api_key = os.getenv(cfg.execution.api_key_env)
            api_secret = os.getenv(cfg.execution.api_secret_env)
            if not api_key or not api_secret:
                raise ValueError(
                    f"Missing API keys in environment. Set "
                    f"{cfg.execution.api_key_env} and "
                    f"{cfg.execution.api_secret_env} before running "
                    f"in {cfg.execution.mode.value} mode."
                )
        return cfg
