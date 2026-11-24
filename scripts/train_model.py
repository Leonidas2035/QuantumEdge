#!/usr/bin/env python3
import os
import json
import time
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import classification_report, accuracy_score


def train_models():
    print("========================================")
    print("XGBoost Dual Binary Training Pipeline")
    print("========================================")
    
    print("[*] Завантаження датасету...")
    try:
        df = pd.read_parquet("data/ml_dataset.parquet")
        print(f"[+] Датасет завантажено: {len(df)} рядків.")
    except Exception as e:
        print(f"[-] Помилка завантаження датасету: {e}")
        return

    features = [
        "spread",
        "micro_imbalance",
        "ofi_proxy",
        "volatility_100t",
        "ofi_z",
        "ofi_ma5",
        "top_qty_sum",
        "trade_rate_1s",
        "volume_1s"
    ]
    
    # Перевіримо наявність фічей
    for f in features:
        if f not in df.columns:
            print(f"[-] Помилка: Фіча '{f}' відсутня в датасеті.")
            return

    # --- Крок 2: Асиметричний (розумний) Downsampling ---
    print("[*] Крок 2: Розумний Downsampling для Hold...")
    
    df_buy = df[df["target"] == 1].copy()
    df_sell = df[df["target"] == -1].copy()
    df_hold = df[df["target"] == 0].copy()
    
    print(f"  Початкові об'єми: Buy={len(df_buy)}, Sell={len(df_sell)}, Hold={len(df_hold)}")
    
    # Видаляємо Hold з мінімальною волатильністю (мертвий ринок, bottom 15% quantile)
    vol_threshold = df_hold["volatility_100t"].quantile(0.15)
    df_hold_active = df_hold[df_hold["volatility_100t"] > vol_threshold].copy()
    print(f"  Активні Hold (після фільтрації волатильності): {len(df_hold_active)}")
    
    # Залишаємо Hold у співвідношенні 1:3 до сигналів (Buy + Sell)
    signals_count = len(df_buy) + len(df_sell)
    target_hold_count = 3 * signals_count
    
    if len(df_hold_active) > target_hold_count:
        df_hold_sampled = df_hold_active.sample(n=target_hold_count, random_state=42)
    else:
        df_hold_sampled = df_hold_active
        
    print(f"  Зменшений Hold: {len(df_hold_sampled)}")
    
    # Об'єднуємо назад та сортуємо за часом
    df_train_ready = pd.concat([df_buy, df_sell, df_hold_sampled]).sort_values("timestamp").reset_index(drop=True)
    print(f"[+] Downsampled датасет готовий: {len(df_train_ready)} рядків.")
    
    X = df_train_ready[features].copy()
    y = df_train_ready["target"].copy()
    
    # --- Хронологічний спліт (80% Train / 20% Test) ---
    print("[*] Розподіл даних на Train/Test (80% / 20%)...")
    split_idx = int(len(df_train_ready) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    
    # --- Крок 3: Тренування моделі UP ---
    print("\n[*] Крок 3.1: Тренування model_up (прогноз росту)...")
    y_train_up = (y_train == 1).astype(int)
    y_test_up = (y_test == 1).astype(int)
    
    # Розрахунок scale_pos_weight
    neg_up = len(y_train_up[y_train_up == 0])
    pos_up = len(y_train_up[y_train_up == 1])
    weight_up = neg_up / (pos_up + 1e-8)
    print(f"  Співвідношення класів для model_up (scale_pos_weight): {weight_up:.2f}")
    
    model_up = xgb.XGBClassifier(
        objective="binary:logistic",
        n_estimators=150,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=weight_up,
        n_jobs=-1,
        random_state=42,
        eval_metric="logloss"
    )
    
    model_up.fit(X_train, y_train_up, eval_set=[(X_test, y_test_up)], verbose=False)
    y_pred_up = model_up.predict(X_test)
    acc_up = accuracy_score(y_test_up, y_pred_up)
    print(f"  [+] Accuracy model_up: {acc_up:.4f}")
    print(classification_report(y_test_up, y_pred_up, target_names=["Not Buy", "Buy"]))
    
    # --- Тренування моделі DOWN ---
    print("\n[*] Крок 3.2: Тренування model_down (прогноз падіння)...")
    y_train_down = (y_train == -1).astype(int)
    y_test_down = (y_test == -1).astype(int)
    
    # Розрахунок scale_pos_weight
    neg_down = len(y_train_down[y_train_down == 0])
    pos_down = len(y_train_down[y_train_down == 1])
    weight_down = neg_down / (pos_down + 1e-8)
    print(f"  Співвідношення класів для model_down (scale_pos_weight): {weight_down:.2f}")
    
    model_down = xgb.XGBClassifier(
        objective="binary:logistic",
        n_estimators=150,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=weight_down,
        n_jobs=-1,
        random_state=42,
        eval_metric="logloss"
    )
    
    model_down.fit(X_train, y_train_down, eval_set=[(X_test, y_test_down)], verbose=False)
    y_pred_down = model_down.predict(X_test)
    acc_down = accuracy_score(y_test_down, y_pred_down)
    print(f"  [+] Accuracy model_down: {acc_down:.4f}")
    print(classification_report(y_test_down, y_pred_down, target_names=["Not Sell", "Sell"]))
    
    # --- Крок 5: Збереження моделей та створення manifest.json ---
    print("\n[*] Крок 5: Збереження моделей та створення маніфесту...")
    os.makedirs("models", exist_ok=True)
    
    model_up_path = "models/model_up.json"
    model_down_path = "models/model_down.json"
    manifest_path = "models/manifest.json"
    
    model_up.save_model(model_up_path)
    model_down.save_model(model_down_path)
    
    manifest = {
        "schema_version": "2.0.0",
        "generated_timestamp": time.time(),
        "features": features,
        "models": {
            "model_up": {
                "file": model_up_path,
                "accuracy": float(acc_up),
                "scale_pos_weight": float(weight_up)
            },
            "model_down": {
                "file": model_down_path,
                "accuracy": float(acc_down),
                "scale_pos_weight": float(weight_down)
            }
        },
        "gating_thresholds": {
            "p_up_threshold": 0.65,
            "p_down_threshold": 0.65,
            "p_cross_threshold": 0.30
        }
    }
    
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        
    print(f"✅ Модель model_up збережена: {model_up_path}")
    print(f"✅ Модель model_down збережена: {model_down_path}")
    print(f"✅ Маніфест моделі створено: {manifest_path}")


if __name__ == "__main__":
    train_models()
