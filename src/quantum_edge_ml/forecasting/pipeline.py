"""Causal Quantitative Forecasting Layer (Phase 6: HMM + EGARCH + Multi-Head LightGBM + ACI-CQR + Shadow TSFM).

Runs 100% locally on CPU/GPU with zero LLM tokens and zero look-ahead leakage,
producing a compact calibrated ForecastBundleV1 (~220-260 tokens/pair).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import warnings
from typing import Any, Callable, Literal, Mapping

import lightgbm as lgb
import numpy as np
import pandas as pd
from arch import arch_model
from hmmlearn.hmm import GaussianHMM
from scipy.special import logsumexp

from quantum_edge_core.contracts.optimization import ForecastBundleV1


def purged_walk_forward_splits(
    n_samples: int,
    *,
    n_splits: int = 5,
    label_horizon: int = 12,
    embargo_bars: int = 6,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Strictly chronological walk-forward splits with purge + embargo gap (zero label overlap)."""
    fold_size = n_samples // (n_splits + 1)
    if fold_size <= label_horizon + embargo_bars:
        raise ValueError(
            "Insufficient samples for requested n_splits / label_horizon / embargo_bars."
        )
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(1, n_splits + 1):
        test_start = k * fold_size
        test_end = min(n_samples, test_start + fold_size)
        train_end = max(0, test_start - label_horizon - embargo_bars)
        train_idx = np.arange(0, train_end, dtype=np.int64)
        test_idx = np.arange(test_start, test_end, dtype=np.int64)
        assert int(train_idx[-1]) + label_horizon < int(test_idx[0]), "Leakage detected!"
        splits.append((train_idx, test_idx))
    return splits


def compute_triple_barrier_labels(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    cond_vol_series: np.ndarray,
    *,
    horizon_steps: int = 3,
    k_up: float = 1.0,
    k_dn: float = 1.0,
) -> dict[str, np.ndarray]:
    """Compute volatility-normalized return, Triple-Barrier {-1,0,+1}, MFE (bps), and MAE (bps)."""
    c = np.asarray(closes, dtype=np.float64)
    h = np.asarray(highs, dtype=np.float64)
    l = np.asarray(lows, dtype=np.float64)
    v = np.asarray(cond_vol_series, dtype=np.float64)
    n = len(c)
    if n <= horizon_steps:
        raise ValueError(f"Need > {horizon_steps} bars to compute Triple-Barrier labels (got {n}).")

    # Auto-detect whether cond_vol_series is in bps (e.g. 25.0) or decimal return (e.g. 0.0025)
    vol_frac = (v / 10_000.0) if float(np.nanmedian(np.abs(v))) > 0.25 else v
    valid_n = n - horizon_steps

    y_zret = np.empty(valid_n, dtype=np.float64)
    y_tb = np.zeros(valid_n, dtype=np.int64)
    y_mfe_bps = np.empty(valid_n, dtype=np.float64)
    y_mae_bps = np.empty(valid_n, dtype=np.float64)

    for t in range(valid_n):
        p0 = max(c[t], 1e-12)
        sig = max(float(vol_frac[t]), 1e-4)
        up_bar = p0 * (1.0 + k_up * sig)
        dn_bar = p0 * (1.0 - k_dn * sig)

        hit_up = horizon_steps + 1
        hit_dn = horizon_steps + 1
        for j in range(1, horizon_steps + 1):
            if hit_up > horizon_steps and h[t + j] >= up_bar:
                hit_up = j
            if hit_dn > horizon_steps and l[t + j] <= dn_bar:
                hit_dn = j

        if hit_up < hit_dn:
            y_tb[t] = 1
        elif hit_dn < hit_up:
            y_tb[t] = -1
        elif hit_up <= horizon_steps and hit_up == hit_dn:
            y_tb[t] = 1 if c[t + hit_up] >= p0 else -1
        else:
            y_tb[t] = 0

        ret_h = (c[t + horizon_steps] - p0) / p0
        y_zret[t] = ret_h / sig
        max_hi = float(np.max(h[t + 1 : t + horizon_steps + 1]))
        min_lo = float(np.min(l[t + 1 : t + horizon_steps + 1]))
        y_mfe_bps[t] = max(0.0, (max_hi - p0) / p0 * 10_000.0)
        y_mae_bps[t] = min(0.0, (min_lo - p0) / p0 * 10_000.0)

    return {
        "y_zret": y_zret,
        "y_tb": y_tb,
        "y_mfe_bps": y_mfe_bps,
        "y_mae_bps": y_mae_bps,
    }


