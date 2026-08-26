#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2016,SC2034
set -Eeuo pipefail

export LC_ALL=C
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

MOCK_ROOT="${TEST_ROOT}/mock"
MOCK_BIN="${TEST_ROOT}/bin"
FAKE_SYS="${TEST_ROOT}/sys/class/net"
FAKE_DEVICES="${TEST_ROOT}/sys/devices/pci0000/usb7/7-1"
FAKE_DEVICES_2="${TEST_ROOT}/sys/devices/pci0000/usb8/8-1"
export MOCK_ROOT
mkdir -p "$MOCK_ROOT" "$MOCK_BIN" "$FAKE_SYS" "$FAKE_DEVICES/7-1:1.0" \
  "$FAKE_DEVICES/7-1:1.1" "$FAKE_DEVICES_2/8-1:1.0"
printf '1234\n' >"$FAKE_DEVICES/idVendor"
printf '5678\n' >"$FAKE_DEVICES/idProduct"
printf '9876\n' >"$FAKE_DEVICES_2/idVendor"
printf '5432\n' >"$FAKE_DEVICES_2/idProduct"

make_netdev() {
  local name="$1" function_path="$2" wiphy="$3"
  mkdir -p "$FAKE_SYS/$name/wireless"
  ln -s "$function_path" "$FAKE_SYS/$name/device"
  printf '%s\n' "$wiphy" >"$MOCK_ROOT/wiphy-$name"
}

make_netdev wlxap "$FAKE_DEVICES/7-1:1.0" 7

write_mock() {
  local name="$1"
  shift
  printf '%s\n' "$@" >"$MOCK_BIN/$name"
  chmod +x "$MOCK_BIN/$name"
}

write_mock ip '#!/usr/bin/env bash' 'set -eu' '
family="$1"; shift
case "$family $*" in
  "-4 route show table all default") file="$MOCK_ROOT/routes4"; failure="$MOCK_ROOT/ip-fail-v4-default" ;;
  "-6 route show table all default") file="$MOCK_ROOT/routes6" ;;
  "-4 route show table all") file="$MOCK_ROOT/routes4all" ;;
  "-4 -o address show") file="$MOCK_ROOT/addresses4" ;;
  "-4 rule show") file="$MOCK_ROOT/rules4"; failure="$MOCK_ROOT/ip-fail-v4-rules" ;;
  "-6 rule show") file="$MOCK_ROOT/rules6" ;;
  "-4 route get 1.1.1.1") file="$MOCK_ROOT/routeget4"; failure="$MOCK_ROOT/ip-fail-v4-routeget" ;;
  "-6 route get 2606:4700:4700::1111") file="$MOCK_ROOT/routeget6" ;;
  *) exit 2 ;;
esac
[[ -z "${failure:-}" || ! -f "$failure" ]] || exit 42
[[ -f "$file" ]] && cat "$file"
'

write_mock iw '#!/usr/bin/env bash' 'set -eu' '
if [[ "$1" == "dev" ]]; then
  printf "Interface %s\n\twiphy %s\n" "$2" "$(cat "$MOCK_ROOT/wiphy-$2")"
elif [[ "$1" == "phy" ]]; then
  printf "Supported interface modes:\n\t * managed\n\t * AP\n"
else
  exit 2
fi
'

write_mock nmcli '#!/usr/bin/env bash' 'set -eu' '
printf "nmcli %s\n" "$*" >>"$MOCK_ROOT/nmcli.log"
connection="ubts-offline-ap-wlxap"
uuid="12345678-1234-4234-8234-123456789abc"
[[ ! -f "$MOCK_ROOT/profile-uuid" ]] || uuid="$(cat "$MOCK_ROOT/profile-uuid")"
if [[ "$1" == "general" && "$2" == "status" ]]; then
  [[ ! -f "$MOCK_ROOT/nm-unavailable" ]]
  exit
fi
if [[ "$*" == "-t -f NAME connection show" ]]; then
  [[ -f "$MOCK_ROOT/profile" ]] && printf "%s\n" "$connection"
  exit 0
fi
if [[ "$*" == "-t -f NAME,UUID connection show" ]]; then
  if [[ -f "$MOCK_ROOT/profile" ]]; then
    printf "%s:%s\n" "$connection" "$uuid"
    [[ ! -f "$MOCK_ROOT/duplicate-profile" ]] || printf "%s:%s\n" "$connection" aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
  fi
  exit 0
fi
if [[ "$*" == "-t -f NAME,DEVICE connection show --active" ]]; then
  [[ -f "$MOCK_ROOT/active" ]] && printf "%s:wlxap\n" "$connection"
  exit 0
fi
if [[ "$*" == "-t -f NAME,UUID,DEVICE connection show --active" ]]; then
  if [[ -f "$MOCK_ROOT/active" ]]; then
    printf "%s:%s:wlxap\n" "$connection" "$uuid"
    [[ ! -f "$MOCK_ROOT/duplicate-active" ]] || printf "other:%s:wlxap\n" aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
  elif [[ -f "$MOCK_ROOT/foreign-active" ]]; then
    printf "foreign:%s:wlxap\n" aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
  fi
  exit 0
fi
if [[ "$1" == "-g" && "$2" == "GENERAL.CONNECTION" ]]; then
  target="${5:-}"
  [[ ! -f "$MOCK_ROOT/nm-device-query-fail-$target" ]] || exit 41
  [[ -f "$MOCK_ROOT/active-$target" ]] && cat "$MOCK_ROOT/active-$target" || printf "%s\n" --
  exit 0
