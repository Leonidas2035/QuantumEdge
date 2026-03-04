"""Train XGBoost model for AI Scalper entry signal prediction.

Usage:
    python -m quantum_edge_core.ai_scalper_bot.training.train_model

Reads:  runtime/dataset.csv
Writes: src/quantum_edge_core/ai_scalper_bot/model.xgb
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

logger = logging.getLogger(__name__)

# ── Feature columns (must match dataset_builder.py) ──────────────
FEATURE_COLS = [
    "returns_1m",
    "micro_vol_5",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "vwap_dist_pct",
    "vroc_10",
    "atr_14",
    "body_ratio",
    "vol_ma_ratio",
]

# ── Paths ────────────────────────────────────────────────────────
DEFAULT_DATASET_PATH = "runtime/dataset.csv"
DEFAULT_MODEL_PATH = "src/quantum_edge_core/ai_scalper_bot/model.xgb"


def train(
    dataset_path: str = DEFAULT_DATASET_PATH,
    model_path: str = DEFAULT_MODEL_PATH,
    test_size: float = 0.20,
    random_state: int = 42,
) -> None:
    """Full training pipeline: load → split → train → evaluate → save."""
    logger.info("═" * 60)
    logger.info("XGBoost Training Pipeline")
    logger.info("═" * 60)

    # ── 1. Load dataset ──────────────────────────────────────────
    ds_path = Path(dataset_path)
    if not ds_path.exists():
        logger.error(
            "Dataset not found: %s\n"
            "Run dataset_builder.py first:\n"
            "  python -m quantum_edge_core.ai_scalper_bot.training.dataset_builder",
            ds_path,
        )
        return

    df = pd.read_csv(ds_path)
    logger.info("[1/5] Loaded dataset: %d rows, %d columns", len(df), len(df.columns))

    # Verify feature columns exist
    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        logger.error("Missing feature columns: %s", missing)
        return

    # ── 2. Prepare X, y ──────────────────────────────────────────
    X = df[FEATURE_COLS].copy()
    y = df["target"].copy()

    # Map target: -1 → 0 (DOWN), 0 → 1 (FLAT), 1 → 2 (UP)
    # XGBoost needs 0-indexed classes
    y_mapped = y.map({-1: 0, 0: 1, 1: 2})
    class_names = {0: "DOWN", 1: "FLAT", 2: "UP"}

    # Drop any remaining NaN
    valid_mask = X.notna().all(axis=1) & y_mapped.notna()
    X = X[valid_mask]
    y_mapped = y_mapped[valid_mask]

    logger.info("[2/5] Features: %d | Samples: %d", len(FEATURE_COLS), len(X))
    logger.info("  Class distribution: %s", y_mapped.value_counts().to_dict())

    # ── 3. Train/Test split ──────────────────────────────────────
    # Time-series: use last 20% as test (no shuffle to preserve temporal order)
    split_idx = int(len(X) * (1 - test_size))
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y_mapped.iloc[:split_idx], y_mapped.iloc[split_idx:]

    logger.info(
        "[3/5] Split: Train=%d (%.0f%%) | Test=%d (%.0f%%)",
        len(X_train), (1 - test_size) * 100,
        len(X_test), test_size * 100,
    )

    # ── 4. Train XGBoost ─────────────────────────────────────────
    logger.info("[4/5] Training XGBClassifier...")
    t0 = time.monotonic()

    model = XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        gamma=0.1,
        reg_alpha=0.5,
        reg_lambda=1.0,
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        tree_method="hist",   # fast histogram-based
        random_state=random_state,
        verbosity=0,
        use_label_encoder=False,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    train_time = time.monotonic() - t0
    logger.info("  Training completed in %.1f seconds.", train_time)

    # ── 5. Evaluate ──────────────────────────────────────────────
    y_pred = model.predict(X_test)

    accuracy = accuracy_score(y_test, y_pred)
    report = classification_report(
        y_test, y_pred,
        target_names=["DOWN", "FLAT", "UP"],
        digits=4,
    )
    cm = confusion_matrix(y_test, y_pred)

    logger.info("\n" + "─" * 50)
    logger.info("📊 MODEL EVALUATION RESULTS")
    logger.info("─" * 50)
    logger.info("Accuracy: %.4f (%.2f%%)", accuracy, accuracy * 100)
    logger.info("\nClassification Report:\n%s", report)
    logger.info("Confusion Matrix:\n%s", cm)

    # Feature importance
    importance = dict(zip(FEATURE_COLS, model.feature_importances_))
    sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)
    logger.info("\n📈 Feature Importance (Top 5):")
    for feat, imp in sorted_imp[:5]:
        bar = "█" * int(imp * 50)
        logger.info("  %-18s %.4f %s", feat, imp, bar)

    # ── 6. Save model ────────────────────────────────────────────
    out = Path(model_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(out))
    size_mb = out.stat().st_size / 1e6
    logger.info("\n[5/5] Model saved → %s (%.2f MB)", out, size_mb)

    # Summary
    logger.info("\n" + "═" * 50)
    logger.info("✅ TRAINING COMPLETE")
    logger.info("  Accuracy : %.2f%%", accuracy * 100)
    logger.info("  Model    : %s", out)
    logger.info("  Features : %d", len(FEATURE_COLS))
    logger.info("  Train    : %d samples", len(X_train))
    logger.info("  Test     : %d samples", len(X_test))
    logger.info("═" * 50)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    train()
