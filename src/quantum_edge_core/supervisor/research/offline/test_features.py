from pathlib import Path

from .indicators.feature_builder import FeatureBuilder

DATA_DIR = Path(__file__).resolve().parent / "data"

fb = FeatureBuilder(DATA_DIR)
features = fb.build("BTCUSDT")
print("\n=== FEATURES ===")
print(features)
