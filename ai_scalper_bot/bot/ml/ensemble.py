from dataclasses import dataclass
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from bot.core.config_loader import config
from bot.ml.signal_model.model import SignalModel, SignalOutput


@dataclass
class EnsembleOutput:
    meta_edge: float
    direction: int
    components: Dict[int, SignalOutput]


class EnsembleSignalModel:
    """
    Loads multiple horizons and combines edges with fixed weights.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        horizons: Optional[List[int]] = None,
        runtime_models: Optional[Dict[int, SignalModel]] = None,
        thresholds: Optional[Dict[int, float]] = None,
    ):
        self.symbol = symbol
        cfg_horizons = config.get("ml.horizons", [1, 5, 30])
        self.horizons = horizons or cfg_horizons
        backend = config.get("ml.inference_backend") or os.getenv("INFERENCE_BACKEND")
        # simple equal weights by default
        self.weights = {h: 1.0 for h in self.horizons}
        self.models: Dict[int, SignalModel] = {}
        self.thresholds: Dict[int, float] = thresholds or {}
        if runtime_models:
            self.models.update(runtime_models)
        else:
            for h in self.horizons:
                try:
                    self.models[h] = SignalModel(symbol=symbol, horizon=h, backend=backend)
                except FileNotFoundError:
                    print(f"[WARN] Model for horizon {h} missing. Skipping in ensemble.")

    def _combine(self, outputs: Dict[int, SignalOutput]) -> EnsembleOutput:
        if not outputs:
            return EnsembleOutput(meta_edge=0.0, direction=0, components={})

        total_weight = sum(self.weights.get(h, 0.0) for h in outputs)
        if total_weight == 0:
            total_weight = 1.0

        meta_edge = 0.0
        for h, out in outputs.items():
            w = self.weights.get(h, 0.0) / total_weight
            meta_edge += out.edge * w

        direction = 1 if meta_edge > 0 else (-1 if meta_edge < 0 else 0)
        return EnsembleOutput(meta_edge=meta_edge, direction=direction, components=outputs)

    def predict(self, features: np.ndarray) -> EnsembleOutput:
        outputs: Dict[int, SignalOutput] = {}
        for h, model in self.models.items():
            try:
                outputs[h] = model.predict_proba(features)
            except Exception as exc:
                print(f"[WARN] Horizon {h} prediction failed: {exc}")
        return self._combine(outputs)

    def predict_all_horizons(self, features: np.ndarray) -> Dict[int, SignalOutput]:
        outputs: Dict[int, SignalOutput] = {}
        for h, model in self.models.items():
            try:
                outputs[h] = model.predict_proba(features)
            except Exception as exc:
                print(f"[WARN] Horizon {h} prediction failed: {exc}")
        return outputs

    def thresholds_met(self, outputs: Dict[int, SignalOutput]) -> bool:
        return self.entry_gate(outputs, direction=None)

    def entry_gate(self, outputs: Dict[int, SignalOutput], direction: Optional[str]) -> bool:
        if not self.thresholds:
            return True
        for horizon, threshold in self.thresholds.items():
            sig = outputs.get(horizon)
            if sig is None:
                return False
            if direction is None:
                if sig.p_up < threshold and sig.p_down < threshold:
                    return False
                continue
            if direction == "long":
                if sig.p_up < threshold:
                    return False
            elif direction == "short":
                if sig.p_down < threshold:
                    return False
        return True

    @staticmethod
    def filter_blocks(features: np.ndarray) -> Tuple[bool, str]:
        """
        Simple pre-trade filters:
         - block if volatility too low (ret_std_10 tiny)
         - block if sudden shock (ret_1 very large)
         - block if inactivity (vol_sum_10 almost zero)
        """
        try:
            ret_std_10 = abs(float(features[7]))
            ret_1 = abs(float(features[0]))
            vol_sum_10 = float(features[10])
        except Exception:
            return False, "invalid features"

        if ret_std_10 < 1e-5:
            return True, "volatility too low"
        if ret_1 > 0.01:
            return True, "sudden price shock"
        if vol_sum_10 < 1e-6:
            return True, "inactivity"

        return False, ""
