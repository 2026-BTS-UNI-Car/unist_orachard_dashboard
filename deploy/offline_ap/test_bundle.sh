#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
IMAGE="${UBTS_OFFLINE_PROXY_IMAGE:-nginx:stable-alpine}"

printf '%s\n' 'Checking Bash syntax...'
bash -n "${SCRIPT_DIR}/install.sh"
bash -n "${SCRIPT_DIR}/ubts-offline-ap"
bash -n "${SCRIPT_DIR}/test_bundle.sh"
sh -n "${SCRIPT_DIR}/render-and-run.sh"
bash -n "${SCRIPT_DIR}/tests/safety_regression.sh"

if command -v shellcheck >/dev/null 2>&1; then
  printf '%s\n' 'Running shellcheck...'
  shellcheck \
    "${SCRIPT_DIR}/install.sh" \
    "${SCRIPT_DIR}/ubts-offline-ap" \
    "${SCRIPT_DIR}/test_bundle.sh" \
    "${SCRIPT_DIR}/render-and-run.sh" \
    "${SCRIPT_DIR}/tests/safety_regression.sh"
else
  printf '%s\n' 'SKIP: shellcheck is not installed.'
fi

printf '%s\n' 'Running required mock safety regression tests...'
bash "${SCRIPT_DIR}/tests/safety_regression.sh"
printf '%s\n' 'REQUIRED CORE TESTS PASSED.'

if ! command -v docker >/dev/null 2>&1 || ! docker version >/dev/null 2>&1; then
  printf '%s\n' 'OPTIONAL SKIP: Docker integration validation is unavailable; required core tests already passed.'
  exit 0
fi

printf '%s\n' 'Rendering Compose configuration...'
AP_ADDRESS=10.77.0.1 \
PORTAINER_TLS_NAME=localhost \
DASHBOARD_HOSTNAME=dashboard.example.invalid \
PORTAINER_HOSTNAME=docker.example.invalid \
UBTS_PROXY_CONFIG_FINGERPRINT=test-only \
UBTS_OFFLINE_PROXY_IMAGE="$IMAGE" \
  docker compose -f "${SCRIPT_DIR}/compose.yaml" config --quiet

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  printf 'OPTIONAL SKIP: local image is missing: %s\n' "$IMAGE"
  printf '%s\n' 'Pull it explicitly, then rerun to test nginx syntax.'
  exit 0
fi

printf '%s\n' 'Testing nginx configuration without network access or host listeners...'
docker run --rm --network none \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  --cap-add CHOWN \
  --cap-add DAC_OVERRIDE \
  --cap-add SETUID \
  --cap-add SETGID \
  --security-opt no-new-privileges:true \
  --entrypoint /opt/ubts-offline-ap/render-and-run.sh \
  --tmpfs /etc/nginx/conf.d:uid=0,gid=0,mode=0755 \
  --tmpfs /var/cache/nginx:uid=101,gid=101,mode=0755 \
  --tmpfs /var/run:uid=0,gid=0,mode=0755 \
  --tmpfs /tmp:uid=101,gid=101,mode=1777 \
  -e AP_ADDRESS=127.0.0.1 \
  -e PORTAINER_TLS_NAME=localhost \
  -e DASHBOARD_HOSTNAME=dashboard.example.invalid \
  -e PORTAINER_HOSTNAME=docker.example.invalid \
  -e PORTAINER_CA_PATH=/etc/ssl/certs/ca-certificates.crt \
  -v "${SCRIPT_DIR}/nginx/default.conf.template:/etc/nginx/templates/default.conf.template:ro" \
  -v "${SCRIPT_DIR}/render-and-run.sh:/opt/ubts-offline-ap/render-and-run.sh:ro" \
  "$IMAGE" test

printf '%s\n' 'Offline AP bundle static validation passed.'
