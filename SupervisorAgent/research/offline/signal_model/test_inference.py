from .dataset import SignalDataset
from bot.ml.signal_model.model import SignalModel
import pandas as pd

# adjust path if needed
ds = SignalDataset(data_path="../../data")
symbols = ds.available_symbols()
print("Available symbols:", symbols)
symbol = symbols[-1]  # choose last
print("Using symbol:", symbol)

# build a single-row dataset (most recent seconds)
X, y = ds.build_dataset(symbol, limit_files=500, horizon_seconds=1, sample_limit=200)
print("Built X shape:", X.shape)

model = SignalModel()
model.load("signal_model.pkl")
# use last row as example
x_last = X.tail(1)
pred = model.predict(x_last)
proba = model.predict_proba(x_last)
print("Last features:\n", x_last.to_dict(orient="records"))
print("Pred:", pred, "Proba:", proba)
