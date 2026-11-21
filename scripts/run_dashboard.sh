#!/usr/bin/env bash
set -euo pipefail

# The repository folder: APP_DIR, or the folder this script lives in.
APP_DIR="${APP_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
HOST="${DASHBOARD_HOST:-0.0.0.0}"
PORT="${DASHBOARD_PORT:-8080}"
TELEMETRY_FILE="${TELEMETRY_FILE:-$APP_DIR/runtime/telemetry_snapshot.json}"

mkdir -p "$(dirname "$TELEMETRY_FILE")"
cd "$APP_DIR"

export TELEMETRY_FILE
exec python3 -m dashboard_server --host "$HOST" --port "$PORT"