fi
if [[ "$1" == "connection" && "$2" == "load" ]]; then
  file="$3"
  required=(
    "id=$connection" "interface-name=wlxap" "autoconnect=false" "mode=ap"
    "method=manual" "address1=10.77.0.1/24" "never-default=true" "method=disabled"
    "key-mgmt=wpa-psk" "psk-flags=2"
  )
  [[ "$(stat -c %a "$file")" == "600" ]] || exit 31
  for item in "${required[@]}"; do grep -Fqx "$item" "$file" || exit 32; done
  cp "$file" "$MOCK_ROOT/loaded-keyfile"
  grep "^uuid=" "$file" | head -n 1 | cut -d= -f2 >"$MOCK_ROOT/profile-uuid"
  touch "$MOCK_ROOT/profile"
  [[ ! -f "$MOCK_ROOT/load-fail" ]] || exit 33
  exit 0
fi
if [[ "$1" == "-g" && "$3" == "connection" && "$4" == "show" ]]; then
  [[ -f "$MOCK_ROOT/profile" ]] || exit 37
  property="$2"
  case "$property" in
    connection.id) printf "%s\n" "$connection" ;;
    connection.uuid) printf "%s\n" "$uuid" ;;
    connection.interface-name) printf "%s\n" wlxap ;;
    connection.autoconnect) printf "%s\n" no ;;
    802-11-wireless.mode) printf "%s\n" ap ;;
    802-11-wireless.ssid) printf "%s\n" UBTS-Jetson ;;
    802-11-wireless.band) printf "%s\n" bg ;;
    802-11-wireless-security.key-mgmt) printf "%s\n" wpa-psk ;;
    802-11-wireless-security.psk-flags) printf "%s\n" "2 (not saved)" ;;
    ipv4.method) [[ ! -f "$MOCK_ROOT/unsafe-method" ]] && printf "%s\n" manual || printf "%s\n" shared ;;
    ipv4.never-default|ipv6.never-default) printf "%s\n" yes ;;
    ipv6.method) printf "%s\n" disabled ;;
    ipv4.addresses) printf "%s\n" 10.77.0.1/24 ;;
    *) exit 34 ;;
  esac
  exit 0
fi
if [[ "$1" == "connection" && "$2" == "up" ]]; then
  printf "ipv4=%s ipv6=%s\n" \
    "$(cat "$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding")" \
    "$(cat "$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/wlxap/forwarding")" >"$MOCK_ROOT/forwarding-at-up"
  touch "$MOCK_ROOT/active"
  printf "%s\n" "$connection" >"$MOCK_ROOT/active-wlxap"
  printf "%s\n" "7: wlxap    inet 10.77.0.1/24 brd 10.77.0.255 scope global wlxap" >"$MOCK_ROOT/addresses4"
  printf "%s\n" "10.77.0.0/24 dev wlxap proto kernel scope link src 10.77.0.1" >"$MOCK_ROOT/routes4all"
  [[ ! -f "$MOCK_ROOT/up-fail" ]] || exit 35
  exit 0
fi
if [[ "$1" == "connection" && "$2" == "down" ]]; then
  if [[ -f "$MOCK_ROOT/down-fail-still-active" ]]; then exit 38; fi
  rm -f "$MOCK_ROOT/active" "$MOCK_ROOT/active-wlxap"
  : >"$MOCK_ROOT/addresses4"; : >"$MOCK_ROOT/routes4all"
  if [[ -f "$MOCK_ROOT/down-fail-inactive" ]]; then exit 39; fi
  exit 0
fi
if [[ "$1" == "connection" && "$2" == "delete" ]]; then
  [[ ! -f "$MOCK_ROOT/delete-fail" ]] || exit 40
  rm -f "$MOCK_ROOT/profile" "$MOCK_ROOT/profile-uuid" "$MOCK_ROOT/active" "$MOCK_ROOT/active-wlxap"
  : >"$MOCK_ROOT/addresses4"; : >"$MOCK_ROOT/routes4all"
  exit 0
fi
exit 36
'

write_mock docker '#!/usr/bin/env bash' 'set -eu' '
printf "docker %s\n" "$*" >>"$MOCK_ROOT/docker.log"
if [[ "$1" == "inspect" ]]; then
  format="$3"
  if [[ "$format" == *State.Running* ]]; then [[ -f "$MOCK_ROOT/proxy" ]] && printf true || printf false
  elif [[ "$format" == *State.Health* ]]; then [[ -f "$MOCK_ROOT/proxy-healthy" ]] && printf healthy || printf none
  elif [[ "$format" == *config-sha256* ]]; then cat "$MOCK_ROOT/proxy-fingerprint" 2>/dev/null || true
  fi
  exit 0
fi
if [[ "$1" == "container" && "$2" == "inspect" ]]; then
  [[ -f "$MOCK_ROOT/installer-proxy" || -f "$MOCK_ROOT/proxy-container" ]]
  exit
fi
if [[ "$1" == "image" && "$2" == "inspect" ]]; then
  printf "%s\n" sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
  exit 0
fi
if [[ "$1" == "ps" ]]; then
  [[ ! -f "$MOCK_ROOT/project-state" ]] || printf "%s\n" deadbeef
  exit 0
fi
if [[ "$1" == "compose" ]]; then
  if [[ "$*" == *" down "* || "$*" == *" down" ]]; then
    rm -f "$MOCK_ROOT/proxy" "$MOCK_ROOT/proxy-healthy" "$MOCK_ROOT/proxy-container"; exit 0
  fi
  if [[ "$*" == *" up "* ]]; then
    touch "$MOCK_ROOT/proxy" "$MOCK_ROOT/proxy-healthy" "$MOCK_ROOT/proxy-container"; exit 0
  fi
  exit 0
fi
exit 0
'

