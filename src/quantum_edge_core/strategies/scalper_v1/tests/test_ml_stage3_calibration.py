import numpy as np
from bot.ml.eval.calibrate import _apply_calibrator, _fit_calibrator


def test_platt_calibration_changes_brier():
    rng = np.random.default_rng(42)
    y_true = rng.integers(0, 2, size=200)
    p_raw = rng.uniform(0.2, 0.8, size=200)

    calibrator = _fit_calibrator("platt", y_true, p_raw)
    p_cal = _apply_calibrator(calibrator, p_raw)

    raw_brier = float(((p_raw - y_true) ** 2).mean())
    cal_brier = float(((p_cal - y_true) ** 2).mean())

    assert abs(cal_brier - raw_brier) > 1e-6
