#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077

export LC_ALL=C

DRY_RUN=0
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LOCK_FILE="/run/lock/ubts-offline-ap.lock"

usage() {
  cat <<'USAGE'
Usage: sudo ./install.sh [--dry-run]

Installs the UBTS offline AP helper, dedicated local-only dnsmasq/proxy files,
and disabled systemd units. It does not reload NetworkManager, create an
AP connection, start the proxy, enable the timer, or change any route.
USAGE
}

print_command() {
  printf 'DRY-RUN:'
  printf ' %q' "$@"
  printf '\n'
}

run() {
  if ((DRY_RUN)); then
    print_command "$@"
    return 0
  fi
  "$@"
}

die() {
  printf '[install-offline-ap] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"
}

assert_clean_install_state() {
  local path
  local -a managed_paths=(
    /opt/ubts-offline-ap
    /usr/local/sbin/ubts-offline-ap
    /etc/systemd/system/ubts-offline-ap-ensure.service
    /etc/systemd/system/ubts-offline-ap-ensure.timer
    /etc/systemd/system/ubts-offline-ap-dnsmasq.service
    /etc/ubts-offline-ap/ap.conf
    /etc/ubts-offline-ap/ap.conf.example
    /run/ubts-offline-ap
  )
  for path in "${managed_paths[@]}"; do
    [[ ! -e "$path" && ! -L "$path" ]] || die "managed install/runtime state already exists: $path"
  done

  local unit
  for unit in ubts-offline-ap-ensure.timer ubts-offline-ap-ensure.service \
              ubts-offline-ap-dnsmasq.service; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null ||
       systemctl is-active --quiet "$unit" 2>/dev/null; then
      die "offline AP unit is enabled or active: $unit"
    fi
  done
  if docker container inspect ubts-offline-proxy >/dev/null 2>&1; then
    die 'offline AP proxy container already exists'
  fi
  if docker ps -aq --filter label=com.docker.compose.project=ubts-offline-ap 2>/dev/null |
     grep -q .; then
    die 'offline AP Compose project already has container state'
  fi
  if nmcli -t -f NAME connection show | grep -Eq '^ubts-offline-ap-'; then
    die 'an offline AP NetworkManager profile already exists'
  fi
  if compgen -G '/run/NetworkManager/system-connections/ubts-offline-ap-*.nmconnection' >/dev/null ||
     compgen -G '/etc/NetworkManager/system-connections/ubts-offline-ap-*.nmconnection' >/dev/null; then
    die 'an offline AP keyfile already exists'
  fi
}

if [[ "${UBTS_OFFLINE_AP_INSTALL_TESTING:-0}" == "1" && "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

while (($#)); do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

if ((!DRY_RUN && EUID != 0)); then
  die 'run this installer as root'
fi

for dependency in install systemctl docker nmcli flock grep dnsmasq ip iw readlink stat awk sed sort \
                  dirname basename iptables-save ip6tables-save curl ss dig sha256sum uuidgen mktemp; do
  require_command "$dependency"
done
[[ "$(command -v dnsmasq)" == "/usr/sbin/dnsmasq" ]] ||
  die 'dnsmasq must be installed at /usr/sbin/dnsmasq for the hardened unit'
systemctl list-units --no-pager >/dev/null 2>&1 || die 'systemd manager is unavailable'
docker info >/dev/null 2>&1 || die 'Docker Engine is unavailable'
nmcli general status >/dev/null 2>&1 || die 'NetworkManager is unavailable'

if ((!DRY_RUN)); then
  install -d -o root -g root -m 0755 "$(dirname -- "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  flock -n 9 || die 'another offline-AP install/activation operation is running'
fi

assert_clean_install_state

required_files=(
  ubts-offline-ap
  render-and-run.sh
  compose.yaml
  dnsmasq.conf.template
  nginx/default.conf.template
  systemd/ubts-offline-ap-ensure.service
  systemd/ubts-offline-ap-ensure.timer
  systemd/ubts-offline-ap-dnsmasq.service
  ap.conf.example
  README.md
)
for relative_path in "${required_files[@]}"; do
  [[ -f "${SCRIPT_DIR}/${relative_path}" ]] || die "bundle file is missing: $relative_path"
done

run install -d -o root -g root -m 0755 /opt/ubts-offline-ap/nginx
run install -d -o root -g root -m 0700 /etc/ubts-offline-ap

run install -o root -g root -m 0755 \
  "${SCRIPT_DIR}/ubts-offline-ap" /usr/local/sbin/ubts-offline-ap
run install -o root -g root -m 0755 \
  "${SCRIPT_DIR}/render-and-run.sh" /opt/ubts-offline-ap/render-and-run.sh
run install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/compose.yaml" /opt/ubts-offline-ap/compose.yaml
run install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/dnsmasq.conf.template" /opt/ubts-offline-ap/dnsmasq.conf.template
run install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/nginx/default.conf.template" \
  /opt/ubts-offline-ap/nginx/default.conf.template
run install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/README.md" /opt/ubts-offline-ap/README.md
run install -o root -g root -m 0600 \
  "${SCRIPT_DIR}/ap.conf.example" /etc/ubts-offline-ap/ap.conf.example
run install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/systemd/ubts-offline-ap-ensure.service" \
  /etc/systemd/system/ubts-offline-ap-ensure.service
run install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/systemd/ubts-offline-ap-ensure.timer" \
  /etc/systemd/system/ubts-offline-ap-ensure.timer
run install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/systemd/ubts-offline-ap-dnsmasq.service" \
  /etc/systemd/system/ubts-offline-ap-dnsmasq.service

run systemctl daemon-reload

printf '%s\n' \
  'Installed only; NetworkManager and routes were not reloaded or changed.' \
  'The timer remains disabled and the AP remains inactive.' \
  'Read /opt/ubts-offline-ap/README.md before running preflight or activate.'
