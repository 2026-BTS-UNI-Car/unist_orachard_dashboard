#!/usr/bin/env bash
set -euo pipefail

HOST="unist"
USER_NAME="unist"
DEST="/home/unist/unist_orachard_dashboard"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

rsync -az --delete \
  --exclude ".git/" \
  --exclude "__pycache__/" \
  --exclude ".pytest_cache/" \
  --exclude "runtime/" \
  --exclude "data/" \
  --exclude "*.pyc" \
  ./ "${USER_NAME}@${HOST}:${DEST}/"
