"""CoinGecko Global Market Intelligence adapter (RAM TTL cache + fail-soft advisory).

Strictly read-only global macro context (Market Cap, BTC/ETH dominance, 24h volume,
and cross-exchange basis check vs BingX). NEVER used as an execution price authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import threading
import time
from typing import Any, Callable, Literal, Mapping, Sequence

import httpx

from quantum_edge_core.contracts.optimization import ExternalAdvisoryV1

logger = logging.getLogger("quantum_edge_core.external_intel.coingecko_client")

SYMBOL_TO_COINGECKO_ID: dict[str, str] = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "BNBUSDT": "binancecoin",
    "LINKUSDT": "chainlink",
    "SUIUSDT": "sui",
    "SOLUSDT": "solana",
    "XRPUSDT": "ripple",
    "AVAXUSDT": "avalanche-2",
    "AAVEUSDT": "aave",
    "LTCUSDT": "litecoin",
    "DOGEUSDT": "dogecoin",
}


class CoinGeckoIntelClient:
    """Thread-safe RAM-cached CoinGecko global & cross-exchange advisory client."""

    def __init__(
        self,
        *,
        ttl_sec: float = 600.0,
        stale_max_sec: float = 3600.0,
        timeout_sec: float = 8.0,
        api_key: str | None = None,
        base_url: str = "https://api.coingecko.com/api/v3",
        http_get_override: Callable[
            [str, dict[str, Any], dict[str, str], float], dict[str, Any]
        ]
        | None = None,
    ) -> None:
        self.ttl_sec = max(1.0, float(ttl_sec))
        self.stale_max_sec = max(self.ttl_sec, float(stale_max_sec))
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.api_key = (
            api_key if api_key is not None else os.environ.get("COINGECKO_API_KEY")
        )
        self.base_url = base_url.rstrip("/")
        self.http_get_override = http_get_override

        self._lock = threading.Lock()
        self._cached_at: float | None = None
        self._cached_advisory: ExternalAdvisoryV1 | None = None
        self.stats: dict[str, int] = {
            "http_calls": 0,
            "cache_hits": 0,
            "errors": 0,
            "stale_fallbacks": 0,
        }

    def _http_get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-cg-demo-api-key"] = str(self.api_key)
        query = dict(params or {})
        self.stats["http_calls"] += 1
        if self.http_get_override is not None:
            return dict(self.http_get_override(url, query, headers, self.timeout_sec))
        with httpx.Client(timeout=self.timeout_sec) as client:
            resp = client.get(url, params=query, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return dict(data) if isinstance(data, Mapping) else {}

    @staticmethod
    def _classify_macro_regime(
        mcap_chg_24h_pct: float | None,
        btc_dom_pct: float | None,
    ) -> Literal["RISK_ON", "NEUTRAL", "RISK_OFF", "ALT_ROTATION", "BTC_DRAIN"]:
        chg = float(mcap_chg_24h_pct) if mcap_chg_24h_pct is not None else 0.0
        btcd = float(btc_dom_pct) if btc_dom_pct is not None else 55.0
        if chg <= -2.0:
            return "RISK_OFF"
        if btcd >= 58.0 and chg < 0.0:
            return "BTC_DRAIN"
        if chg >= 1.0 and btcd < 54.0:
            return "ALT_ROTATION"
        if chg >= 2.0 and btcd < 58.0:
            return "RISK_ON"
        return "NEUTRAL"

    @staticmethod
    def _with_basis(
        base_snapshots: Mapping[str, Mapping[str, float]],
        bingx_prices: Mapping[str, float] | None,
    ) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {
            sym: dict(vals) for sym, vals in base_snapshots.items()
        }
        if not bingx_prices:
            return out
        for sym, snap in out.items():
            ref_usd = float(snap.get("ref_usd") or 0.0)
            bx_raw = bingx_prices.get(sym)
            if ref_usd > 0 and bx_raw is not None and float(bx_raw) > 0:
                basis_bps = (float(bx_raw) - ref_usd) / ref_usd * 10_000.0
                snap["bingx_basis_bps"] = round(basis_bps, 2)
        return out

    def get_advisory(
        self,
        symbols: Sequence[str],
        *,
        bingx_prices: Mapping[str, float] | None = None,
        now_ts: float | None = None,
    ) -> ExternalAdvisoryV1:
        """Return RAM-cached or freshly fetched ExternalAdvisoryV1 (fail-soft, never raises)."""
        ref_now = time.time() if now_ts is None else float(now_ts)
        norm_syms = [str(s).strip().upper() for s in symbols if str(s).strip()]

        with self._lock:
            if self._cached_advisory is not None and self._cached_at is not None:
                age = max(0.0, ref_now - self._cached_at)
                if age < self.ttl_sec:
                    self.stats["cache_hits"] += 1
                    updated_assets = self._with_basis(
                        self._cached_advisory.asset_snapshots, bingx_prices
                    )
                    return self._cached_advisory.model_copy(
                        update={
                            "status": "OK",
                            "age_sec": round(age, 2),
                            "asset_snapshots": updated_assets,
                        }
                    )

        try:
            global_raw = self._http_get_json("/global")
            g_data = global_raw.get("data") if isinstance(global_raw.get("data"), Mapping) else global_raw
            mcap_map = g_data.get("total_market_cap") if isinstance(g_data.get("total_market_cap"), Mapping) else {}
            vol_map = g_data.get("total_volume") if isinstance(g_data.get("total_volume"), Mapping) else {}
            dom_map = g_data.get("market_cap_percentage") if isinstance(g_data.get("market_cap_percentage"), Mapping) else {}

            mcap_b = (
                round(float(mcap_map["usd"]) / 1e9, 2)
                if mcap_map.get("usd") is not None
                else None
            )
            vol_b = (
                round(float(vol_map["usd"]) / 1e9, 2)
                if vol_map.get("usd") is not None
                else None
            )
            mcap_chg = (
                round(float(g_data["market_cap_change_percentage_24h_usd"]), 2)
                if g_data.get("market_cap_change_percentage_24h_usd") is not None
                else None
            )
            btc_dom = round(float(dom_map["btc"]), 2) if dom_map.get("btc") is not None else None
            eth_dom = round(float(dom_map["eth"]), 2) if dom_map.get("eth") is not None else None

            id_to_sym: dict[str, str] = {}
            for sym in norm_syms:
                cg_id = SYMBOL_TO_COINGECKO_ID.get(sym)
                if cg_id:
                    id_to_sym[cg_id] = sym

            raw_assets: dict[str, dict[str, float]] = {}
            if id_to_sym:
                prices_raw = self._http_get_json(
                    "/simple/price",
                    params={
                        "ids": ",".join(sorted(id_to_sym.keys())),
                        "vs_currencies": "usd",
                        "include_24hr_vol": "true",
                        "include_24hr_change": "true",
                    },
                )
                for cg_id, sym in id_to_sym.items():
                    row = prices_raw.get(cg_id)
                    if not isinstance(row, Mapping):
                        continue
                    usd_px = row.get("usd")
                    if usd_px is None or float(usd_px) <= 0:
                        continue
                    snap_row: dict[str, float] = {"ref_usd": round(float(usd_px), 6)}
                    if row.get("usd_24h_change") is not None:
                        snap_row["chg_24h_pct"] = round(float(row["usd_24h_change"]), 2)
                    if row.get("usd_24h_vol") is not None:
                        snap_row["vol_24h_m"] = round(float(row["usd_24h_vol"]) / 1e6, 2)
                    raw_assets[sym] = snap_row

            assets_with_basis = self._with_basis(raw_assets, bingx_prices)
            regime_tag = self._classify_macro_regime(mcap_chg, btc_dom)
            advisory = ExternalAdvisoryV1(
                source="coingecko",
                status="OK",
                fetched_ts_utc=datetime.fromtimestamp(ref_now, tz=timezone.utc).isoformat(),
                age_sec=0.0,
                total_market_cap_usd_b=mcap_b,
                market_cap_change_24h_pct=mcap_chg,
                btc_dominance_pct=btc_dom,
                eth_dominance_pct=eth_dom,
                global_volume_24h_usd_b=vol_b,
                macro_regime_tag=regime_tag,
                asset_snapshots=assets_with_basis,
                trending_symbols=[],
            )
            with self._lock:
                self._cached_at = ref_now
                self._cached_advisory = advisory
            return advisory
        except Exception as exc:
            logger.warning("CoinGeckoIntelClient fail-soft fallback: %s", exc)
            with self._lock:
                self.stats["errors"] += 1
                if self._cached_advisory is not None and self._cached_at is not None:
                    age = max(0.0, ref_now - self._cached_at)
                    if age < self.stale_max_sec:
                        self.stats["stale_fallbacks"] += 1
                        updated_assets = self._with_basis(
                            self._cached_advisory.asset_snapshots, bingx_prices
                        )
                        return self._cached_advisory.model_copy(
                            update={
                                "status": "STALE",
                                "age_sec": round(age, 2),
                                "asset_snapshots": updated_assets,
                            }
                        )
            return ExternalAdvisoryV1(
                source="coingecko",
                status="UNAVAILABLE",
                fetched_ts_utc=datetime.fromtimestamp(ref_now, tz=timezone.utc).isoformat(),
                age_sec=0.0,
            )


__all__ = ["CoinGeckoIntelClient", "SYMBOL_TO_COINGECKO_ID"]