write_mock curl '#!/usr/bin/env bash' 'printf "curl %s\n" "$*" >>"$MOCK_ROOT/curl.log"; exit 0'
write_mock dig '#!/usr/bin/env bash' '
if [[ -f "$MOCK_ROOT/dns-wrong" ]]; then printf "10.77.0.9\n"
elif [[ -f "$MOCK_ROOT/dns-extra" ]]; then printf "10.77.0.1\n10.77.0.9\n"
else printf "10.77.0.1\n"
fi'
write_mock ss '#!/usr/bin/env bash' 'printf "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"'
write_mock uuidgen '#!/usr/bin/env bash' 'printf "12345678-1234-4234-8234-123456789abc\n"'
write_mock iptables-save '#!/usr/bin/env bash' '[[ ! -f "$MOCK_ROOT/iptables-save-fail" ]] || exit 42' 'cat "$MOCK_ROOT/iptables"'
write_mock ip6tables-save '#!/usr/bin/env bash' 'cat "$MOCK_ROOT/ip6tables"'
write_mock nft '#!/usr/bin/env bash' 'cat "$MOCK_ROOT/nft"'
write_mock dnsmasq '#!/usr/bin/env bash' '[[ "$1" == "--test" ]] || exit 2; exit 0'
write_mock systemctl '#!/usr/bin/env bash' '
printf "systemctl %s\n" "$*" >>"$MOCK_ROOT/systemctl.log"
unit="${*: -1}"
if [[ "$unit" == "ubts-offline-ap-dnsmasq.service" ]]; then
  case "$1" in
    start|restart) touch "$MOCK_ROOT/dnsmasq-active"; exit 0 ;;
    stop) rm -f "$MOCK_ROOT/dnsmasq-active"; exit 0 ;;
    is-active) [[ -f "$MOCK_ROOT/dnsmasq-active" ]]; exit ;;
  esac
fi
if [[ "$1" == "start" && -f "$MOCK_ROOT/timer-start-fail" ]]; then exit 1; fi
if [[ "$1" == "enable" && -f "$MOCK_ROOT/timer-enable-fail" ]]; then exit 1; fi
if [[ "$1" == "is-enabled" || "$1" == "is-active" ]]; then [[ -f "$MOCK_ROOT/timer-ok" ]]; exit; fi
exit 0
'

export PATH="$MOCK_BIN:$PATH"
export UBTS_OFFLINE_AP_TESTING=1
export UBTS_TEST_SYS_CLASS_NET_ROOT="$FAKE_SYS"
export UBTS_TEST_PROC_SYS_ROOT="${TEST_ROOT}/proc/sys"
export UBTS_TEST_NM_RUNTIME_DIR="${TEST_ROOT}/nm"
export UBTS_TEST_NM_PERSISTENT_DIR="${TEST_ROOT}/nm-persistent"
export UBTS_TEST_RUNTIME_STATE_DIR="${TEST_ROOT}/runtime"
export UBTS_TEST_CONFIG_DIR="${TEST_ROOT}/config"
export UBTS_TEST_INSTALL_DIR="${TEST_ROOT}/install"
export UBTS_TEST_LOCK_FILE="${TEST_ROOT}/lock/operation.lock"

mkdir -p "$UBTS_TEST_CONFIG_DIR" "$UBTS_TEST_INSTALL_DIR/nginx"
mkdir -p "$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap" \
  "$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/all" "$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/wlxap"
printf '1\n' >"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/ip_forward"
printf '0\n' >"$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/all/forwarding"
printf '1\n' >"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding"
printf '1\n' >"$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/wlxap/forwarding"
cp "$SCRIPT_DIR/compose.yaml" "$UBTS_TEST_INSTALL_DIR/compose.yaml"
cp "$SCRIPT_DIR/dnsmasq.conf.template" "$UBTS_TEST_INSTALL_DIR/dnsmasq.conf.template"
cp "$SCRIPT_DIR/nginx/default.conf.template" "$UBTS_TEST_INSTALL_DIR/nginx/default.conf.template"
cp "$SCRIPT_DIR/render-and-run.sh" "$UBTS_TEST_INSTALL_DIR/render-and-run.sh"
printf '%s\n' '-----BEGIN CERTIFICATE-----' 'TEST' '-----END CERTIFICATE-----' >"$UBTS_TEST_CONFIG_DIR/portainer-ca.pem"
chmod 0644 "$UBTS_TEST_CONFIG_DIR/portainer-ca.pem"
printf '%s\n' 'StrongPass_123' >"$UBTS_TEST_CONFIG_DIR/wpa2.pass"
chmod 0600 "$UBTS_TEST_CONFIG_DIR/wpa2.pass"

# shellcheck source=../ubts-offline-ap
source "$SCRIPT_DIR/ubts-offline-ap"
INTERFACE=wlxap
SSID=UBTS-Jetson
PASSWORD_FILE="$UBTS_TEST_CONFIG_DIR/wpa2.pass"
PORTAINER_TLS_NAME=localhost

