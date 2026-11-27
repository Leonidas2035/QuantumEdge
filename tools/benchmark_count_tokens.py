#!/usr/bin/env python3
"""Offline/hybrid CountTokens benchmark for QuantumEdge Phase 2 serialization modes.

Default ``--counter local-sp`` runs 100% offline with zero API keys and zero
OAuth quota usage, applying Gemini SentencePiece ``split_digits=True`` rules
plus official ``GEMINI4_IMAGE_TOKENS`` budgets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from quantum_edge_core.execution.market_context_serializer import (  # noqa: E402
    MarketContextSerializer,
)
from quantum_edge_core.execution.storage_policy import get_ram_tmp_dir  # noqa: E402
from quantum_edge_ml.model_router.backends.transport import (  # noqa: E402
    GEMINI4_IMAGE_TOKENS,
    ImageAttachment,
)

# Deterministic Gemini SentencePiece approximation with split_digits=True:
# - Each individual digit 0-9 is 1 token
# - Whitespace/newline blocks (pretty-print indentation) collapse per chunk (~4 chars)
# - Alphanumeric identifier chunks ~4 chars per token
# - Punctuation characters ({ } [ ] : , " | .) count as 1 token each
_TOKEN_CHUNK_RE = re.compile(r"[0-9]|[A-Za-z_]+|\n\s*|[^\s0-9A-Za-z_]")


def estimate_gemini_sp_tokens(text: str) -> int:
    """Estimate Gemini SentencePiece tokens with split_digits=True (local_sp_estimate)."""
    if not text:
        return 0
    total = 0
    for m in _TOKEN_CHUNK_RE.finditer(text):
        tok = m.group(0)
        if tok.isdigit() or (len(tok) == 1 and not tok.isalnum()):
            total += 1
        elif tok.startswith("\n"):
            # Indentation spaces in pretty-printed JSON add whitespace tokens
            total += max(1, (len(tok) - 1 + 3) // 4)
        else:
            total += max(1, (len(tok) + 3) // 4)
    return total


def build_synthetic_enriched_context(symbol: str = "BTCUSDT") -> dict[str, Any]:
    """Generate a realistic 5-TF, 44-level liqmap, top-15 L2 synthetic fixture."""
    base_ts = 1_795_641_840_000
    base_p = 60200.0
    ohlcv_by_tf: dict[str, list[dict[str, Any]]] = {}
    tf_steps = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
    for tf, step in tf_steps.items():
        bars = []
        p = base_p
        for i in range(100):
            delta = ((i % 7) - 3) * 4.5
            o = round(p, 1)
            c = round(p + delta, 1)
            h = round(max(o, c) + 8.2, 1)
            l = round(min(o, c) - 6.7, 1)
            v = round(10.0 + (i % 13) * 3.1234, 4)
            bars.append(
                {
                    "ts": base_ts + i * step,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                }
            )
            p = c
        ohlcv_by_tf[tf] = bars

    zones = []
    for i in range(44):
        side = "SHORT_LIQ" if i % 2 == 0 else "LONG_LIQ"
        px = round(base_p + (i - 22) * 35.5, 2)
        zones.append(
            {
                "price": px,
                "intensity": round(0.1 + (i % 10) * 0.09, 3),
                "side": side,
                "dist_pct": round((px - base_p) / base_p * 100.0, 3),
                "confidence": 0.62,
            }
        )
    bids = [[round(base_p - i * 1.5, 1), round(0.5 + i * 0.1, 4)] for i in range(25)]
    asks = [[round(base_p + 1.5 + i * 1.5, 1), round(0.4 + i * 0.1, 4)] for i in range(25)]
    return {
        "market_snapshot": {
            "symbol": symbol,
            "bid": base_p,
            "ask": base_p + 1.5,
            "last_price": base_p + 0.8,
            "spread_bps": 0.25,
            "ts_utc": "2026-11-27T00:00:00Z",
            "source": "synthetic",
            "is_stale": False,
        },
        "ohlcv_by_tf": ohlcv_by_tf,
        "orderbook": {"bids": bids, "asks": asks, "timestamp": base_ts},
        "derivatives": {"funding_rate": 0.0001, "open_interest": 125000.0, "mark_price": base_p},
        "microstructure_and_liquidity": {
            "liqmap": {
                "status": "OK",
                "symbol": symbol,
                "timeframe": "1h",
                "regime": "flat",
                "last_price": base_p,
                "zones": zones,
                "nearest_long_liq": zones[10],
                "nearest_short_liq": zones[32],
            },
            "orderflow": {"status": "OK", "metrics": {"imbalance_top10": 0.18, "delta_source": "synthetic"}},
            "composite_signal": {"status": "OK", "signal": "LONG", "score": 47, "components": {"trend": 0.0, "momentum": 17.3}},
        },
        "account_state": {"free_margin_vst": 9000.0, "total_equity_vst": 10000.0, "open_positions": [], "open_orders": []},
        "risk_limits": {"allowed_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"], "max_leverage": 5},
    }


def _measure_roundtrip_max_err_bps(
    ser: MarketContextSerializer,
    ctx: dict[str, Any],
    mode: str,
) -> float | None:
    if mode not in ("tick_delta", "delta_bps", "multiscale", "hybrid_webp"):
        return None
    sym = str((ctx.get("market_snapshot") or {}).get("symbol") or "BTCUSDT").upper()
    bars = (ctx.get("ohlcv_by_tf") or {}).get("15m") or []
    if not bars:
        return None
    if mode in ("tick_delta", "hybrid_webp"):
        enc = ser.encode_ohlcv_tick_delta(sym, "15m", bars)
        dec = ser.decode_ohlcv_tick_delta(enc)["bars"]
        max_err = 0.0
        for orig, rec in zip(sorted(bars, key=lambda b: int(b["ts"])), dec):
            err = abs(float(rec["close"]) - float(orig["close"])) / float(orig["close"]) * 10_000.0
            max_err = max(max_err, err)
        return round(max_err, 6)
    enc = ser.encode_ohlcv_delta_bps(sym, "15m", bars)
    dec = ser.decode_ohlcv_delta_bps(enc)["bars"]
    max_err = 0.0
    for orig, rec in zip(sorted(bars, key=lambda b: int(b["ts"])), dec):
        err = abs(float(rec["close"]) - float(orig["close"])) / float(orig["close"]) * 10_000.0
        max_err = max(max_err, err)
    return round(max_err, 4)


async def _count_tokens_for_candidate(
    counter_mode: str,
    model: str,
    text: str,
    images: list[ImageAttachment],
    client: Any | None,
) -> dict[str, Any]:
    if counter_mode == "local-sp":
        t_tok = estimate_gemini_sp_tokens(text)
        i_tok = sum(GEMINI4_IMAGE_TOKENS[img.resolution] for img in images)
        return {
            "text_tokens": t_tok,
            "image_tokens_media_res": i_tok,
            "total_tokens": t_tok + i_tok,
            "source": "local_sp_estimate",
        }
    if counter_mode == "direct-api" and client is not None:
        res = await client.count_tokens(model=model, text_parts=[text], images=images)
        return {**res, "source": "provider_count_tokens"}
    if counter_mode == "agy-proxy":
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as hc:
            r = await hc.post(
                "http://127.0.0.1:8045/v1/chat/completions",
                json={
                    "model": f"{model}-low",
                    "messages": [{"role": "user", "content": f"Reply OK only.\n{text[:2000]}"}],
                },
            )
            r.raise_for_status()
            u = r.json().get("usage") or {}
            t_tok = int(u.get("prompt_tokens") or 0)
            i_tok = sum(img.token_budget for img in images)
            return {
                "text_tokens": t_tok,
                "image_tokens_media_res": i_tok,
                "total_tokens": t_tok + i_tok,
                "source": "agy_proxy_usage",
            }
    raise ValueError(f"Unsupported counter mode: {counter_mode}")


async def run_benchmark(
    *,
    fixture_path: Path,
    synthetic: bool,
    counter_mode: str,
    model: str,
    out_json: Path | None = None,
) -> dict[str, Any]:
    if synthetic or not fixture_path.is_file():
        fixture_source = "SYNTHETIC"
        ctx = build_synthetic_enriched_context()
        raw_text = json.dumps(ctx, indent=2)
    else:
        fixture_source = "REAL_BTC_105KB"
        raw_text = fixture_path.read_text(encoding="utf-8")
        ctx = json.loads(raw_text)

    ser = MarketContextSerializer()
    client = None
    ledger = None
    if counter_mode == "direct-api":
        from quantum_edge_ml.model_router.backends.google_gemini import GeminiDirectClient
        from quantum_edge_ml.model_router.usage_ledger import UsageLedger

        ledger = UsageLedger(get_ram_tmp_dir("phase2_bench") / "count_tokens_ledger.sqlite3")
        client = GeminiDirectClient(ledger)

    candidates: list[tuple[str, str, list[ImageAttachment], float]] = [
        ("json_pretty_baseline", raw_text, [], 0.0),
        ("json_minified", json.dumps(ctx, separators=(",", ":"), sort_keys=True), [], 0.0),
    ]
    for mode in ("tick_delta", "delta_bps", "multiscale", "hybrid_webp"):
        t0 = time.perf_counter()
        payload = ser.serialize_single_asset(ctx, mode=mode, ohlcv_tail=25)  # type: ignore[arg-type]
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        candidates.append((mode, payload.text, payload.images, elapsed_ms))

    rows: list[dict[str, Any]] = []
    base_tokens: int | None = None
    try:
        for name, txt, imgs, render_ms in candidates:
            counts = await _count_tokens_for_candidate(counter_mode, model, txt, imgs, client)
            tot = int(counts["total_tokens"])
            if base_tokens is None:
                base_tokens = tot
            ratio = round(base_tokens / max(tot, 1), 2)
            max_err = _measure_roundtrip_max_err_bps(ser, ctx, name)
            rows.append(
                {
                    "format": name,
                    "text_bytes": len(txt.encode("utf-8")),
                    "text_tokens": int(counts["text_tokens"]),
                    "image_tokens": int(counts["image_tokens_media_res"]),
                    "total_tokens": tot,
                    "ratio_vs_pretty": ratio,
                    "roundtrip_max_err_bps": max_err,
                    "render_ms": render_ms,
                    "counter_source": counts["source"],
                }
            )
    finally:
        if client is not None:
            await client.close()
        if ledger is not None:
            ledger.close()

    # 4-pair projection (deducting shared account_state/risk_limits overhead on 3 pairs)
    pretty_4pair = int(rows[0]["total_tokens"] * 4 * 0.92)
    hybrid_row = next(r for r in rows if r["format"] == "hybrid_webp")
    # Stage 1 screening grid: 4 compact summaries + 1 medium 2x2 grid (560 img tokens)
    stage1_4pair_tokens = int(sum(r["text_tokens"] for r in rows if r["format"] == "multiscale") * 0.65) + 560
    # Stage 2 candidate drill-down: 1 candidate hybrid_webp
    stage2_1pair_tokens = hybrid_row["total_tokens"]

    summary = {
        "fixture_source": fixture_source,
        "counter_mode": counter_mode,
        "model": model,
        "rows": rows,
        "four_pair_projection": {
            "pretty_4pair_tokens": pretty_4pair,
            "stage1_4pair_screening_tokens": stage1_4pair_tokens,
            "stage2_1pair_drill_tokens": stage2_1pair_tokens,
            "combined_2stage_tokens": stage1_4pair_tokens + stage2_1pair_tokens,
        },
    }

    target_json = out_json or (get_ram_tmp_dir("phase2_bench") / "count_tokens_bench.json")
    target_json.parent.mkdir(parents=True, exist_ok=True)
    target_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["out_json"] = str(target_json)
    return summary


def _print_table(summary: dict[str, Any]) -> None:
    print(f"Fixture: {summary['fixture_source']} | Counter: {summary['counter_mode']} ({summary['rows'][0]['counter_source']})")
    print(
        f"{'Format':<22} | {'Text B':>8} | {'Text Tok':>8} | {'Img Tok':>7} | "
        f"{'Total Tok':>9} | {'Ratio':>6} | {'MaxErr(bps)':>11} | {'Render(ms)':>10}"
    )
    print("-" * 98)
    for r in summary["rows"]:
        err_str = "-" if r["roundtrip_max_err_bps"] is None else f"{r['roundtrip_max_err_bps']:.4f}"
        print(
            f"{r['format']:<22} | {r['text_bytes']:8,d} | {r['text_tokens']:8,d} | "
            f"{r['image_tokens']:7,d} | {r['total_tokens']:9,d} | {r['ratio_vs_pretty']:5.2f}x | "
            f"{err_str:>11} | {r['render_ms']:10.2f}"
        )
    proj = summary["four_pair_projection"]
    print(
        f"\n4-Pair Projection: Pretty={proj['pretty_4pair_tokens']:,d} tok | "
        f"Stage1(4-pair+2x2WebP)={proj['stage1_4pair_screening_tokens']:,d} tok | "
        f"Stage2(1-pair hybrid)={proj['stage2_1pair_drill_tokens']:,d} tok"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="QuantumEdge Phase 2 CountTokens Serialization Benchmark")
    ap.add_argument("--fixture", type=Path, default=Path("runtime/step1_enriched_ctx_btcusdt.json"))
    ap.add_argument("--synthetic", action="store_true", help="Use synthetic 44-level liqmap 5-TF fixture")
    ap.add_argument(
        "--counter",
        choices=("local-sp", "agy-proxy", "direct-api"),
        default="local-sp",
        help="Token counter backend (default: local-sp offline estimator)",
    )
    ap.add_argument("--model", default="gemini-4-carbon")
    ap.add_argument("--out-json", type=Path, default=None)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = asyncio.run(
        run_benchmark(
            fixture_path=args.fixture,
            synthetic=args.synthetic,
            counter_mode=args.counter,
            model=args.model,
            out_json=args.out_json,
        )
    )
    _print_table(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