def compute_calibration_metrics(
    y_true_probs: np.ndarray,
    y_pred_probs: np.ndarray,
    y_true_vals: np.ndarray,
    quantile_preds_dict: Mapping[float | str, np.ndarray],
    *,
    n_bins: int = 10,
) -> dict[str, float]:
    """Compute Brier score, LogLoss, Pinball loss, CRPS approx, and 10-bin ECE."""
    pred_p = np.asarray(y_pred_probs, dtype=np.float64)
    true_raw = np.asarray(y_true_probs)

    if pred_p.ndim == 1:
        p_clip = np.clip(pred_p, 1e-12, 1.0 - 1e-12)
        y_bin = true_raw.astype(np.float64)
        brier = float(np.mean((p_clip - y_bin) ** 2))
        logloss = float(-np.mean(y_bin * np.log(p_clip) + (1.0 - y_bin) * np.log(1.0 - p_clip)))
        conf = np.where(p_clip >= 0.5, p_clip, 1.0 - p_clip)
        correct = ((p_clip >= 0.5).astype(np.float64) == y_bin).astype(np.float64)
    else:
        n_rows, n_cls = pred_p.shape
        p_clip = np.clip(pred_p, 1e-12, 1.0)
        p_clip = p_clip / np.sum(p_clip, axis=1, keepdims=True)
        if true_raw.ndim == 1:
            onehot = np.zeros((n_rows, n_cls), dtype=np.float64)
            onehot[np.arange(n_rows), true_raw.astype(np.int64)] = 1.0
            true_idx = true_raw.astype(np.int64)
        else:
            onehot = true_raw.astype(np.float64)
            true_idx = np.argmax(onehot, axis=1)
        brier = float(np.mean(np.sum((p_clip - onehot) ** 2, axis=1)))
        logloss = float(-np.mean(np.sum(onehot * np.log(p_clip), axis=1)))
        pred_idx = np.argmax(p_clip, axis=1)
        conf = np.max(p_clip, axis=1)
        correct = (pred_idx == true_idx).astype(np.float64)

    # 10-bin Expected Calibration Error (ECE)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n_tot = max(len(conf), 1)
    for b in range(n_bins):
        lo, hi = bins[b], bins[b + 1]
        mask = (conf >= lo) & (conf <= hi if b == n_bins - 1 else conf < hi)
        cnt = int(np.sum(mask))
        if cnt > 0:
            acc_b = float(np.mean(correct[mask]))
            conf_b = float(np.mean(conf[mask]))
            ece += (cnt / n_tot) * abs(acc_b - conf_b)

    # Pinball loss & CRPS approximation over quantile grid
    y_val = np.asarray(y_true_vals, dtype=np.float64)
    parsed_q: list[tuple[float, np.ndarray]] = []
    for k, q_arr in quantile_preds_dict.items():
        tau = (
            float(k)
            if isinstance(k, (int, float))
            else float(str(k).lstrip("qQ")) / (100.0 if float(str(k).lstrip("qQ")) > 1.0 else 1.0)
        )
        parsed_q.append((tau, np.asarray(q_arr, dtype=np.float64)))
    parsed_q.sort(key=lambda item: item[0])

    pinballs: list[float] = []
    for tau, q_hat in parsed_q:
        u = y_val - q_hat
        pb = float(np.mean(np.maximum(tau * u, (tau - 1.0) * u)))
        pinballs.append(pb)

    pinball_mean = float(np.mean(pinballs)) if pinballs else 0.0
    if len(parsed_q) >= 2:
        taus = np.array([t for t, _ in parsed_q], dtype=np.float64)
        crps = float(2.0 * np.trapezoid(pinballs, taus) / max(taus[-1] - taus[0], 1e-6))
    else:
        crps = 2.0 * pinball_mean

    return {
        "brier_score": round(brier, 6),
        "log_loss": round(logloss, 6),
        "pinball_loss": round(pinball_mean, 6),
        "crps_approx": round(crps, 6),
        "ece": round(ece, 6),
    }