reset_network() {
  : >"$MOCK_ROOT/routes4"; : >"$MOCK_ROOT/routes6"
  : >"$MOCK_ROOT/routes4all"; : >"$MOCK_ROOT/addresses4"
  printf '%s\n' '0: from all lookup local' '32766: from all lookup main' >"$MOCK_ROOT/rules4"
  printf '%s\n' '0: from all lookup local' '32766: from all lookup main' >"$MOCK_ROOT/rules6"
  printf '%s\n' '1.1.1.1 via 192.0.2.1 dev uplink0 src 192.0.2.2' >"$MOCK_ROOT/routeget4"
  : >"$MOCK_ROOT/routeget6"
  printf '%s\n' '*filter' ':INPUT ACCEPT [0:0]' 'COMMIT' >"$MOCK_ROOT/iptables"
  printf '%s\n' '*filter' ':INPUT ACCEPT [0:0]' 'COMMIT' >"$MOCK_ROOT/ip6tables"
  printf '%s\n' 'table inet filter { }' >"$MOCK_ROOT/nft"
  printf '1\n' >"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding"
  printf '1\n' >"$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/wlxap/forwarding"
  rm -f "$MOCK_ROOT"/{profile,profile-uuid,active,active-wlxap,up-fail,load-fail,unsafe-method,nm-unavailable,dns-wrong,dns-extra,proxy,proxy-healthy,proxy-container}
  rm -f "$MOCK_ROOT"/{duplicate-profile,duplicate-active,foreign-active,down-fail-still-active,down-fail-inactive,delete-fail,forwarding-at-up}
  rm -f "$MOCK_ROOT"/{ip-fail-v4-default,ip-fail-v4-rules,ip-fail-v4-routeget,iptables-save-fail,nm-device-query-fail-wlxap,nm-device-query-fail-wlxpeer,nm-device-query-fail-wlxphy}
  rm -f "$MOCK_ROOT"/{dnsmasq-active,timer-start-fail,timer-enable-fail,timer-ok,loaded-keyfile,installer-proxy,project-state}
  : >"$MOCK_ROOT/nmcli.log"; : >"$MOCK_ROOT/docker.log"; : >"$MOCK_ROOT/curl.log"
  : >"$MOCK_ROOT/systemctl.log"
  rm -rf "$UBTS_TEST_NM_RUNTIME_DIR" "$UBTS_TEST_NM_PERSISTENT_DIR" "$UBTS_TEST_RUNTIME_STATE_DIR"
  rm -f "$AUTO_CONFIG"
  rm -rf "$FAKE_SYS/wlxpeer" "$FAKE_SYS/wlxphy"
  rm -f "$MOCK_ROOT/wiphy-wlxpeer" "$MOCK_ROOT/wiphy-wlxphy"
  PROFILE_CREATED_BY_TRANSACTION=0; PROFILE_FILE_CREATED_BY_TRANSACTION=0; PROFILE_UUID_BY_TRANSACTION=""
  ROLLBACK_RUNNING=0; BASELINE_ROUTING=""; BASELINE_UPLINK=""; BASELINE_TARGET_FORWARDING=""
  BASELINE_TARGET_IPV6_FORWARDING=""
}

PASS=0
FAIL=0
run_test() {
  local name="$1" status; shift
  set +e
  (set -Eeuo pipefail; "$@")
  status=$?
  set -e
  if ((status == 0)); then printf 'PASS: %s\n' "$name"; PASS=$((PASS + 1))
  else printf 'FAIL: %s\n' "$name" >&2; FAIL=$((FAIL + 1)); fi
}

test_policy_default_detected() {
  reset_network
  printf '%s\n' 'default via 198.51.100.1 dev wlxap table 100' >"$MOCK_ROOT/routes4"
  all_default_route_uses_interface_named wlxap
}

test_policy_rule_detected() {
  reset_network
  printf '%s\n' '100: from all iif wlxap lookup 100' >"$MOCK_ROOT/rules4"
  rules_use_interface_named wlxap
}

test_same_usb_sibling_active_rejected() {
  reset_network
  make_netdev wlxpeer "$FAKE_DEVICES/7-1:1.1" 8
  printf '%s\n' SiblingUplink >"$MOCK_ROOT/active-wlxpeer"
  ! (assert_usb_wifi_ap_interface >/dev/null 2>&1)
}

test_same_wiphy_sibling_active_rejected() {
  reset_network
  make_netdev wlxphy "$FAKE_DEVICES_2/8-1:1.0" 7
  printf '%s\n' OtherUsbSamePhy >"$MOCK_ROOT/active-wlxphy"
  ! (assert_usb_wifi_ap_interface >/dev/null 2>&1)
}

test_atomic_complete_profile() {
  reset_network
  create_profile_atomic
  [[ -f "$MOCK_ROOT/loaded-keyfile" ]]
  [[ "$(stat -c %a "$(profile_file_path)")" == 600 ]]
  grep -Fqx 'autoconnect=false' "$MOCK_ROOT/loaded-keyfile"
  grep -Fqx 'method=manual' "$MOCK_ROOT/loaded-keyfile"
  grep -Fqx 'never-default=true' "$MOCK_ROOT/loaded-keyfile"
  grep -Fqx 'method=disabled' "$MOCK_ROOT/loaded-keyfile"
  if grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"; then return 1; fi
}

test_transaction_uuid_protects_replaced_keyfile() {
  reset_network
  create_profile_atomic
  write_baseline_state
  sed -i 's/^uuid=.*/uuid=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/' "$(profile_file_path)"
  set +e
  rollback_activation >/dev/null 2>&1
  result=$?
  set -e
  [[ "$result" == 70 && -f "$(profile_file_path)" ]]
}

test_existing_profile_not_deleted() {
  reset_network
  touch "$MOCK_ROOT/profile"
  if create_profile_atomic; then return 1; fi
  if grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"; then return 1; fi
}

test_persistent_keyfile_not_overwritten() {
  reset_network
  mkdir -p "$UBTS_TEST_NM_PERSISTENT_DIR"
  printf '%s\n' '[connection]' 'id=foreign' >"$(persistent_profile_file_path)"
  if create_profile_atomic; then return 1; fi
  [[ -f "$(persistent_profile_file_path)" && ! -e "$(profile_file_path)" ]]
}

test_partial_up_verified_rollback() {
  reset_network
  create_profile_atomic
  write_baseline_state
  touch "$MOCK_ROOT/up-fail"
  if activate_loaded_profile; then return 1; fi
  rollback_activation || return 1
  [[ ! -e "$MOCK_ROOT/profile" && ! -e "$MOCK_ROOT/active" && ! -e "$(profile_file_path)" ]]
  grep -q 'compose .* down' "$MOCK_ROOT/docker.log"
  if grep -q 'StrongPass_123' "$MOCK_ROOT/nmcli.log"; then return 1; fi
  if compgen -G "${UBTS_TEST_RUNTIME_STATE_DIR}/ubts-offline-ap-passwd.*" >/dev/null; then return 1; fi
}

