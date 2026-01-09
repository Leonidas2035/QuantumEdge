"""LockBotBTC service entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import msgspec

from LockBotBTC.lockbot.contracts.lockbot_control_v1 import (
    ACK_TOPIC,
    CMD_TOPIC,
    STATUS_TOPIC,
    AckEnvelope,
    StatusEnvelope,
    validate_command,
)
from LockBotBTC.lockbot_btc.config import LockbotConfig
from LockBotBTC.lockbot_btc.ipc.control_subscriber import ControlSubscriber
from LockBotBTC.lockbot_btc.ipc.hub_subscriber import HubSubscriber
from LockBotBTC.lockbot_btc.ipc.publisher import BotPublisher
from LockBotBTC.lockbot_btc.ipc.raw_subscriber import RawSubscriber
from LockBotBTC.lockbot_btc.state.account_state import AccountState
from LockBotBTC.lockbot_btc.state.bot_state import BotState
from LockBotBTC.lockbot_btc.state.market_state import MarketState


class LockBotService:
    def __init__(self, cfg: LockbotConfig) -> None:
        self._cfg = cfg
        self._bot_state = BotState(bot_id=cfg.bot_id, symbol=cfg.symbol)
        self._bot_state.configure_cache(cfg.cmd_cache_size)
        self._market_state = MarketState()
        self._account_state = AccountState()
        self._publisher = BotPublisher(cfg.bot_pub_endpoint)
        self._hub_sub = HubSubscriber(cfg.hub_sub_endpoint, cfg.market_topics)
        self._cmd_sub = ControlSubscriber(cfg.supervisor_cmd_sub_endpoint, CMD_TOPIC)
        self._account_sub: Optional[RawSubscriber] = None
        if cfg.account_topics:
            self._account_sub = RawSubscriber(cfg.hub_sub_endpoint, cfg.account_topics)
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._seq = 0
        self._start_ts = time.time()
        self._loop_counter = 0
        self._last_loop_ts = time.time()
        self._dropped_msgs = 0

    async def start(self) -> None:
        await self._hub_sub.start()
        await self._cmd_sub.start()
        if self._account_sub:
            await self._account_sub.start()
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._market_loop()),
            asyncio.create_task(self._cmd_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        ]
        if self._account_sub:
            self._tasks.append(asyncio.create_task(self._account_loop()))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await self._hub_sub.stop()
        await self._cmd_sub.stop()
        if self._account_sub:
            await self._account_sub.stop()
        self._publisher.close()

    def process_command(self, command: Dict[str, Any]) -> AckEnvelope:
        ok, reason = validate_command(command)
        cmd_id = str(command.get("cmd_id") or "")
        now_ms = int(time.time() * 1000)
        ttl_ms = int(command.get("ttl_ms") or self._cfg.cmd_ttl_ms)
        if self._bot_state.is_duplicate(cmd_id):
            return self._build_ack(cmd_id, "IGNORED_DUPLICATE", state_version=self._bot_state.state_version)
        if not ok:
            return self._build_ack(cmd_id, "REJECTED", error_code=reason, state_version=self._bot_state.state_version)
        ts_cmd = int(command.get("ts_cmd") or 0)
        if ts_cmd + ttl_ms < now_ms:
            self._bot_state.remember_cmd(cmd_id)
            return self._build_ack(cmd_id, "EXPIRED", error_code="ttl", state_version=self._bot_state.state_version)

        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        cmd_type = payload.get("cmd")
        if cmd_type == "SET_REGIME":
            self._bot_state.regime = str(payload.get("regime"))
        elif cmd_type == "PAUSE":
            self._bot_state.mode = "PAUSED"
        elif cmd_type == "RESUME":
            self._bot_state.mode = "IDLE"
        elif cmd_type == "PANIC_LOCK":
            self._bot_state.mode = "PANIC"
        elif cmd_type == "EXIT_LOCK":
            self._bot_state.mode = "EXITING"
        elif cmd_type == "EXEC_STEP":
            if self._bot_state.mode == "IDLE":
                self._bot_state.mode = "LOCKED"
        elif cmd_type == "SET_DELTA_TARGET":
            if self._bot_state.mode == "IDLE":
                self._bot_state.mode = "LOCKED"
        self._bot_state.remember_cmd(cmd_id)
        self._bot_state.bump_state()
        return self._build_ack(cmd_id, "ACCEPTED", state_version=self._bot_state.state_version)

    def build_status(self) -> StatusEnvelope:
        now_ms = int(time.time() * 1000)
        market_lag = _lag_ms(now_ms, self._market_state.last_market_ts)
        account_lag = _lag_ms(now_ms, self._account_state.last_account_ts)
        loop_hz = self._calc_loop_hz()
        payload = {
            "mode": self._bot_state.mode,
            "regime": self._bot_state.regime,
            "net_delta_est": self._account_state.net_delta_est(),
            "risk": {
                "margin_usage": self._account_state.margin_usage,
                "distance_to_liq_bps": self._account_state.distance_to_liq_bps,
                "funding_rate": self._market_state.funding_rate,
            },
            "positions": {
                "long_qty": self._account_state.long_qty,
                "short_qty": self._account_state.short_qty,
                "long_avg_px": self._account_state.long_avg_px,
                "short_avg_px": self._account_state.short_avg_px,
            },
            "market": {
                "mark_price": self._market_state.mark_price,
                "vwap_d": self._market_state.vwap_d,
                "band_1u": self._market_state.band_1u,
                "band_1l": self._market_state.band_1l,
                "band_2u": self._market_state.band_2u,
                "band_2l": self._market_state.band_2l,
            },
            "lags": {
                "last_market_ts": self._market_state.last_market_ts,
                "last_account_ts": self._account_state.last_account_ts,
                "market_lag_ms": market_lag,
                "account_lag_ms": account_lag,
            },
            "health": {
                "uptime_s": int(time.time() - self._start_ts),
                "loop_hz": loop_hz,
                "dropped_msgs": self._dropped_msgs,
                "last_error": self._bot_state.last_error,
            },
        }
        self._seq += 1
        return StatusEnvelope(
            schema="lockbot_control.v1",
            msg_type="status",
            bot_id=self._cfg.bot_id,
            symbol=self._cfg.symbol,
            ts_event=now_ms,
            seq=self._seq,
            payload=payload,
        )

    async def _market_loop(self) -> None:
        async for _topic, event in self._hub_sub.events():
            self._loop_counter += 1
            ts_event = int(event.ts_event)
            if event.event_type == "mark_price_1s":
                self._market_state.mark_price = event.payload.get("mark_price")
                self._market_state.funding_rate = event.payload.get("funding_rate")
            elif event.event_type == "vwap_d":
                self._market_state.vwap_d = event.payload.get("vwap")
            elif event.event_type == "vwap_bands_d":
                self._market_state.band_1u = event.payload.get("band_1u")
                self._market_state.band_1l = event.payload.get("band_1l")
                self._market_state.band_2u = event.payload.get("band_2u")
                self._market_state.band_2l = event.payload.get("band_2l")
            elif event.event_type == "avwap":
                self._market_state.avwap = event.payload
            elif event.event_type == "liq_heatmap":
                self._market_state.liq_heatmap = event.payload
            self._market_state.update_timestamp(ts_event)

    async def _account_loop(self) -> None:
        if not self._account_sub:
            return
        async for _topic, payload in self._account_sub.events():
            self._loop_counter += 1
            ts_event = payload.get("ts_event") or payload.get("ts_ms")
            if ts_event is not None:
                self._account_state.update_timestamp(int(ts_event))
            positions = payload.get("positions")
            if isinstance(positions, dict):
                self._account_state.long_qty = positions.get("long_qty", self._account_state.long_qty)
                self._account_state.short_qty = positions.get("short_qty", self._account_state.short_qty)
                self._account_state.long_avg_px = positions.get("long_avg_px", self._account_state.long_avg_px)
                self._account_state.short_avg_px = positions.get("short_avg_px", self._account_state.short_avg_px)
            risk = payload.get("risk")
            if isinstance(risk, dict):
                self._account_state.margin_usage = risk.get("margin_usage", self._account_state.margin_usage)
                self._account_state.distance_to_liq_bps = risk.get("distance_to_liq_bps", self._account_state.distance_to_liq_bps)

    async def _cmd_loop(self) -> None:
        async for cmd in self._cmd_sub.commands():
            self._loop_counter += 1
            command = msgspec.structs.asdict(cmd)
            ack = self.process_command(command)
            self._publisher.publish_ack(ACK_TOPIC, ack)
            status = self.build_status()
            self._publisher.publish_status(STATUS_TOPIC, status)

    async def _heartbeat_loop(self) -> None:
        interval = max(self._cfg.heartbeat_interval_ms / 1000.0, 0.2)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            status = self.build_status()
            self._publisher.publish_status(STATUS_TOPIC, status)

    def _build_ack(
        self,
        cmd_id: str,
        status: str,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        state_version: int = 0,
    ) -> AckEnvelope:
        payload = {"status": status, "state_version": state_version}
        if error_code:
            payload["error_code"] = error_code
        if error_detail:
            payload["error_detail"] = error_detail
        return AckEnvelope(
            schema="lockbot_control.v1",
            msg_type="ack",
            bot_id=self._cfg.bot_id,
            symbol=self._cfg.symbol,
            cmd_id=cmd_id,
            ts_ack=int(time.time() * 1000),
            payload=payload,
        )

    def _calc_loop_hz(self) -> float:
        now = time.time()
        elapsed = max(now - self._last_loop_ts, 1e-6)
        loop_hz = self._loop_counter / elapsed
        self._loop_counter = 0
        self._last_loop_ts = now
        return round(loop_hz, 3)


def _lag_ms(now_ms: int, last_ts_ms: Optional[int]) -> Optional[int]:
    if last_ts_ms is None:
        return None
    return max(0, now_ms - int(last_ts_ms))


async def _run(config_path: Optional[Path]) -> None:
    cfg = LockbotConfig.load(config_path)
    log_path = Path(cfg.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    service = LockBotService(cfg)
    await service.start()
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/lockbot_btc.yaml"))
    args = parser.parse_args()
    asyncio.run(_run(args.config))


if __name__ == "__main__":
    main()
