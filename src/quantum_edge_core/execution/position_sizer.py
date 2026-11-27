"""Deterministic ConvictionPositionSizer using 6 orthogonal risk signals + liquidation & SL-loss caps."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from quantum_edge_core.execution.risk_guard import RiskConfig


def _clip01(val: float) -> float:
    return max(0.0, min(1.0, float(val)))


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Result of ConvictionPositionSizer for auditability and HarnessBridge submission."""

    risk_score: float
    conviction: float
    components: dict[str, float]
    notional_vst: float
    margin_vst: float
    leverage: int
    sl_dist_bps: float
    est_loss_at_sl_vst: float
    clamped_from_llm_margin: float | None = None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "risk_score": self.risk_score,
            "conviction": self.conviction,
            "components": dict(self.components),
            "notional_vst": self.notional_vst,
            "margin_vst": self.margin_vst,
            "leverage": self.leverage,
            "sl_dist_bps": self.sl_dist_bps,
            "est_loss_at_sl_vst": self.est_loss_at_sl_vst,
            "clamped_from_llm_margin": self.clamped_from_llm_margin,
        }


class ConvictionPositionSizer:
    """Sizes ISOLATED margin & leverage from 6 orthogonal risk factors with SL-before-liquidation guarantee."""

    WEIGHTS: dict[str, float] = {
        "regime_alignment_risk": 0.20,
        "volatility_tail_risk": 0.20,
        "microstructure_risk": 0.20,
        "liquidity_spread_risk": 0.15,
        "barrier_edge_risk": 0.15,
        "model_confidence_risk": 0.10,
    }

    _MATURITY_PENALTY: dict[str, float] = {
        "EARLY": 0.0,
        "ESTABLISHED": 0.05,
        "MATURE": 0.15,
        "EXHAUSTION": 0.35,
    }

    def __init__(self, *, sl_liq_buffer_factor: float = 0.75, round_trip_fee_bps: float = 10.0) -> None:
        self.sl_liq_buffer_factor = float(sl_liq_buffer_factor)
        self.round_trip_fee_bps = float(round_trip_fee_bps)

    @staticmethod
    def _extract_forecast_dict(forecast: Any | None) -> dict[str, Any]:
        if forecast is None:
            return {}
        if hasattr(forecast, "model_dump"):
            return dict(forecast.model_dump())
        if isinstance(forecast, Mapping):
            return dict(forecast)
        return {}

    @staticmethod
    def _extract_1d_di_diff(ctx: Mapping[str, Any]) -> float | None:
        indicators = ctx.get("indicators")
        if isinstance(indicators, Mapping):
            for key in ("1d_di_diff", "di_diff_1d", "di_diff"):
                if indicators.get(key) is not None:
                    try:
                        return float(indicators[key])
                    except (TypeError, ValueError):
                        pass
        prof_map = ctx.get("structural_profiles")
        if isinstance(prof_map, Mapping) and isinstance(prof_map.get("1d"), Mapping):
            val = prof_map["1d"].get("di_diff")
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        bars_1d = ((ctx.get("ohlcv_by_tf") or {}).get("1d")) or []
        if bars_1d:
            try:
                from quantum_edge_core.execution.market_context_serializer import (
                    MarketContextSerializer,
                )

                prof = MarketContextSerializer().build_structural_profile("1d", bars_1d)
                if prof.get("di_diff") is not None:
                    return float(prof["di_diff"])
            except Exception:
                return None
        return None

    def compute_risk_score(
        self,
        *,
        ctx: Mapping[str, Any],
        forecast: Any | None,
        decision: Any,
        current_price: float | None = None,
        market_leader: Mapping[str, Any] | None = None,
    ) -> tuple[float, dict[str, float]]:
        """Evaluate composite risk_score in [0, 1] from 6 orthogonal factors."""
        fc = self._extract_forecast_dict(forecast)
        action = str(getattr(decision, "action", "HOLD")).upper()
        side = "LONG" if "LONG" in action else ("SHORT" if "SHORT" in action else "FLAT")
        d_sign = 1.0 if side == "LONG" else (-1.0 if side == "SHORT" else 0.0)

        snap = ctx.get("market_snapshot") or {}
        ref_price = float(
            getattr(decision, "entry_price", None)
            or current_price
            or snap.get("last_price")
            or 0.0
        )
        sl_price = getattr(decision, "sl_price", None)
        tp_price = getattr(decision, "tp_price", None)
        sl_dist = (
            abs(ref_price - float(sl_price)) / ref_price
            if (ref_price > 0 and sl_price is not None)
            else 0.015
        )
        sl_dist_bps = max(1.0, sl_dist * 10_000.0)
        tp_dist_bps = (
            abs(float(tp_price) - ref_price) / ref_price * 10_000.0
            if (ref_price > 0 and tp_price is not None)
            else max(40.0, 2.0 * sl_dist_bps)
        )
        spread_bps = max(0.0, float(snap.get("spread_bps") or 0.0))
        micro = ctx.get("microstructure_and_liquidity") or {}

        # 1. Regime alignment & maturity risk (weight 0.20) + Macro-Beta BTC filter (QE-04)
        if fc and isinstance(fc.get("regime_probs"), Mapping):
            rp = fc["regime_probs"]
            p_fav = float(rp.get("UP" if side == "LONG" else "DOWN", 0.33))
            p_adv = float(rp.get("DOWN" if side == "LONG" else "UP", 0.33))
            base_reg = _clip01(0.5 + 0.5 * (p_adv - p_fav))
            mat_pen = self._MATURITY_PENALTY.get(str(fc.get("regime_maturity") or "ESTABLISHED"), 0.05)
            regime_alignment_risk = _clip01(base_reg + mat_pen)
        else:
            regime_alignment_risk = 0.45

        if isinstance(market_leader, Mapping) and side in ("LONG", "SHORT"):
            btc_4h = float(market_leader.get("chg_4h_bps") or 0.0)
            di_1d = self._extract_1d_di_diff(ctx)
            if btc_4h <= -150.0:
                if side == "LONG" and di_1d is not None and di_1d < 0.0:
                    regime_alignment_risk = _clip01(regime_alignment_risk + 0.25)
                elif side == "SHORT":
                    regime_alignment_risk = _clip01(regime_alignment_risk - 0.10)
            elif btc_4h >= 150.0:
                if side == "SHORT" and di_1d is not None and di_1d > 0.0:
                    regime_alignment_risk = _clip01(regime_alignment_risk + 0.25)
                elif side == "LONG":
                    regime_alignment_risk = _clip01(regime_alignment_risk - 0.10)

        # 2. Volatility & MAE tail risk (weight 0.20)
        if fc:
            vol_z = max(0.0, float(fc.get("vol_zscore", 0.0)))
            z_part = _clip01(vol_z / 3.0)
            mae_q90 = abs(float((fc.get("expected_mae_bps") or {}).get("q90", sl_dist_bps * 0.5)))
            mae_ratio = _clip01(mae_q90 / max(sl_dist_bps, 1.0))
            volatility_tail_risk = _clip01(0.45 * z_part + 0.55 * mae_ratio)
        else:
            volatility_tail_risk = 0.40

        # 3. Microstructure orderflow + composite alignment risk (weight 0.20)
        of_imb = float((micro.get("orderflow") or {}).get("imbalance") or 0.0)
        comp_score = float((micro.get("composite_signal") or {}).get("score") or 0.0)
        imb_risk = 0.5 * (1.0 - max(-1.0, min(1.0, d_sign * of_imb)))
        comp_risk = 0.5 * (1.0 - max(-1.0, min(1.0, d_sign * (comp_score / 100.0))))
        microstructure_risk = _clip01(0.5 * imb_risk + 0.5 * comp_risk)

        # 4. Spread friction + LiqMap magnet risk (weight 0.15)
        spread_ratio = _clip01((spread_bps / max(tp_dist_bps, 1.0)) * 2.0)
        liq = micro.get("liqmap") or {}
        mag_risk = 0.50
        if liq.get("status") == "OK":
            mag_key = "nearest_short_liq" if side == "LONG" else "nearest_long_liq"
            mag = liq.get(mag_key)
            if isinstance(mag, Mapping) and mag.get("dist_pct") is not None:
                mag_dist_bps = max(0.0, abs(float(mag["dist_pct"])) * 100.0)
                mag_risk = _clip01(0.20 if mag_dist_bps <= tp_dist_bps * 1.5 else 0.50)
            else:
                mag_risk = 0.45
        liquidity_spread_risk = _clip01(0.65 * spread_ratio + 0.35 * mag_risk)

        # 5. Triple-barrier edge risk (weight 0.15)
        if fc and isinstance(fc.get("barrier_probs"), Mapping):
            bp = fc["barrier_probs"]
            p_fav_first = float(bp.get("up_first" if side == "LONG" else "down_first", 0.33))
            barrier_edge_risk = _clip01(1.0 - p_fav_first)
        else:
            barrier_edge_risk = 0.45

        # 6. Model confidence risk (weight 0.10)
        conf = _clip01(float(getattr(decision, "confidence", 0.5) or 0.5))
        model_confidence_risk = _clip01(1.0 - conf)

        components = {
            "regime_alignment_risk": round(regime_alignment_risk, 4),
            "volatility_tail_risk": round(volatility_tail_risk, 4),
            "microstructure_risk": round(microstructure_risk, 4),
            "liquidity_spread_risk": round(liquidity_spread_risk, 4),
            "barrier_edge_risk": round(barrier_edge_risk, 4),
            "model_confidence_risk": round(model_confidence_risk, 4),
        }
        total_risk = round(
            _clip01(sum(self.WEIGHTS[k] * components[k] for k in self.WEIGHTS)),
            4,
        )
        return total_risk, components

    def size_order(
        self,
        *,
        decision: Any,
        ctx: Mapping[str, Any],
        forecast: Any | None,
        risk_config: RiskConfig,
        current_price: float | None = None,
        market_leader: Mapping[str, Any] | None = None,
    ) -> SizingResult:
        """Compute risk-scaled ISOLATED margin and leverage guaranteeing SL before liquidation."""
        risk_score, components = self.compute_risk_score(
            ctx=ctx,
            forecast=forecast,
            decision=decision,
            current_price=current_price,
            market_leader=market_leader,
        )
        action = str(getattr(decision, "action", "HOLD")).upper()
        if action not in ("OPEN_LONG", "OPEN_SHORT"):
            return SizingResult(
                risk_score=risk_score,
                conviction=round(1.0 - risk_score, 4),
                components=components,
                notional_vst=0.0,
                margin_vst=float(getattr(decision, "margin_vst", 0.0) or 0.0),
                leverage=int(getattr(decision, "leverage", 1) or 1),
                sl_dist_bps=0.0,
                est_loss_at_sl_vst=0.0,
                clamped_from_llm_margin=None,
            )

        snap = ctx.get("market_snapshot") or {}
        ref_price = float(
            getattr(decision, "entry_price", None)
            or current_price
            or snap.get("last_price")
            or 0.0
        )
        sl_price = getattr(decision, "sl_price", None)
        sl_dist = (
            abs(ref_price - float(sl_price)) / ref_price
            if (ref_price > 0 and sl_price is not None)
            else 0.015
        )
        spread_bps = max(0.0, float(snap.get("spread_bps") or 0.0))
        cost_frac = (spread_bps + self.round_trip_fee_bps) / 10_000.0

        conviction = max(0.10, min(0.95, 1.0 - risk_score))
        notional_target = conviction * float(risk_config.max_notional_per_pair_vst)

        safe_leverage = max(
            1,
            min(
                int(risk_config.max_leverage),
                int(math.floor(self.sl_liq_buffer_factor / max(sl_dist, 1e-4))),
            ),
        )
        notional_liq_cap = float(risk_config.max_margin_vst) * float(safe_leverage)
        max_loss_at_sl_vst = float(risk_config.max_margin_vst) * (0.10 + 0.40 * conviction)
        notional_stop_cap = max_loss_at_sl_vst / max(sl_dist + cost_frac, 1e-4)

        final_notional = min(
            notional_target,
            notional_liq_cap,
            notional_stop_cap,
            float(risk_config.max_notional_per_pair_vst),
        )
        final_leverage = safe_leverage
        max_margin_by_notional = float(risk_config.max_notional_per_pair_vst) / float(final_leverage)
        raw_margin = min(float(risk_config.max_margin_vst), max_margin_by_notional, final_notional / float(final_leverage))
        # Floor to 2 decimals so margin * leverage never rounds above cap
        final_margin_vst = max(1.0, math.floor(raw_margin * 100.0) / 100.0)
        actual_notional = round(final_margin_vst * float(final_leverage), 2)
        est_loss_at_sl = round(actual_notional * (sl_dist + cost_frac), 2)

        llm_margin_raw = getattr(decision, "margin_vst", None)
        clamped_from: float | None = None
        if llm_margin_raw is not None and abs(float(llm_margin_raw) - final_margin_vst) > 1e-6:
            clamped_from = float(llm_margin_raw)

        return SizingResult(
            risk_score=round(risk_score, 4),
            conviction=round(conviction, 4),
            components=components,
            notional_vst=actual_notional,
            margin_vst=final_margin_vst,
            leverage=final_leverage,
            sl_dist_bps=round(sl_dist * 10_000.0, 2),
            est_loss_at_sl_vst=est_loss_at_sl,
            clamped_from_llm_margin=clamped_from,
        )


__all__ = ["ConvictionPositionSizer", "SizingResult"]
