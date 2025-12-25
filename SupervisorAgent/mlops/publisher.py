"""Publish model artifacts into runtime (atomic swap)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Optional

from SupervisorAgent.mlops.manifest import ModelManifest


def publish_model(artifact_dir: Path, runtime_root: Path, keep_previous: bool = False) -> Path:
    artifact_dir = artifact_dir.resolve()
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found in {artifact_dir}")
    manifest = ModelManifest.load(manifest_path)
    model_rel = Path(manifest.files["model"]["path"])
    model_path = artifact_dir / model_rel
    if not model_path.exists():
        raise FileNotFoundError(f"Model file missing: {model_path}")

    dest_parent = runtime_root / "models" / manifest.symbol / str(manifest.horizon)
    dest_parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = dest_parent / f"current.tmp.{os.getpid()}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    shutil.copytree(artifact_dir, tmp_dir)

    current_dir = dest_parent / "current"
    if keep_previous and current_dir.exists():
        prev_dir = dest_parent / "previous" / time.strftime("%Y%m%d-%H%M%S")
        prev_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(current_dir, prev_dir)
    elif current_dir.exists():
        shutil.rmtree(current_dir)

    os.replace(tmp_dir, current_dir)
    return current_dir