test_partial_load_verified_rollback() {
  reset_network
  touch "$MOCK_ROOT/load-fail"
  if (activate_ap_locked >/dev/null 2>&1); then return 1; fi
  [[ ! -e "$MOCK_ROOT/profile" && ! -e "$(profile_file_path)" &&
     ! -e "$UBTS_TEST_RUNTIME_STATE_DIR" ]]
}

test_profile_assertion_failure_verified_rollback() {
  reset_network
  touch "$MOCK_ROOT/unsafe-method"
  if (activate_ap_locked >/dev/null 2>&1); then return 1; fi
  [[ ! -e "$MOCK_ROOT/profile" && ! -e "$(profile_file_path)" &&
     ! -e "$UBTS_TEST_RUNTIME_STATE_DIR" ]]
}

test_incomplete_rollback_is_fatal() {
  local baseline_hash
  reset_network
  create_profile_atomic
  write_baseline_state
  baseline_hash="$(sha256sum "$ROUTING_BASELINE_FILE")"
  printf '%s\n' 'default via 203.0.113.1 dev changed0 table 300' >"$MOCK_ROOT/routes4"
  set +e
  rollback_activation >/dev/null 2>&1
  result=$?
  set -e
  [[ "$result" == 70 ]] || return 1
  runtime_transaction_state_exists || return 1
  ! (write_baseline_state >/dev/null 2>&1) || return 1
  [[ "$(sha256sum "$ROUTING_BASELINE_FILE")" == "$baseline_hash" ]]
}

test_absent_usb_stops_proxy_and_profile() {
  reset_network
  create_profile_atomic
  write_baseline_state
  touch "$MOCK_ROOT/proxy" "$MOCK_ROOT/proxy-healthy" "$MOCK_ROOT/proxy-container"
  handle_absent_usb
  [[ ! -e "$MOCK_ROOT/proxy" && ! -e "$MOCK_ROOT/proxy-container" &&
     ! -e "$MOCK_ROOT/profile" && ! -e "$(profile_file_path)" ]]
}

test_absent_usb_refuses_orphan_keyfile() {
  reset_network
  create_profile_atomic
  write_baseline_state
  rm -f "$MOCK_ROOT/profile"
  if (handle_absent_usb >/dev/null 2>&1); then return 1; fi
  [[ -f "$(profile_file_path)" ]]
}

test_absent_usb_refuses_partial_runtime_state() {
  reset_network
  mkdir -p "$UBTS_TEST_RUNTIME_STATE_DIR"
  printf '%s\n' partial >"$ROUTING_BASELINE_FILE"
  if (handle_absent_usb >/dev/null 2>&1); then return 1; fi
  [[ -f "$ROUTING_BASELINE_FILE" ]]
}

test_timer_start_rollback() {
  reset_network
  printf '%s\n' config >"$AUTO_CONFIG"
  touch "$MOCK_ROOT/timer-start-fail"
  if enable_timer_transaction; then return 1; fi
  rm -f "$MOCK_ROOT/timer-start-fail"
  [[ ! -e "$AUTO_CONFIG" ]]
  grep -q 'systemctl disable --now' "$MOCK_ROOT/systemctl.log"
}

test_timer_enable_rollback() {
  reset_network
  printf '%s\n' config >"$AUTO_CONFIG"
  touch "$MOCK_ROOT/timer-enable-fail"
  if enable_timer_transaction; then return 1; fi
  [[ ! -e "$AUTO_CONFIG" ]]
  grep -q 'systemctl disable --now' "$MOCK_ROOT/systemctl.log"
}

test_auto_config_persists_proxy_image() {
  reset_network
  PROXY_IMAGE=nginx:validated-arm64
  write_auto_config
  PROXY_IMAGE=""; INTERFACE=""; SSID=""; PASSWORD_FILE=""; PORTAINER_TLS_NAME=""
  load_auto_config
  [[ "$PROXY_IMAGE" == nginx:validated-arm64 ]] || return 1
  if grep -q 'StrongPass_123' "$AUTO_CONFIG"; then return 1; fi
}

test_dns_all_names_required() {
  reset_network
  assert_ap_dns
  touch "$MOCK_ROOT/dns-wrong"
  ! (assert_ap_dns >/dev/null 2>&1) || return 1
  rm -f "$MOCK_ROOT/dns-wrong"
  touch "$MOCK_ROOT/dns-extra"
  ! (assert_ap_dns >/dev/null 2>&1)
}

test_dry_run_writes_no_runtime_state() {
  reset_network
  DRY_RUN=1
  activate_ap_locked >/dev/null
  DRY_RUN=0
  [[ ! -e "$UBTS_TEST_RUNTIME_STATE_DIR" && ! -e "$(profile_file_path)" ]]
}

test_deactivate_dry_run_changes_nothing() {
  reset_network
  create_profile_atomic
  write_baseline_state
  touch "$MOCK_ROOT/active" "$MOCK_ROOT/proxy" "$MOCK_ROOT/proxy-healthy" \
    "$MOCK_ROOT/proxy-container" \
    "$MOCK_ROOT/dnsmasq-active"
  printf '%s\n' ubts-offline-ap-wlxap >"$MOCK_ROOT/active-wlxap"
  DRY_RUN=1
  deactivate_ap_locked >/dev/null
  DRY_RUN=0
  [[ -e "$MOCK_ROOT/profile" && -e "$MOCK_ROOT/active" && -e "$MOCK_ROOT/proxy" &&
     -e "$MOCK_ROOT/proxy-container" &&
     -e "$MOCK_ROOT/dnsmasq-active" && -e "$(profile_file_path)" &&
     -e "$ROUTING_BASELINE_FILE" ]]
}

