#!/usr/bin/env python3
"""Prepare local/offline vision models for the orchard AI worker.

This script may use HF_TOKEN/ROBOFLOW_API_KEY on the development machine, but
the generated runtime/models directory can be copied to Jetson and used without
placing API keys on the board.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


SEGFORMER_MODEL_ID = "nvidia/segformer-b0-finetuned-ade-512-512"
YOLO_BASE_MODEL = "yolo11n-seg.pt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", default="runtime/models")
    parser.add_argument("--download-segformer", action="store_true")
    parser.add_argument("--download-yolo-base", action="store_true")
    parser.add_argument("--export-yolo-engine", default="", help="Path to a local fine-tuned .pt model to export to TensorRT.")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="0")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"ok": True, "models_dir": str(models_dir), "prepared": []}

    if args.download_segformer:
        path = download_segformer(models_dir)
        manifest["prepared"].append({"kind": "segformer", "model_id": SEGFORMER_MODEL_ID, "path": str(path)})

    if args.download_yolo_base:
        path = download_yolo_base(models_dir)
        manifest["prepared"].append({"kind": "yolo_base", "model": YOLO_BASE_MODEL, "path": str(path)})

    if args.export_yolo_engine:
        path = export_yolo_engine(Path(args.export_yolo_engine), models_dir, imgsz=args.imgsz, device=args.device)
        manifest["prepared"].append({"kind": "yolo_tensorrt_engine", "path": str(path)})

    (models_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def download_segformer(models_dir: Path) -> Path:
    from huggingface_hub import snapshot_download  # type: ignore

    target = models_dir / "segformer-b0-ade20k"
    snapshot_download(
        repo_id=SEGFORMER_MODEL_ID,
        local_dir=target,
        local_dir_use_symlinks=False,
        token=os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN"),
    )
    return target


def download_yolo_base(models_dir: Path) -> Path:
    from ultralytics import YOLO  # type: ignore

    model = YOLO(YOLO_BASE_MODEL)
    source = Path(getattr(model, "ckpt_path", "") or YOLO_BASE_MODEL)
    target = models_dir / YOLO_BASE_MODEL
    if source.exists() and source.resolve() != target.resolve():
        target.write_bytes(source.read_bytes())
        return target
    return source


def export_yolo_engine(model_path: Path, models_dir: Path, imgsz: int, device: str) -> Path:
    if not model_path.exists():
        raise FileNotFoundError(model_path)
    from ultralytics import YOLO  # type: ignore

    model = YOLO(str(model_path))
    exported = model.export(format="engine", half=True, imgsz=imgsz, device=device)
    exported_path = Path(str(exported))
    target = models_dir / exported_path.name
    if exported_path.exists() and exported_path.resolve() != target.resolve():
        target.write_bytes(exported_path.read_bytes())
    return target if target.exists() else exported_path


if __name__ == "__main__":
    raise SystemExit(main())
