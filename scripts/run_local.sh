#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 -m dashboard_server --host "${DASHBOARD_HOST:-127.0.0.1}" --port "${DASHBOARD_PORT:-8080}"
