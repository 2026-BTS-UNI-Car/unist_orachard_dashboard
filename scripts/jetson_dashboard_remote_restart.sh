#!/usr/bin/env bash
set -euo pipefail

HOST="unist"
USER_NAME="unist"
APP_PATH="/home/unist/unist_orachard_dashboard"
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
  docker compose up -d --build || docker-compose up -d --build
fi
if [[ '${SYSTEMD_RESTART}' == '1' ]]; then
  sudo systemctl restart unist-orachard-dashboard.service
  sudo systemctl restart unist-orachard-lidar-bridge.service || true
fi"
