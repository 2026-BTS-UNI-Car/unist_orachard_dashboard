#!/bin/sh
set -eu

: "${AP_ADDRESS:?AP_ADDRESS is required}"
: "${PORTAINER_TLS_NAME:?PORTAINER_TLS_NAME is required}"
: "${PORTAINER_CA_PATH:?PORTAINER_CA_PATH is required}"

if [ "$AP_ADDRESS" != "10.77.0.1" ] && [ "${1:-}" != "test" ]; then
  printf '%s\n' 'ERROR: production AP_ADDRESS must be 10.77.0.1' >&2
  exit 1
fi

case "$PORTAINER_TLS_NAME" in
  ''|.*|*.|*[!A-Za-z0-9.-]*)
    printf '%s\n' 'ERROR: invalid PORTAINER_TLS_NAME' >&2
    exit 1
    ;;
esac

case "$PORTAINER_CA_PATH" in
  /*) ;;
  *)
    printf '%s\n' 'ERROR: PORTAINER_CA_PATH must be absolute' >&2
    exit 1
    ;;
esac

if [ ! -r "$PORTAINER_CA_PATH" ]; then
  printf 'ERROR: Portainer CA is not readable: %s\n' "$PORTAINER_CA_PATH" >&2
  exit 1
fi

/docker-entrypoint.d/20-envsubst-on-templates.sh

if [ "${1:-}" = "test" ]; then
  exec nginx -t
fi

if [ "${1:-}" != "serve" ]; then
  printf '%s\n' 'ERROR: expected serve or test command' >&2
  exit 1
fi

exec nginx -g 'daemon off;'