test_manual_local_only_dhcp_dns() {
  reset_network
  create_profile_atomic
  grep -Fqx 'method=manual' "$MOCK_ROOT/loaded-keyfile"
  if grep -Fqx 'method=shared' "$MOCK_ROOT/loaded-keyfile"; then return 1; fi
  grep -Fqx 'no-resolv' "$SCRIPT_DIR/dnsmasq.conf.template"
  grep -Fqx 'dhcp-option=3' "$SCRIPT_DIR/dnsmasq.conf.template"
  if grep -Eqi 'masquerade|ip_forward' "$SCRIPT_DIR/dnsmasq.conf.template"; then return 1; fi
  mkdir -p "$RUNTIME_STATE_DIR"
  render_dnsmasq_config
  grep -Fqx 'interface=wlxap' "$DNSMASQ_RUNTIME_CONFIG"
  grep -Fqx 'address=/bts-docker.sarihub.com/10.77.0.1' "$DNSMASQ_RUNTIME_CONFIG"
  grep -Fqx 'address=/jetson-ssh.sarihub.com/10.77.0.1' "$DNSMASQ_RUNTIME_CONFIG"
}

test_target_forwarding_restored() {
  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding")" == 0 ]]
  rollback_activation || return 1
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding")" == 1 ]]
}

test_firewall_drift_is_fatal() {
  reset_network
  create_profile_atomic
  write_baseline_state
  printf '%s\n' '*filter' ':INPUT DROP [0:0]' 'COMMIT' >"$MOCK_ROOT/iptables"
  set +e
  rollback_activation >/dev/null 2>&1
  result=$?
  set -e
  [[ "$result" == 70 ]]
}

test_networkmanager_unavailable_is_fatal() {
  reset_network
  create_profile_atomic
  write_baseline_state
  touch "$MOCK_ROOT/nm-unavailable"
  set +e
  rollback_activation >/dev/null 2>&1
  result=$?
  set -e
  [[ "$result" == 70 && -f "$ROUTING_BASELINE_FILE" ]]
}

test_proxy_health_checks_both_backends() {
  local expected actual
  reset_network
  touch "$MOCK_ROOT/proxy" "$MOCK_ROOT/proxy-healthy"
  if ! proxy_config_fingerprint >"$MOCK_ROOT/proxy-fingerprint"; then
    printf 'fingerprint generation failed for image id: %q\n' \
      "$(docker image inspect -f '{{.Id}}' "$PROXY_IMAGE" 2>&1 || true)" >&2
    return 1
  fi
  expected="$(proxy_config_fingerprint)" || return 1
  actual="$(docker inspect -f '{{index .Config.Labels "com.unist.ubts.offline-ap.config-sha256"}}' \
    ubts-offline-proxy)"
  if [[ "$actual" != "$expected" ]]; then
    printf 'fingerprint mismatch: expected=%s actual=%s image=%s\n' "$expected" "$actual" "$PROXY_IMAGE" >&2
    return 1
  fi
  assert_proxy_health
  grep -q 'http://bts.sarihub.com/api/health' "$MOCK_ROOT/curl.log"
  grep -q 'http://bts-docker.sarihub.com/api/status' "$MOCK_ROOT/curl.log"
}

test_reconcile_dns_failure_rolls_back() {
  reset_network
  create_profile_atomic
  write_baseline_state
  touch "$MOCK_ROOT/active" "$MOCK_ROOT/dns-wrong"
  printf '%s\n' ubts-offline-ap-wlxap >"$MOCK_ROOT/active-wlxap"
  if (reconcile_active_ap >/dev/null 2>&1); then return 1; fi
  [[ ! -e "$MOCK_ROOT/profile" && ! -e "$MOCK_ROOT/active" && ! -e "$(profile_file_path)" ]]
}

installer_state_check() (
  export UBTS_OFFLINE_AP_INSTALL_TESTING=1
  # shellcheck source=../install.sh
  source "$SCRIPT_DIR/install.sh"
  assert_clean_install_state
)

test_installer_race_guards() {
  reset_network
  installer_state_check
  touch "$MOCK_ROOT/timer-ok"
  ! installer_state_check >/dev/null 2>&1 || return 1
  rm -f "$MOCK_ROOT/timer-ok"
  touch "$MOCK_ROOT/installer-proxy"
  ! installer_state_check >/dev/null 2>&1 || return 1
  rm -f "$MOCK_ROOT/installer-proxy"
  touch "$MOCK_ROOT/profile"
  ! installer_state_check >/dev/null 2>&1 || return 1
  rm -f "$MOCK_ROOT/profile"
  touch "$MOCK_ROOT/project-state"
  ! installer_state_check >/dev/null 2>&1 || return 1
  grep -Fq '/opt/ubts-offline-ap' "$SCRIPT_DIR/install.sh"
}

test_forwarding_is_disabled_before_nm_up_and_restored() {
  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  activate_loaded_profile
  [[ "$(<"$MOCK_ROOT/forwarding-at-up")" == 'ipv4=0 ipv6=0' ]]
  rollback_activation
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding")" == 1 ]]
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/wlxap/forwarding")" == 1 ]]
}

rollback_failure_preserves_transaction() {
  local marker="$1" expect_delete_attempt="${2:-0}" result
  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  activate_loaded_profile
  touch "$MOCK_ROOT/$marker"
  set +e
  rollback_activation >/dev/null 2>&1
  result=$?
  set -e
  [[ "$result" == 70 ]]
  [[ -f "$MOCK_ROOT/profile" && -f "$(profile_file_path)" ]]
  [[ -f "$ROUTING_BASELINE_FILE" && -f "$UPLINK_BASELINE_FILE" ]]
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding")" == 0 ]]
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/wlxap/forwarding")" == 0 ]]
  if ((expect_delete_attempt)); then
    grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"
  else
    if grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"; then return 1; fi
  fi
}

