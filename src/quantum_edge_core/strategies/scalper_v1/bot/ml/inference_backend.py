from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import xgboost as xgb

from quantum_edge_core.strategies.scalper_v1.bot.ml.feature_schema import \
    FEATURE_NAMES

_LOG = logging.getLogger("inference_backend")


@dataclass(frozen=True)
class BackendInfo:
    name: str
    device: str


class InferenceBackend:
    info: BackendInfo

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        raise NotImplementedError


def _ensure_2d(features: np.ndarray) -> np.ndarray:
    arr = np.asarray(features, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return arr


def _gpu_visible() -> bool:
    if os.getenv("CUDA_VISIBLE_DEVICES") in {"", "-1"}:
        return False
    return shutil.which("nvidia-smi") is not None


class XGBoostBackend(InferenceBackend):
    def __init__(self, model_path: Path, use_gpu: bool) -> None:
        self.model_path = Path(model_path)
        name = "xgboost_gpu" if use_gpu else "xgboost_cpu"
        device = "cuda" if use_gpu else "cpu"
        self.info = BackendInfo(name=name, device=device)
        self.model = xgb.XGBClassifier()
        self.model.load_model(str(self.model_path))
        if use_gpu:
            try:
                self.model.set_params(predictor="gpu_predictor")
            except Exception as exc:
                raise RuntimeError(f"gpu_predictor_unavailable:{exc}") from exc

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        arr = _ensure_2d(features)
        return self.model.predict_proba(arr)


class OnnxRuntimeBackend(InferenceBackend):
    def __init__(self, model_path: Path, prefer_gpu: bool) -> None:
        import onnxruntime as ort

        providers = ["CPUExecutionProvider"]
        device = "cpu"
        if prefer_gpu:
            available = ort.get_available_providers()
            if "CUDAExecutionProvider" in available:
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                device = "cuda"
        self.info = BackendInfo(name=f"onnx_{device}", device=device)
        self.session = ort.InferenceSession(str(model_path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        arr = _ensure_2d(features).astype(np.float32)
        outputs = self.session.run(None, {self.input_name: arr})
        if not outputs:
            raise ValueError("onnx_backend_no_outputs")
        out = np.asarray(outputs[0])
        if out.ndim == 1:
            out = out.reshape(-1, 1)
        if out.shape[1] == 1:
            p_up = out.reshape(-1)
            p_down = 1.0 - p_up
            return np.stack([p_down, p_up], axis=1)
        return out


class TorchScriptBackend(InferenceBackend):
    def __init__(self, model_path: Path, prefer_gpu: bool) -> None:
        import torch

        device = "cuda" if prefer_gpu and torch.cuda.is_available() else "cpu"
        self.info = BackendInfo(name=f"torch_{device}", device=device)
        self.device = torch.device(device)
        self.model = torch.jit.load(str(model_path), map_location=self.device)
        self.model.eval()

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        import torch

        arr = _ensure_2d(features).astype(np.float32)
        with torch.no_grad():
            tensor = torch.from_numpy(arr).to(self.device)
            outputs = self.model(tensor)
            out = outputs.detach().cpu().numpy()
        if out.ndim == 1:
            out = out.reshape(-1, 1)
        if out.shape[1] == 1:
            p_up = out.reshape(-1)
            p_down = 1.0 - p_up
            return np.stack([p_down, p_up], axis=1)
        return out


def _probe_backend(backend: InferenceBackend) -> None:
    probe = np.zeros((1, len(FEATURE_NAMES)), dtype=float)
    backend.predict_proba(probe)


def _create_onnx_backend(
    model_path: Path, prefer_gpu: bool, logger: logging.Logger
) -> Optional[InferenceBackend]:
    if model_path.suffix.lower() != ".onnx":
        logger.warning(
            "ONNX backend requested but model is %s; falling back to CPU.",
            model_path.suffix,
        )
        return None
    try:
        backend = OnnxRuntimeBackend(model_path, prefer_gpu=prefer_gpu)
        _probe_backend(backend)
        return backend
    except Exception as exc:
        logger.warning("ONNX backend unavailable (%s); falling back to CPU.", exc)
        return None


def _create_torch_backend(
    model_path: Path, prefer_gpu: bool, logger: logging.Logger
) -> Optional[InferenceBackend]:
    if model_path.suffix.lower() not in {".pt", ".pth", ".torchscript"}:
        logger.warning(
            "Torch backend requested but model is %s; falling back to CPU.",
            model_path.suffix,
        )
        return None
    try:
        backend = TorchScriptBackend(model_path, prefer_gpu=prefer_gpu)
        _probe_backend(backend)
        return backend
    except Exception as exc:
        logger.warning("Torch backend unavailable (%s); falling back to CPU.", exc)
        return None


def create_backend(
    model_path: Path,
    backend_name: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> InferenceBackend:
    logger = logger or _LOG
    requested = (backend_name or os.getenv("INFERENCE_BACKEND", "auto")).lower()

    if requested == "auto":
        if model_path.suffix.lower() == ".onnx":
            backend = _create_onnx_backend(model_path, prefer_gpu=True, logger=logger)
            if backend:
                return backend
        if model_path.suffix.lower() in {".pt", ".pth", ".torchscript"}:
            backend = _create_torch_backend(model_path, prefer_gpu=True, logger=logger)
            if backend:
                return backend
        if _gpu_visible():
            try:
                backend = XGBoostBackend(model_path, use_gpu=True)
                _probe_backend(backend)
                return backend
            except Exception as exc:
                logger.warning("XGBoost GPU backend unavailable (%s); using CPU.", exc)
        return XGBoostBackend(model_path, use_gpu=False)

    if requested in {"cpu", "xgb_cpu", "xgboost_cpu"}:
        return XGBoostBackend(model_path, use_gpu=False)

    if requested in {"xgb_gpu", "xgboost_gpu"}:
        try:
            backend = XGBoostBackend(model_path, use_gpu=True)
            _probe_backend(backend)
            return backend
        except Exception as exc:
            logger.warning("XGBoost GPU backend unavailable (%s); using CPU.", exc)
            return XGBoostBackend(model_path, use_gpu=False)

    if requested in {"onnx_cuda", "onnx"}:
        backend = _create_onnx_backend(model_path, prefer_gpu=True, logger=logger)
        if backend:
            return backend
        return XGBoostBackend(model_path, use_gpu=False)

    if requested == "onnx_cpu":
        backend = _create_onnx_backend(model_path, prefer_gpu=False, logger=logger)
        if backend:
            return backend
        return XGBoostBackend(model_path, use_gpu=False)

    if requested in {"torch_cuda", "torch"}:
        backend = _create_torch_backend(model_path, prefer_gpu=True, logger=logger)
        if backend:
            return backend
        return XGBoostBackend(model_path, use_gpu=False)

    if requested == "torch_cpu":
        backend = _create_torch_backend(model_path, prefer_gpu=False, logger=logger)
        if backend:
            return backend
        return XGBoostBackend(model_path, use_gpu=False)

    logger.warning("Unknown inference backend '%s'; defaulting to CPU.", requested)
    return XGBoostBackend(model_path, use_gpu=False)
