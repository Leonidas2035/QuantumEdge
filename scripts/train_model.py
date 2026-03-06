#!/usr/bin/env python3
import pandas as pd
import numpy as np
import xgboost as xgb
import os
from sklearn.metrics import classification_report


def train_xgboost():
    print("[*] Завантаження датасету...")
    try:
        df = pd.read_parquet("data/ml_dataset.parquet")
    except Exception as e:
        print(f"[-] Помилка завантаження датасету: {e}")
        return

    # XGBoost вимагає класи від 0 до N. Наш таргет: -1 (Sell), 0 (Hold), 1 (Buy).
    # Зробимо мапінг: -1 -> 0, 0 -> 1, 1 -> 2
    df["target_mapped"] = df["target"].map({-1: 0, 0: 1, 1: 2})

    features = ["spread", "micro_imbalance", "ofi_proxy", "volatility_100t"]
    X = df[features]
    y = df["target_mapped"]

    print("[*] Time-Series Split (80% Train / 20% Test)...")
    # НІЯКОГО RANDOM SPLIT! Для ринкових даних тільки хронологічний поділ
    split_idx = int(len(df) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    print(f"[*] Розмір Train: {len(X_train)}, Розмір Test: {len(X_test)}")

    print("[*] Ініціалізація та тренування XGBoost Classifier...")
    # Налаштування для HFT: швидко, дерева не надто глибокі
    model = xgb.XGBClassifier(
        objective="multi:softmax",
        num_class=3,
        n_estimators=100,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        n_jobs=-1,
        random_state=42,
    )

    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=10)

    print("\n[*] Оцінка моделі (Test Set)...")
    y_pred = model.predict(X_test)

    print("\n=== CLASSIFICATION REPORT ===")
    # Мапінг назад для зрозумілості у звіті: 0->Sell, 1->Hold, 2->Buy
    target_names = ["Sell (-1)", "Hold (0)", "Buy (1)"]
    print(classification_report(y_test, y_pred, target_names=target_names))

    print("\n=== ВАЖЛИВІСТЬ ФІЧ (Feature Importance) ===")
    importances = model.feature_importances_
    for name, imp in zip(features, importances):
        print(f"{name}: {imp:.4f}")

    # Збереження моделі
    os.makedirs("models", exist_ok=True)
    model_path = "models/xgboost_alpha.json"
    model.save_model(model_path)
    print(f"\n✅ Модель успішно збережено у: {model_path}")


if __name__ == "__main__":
    train_xgboost()