test_down_failure_while_active_preserves_transaction() {
  rollback_failure_preserves_transaction down-fail-still-active
}

test_down_failure_after_inactive_preserves_transaction() {
  rollback_failure_preserves_transaction down-fail-inactive
}

test_delete_failure_preserves_transaction() {
  rollback_failure_preserves_transaction delete-fail 1
}

test_exact_keyfile_tamper_is_refused_without_delete() {
  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  sed -i 's/^ssid=.*/ssid=Tampered/' "$(profile_file_path)"
  write_auto_config
  if (ensure_ap >/dev/null 2>&1); then return 1; fi
  [[ -f "$MOCK_ROOT/profile" && -f "$(profile_file_path)" && -f "$ROUTING_BASELINE_FILE" ]]
  if grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"; then return 1; fi
}

test_baseline_or_live_drift_is_refused_before_delete() {
  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  printf '%s\n' tampered >>"$ROUTING_BASELINE_FILE"
  write_auto_config
  if (ensure_ap >/dev/null 2>&1); then return 1; fi
  [[ -f "$MOCK_ROOT/profile" && -f "$(profile_file_path)" && -f "$ROUTING_BASELINE_FILE" ]]
  if grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"; then return 1; fi

  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  printf '%s\n' 'default via 203.0.113.1 dev changed0 table 300' >"$MOCK_ROOT/routes4"
  write_auto_config
  if (ensure_ap >/dev/null 2>&1); then return 1; fi
  [[ -f "$MOCK_ROOT/profile" && -f "$(profile_file_path)" && -f "$ROUTING_BASELINE_FILE" ]]
  if grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"; then return 1; fi
}

test_inactive_fail_closed_transaction_is_recovered_and_reactivated() {
  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  proxy_config_fingerprint >"$MOCK_ROOT/proxy-fingerprint"
  write_auto_config
  ensure_ap
  [[ -f "$MOCK_ROOT/active" && -f "$MOCK_ROOT/profile" && -f "$(profile_file_path)" ]]
  [[ "$(<"$MOCK_ROOT/forwarding-at-up")" == 'ipv4=0 ipv6=0' ]]
  grep -q 'connection delete' "$MOCK_ROOT/nmcli.log"
  rollback_activation
}

test_active_sibling_failure_gets_verified_cleanup() {
  reset_network
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  activate_loaded_profile
  make_netdev wlxpeer "$FAKE_DEVICES/7-1:1.1" 8
  printf '%s\n' SiblingUplink >"$MOCK_ROOT/active-wlxpeer"
  write_auto_config
  if (ensure_ap >/dev/null 2>&1); then return 1; fi
  [[ ! -e "$MOCK_ROOT/profile" && ! -e "$MOCK_ROOT/active" && ! -e "$(profile_file_path)" ]]
  [[ ! -e "$UBTS_TEST_RUNTIME_STATE_DIR" ]]
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding")" == 1 ]]
}

active_policy_preflight_failure_gets_verified_cleanup() {
  local kind="$1"
  reset_network
  if [[ "$kind" == default ]]; then
    printf '%s\n' 'default via 198.51.100.1 dev wlxap table 100' >"$MOCK_ROOT/routes4"
  else
    printf '%s\n' '100: from all iif wlxap lookup 100' >"$MOCK_ROOT/rules4"
  fi
  create_profile_atomic
  write_baseline_state
  disable_target_forwarding
  activate_loaded_profile
  write_auto_config
  if (ensure_ap >/dev/null 2>&1); then return 1; fi
  [[ ! -e "$MOCK_ROOT/profile" && ! -e "$MOCK_ROOT/active" && ! -e "$(profile_file_path)" ]]
  [[ ! -e "$UBTS_TEST_RUNTIME_STATE_DIR" ]]
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv4/conf/wlxap/forwarding")" == 1 ]]
  [[ "$(<"$UBTS_TEST_PROC_SYS_ROOT/net/ipv6/conf/wlxap/forwarding")" == 1 ]]
}

test_active_default_route_failure_gets_verified_cleanup() {
  active_policy_preflight_failure_gets_verified_cleanup default
}

test_active_policy_rule_failure_gets_verified_cleanup() {
  active_policy_preflight_failure_gets_verified_cleanup rule
}

test_overlapping_address_and_routes_are_rejected() {
  reset_network
  printf '%s\n' '8: docker0 inet 10.77.0.8/16 brd 10.77.255.255 scope global docker0' >"$MOCK_ROOT/addresses4"
  if (assert_ap_subnet_available 0 >/dev/null 2>&1); then return 1; fi

  reset_network
  printf '%s\n' '10.77.0.0/16 dev docker0 proto kernel scope link src 10.77.0.8' >"$MOCK_ROOT/routes4all"
  if (assert_ap_subnet_available 0 >/dev/null 2>&1); then return 1; fi

  reset_network
  printf '%s\n' '10.77.0.0/24 dev wlxap proto kernel scope link src 10.77.0.1' \
    '10.77.0.0/24 dev wlxap table 901 proto static scope link src 10.77.0.1' >"$MOCK_ROOT/routes4all"
  printf '%s\n' '7: wlxap inet 10.77.0.1/24 brd 10.77.0.255 scope global wlxap' >"$MOCK_ROOT/addresses4"
  if (assert_ap_subnet_available 1 >/dev/null 2>&1); then return 1; fi
}

test_nmcli_device_query_failure_is_not_inactive() {
  reset_network
  touch "$MOCK_ROOT/nm-device-query-fail-wlxap"
  if (assert_usb_wifi_ap_interface >/dev/null 2>&1); then return 1; fi
  if interface_has_no_active_connection; then return 1; fi
}

