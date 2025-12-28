"""Metric helpers for offline evaluation."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

import numpy as np


def _safe_float(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> Optional[float]:
    try:
        from sklearn.metrics import roc_auc_score

        if len(np.unique(y_true)) < 2:
            return None
        return float(roc_auc_score(y_true, y_prob))
    except Exception:
        return None


def _safe_pr_auc(y_true: np.ndarray, y_prob: np.ndarray) -> Optional[float]:
    try:
        from sklearn.metrics import average_precision_score

        if len(np.unique(y_true)) < 2:
            return None
        return float(average_precision_score(y_true, y_prob))
    except Exception:
        return None


def _safe_logloss(y_true: np.ndarray, y_prob: np.ndarray) -> Optional[float]:
    try:
        from sklearn.metrics import log_loss

        return float(log_loss(y_true, y_prob))
    except Exception:
        return None


def _safe_brier(y_true: np.ndarray, y_prob: np.ndarray) -> Optional[float]:
    try:
        from sklearn.metrics import brier_score_loss

        return float(brier_score_loss(y_true, y_prob))
    except Exception:
        return None


def _threshold_metrics(y_true: np.ndarray, y_prob: np.ndarray, threshold: float) -> Dict[str, Optional[float]]:
    try:
        from sklearn.metrics import confusion_matrix, precision_recall_fscore_support, accuracy_score

        preds = (y_prob >= threshold).astype(int)
        precision, recall, f1, _ = precision_recall_fscore_support(
            y_true, preds, average="binary", zero_division=0
        )
        accuracy = accuracy_score(y_true, preds)
        conf = confusion_matrix(y_true, preds).tolist()
        return {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "accuracy": float(accuracy),
            "confusion": conf,
        }
    except Exception:
        return {
            "precision": None,
            "recall": None,
            "f1": None,
            "accuracy": None,
            "confusion": None,
        }


def compute_metrics(y_true: np.ndarray, y_prob: np.ndarray, thresholds: Iterable[float]) -> Dict[str, object]:
    metrics: Dict[str, object] = {
        "rows": int(len(y_true)),
        "class_balance": {
            "0": int((y_true == 0).sum()),
            "1": int((y_true == 1).sum()),
        },
        "auc": _safe_auc(y_true, y_prob),
        "pr_auc": _safe_pr_auc(y_true, y_prob),
        "logloss": _safe_logloss(y_true, y_prob),
        "brier": _safe_brier(y_true, y_prob),
        "thresholds": {},
    }

    for thr in thresholds:
        metrics["thresholds"][str(thr)] = _threshold_metrics(y_true, y_prob, float(thr))

    return metrics


def ece_score(y_true: np.ndarray, y_prob: np.ndarray, bins: int = 10) -> float:
    if len(y_true) == 0:
        return 0.0
    y_true = y_true.astype(float)
    y_prob = y_prob.astype(float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for i in range(bins):
        mask = (y_prob >= edges[i]) & (y_prob < edges[i + 1])
        if not mask.any():
            continue
        acc = float(y_true[mask].mean())
        conf = float(y_prob[mask].mean())
        ece += abs(acc - conf) * (mask.mean())
    return float(ece)
