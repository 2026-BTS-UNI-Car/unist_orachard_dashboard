#!/usr/bin/env bash
set -euo pipefail

source_setup() {
  local setup_file="$1"
  if [ ! -r "$setup_file" ]; then
    log setup_missing "ROS setup file not found: $setup_file"
    return 1
  fi
  set +u
  # shellcheck disable=SC1090
  source "$setup_file"
  set -u
}

DEFAULT_PORT="${LIDAR_SERIAL_PORT:-/dev/ttyUSB0}"
RESTART_DELAY="${LIDAR_DRIVER_RESTART_DELAY_S:-2}"

log() {
  local event="$1"
  shift || true
  printf '{"ts":%s,"event":"lidar_driver.%s","message":%s}\n' \
    "$(date +%s)" "$event" "$(python3 -c 'import json,sys; print(json.dumps(" ".join(sys.argv[1:])))' "$@")"
}

source_setup /opt/ros/iron/setup.bash
source_setup /opt/sllidar_ros2_ws/install/setup.bash

resolve_port() {
  if [ -n "${LIDAR_SERIAL_PORT:-}" ] && [ -e "$LIDAR_SERIAL_PORT" ]; then
    printf '%s\n' "$LIDAR_SERIAL_PORT"
    return 0
  fi
  for pattern in \
    "/dev/serial/by-id/*CP210*" \
    "/dev/serial/by-id/*Silicon*" \
    "/dev/serial/by-id/*Slamtec*" \
    "/dev/ttyUSB*"; do
    for candidate in $pattern; do
      if [ -e "$candidate" ]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
  done
  if [ -e "$DEFAULT_PORT" ]; then
    printf '%s\n' "$DEFAULT_PORT"
    return 0
  fi
  return 1
}

while true; do
  if ! PORT="$(resolve_port)"; then
    log waiting "LiDAR serial device not found; waiting for replug"
    sleep "$RESTART_DELAY"
    continue
  fi

  log starting "Starting SLLIDAR driver on $PORT"
  set +e
  ros2 launch sllidar_ros2 sllidar_s2_launch.py \
    serial_port:="$PORT" \
    serial_baudrate:="${LIDAR_SERIAL_BAUDRATE:-1000000}" \
    frame_id:="${LIDAR_FRAME_ID:-laser}" \
    scan_mode:="${LIDAR_SCAN_MODE:-DenseBoost}"
  code=$?
  set -e
  log exited "SLLIDAR driver exited with code $code; rescanning device"
  sleep "$RESTART_DELAY"
done