test_snapshot_command_failures_fail_preflight() {
  local marker
  for marker in iptables-save-fail ip-fail-v4-default ip-fail-v4-rules ip-fail-v4-routeget; do
    reset_network
    if [[ "$marker" == ip-fail-v4-routeget ]]; then
      printf '%s\n' 'default via 192.0.2.1 dev uplink0' >"$MOCK_ROOT/routes4"
    fi
    touch "$MOCK_ROOT/$marker"
    if (preflight_common 0 >/dev/null 2>&1); then return 1; fi
  done
}

test_no_default_route_allows_offline_ap_preflight() {
  reset_network
  : >"$MOCK_ROOT/routes4"
  touch "$MOCK_ROOT/ip-fail-v4-routeget"
  preflight_common 0
  [[ "$(route_get_signature -4 1.1.1.1)" == '-4 unavailable' ]]
}

test_atomic_profile_publish_never_clobbers_racing_destination() (
  reset_network
  # Invoked indirectly by create_profile_atomic.
  # shellcheck disable=SC2329
  ln() {
    printf '%s\n' foreign-writer >"$3"
    command ln -- "$2" "$3"
  }
  if create_profile_atomic >/dev/null 2>&1; then return 1; fi
  [[ "$(<"$(profile_file_path)")" == foreign-writer ]]
  [[ ! -e "$MOCK_ROOT/profile" ]]
)

run_test 'all-table policy default detected' test_policy_default_detected
run_test 'policy iif/oif rule detected' test_policy_rule_detected
run_test 'same physical USB active sibling rejected' test_same_usb_sibling_active_rejected
run_test 'same wiphy active sibling on another USB path rejected' test_same_wiphy_sibling_active_rejected
run_test 'complete keyfile atomically loaded and asserted' test_atomic_complete_profile
run_test 'existing same-name profile is never deleted' test_existing_profile_not_deleted
run_test 'persistent same-name keyfile is never overwritten' test_persistent_keyfile_not_overwritten
run_test 'transaction UUID prevents deletion of a replaced keyfile' test_transaction_uuid_protects_replaced_keyfile
run_test 'partial nmcli up gets verified rollback' test_partial_up_verified_rollback
run_test 'partial nmcli load gets verified rollback' test_partial_load_verified_rollback
run_test 'post-load property assertion failure gets verified rollback' test_profile_assertion_failure_verified_rollback
run_test 'routing mismatch marks rollback incomplete/fatal' test_incomplete_rollback_is_fatal
run_test 'timer USB-absent path stops proxy/profile' test_absent_usb_stops_proxy_and_profile
run_test 'USB-absent path refuses an orphan keyfile' test_absent_usb_refuses_orphan_keyfile
run_test 'USB-absent path refuses partial runtime baseline' test_absent_usb_refuses_partial_runtime_state
run_test 'timer start failure removes auto state' test_timer_start_rollback
run_test 'timer enable failure removes partial timer/config state' test_timer_enable_rollback
run_test 'auto config persists image reference but not password value' test_auto_config_persists_proxy_image
run_test 'all AP DNS answers are mandatory' test_dns_all_names_required
run_test 'activation dry-run creates no runtime state' test_dry_run_writes_no_runtime_state
run_test 'deactivation dry-run changes no managed state' test_deactivate_dry_run_changes_nothing
run_test 'manual AP and local-only DHCP/DNS replace shared/NAT' test_manual_local_only_dhcp_dns
run_test 'target-interface forwarding is restored on rollback' test_target_forwarding_restored
run_test 'firewall drift marks rollback incomplete/fatal' test_firewall_drift_is_fatal
run_test 'NetworkManager verification failure marks rollback fatal' test_networkmanager_unavailable_is_fatal
run_test 'proxy health checks dashboard and Portainer' test_proxy_health_checks_both_backends
run_test 'active AP DNS failure triggers verified rollback' test_reconcile_dns_failure_rolls_back
run_test 'installer refuses timer/container/profile race state' test_installer_race_guards
run_test 'forwarding is zero before nm up and baseline is restored' test_forwarding_is_disabled_before_nm_up_and_restored
run_test 'active down failure preserves fail-closed transaction' test_down_failure_while_active_preserves_transaction
run_test 'inactive down failure preserves fail-closed transaction' test_down_failure_after_inactive_preserves_transaction
run_test 'profile delete failure preserves fail-closed transaction' test_delete_failure_preserves_transaction
run_test 'tampered keyfile is refused before deletion' test_exact_keyfile_tamper_is_refused_without_delete
run_test 'baseline and live drift are refused before deletion' test_baseline_or_live_drift_is_refused_before_delete
run_test 'inactive exact state is cleaned and reactivated' test_inactive_fail_closed_transaction_is_recovered_and_reactivated
run_test 'active sibling preflight failure gets verified cleanup' test_active_sibling_failure_gets_verified_cleanup
run_test 'active default-route preflight failure gets verified cleanup' test_active_default_route_failure_gets_verified_cleanup
run_test 'active policy-rule preflight failure gets verified cleanup' test_active_policy_rule_failure_gets_verified_cleanup
run_test 'overlapping addresses and all-table routes are rejected' test_overlapping_address_and_routes_are_rejected
run_test 'nmcli device query failure is not treated as inactive' test_nmcli_device_query_failure_is_not_inactive
run_test 'snapshot command failures fail preflight' test_snapshot_command_failures_fail_preflight
run_test 'no default route is a valid offline AP baseline' test_no_default_route_allows_offline_ap_preflight
run_test 'atomic profile publish never clobbers a racing destination' test_atomic_profile_publish_never_clobbers_racing_destination

printf 'Required safety regression tests: %d passed, %d failed\n' "$PASS" "$FAIL"
((FAIL == 0))
