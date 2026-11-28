"""Offline regression tests for QE-05 CoinGecko External Intelligence Layer."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from quantum_edge_core.contracts.optimization import ExternalAdvisoryV1
from quantum_edge_core.external_intel.coingecko_client import CoinGeckoIntelClient
from quantum_edge_core.orchestrator.deterministic_orchestrator import (
    PreFetchedBundle,
    StatelessStagePromptAssembler,
    format_external_advisory_line,
)
from quantum_edge_core.orchestrator.prefix_context_assembler import (
    PrefixContextAssembler,
    ThesisMemoryStore,
)
from tests.test_phase4_prefix_cache import _make_4pair_bundle
from tools.qe_briefing import build_briefing_payload, render_human_briefing


def _fake_coingecko_http(
    *,
    mcap_usd: float = 2_850_000_000_000.0,
    mcap_chg: float = 1.4,
    btcd: float = 54.2,
    ethd: float = 17.5,
    vol_usd: float = 110_000_000_000.0,
    prices: dict[str, float] | None = None,
):
    px_map = prices or {"binancecoin": 600.0, "chainlink": 15.0, "sui": 2.0}
    calls: list[str] = []

    def _get(
        url: str, params: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        calls.append(url)
        if "/global" in url:
            return {
                "data": {
                    "total_market_cap": {"usd": mcap_usd},
                    "market_cap_change_percentage_24h_usd": mcap_chg,
                    "market_cap_percentage": {"btc": btcd, "eth": ethd},
                    "total_volume": {"usd": vol_usd},
                }
            }
        if "/simple/price" in url:
            return {cid: {"usd": val} for cid, val in px_map.items()}
        raise RuntimeError(f"Unexpected URL: {url}")

    return _get, calls


def test_coingecko_client_fetches_parses_basis_and_caches_in_ram_for_ttl() -> None:
    http_get, calls = _fake_coingecko_http(
        prices={"binancecoin": 600.0, "chainlink": 15.0, "sui": 2.0}
    )
    client = CoinGeckoIntelClient(ttl_sec=600.0, http_get_override=http_get)

    adv1 = client.get_advisory(
        ["BNBUSDT", "LINKUSDT", "SUIUSDT"],
        bingx_prices={"BNBUSDT": 600.6, "LINKUSDT": 14.97, "SUIUSDT": 2.0},
        now_ts=1000.0,
    )
    assert isinstance(adv1, ExternalAdvisoryV1)
    assert adv1.status == "OK"
    assert adv1.total_market_cap_usd_b == 2850.0
    assert adv1.btc_dominance_pct == 54.2
    assert adv1.asset_snapshots["BNBUSDT"]["bingx_basis_bps"] == 10.0
    assert adv1.asset_snapshots["LINKUSDT"]["bingx_basis_bps"] == -20.0
    assert adv1.asset_snapshots["SUIUSDT"]["bingx_basis_bps"] == 0.0
    assert len(calls) == 2

    # Within TTL -> zero additional HTTP calls, cache_hits increments
    adv2 = client.get_advisory(
        ["BNBUSDT", "LINKUSDT", "SUIUSDT"],
        bingx_prices={"BNBUSDT": 601.2},
        now_ts=1200.0,
    )
    assert len(calls) == 2
    assert adv2.status == "OK"
    assert round(adv2.age_sec, 1) == 200.0
    assert adv2.asset_snapshots["BNBUSDT"]["bingx_basis_bps"] == 20.0
    assert client.stats["cache_hits"] == 1
    assert client.stats["http_calls"] == 2

    # Past TTL -> refreshes
    adv3 = client.get_advisory(
        ["BNBUSDT", "LINKUSDT", "SUIUSDT"],
        bingx_prices={"BNBUSDT": 600.0},
        now_ts=1700.0,
    )
    assert len(calls) == 4
    assert adv3.status == "OK"
    assert adv3.age_sec == 0.0


def test_coingecko_client_fail_soft_returns_stale_then_unavailable_on_http_429() -> None:
    should_fail = {"flag": False}
    base_get, _ = _fake_coingecko_http()

    def _flaky_get(
        url: str, params: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        if should_fail["flag"]:
            raise RuntimeError("HTTP 429 Too Many Requests")
        return base_get(url, params, headers, timeout)

    client = CoinGeckoIntelClient(
        ttl_sec=60.0,
        stale_max_sec=300.0,
        http_get_override=_flaky_get,
    )
    ok_adv = client.get_advisory(["BNBUSDT"], now_ts=1000.0)
    assert ok_adv.status == "OK"

    # Trigger 429 past TTL but within stale_max_sec -> STALE fallback
    should_fail["flag"] = True
    stale_adv = client.get_advisory(["BNBUSDT"], now_ts=1100.0)
    assert stale_adv.status == "STALE"
    assert stale_adv.total_market_cap_usd_b == ok_adv.total_market_cap_usd_b
    assert client.stats["stale_fallbacks"] == 1

    # Past stale_max_sec -> UNAVAILABLE fail-soft (never raises)
    unavail_adv = client.get_advisory(["BNBUSDT"], now_ts=1500.0)
    assert unavail_adv.status == "UNAVAILABLE"
    assert unavail_adv.total_market_cap_usd_b is None


def test_coingecko_macro_regime_classification_risk_off_and_alt_rotation() -> None:
    risk_off_get, _ = _fake_coingecko_http(mcap_chg=-2.5, btcd=58.0)
    c_off = CoinGeckoIntelClient(http_get_override=risk_off_get)
    adv_off = c_off.get_advisory(["BNBUSDT"], now_ts=100.0)
    assert adv_off.macro_regime_tag == "RISK_OFF"

    alt_rot_get, _ = _fake_coingecko_http(mcap_chg=2.2, btcd=49.5)
    c_rot = CoinGeckoIntelClient(http_get_override=alt_rot_get)
    adv_rot = c_rot.get_advisory(["BNBUSDT"], now_ts=100.0)
    assert adv_rot.macro_regime_tag == "ALT_ROTATION"

    drain_get, _ = _fake_coingecko_http(mcap_chg=-0.5, btcd=60.5)
    c_drain = CoinGeckoIntelClient(http_get_override=drain_get)
    adv_drain = c_drain.get_advisory(["BNBUSDT"], now_ts=100.0)
    assert adv_drain.macro_regime_tag == "BTC_DRAIN"


def test_external_advisory_compact_line_token_budget_and_p2_wiring_preserves_p0_p1_hash(
    tmp_path: Path,
) -> None:
    adv = ExternalAdvisoryV1(
        status="OK",
        total_market_cap_usd_b=2850.0,
        market_cap_change_24h_pct=-1.8,
        btc_dominance_pct=56.4,
        eth_dominance_pct=17.0,
        global_volume_24h_usd_b=98.0,
        macro_regime_tag="RISK_OFF",
        asset_snapshots={
            "BNBUSDT": {"ref_usd": 600.0, "bingx_basis_bps": 3.2},
            "LINKUSDT": {"ref_usd": 15.0, "bingx_basis_bps": -1.5},
            "SUIUSDT": {"ref_usd": 2.0, "bingx_basis_bps": 0.4},
        },
    )
    compact = adv.to_compact_prompt_line()
    assert compact.startswith("EXT_MACRO_V1|src=coingecko|st=OK|")
    assert len(compact) <= 180
    assert format_external_advisory_line(adv.model_dump(mode="json")) == compact
    assert format_external_advisory_line(None) == ""

    store = ThesisMemoryStore(tmp_path / "thesis_qe05.sqlite3")
    try:
        asm = PrefixContextAssembler(thesis_store=store, include_images=False)
        base_bundle = _make_4pair_bundle()
        s1_base = asm.assemble_stage1(bundle=base_bundle, trigger_summary="BASE")

        bundle_with_ext = dataclasses.replace(
            base_bundle,
            external_advisory=adv.model_dump(mode="json"),
        )
        s1_ext = asm.assemble_stage1(bundle=bundle_with_ext, trigger_summary="EXT")
        s2_ext = asm.assemble_stage2(
            bundle=bundle_with_ext,
            candidate_symbol="BTCUSDT",
            stage1_summary={"action": "DRILL_DOWN", "candidate_symbol": "BTCUSDT"},
        )
        assert s1_ext.prefix_hash == s1_base.prefix_hash
        assert s2_ext.prefix_hash == s1_base.prefix_hash
        assert compact in s1_ext.p2_dynamic
        assert compact in s2_ext.p2_dynamic

        stateless = StatelessStagePromptAssembler()
        st1 = stateless.assemble_stage1(bundle=bundle_with_ext, trigger_summary="STATELESS")
        assert compact in st1.p2_dynamic
    finally:
        store.close()


def test_qe_briefing_renders_coingecko_external_advisory_section(tmp_path: Path) -> None:
    sess_file = tmp_path / "latest_session.json"
    sess_file.write_text(
        json.dumps(
            {
                "status": "IN_PROGRESS",
                "dry_run": False,
                "transport": "agy-direct",
                "model": "gemini-4-carbon",
                "symbols": ["BNBUSDT", "LINKUSDT", "SUIUSDT"],
                "cycles_count": 1,
                "start_equity_vst": 1000.0,
                "current_equity_vst": 1005.0,
                "external_advisory": {
                    "source": "coingecko",
                    "status": "OK",
                    "macro_regime_tag": "ALT_ROTATION",
                    "total_market_cap_usd_b": 3100.0,
                    "market_cap_change_24h_pct": 2.4,
                    "btc_dominance_pct": 50.1,
                    "eth_dominance_pct": 18.2,
                    "global_volume_24h_usd_b": 125.0,
                    "asset_snapshots": {"BNBUSDT": {"ref_usd": 600.0, "bingx_basis_bps": 4.5}},
                    "age_sec": 12.0,
                },
            }
        ),
        encoding="utf-8",
    )
    payload = build_briefing_payload(
        session_json=sess_file,
        ledger_db=tmp_path / "missing_ledger.sqlite3",
        thesis_db=tmp_path / "missing_thesis.sqlite3",
        journal_path=tmp_path / "missing_journal.jsonl",
    )
    assert payload["external_advisory"]["macro_regime_tag"] == "ALT_ROTATION"
    md = render_human_briefing(payload)
    assert "## 4. Глобальний макро-контекст (CoinGecko)" in md
    assert "ALT_ROTATION" in md
    assert "50.1%" in md
    assert "BNBUSDT:+4.5bps" in md
