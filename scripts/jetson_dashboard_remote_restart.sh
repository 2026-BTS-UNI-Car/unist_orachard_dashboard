#!/usr/bin/env bash
set -euo pipefail

HOST="unist"
USER_NAME="unist"
APP_PATH="/home/unist/workspace/unist_orachard_dashboard"
SYSTEMD_RESTART="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --path) APP_PATH="$2"; shift 2 ;;
    --systemd) SYSTEMD_RESTART="1"; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

ssh "${USER_NAME}@${HOST}" "set -euo pipefail
cd '${APP_PATH}'
python3 -m compileall dashboard_server scripts
python3 -m unittest discover -s tests
if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    docker compose up -d --build
  else
    if ! docker-compose up -d --build; then
      echo 'docker-compose up failed; removing dashboard-managed containers and retrying' >&2
      docker rm -f \
        unist-orachard-dashboard \
        unist-orachard-ros2-data-recorder \
        unist-orachard-ros2-camera-bridge \
        unist-orachard-ros2-lidar-bridge \
        unist-orachard-ros2-rover-bridge \
        unist-orachard-ros2-camera-driver \
        unist-orachard-ros2-lidar-driver >/dev/null 2>&1 || true
      docker ps -a --format '{{.Names}}' \
        | grep -E '^[0-9a-f]+_unist-orachard-' \
        | xargs -r docker rm -f >/dev/null 2>&1 || true
      docker-compose up -d --build
    fi
  fi
fi
if [[ '${SYSTEMD_RESTART}' == '1' ]]; then
  sudo systemctl restart unist-orachard-dashboard.service
  sudo systemctl restart unist-orachard-lidar-bridge.service || true
fi"