class CausalHMMFilter:
    """Strictly causal forward-only HMM filter P(S_t | O_{1:t}) in log-space (zero Baum-Welch smoothing)."""

    def __init__(self, n_states: int = 3, random_state: int = 42) -> None:
        self.n_states = n_states
        self.model = GaussianHMM(
            n_components=n_states,
            covariance_type="diag",
            n_iter=100,
            random_state=random_state,
        )
        self._state_order: list[int] = list(range(n_states))
        self._is_fitted = False

    def fit(self, X_train: np.ndarray) -> CausalHMMFilter:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.model.fit(np.asarray(X_train, dtype=np.float64))
        self._state_order = list(np.argsort(self.model.means_[:, 0]))  # DOWN < RANGE < UP
        self._is_fitted = True
        return self

    def filter_causal(self, X: np.ndarray) -> np.ndarray:
        """Forward recursion in log-space: step t depends strictly on X[:t+1]."""
        if not self._is_fitted:
            raise RuntimeError("CausalHMMFilter is not fitted.")
        X_arr = np.asarray(X, dtype=np.float64)
        log_lik = self.model._compute_log_likelihood(X_arr)
        log_start = np.log(np.clip(self.model.startprob_, 1e-300, 1.0))
        log_trans = np.log(np.clip(self.model.transmat_, 1e-300, 1.0))
        t_len, k_states = log_lik.shape
        post = np.empty((t_len, k_states), dtype=np.float64)

        log_alpha = log_start + log_lik[0]
        log_alpha -= logsumexp(log_alpha)
        post[0] = np.exp(log_alpha)
        for t in range(1, t_len):
            log_pred = logsumexp(log_alpha[:, None] + log_trans, axis=0)
            log_alpha = log_pred + log_lik[t]
            log_alpha -= logsumexp(log_alpha)
            post[t] = np.exp(log_alpha)
        return post[:, self._state_order]


