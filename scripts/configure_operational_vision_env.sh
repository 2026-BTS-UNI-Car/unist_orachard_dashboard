#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env}"
touch "$ENV_FILE"

upsert() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

upsert ORCHARD_VISION_BACKEND operational
upsert ORCHARD_YOLO_MODEL /app/runtime/training_runs/orchard_trunk_teacher_ood_v1/weights/best.engine
upsert ORCHARD_CLIP_MODEL openai/clip-vit-base-patch32
upsert ORCHARD_CLIP_FP16 1
upsert ORCHARD_VISION_REVISION orchard_trunk_yolo11_trt_clip_ood_v1
upsert ORCHARD_VISION_CONFIDENCE 0.08
upsert ORCHARD_VISION_DEVICE cuda
upsert HF_HOME /app/runtime/models/hf

printf 'Configured offline operational vision in %s\n' "$ENV_FILE"
