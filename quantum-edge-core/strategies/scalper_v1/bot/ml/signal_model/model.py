from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from bot.ml.feature_schema import FEATURE_NAMES
from bot.ml.calibration import apply_calibration
from bot.ml.inference_backend import InferenceBackend, create_backend


@dataclass
class SignalOutput:
    p_up: float
    p_down: float
    edge: float
    direction: int


class SignalModel:
    """
    Thin wrapper around an XGBoost binary classifier for signal generation.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        horizon: int = 1,
        model_dir: Optional[Path] = None,
        model_path: Optional[Path] = None,
        calibration: Optional[dict] = None,
        backend: Optional[str] = None,
    ):
        root = Path(__file__).resolve().parents[3]
        self.model_dir = model_dir or (root / "storage" / "models")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        if model_path is not None:
            self.model_path = Path(model_path)
        else:
            self.model_path = self.model_dir / f"signal_xgb_{symbol}_h{horizon}.json"
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model not found: {self.model_path}. Train model first (python -m bot.ml.signal_model.train)."
            )
        self.backend: InferenceBackend = create_backend(
            self.model_path,
            backend_name=backend,
            logger=logging.getLogger("ml_inference"),
        )
        self.calibration = calibration or {}

    def predict_proba(self, features: np.ndarray) -> SignalOutput:
        arr = np.asarray(features, dtype=float).reshape(1, -1)
        if arr.shape[1] != len(FEATURE_NAMES):
            raise ValueError(
                f"Feature length mismatch. Expected {len(FEATURE_NAMES)} features ({FEATURE_NAMES}), got shape {arr.shape}."
            )

        probs = np.asarray(self.backend.predict_proba(arr), dtype=float)
        if probs.ndim == 1 and probs.size == 2:
            p_down = float(probs[0])
            p_up = float(probs[1])
        else:
            if probs.ndim != 2 or probs.shape[1] != 2:
                raise ValueError(f"Unexpected predict_proba output shape {probs.shape}")
            p_down = float(probs[0][0])
            p_up = float(probs[0][1])
        p_up = apply_calibration(p_up, self.calibration)
        p_up = max(0.0, min(1.0, p_up))
        p_down = 1.0 - p_up
        edge = p_up - 0.5
        direction = 1 if edge > 0 else (-1 if edge < 0 else 0)
        return SignalOutput(p_up=p_up, p_down=p_down, edge=edge, direction=direction)