class EGARCHVolatilityModel:
    """EGARCH(1,1,1) conditional volatility model (uses simulation for horizon_steps > 1)."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def forecast_vol_bps(
        self,
        log_returns: np.ndarray,
        *,
        horizon_steps: int = 3,
        simulations: int = 500,
    ) -> tuple[float, float]:
        arr = np.asarray(log_returns, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 80 or float(np.std(arr)) < 1e-10:
            s = (
                float(np.std(arr[-30:])) * math.sqrt(max(horizon_steps, 1)) * 10_000.0
                if len(arr) >= 5
                else 20.0
            )
            return round(max(s, 5.0), 2), 0.0

        scaled = arr[-400:] * 100.0
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                am = arch_model(
                    scaled,
                    mean="Zero",
                    vol="EGARCH",
                    p=1,
                    o=1,
                    q=1,
                    dist="t",
                    rescale=False,
                )
                res = am.fit(disp="off", show_warning=False)
                method: Literal["analytic", "simulation"] = (
                    "analytic" if horizon_steps == 1 else "simulation"
                )
                rs = np.random.RandomState(self.seed)
                fc = res.forecast(
                    horizon=horizon_steps,
                    method=method,
                    simulations=simulations,
                    random_state=rs,
                )
            cum_var_pct2 = float(np.sum(fc.variance.iloc[-1].values))
            if not math.isfinite(cum_var_pct2) or cum_var_pct2 <= 0:
                raise ValueError("Non-finite EGARCH variance forecast")
            vol_bps = math.sqrt(max(cum_var_pct2, 1e-8)) * 100.0
        except Exception:
            vol_bps = max(float(np.std(scaled[-30:])) * math.sqrt(horizon_steps) * 100.0, 5.0)

        hist_vol = float(np.std(scaled[-96:])) * math.sqrt(horizon_steps) * 100.0
        vol_z = (vol_bps - hist_vol) / max(0.25 * hist_vol, 1.0)
        return round(vol_bps, 2), round(vol_z, 3)


@dataclass(slots=True)
class AdaptiveConformalCalibrator:
    """Online Adaptive Conformal Inference (Gibbs & Candes, 2021) + CQR."""

    alpha_target: float = 0.20
    gamma: float = 0.02
    window: int = 100
    alpha_t: float = 0.20
    scores: deque[float] = field(default_factory=lambda: deque(maxlen=100))

    def __post_init__(self) -> None:
        if self.scores.maxlen != self.window:
            self.scores = deque(self.scores, maxlen=self.window)

    def adjust_interval(self, q_lo: float, q_hi: float) -> tuple[float, float]:
        if len(self.scores) < 20:
            return q_lo, q_hi
        arr = np.asarray(self.scores, dtype=np.float64)
        q_lvl = float(np.clip(1.0 - self.alpha_t, 0.0, 1.0))
        n = len(arr)
        adj_lvl = min(1.0, math.ceil((n + 1) * q_lvl) / n) if q_lvl < 1.0 else 1.0
        q_hat = (
            float(np.quantile(arr, adj_lvl, method="higher"))
            if adj_lvl < 1.0
            else float(np.max(arr))
        )
        return q_lo - q_hat, q_hi + q_hat

    def update(self, y_true: float, q_lo_raw: float, q_hi_raw: float) -> None:
        lo_adj, hi_adj = self.adjust_interval(q_lo_raw, q_hi_raw)
        err_t = 0.0 if (lo_adj <= y_true <= hi_adj) else 1.0
        self.scores.append(max(q_lo_raw - y_true, y_true - q_hi_raw))
        self.alpha_t = float(
            np.clip(self.alpha_t + self.gamma * (self.alpha_target - err_t), 0.005, 0.995)
        )


class ShadowTSFMChallenger:
    """Fail-soft shadow wrapper for Chronos-2 / Kronos disagreement scoring (zero execution authority)."""

    def __init__(
        self,
        backend: Literal["none", "chronos2", "kronos"] = "none",
        device: str = "cpu",
        mock_predictor_fn: Callable[[pd.DataFrame, int], float] | None = None,
    ) -> None:
        self.backend = backend
        self.device = device
        self._mock_predictor_fn = mock_predictor_fn
        self._pipe: Any = None

    def lazy_load(self) -> bool:
        if self._mock_predictor_fn is not None:
            return True
        if self.backend == "none":
            return False
        if self._pipe is not None:
            return True
        try:
            if self.backend == "chronos2":
                from chronos import Chronos2Pipeline  # type: ignore[import-not-found]

                self._pipe = Chronos2Pipeline.from_pretrained(
                    "amazon/chronos-2", device_map=self.device
                )
                return True
            if self.backend == "kronos":
                from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore[import-not-found]

                tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
                mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
                self._pipe = KronosPredictor(mdl, tok, device=self.device)
                return True
        except Exception:
            self._pipe = None
        return False

    def compute_disagreement(
        self,
        *,
        lgbm_q50_bps: float,
        ohlcv_df: pd.DataFrame,
        horizon_steps: int = 3,
        cond_vol_bps: float = 30.0,
    ) -> float:
        if not self.lazy_load() or len(ohlcv_df) < 32:
            return 0.0
        try:
            last_p = float(ohlcv_df["close"].iloc[-1])
            if self._mock_predictor_fn is not None:
                tsfm_p50 = float(self._mock_predictor_fn(ohlcv_df, horizon_steps))
            elif self.backend == "chronos2":
                df = pd.DataFrame(
                    {
                        "item_id": "asset",
                        "timestamp": pd.date_range(
                            "2026-01-01", periods=len(ohlcv_df), freq="5min"
                        ),
                        "target": ohlcv_df["close"].values.astype(np.float64),
                    }
                )
                pred = self._pipe.predict_df(
                    df,
                    prediction_length=horizon_steps,
                    quantile_levels=[0.1, 0.5, 0.9],
                    id_column="item_id",
                    timestamp_column="timestamp",
                    target="target",
                )
                tsfm_p50 = float(pred["0.5"].iloc[-1])
            else:
                x_ts = pd.Series(
                    pd.date_range("2026-01-01", periods=len(ohlcv_df), freq="5min")
                )
                y_ts = pd.Series(
                    pd.date_range(
                        x_ts.iloc[-1] + pd.Timedelta(minutes=5),
                        periods=horizon_steps,
                        freq="5min",
                    )
                )
                pred = self._pipe.predict(
                    df=ohlcv_df[["open", "high", "low", "close", "volume"]],
                    x_timestamp=x_ts,
                    y_timestamp=y_ts,
                    pred_len=horizon_steps,
                )
                tsfm_p50 = float(pred["close"].iloc[-1])
            tsfm_q50_bps = (tsfm_p50 - last_p) / max(last_p, 1e-12) * 10_000.0
            return float(
                np.clip(
                    abs(lgbm_q50_bps - tsfm_q50_bps) / max(2.0 * cond_vol_bps, 10.0),
                    0.0,
                    1.0,
                )
            )
        except Exception:
            return 0.0


class MultiHeadLGBMForecaster:
    """Multi-head LightGBM: 7 sorted quantiles + 3-class Triple Barrier + MFE/MAE quantiles."""

    QUANTILES: tuple[float, ...] = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)

    def __init__(self, random_state: int = 42, n_estimators: int = 80) -> None:
        self.random_state = random_state
        self.n_estimators = n_estimators
        self.q_models: dict[float, lgb.LGBMRegressor] = {}
        self.tb_model: lgb.LGBMClassifier | None = None
        self._present_tb_classes: np.ndarray = np.array([0, 1, 2], dtype=np.int64)
        self.mfe_models: dict[float, lgb.LGBMRegressor] = {}
        self.mae_models: dict[float, lgb.LGBMRegressor] = {}

    def fit_causal(
        self,
        X_train: np.ndarray,
        y_zret: np.ndarray,
        y_tb: np.ndarray,
        y_mfe_bps: np.ndarray,
        y_mae_bps: np.ndarray,
    ) -> MultiHeadLGBMForecaster:
        X_arr = np.asarray(X_train, dtype=np.float64)
        min_child = max(5, min(20, len(X_arr) // 5))
        base_kw: dict[str, Any] = dict(
            n_estimators=self.n_estimators,
            learning_rate=0.05,
            num_leaves=15,
            min_child_samples=min_child,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=self.random_state,
            n_jobs=1,
            verbose=-1,
        )
        for tau in self.QUANTILES:
            self.q_models[tau] = lgb.LGBMRegressor(
                objective="quantile", alpha=tau, **base_kw
            ).fit(X_arr, y_zret)

        y_enc = np.asarray(y_tb, dtype=np.int64) + 1  # {-1,0,+1} -> {0,1,2}
        present = np.unique(y_enc)
        self._present_tb_classes = present
        if len(present) == 3:
            self.tb_model = lgb.LGBMClassifier(
                objective="multiclass", num_class=3, **base_kw
            ).fit(X_arr, y_enc)
        elif len(present) == 2:
            self.tb_model = lgb.LGBMClassifier(objective="binary", **base_kw).fit(
                X_arr, (y_enc == present[1]).astype(np.int64)
            )
        else:
            self.tb_model = None

        for tau in (0.50, 0.90):
            self.mfe_models[tau] = lgb.LGBMRegressor(
                objective="quantile", alpha=tau, **base_kw
            ).fit(X_arr, y_mfe_bps)
            self.mae_models[tau] = lgb.LGBMRegressor(
                objective="quantile", alpha=tau, **base_kw
            ).fit(X_arr, y_mae_bps)
        return self

    def predict_row(self, x_row: np.ndarray, cond_vol_bps: float) -> dict[str, Any]:
        row_2d = np.asarray(x_row, dtype=np.float64).reshape(1, -1)
        raw_q = np.sort(
            [float(self.q_models[tau].predict(row_2d)[0]) for tau in self.QUANTILES]
        )
        q_bps = {
            f"q{int(tau * 100):02d}": round(float(val) * cond_vol_bps, 1)
            for tau, val in zip(self.QUANTILES, raw_q)
        }

        probs_3 = np.zeros(3, dtype=np.float64)
        if self.tb_model is None and len(self._present_tb_classes) == 1:
            probs_3[int(self._present_tb_classes[0])] = 1.0
        elif self.tb_model is not None and len(self._present_tb_classes) == 2:
            p_bin = self.tb_model.predict_proba(row_2d)[0]
            probs_3[int(self._present_tb_classes[0])] = float(p_bin[0])
            probs_3[int(self._present_tb_classes[1])] = float(p_bin[1])
        elif self.tb_model is not None:
            probs_3 = np.asarray(self.tb_model.predict_proba(row_2d)[0], dtype=np.float64)
        else:
            probs_3 = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=np.float64)

        p_sum = float(np.sum(probs_3))
        if p_sum > 0:
            probs_3 = probs_3 / p_sum

        mfe50, mfe90 = sorted(
            max(0.0, float(self.mfe_models[t].predict(row_2d)[0])) for t in (0.50, 0.90)
        )
        mae90, mae50 = sorted(
            min(0.0, float(self.mae_models[t].predict(row_2d)[0])) for t in (0.50, 0.90)
        )
        # Re-normalize rounded probabilities so sum == 1.000
        p_dn = round(float(probs_3[0]), 3)
        p_up = round(float(probs_3[2]), 3)
        p_ne = round(max(0.0, 1.0 - p_dn - p_up), 3)
        return {
            "quantiles_bps": q_bps,
            "barrier_probs": {
                "down_first": p_dn,
                "neither": p_ne,
                "up_first": p_up,
            },
            "mfe_bps": {"q50": round(mfe50, 1), "q90": round(mfe90, 1)},
            "mae_bps": {"q50": round(mae50, 1), "q90": round(mae90, 1)},
        }


class ForecastPipeline:
    """Unified facade generating ForecastBundleV1 (CausalHMM + EGARCH + LGBM + ACI-CQR + Shadow TSFM)."""

    def __init__(
        self,
        *,
        hmm: CausalHMMFilter,
        egarch: EGARCHVolatilityModel,
        lgbm: MultiHeadLGBMForecaster,
        aci: AdaptiveConformalCalibrator,
        shadow_tsfm: ShadowTSFMChallenger | None = None,
    ) -> None:
        self.hmm = hmm
        self.egarch = egarch
        self.lgbm = lgbm
        self.aci = aci
        self.shadow_tsfm = shadow_tsfm or ShadowTSFMChallenger(backend="none")

    @staticmethod
    def build_causal_features(ohlcv_df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Extract strictly backward-looking HMM (T, 3) and LightGBM (T, 8) feature matrices."""
        closes = pd.Series(ohlcv_df["close"].values.astype(np.float64))
        opens = pd.Series(ohlcv_df["open"].values.astype(np.float64))
        highs = pd.Series(ohlcv_df["high"].values.astype(np.float64))
        lows = pd.Series(ohlcv_df["low"].values.astype(np.float64))
        vols = pd.Series(ohlcv_df["volume"].values.astype(np.float64))

        log_p = np.log(np.maximum(closes, 1e-12))
        ret_1 = log_p.diff(1).fillna(0.0)
        ret_3 = log_p.diff(3).fillna(0.0)
        ret_6 = log_p.diff(6).fillna(0.0)
        ret_12 = log_p.diff(12).fillna(0.0)

        vol_6 = ret_1.rolling(6, min_periods=1).std(ddof=0).fillna(0.0005)
        vol_10 = ret_1.rolling(10, min_periods=1).std(ddof=0).fillna(0.0005)
        vol_20 = ret_1.rolling(20, min_periods=1).std(ddof=0).fillna(0.0005)

        rng_frac = ((highs - lows) / np.maximum(closes, 1e-12)).fillna(0.0)
        body_ratio = ((closes - opens) / np.maximum(highs - lows, 1e-12)).clip(-1.0, 1.0).fillna(0.0)
        vol_ma20 = vols.rolling(20, min_periods=1).mean().replace(0.0, 1.0)
        vol_rel = (vols / vol_ma20).fillna(1.0)

        hmm_X = np.column_stack([ret_1.values, vol_10.values, rng_frac.values])
        lgbm_X = np.column_stack(
            [
                ret_1.values,
                ret_3.values,
                ret_6.values,
                ret_12.values,
                vol_6.values,
                vol_20.values,
                body_ratio.values,
                vol_rel.values,
            ]
        )
        return hmm_X, lgbm_X

    @classmethod
    def fit_from_ohlcv(
        cls,
        ohlcv_df: pd.DataFrame,
        *,
        horizon_steps: int = 3,
        random_state: int = 42,
        shadow_tsfm: ShadowTSFMChallenger | None = None,
    ) -> ForecastPipeline:
        """Fit a complete causal ForecastPipeline on historical OHLCV bars."""
        hmm_X, lgbm_X = cls.build_causal_features(ohlcv_df)
        closes = ohlcv_df["close"].values.astype(np.float64)
        highs = ohlcv_df["high"].values.astype(np.float64)
        lows = ohlcv_df["low"].values.astype(np.float64)

        # Causal backward rolling volatility for barrier scaling during training
        ret_s = pd.Series(np.log(np.maximum(closes, 1e-12))).diff().fillna(0.0)
        cond_vol_train = (
            ret_s.rolling(20, min_periods=2).std(ddof=0).fillna(0.001).values
            * math.sqrt(horizon_steps)
        )
        labels = compute_triple_barrier_labels(
            closes, highs, lows, cond_vol_train, horizon_steps=horizon_steps
        )
        valid_n = len(labels["y_zret"])

        hmm = CausalHMMFilter(n_states=3, random_state=random_state).fit(hmm_X[:valid_n])
        egarch = EGARCHVolatilityModel(seed=random_state)
        lgbm = MultiHeadLGBMForecaster(random_state=random_state).fit_causal(
            lgbm_X[:valid_n],
            labels["y_zret"],
            labels["y_tb"],
            labels["y_mfe_bps"],
            labels["y_mae_bps"],
        )
        aci = AdaptiveConformalCalibrator()
        return cls(hmm=hmm, egarch=egarch, lgbm=lgbm, aci=aci, shadow_tsfm=shadow_tsfm)

    def predict_bundle(
        self,
        *,
        symbol: str,
        ohlcv_df: pd.DataFrame,
        hmm_features: np.ndarray | None = None,
        lgbm_feature_row: np.ndarray | None = None,
        horizon: Literal["5m", "15m", "1h"] = "15m",
        horizon_steps: int = 3,
    ) -> ForecastBundleV1:
        if hmm_features is None or lgbm_feature_row is None:
            auto_hmm, auto_lgbm = self.build_causal_features(ohlcv_df)
            hmm_features = hmm_features if hmm_features is not None else auto_hmm
            lgbm_feature_row = (
                lgbm_feature_row if lgbm_feature_row is not None else auto_lgbm[-1]
            )

        log_ret = np.diff(np.log(np.maximum(ohlcv_df["close"].values.astype(np.float64), 1e-12)))
        vol_bps, vol_z = self.egarch.forecast_vol_bps(log_ret, horizon_steps=horizon_steps)
        post = self.hmm.filter_causal(hmm_features)
        p_down, p_range, p_up = (
            float(post[-1, 0]),
            float(post[-1, 1]),
            float(post[-1, 2]),
        )
        ent = -sum(p * math.log(max(p, 1e-12)) for p in (p_down, p_range, p_up)) / math.log(
            3.0
        )

        dom_idx = int(np.argmax(post[-1]))
        run_len = 0
        for row in reversed(post):
            if int(np.argmax(row)) == dom_idx:
                run_len += 1
            else:
                break
        maturity: Literal["EARLY", "ESTABLISHED", "MATURE", "EXHAUSTION"] = (
            "EARLY"
            if run_len <= 3
            else (
                "ESTABLISHED"
                if run_len <= 10
                else ("MATURE" if run_len <= 24 else "EXHAUSTION")
            )
        )

        pred = self.lgbm.predict_row(lgbm_feature_row.reshape(1, -1), vol_bps)
        q = dict(pred["quantiles_bps"])
        q10_adj, q90_adj = self.aci.adjust_interval(q["q10"], q["q90"])
        q["q10"], q["q90"] = round(q10_adj, 1), round(q90_adj, 1)
        q["q05"] = min(q["q05"], q["q10"])
        q["q95"] = max(q["q95"], q["q90"])
        # Guarantee strict non-decreasing quantile order across all 7 levels after ACI adjustment
        q_keys = ("q05", "q10", "q25", "q50", "q75", "q90", "q95")
        sorted_vals = np.sort([float(q[k]) for k in q_keys])
        q = {k: round(float(v), 1) for k, v in zip(q_keys, sorted_vals)}

        dis = self.shadow_tsfm.compute_disagreement(
            lgbm_q50_bps=q["q50"],
            ohlcv_df=ohlcv_df,
            horizon_steps=horizon_steps,
            cond_vol_bps=vol_bps,
        )
        dis_lvl: Literal["LOW", "MEDIUM", "HIGH"] = (
            "HIGH" if dis >= 0.65 else ("MEDIUM" if dis >= 0.30 else "LOW")
        )
        return ForecastBundleV1(
            symbol=symbol.upper(),
            horizon=horizon,
            regime_probs={
                "UP": round(p_up, 3),
                "RANGE": round(p_range, 3),
                "DOWN": round(p_down, 3),
            },
            regime_maturity=maturity,
            regime_entropy=round(float(np.clip(ent, 0.0, 1.0)), 3),
            cond_vol_bps=round(vol_bps, 1),
            vol_zscore=round(vol_z, 2),
            return_quantiles_bps=q,
            barrier_probs=pred["barrier_probs"],
            expected_mfe_bps=pred["mfe_bps"],
            expected_mae_bps=pred["mae_bps"],
            tsfm_disagreement=round(dis, 3),
            disagreement_level=dis_lvl,
            aci_alpha_t=round(self.aci.alpha_t, 3),
        )


__all__ = [
    "AdaptiveConformalCalibrator",
    "CausalHMMFilter",
    "EGARCHVolatilityModel",
    "ForecastBundleV1",
    "ForecastPipeline",
    "MultiHeadLGBMForecaster",
    "ShadowTSFMChallenger",
    "compute_calibration_metrics",
    "compute_triple_barrier_labels",
    "purged_walk_forward_splits",
]
