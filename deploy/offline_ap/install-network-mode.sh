#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INTERFACE="${UBTS_WIFI_INTERFACE:-wlP1p1s0}"
SSID="${UBTS_AP_SSID:-BTS_Jetson}"
PASSWORD_FILE="${UBTS_AP_PASSWORD_FILE:-/etc/ubts-offline-ap/wpa2.pass}"
DASHBOARD_HOSTNAME="${UBTS_AP_DASHBOARD_HOSTNAME:-dashboard.example.invalid}"
PORTAINER_HOSTNAME="${UBTS_AP_PORTAINER_HOSTNAME:-docker.example.invalid}"
SSH_HOSTNAME="${UBTS_AP_SSH_HOSTNAME:-ssh.example.invalid}"

[[ ${EUID} -eq 0 ]] || { printf '%s\n' 'ERROR: root is required' >&2; exit 1; }
[[ -d "/sys/class/net/${INTERFACE}" ]] || { printf 'ERROR: interface not found: %s\n' "$INTERFACE" >&2; exit 1; }
[[ -s "$PASSWORD_FILE" ]] || { printf 'ERROR: password file missing: %s\n' "$PASSWORD_FILE" >&2; exit 1; }
[[ "$(stat -c '%u:%a' "$PASSWORD_FILE")" == '0:600' ]] || {
  printf '%s\n' 'ERROR: password file must be root-owned mode 0600' >&2
  exit 1
}

install -o root -g root -m 0755 "${SCRIPT_DIR}/ubts_network_mode.py" /usr/local/sbin/ubts-network-mode
install -o root -g root -m 0644 "${SCRIPT_DIR}/systemd/ubts-network-mode.service" /etc/systemd/system/ubts-network-mode.service
umask 077
printf 'UBTS_WIFI_INTERFACE=%s\nUBTS_AP_SSID=%s\nUBTS_AP_PASSWORD_FILE=%s\nUBTS_AP_IDLE_SECONDS=60\nUBTS_WIFI_SCAN_SECONDS=60\n' \
  "$INTERFACE" "$SSID" "$PASSWORD_FILE" >/etc/ubts-network-mode.conf
printf 'UBTS_AP_DASHBOARD_HOSTNAME=%s\nUBTS_AP_PORTAINER_HOSTNAME=%s\nUBTS_AP_SSH_HOSTNAME=%s\n' \
  "$DASHBOARD_HOSTNAME" "$PORTAINER_HOSTNAME" "$SSH_HOSTNAME" >>/etc/ubts-network-mode.conf
chown root:root /etc/ubts-network-mode.conf
chmod 0600 /etc/ubts-network-mode.conf
systemctl daemon-reload
/usr/local/sbin/ubts-network-mode prepare --json
printf '%s\n' 'Prepared only. Run systemctl enable --now ubts-network-mode.service after validation.'
