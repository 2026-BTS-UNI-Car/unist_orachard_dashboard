#!/usr/bin/env bash
set -euo pipefail

DATA_YAML="${1:-}"
if [[ -z "${DATA_YAML}" ]]; then
  echo "Usage: $0 /path/to/data.yaml [output-name]" >&2
  exit 2
fi

RUN_NAME="${2:-aihub_orchard_tree_smoke}"
DATA_ROOT="$(cd "$(dirname "${DATA_YAML}")" && pwd)"
RUNTIME_DATA_YAML="/tmp/${RUN_NAME}_data.yaml"
DEFAULT_MODEL="/app/runtime/models/yolo11n-seg.pt"
if [[ ! -f "${DEFAULT_MODEL}" ]]; then
  DEFAULT_MODEL="yolo11n-seg.pt"
fi
MODEL="${ORCHARD_TRAIN_BASE_MODEL:-${DEFAULT_MODEL}}"
IMGSZ="${ORCHARD_TRAIN_IMGSZ:-640}"
EPOCHS="${ORCHARD_TRAIN_EPOCHS:-20}"
BATCH="${ORCHARD_TRAIN_BATCH:-4}"
DEVICE="${ORCHARD_TRAIN_DEVICE:-0}"
PROJECT="${ORCHARD_TRAIN_PROJECT:-/app/runtime/training_runs}"

mkdir -p "${PROJECT}"

DATA_YAML="${DATA_YAML}" DATA_ROOT="${DATA_ROOT}" RUNTIME_DATA_YAML="${RUNTIME_DATA_YAML}" python - <<'PY'
import os
from pathlib import Path

source = Path(os.environ["DATA_YAML"])
root = Path(os.environ["DATA_ROOT"])
target = Path(os.environ["RUNTIME_DATA_YAML"])
lines = source.read_text(encoding="utf-8").splitlines()
rewritten: list[str] = []
path_written = False
for line in lines:
    if line.startswith("path:"):
        rewritten.append(f"path: {root}")
        path_written = True
    else:
        rewritten.append(line)
if not path_written:
    rewritten.insert(0, f"path: {root}")
target.write_text("\n".join(rewritten).rstrip() + "\n", encoding="utf-8")
print(f"Using dataset yaml: {target}")
PY

python - <<PY
from ultralytics import YOLO

model = YOLO("${MODEL}")
model.train(
    data="${RUNTIME_DATA_YAML}",
    task="segment",
    imgsz=${IMGSZ},
    epochs=${EPOCHS},
    batch=${BATCH},
    device="${DEVICE}",
    project="${PROJECT}",
    name="${RUN_NAME}",
    workers=2,
    exist_ok=True,
)
PY

echo "Training complete: ${PROJECT}/${RUN_NAME}/weights/best.pt"
