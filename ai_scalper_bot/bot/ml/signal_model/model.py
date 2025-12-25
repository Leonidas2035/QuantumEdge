from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import xgboost as xgb

from bot.ml.feature_schema import FEATURE_NAMES


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

    def __init__(self, symbol: str = "BTCUSDT", horizon: int = 1, model_dir: Optional[Path] = None):
        root = Path(__file__).resolve().parents[3]
        self.model_dir = model_dir or (root / "storage" / "models")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.model_dir / f"signal_xgb_{symbol}_h{horizon}.json"
        self.model = self._load_model()

    def _load_model(self) -> xgb.XGBClassifier:
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model not found: {self.model_path}. Train model first (python -m bot.ml.signal_model.train)."
            )
        model = xgb.XGBClassifier()
        model.load_model(str(self.model_path))
        return model

    def predict_proba(self, features: np.ndarray) -> SignalOutput:
        arr = np.asarray(features, dtype=float).reshape(1, -1)
        if arr.shape[1] != len(FEATURE_NAMES):
            raise ValueError(
                f"Feature length mismatch. Expected {len(FEATURE_NAMES)} features ({FEATURE_NAMES}), got shape {arr.shape}."
            )

        probs = self.model.predict_proba(arr)[0]
        p_down = float(probs[0])
        p_up = float(probs[1])
        edge = p_up - 0.5
        direction = 1 if edge > 0 else (-1 if edge < 0 else 0)
        return SignalOutput(p_up=p_up, p_down=p_down, edge=edge, direction=direction)
