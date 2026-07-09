#!/usr/bin/env python3
"""Prepare Roboflow ONNX artifacts for offline dashboard inference.

This script is intended to run on a development machine with ROBOFLOW_API_KEY
available. It writes only model artifacts into runtime/roboflow_cache so the
Jetson worker can run without storing Roboflow credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_MODEL_ID = "tree-trunk-detection-369pz-vm3hn/2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default=os.environ.get("ORCHARD_ROBOFLOW_MODEL_ID", DEFAULT_MODEL_ID))
    parser.add_argument("--cache-dir", default=os.environ.get("MODEL_CACHE_DIR", "runtime/roboflow_cache"))
    parser.add_argument("--api-base-url", default=os.environ.get("ROBOFLOW_API_BASE_URL", "https://api.roboflow.com"))
    parser.add_argument("--device-id", default="offline-cache-prep")
    return parser.parse_args()


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("ROBOFLOW_API_KEY") or os.environ.get("API_KEY")
    if not api_key:
        raise SystemExit("ROBOFLOW_API_KEY or API_KEY is required to prepare offline cache")

    params = urllib.parse.urlencode(
        {
            "nocache": "true",
            "device": args.device_id,
            "dynamic": "true",
            "api_key": api_key,
        }
    )
    metadata_url = f"{args.api_base_url.rstrip('/')}/ort/{args.model_id}?{params}"
    response = fetch_json(metadata_url)
    ort = response.get("ort") or response
    if "model" not in ort or "environment" not in ort:
        raise SystemExit(f"Unexpected Roboflow response keys: {sorted(ort.keys())}")

    cache_dir = Path(args.cache_dir) / args.model_id
    cache_dir.mkdir(parents=True, exist_ok=True)

    classes = [str(item) for item in (ort.get("classes") or [])]
    (cache_dir / "class_names.txt").write_text("\n".join(classes) + ("\n" if classes else ""), encoding="utf-8")

    environment = fetch_json(str(ort["environment"]))
    if "colors" in ort:
        environment["COLORS"] = ort["colors"]
    (cache_dir / "environment.json").write_text(json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8")

    weights = fetch_bytes(str(ort["model"]))
    (cache_dir / "weights.onnx").write_bytes(weights)

    project_task_type = str(ort.get("taskType") or ort.get("type") or "instance-segmentation")
    model_type = str(ort.get("modelType") or "yolov11n-seg")
    (cache_dir / "model_type.json").write_text(
        json.dumps(
            {
                "project_task_type": project_task_type,
                "model_type": model_type,
            },
            indent=4,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "ok": True,
                "model_id": args.model_id,
                "cache_dir": str(cache_dir),
                "classes": classes,
                "project_task_type": project_task_type,
                "model_type": model_type,
                "weights_bytes": len(weights),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
