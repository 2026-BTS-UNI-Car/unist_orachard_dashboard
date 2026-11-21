#!/usr/bin/env python3
"""Supervise the Jetson's single Wi-Fi radio between station and offline AP.

The service never changes Ethernet, never creates a default route for AP
clients, and on its own only activates already-saved Wi-Fi station profiles.
Dashboard requests are queued so the HTTP response can complete before Wi-Fi
is cut over.

Wi-Fi management (2026-11-16): the dashboard can list the networks of a scan,
list the saved station profiles, change their autoconnect and priority, and
join a network with a password. Joining is always a trial: the controller
tries the network and goes back to the previous connection (Wi-Fi profile or
AP) unless the operator confirms from the dashboard within
UBTS_WIFI_TRIAL_SECONDS (default 150 s). A trial profile is in-memory
(/run/NetworkManager) with autoconnect off until it is confirmed, so a reboot
during a trial forgets it. Passwords reach this program only on stdin and go
straight into a root-only NetworkManager keyfile; they are never put on a
command line, printed or logged.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import string
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - controller only runs on Linux
    fcntl = None  # type: ignore[assignment]


INTERFACE = os.environ.get("UBTS_WIFI_INTERFACE", "wlP1p1s0")
SSID = os.environ.get("UBTS_AP_SSID", "BTS_Jetson")
AP_ADDRESS = "10.77.0.1"
AP_CIDR = f"{AP_ADDRESS}/24"
AP_CONNECTION = "ubts-network-mode-ap"
AP_KEYFILE = Path(f"/etc/NetworkManager/system-connections/{AP_CONNECTION}.nmconnection")
PASSWORD_FILE = Path(os.environ.get("UBTS_AP_PASSWORD_FILE", "/etc/ubts-offline-ap/wpa2.pass"))
STATE_DIR = Path("/var/lib/ubts-network-mode")
RUNTIME_DIR = Path("/run/ubts-network-mode")
STATUS_FILE = RUNTIME_DIR / "status.json"
COMMAND_FILE = RUNTIME_DIR / "command.json"
LOCK_FILE = RUNTIME_DIR / "controller.lock"
AP_UUID_FILE = STATE_DIR / "ap_uuid"
OFFLINE_AP_DIR = Path("/opt/ubts-offline-ap")
DNSMASQ_CONFIG = Path("/run/ubts-offline-ap/dnsmasq.conf")
DNSMASQ_UNIT = "ubts-offline-ap-dnsmasq.service"
PROXY_IMAGE = os.environ.get("UBTS_OFFLINE_PROXY_IMAGE", "nginx:stable-alpine")
PORTAINER_TLS_NAME = os.environ.get("UBTS_PORTAINER_TLS_NAME", "localhost")
# Host names the AP's local DNS answers with the AP address and the proxy serves (/etc/ubts-network-mode.conf).
# The defaults are reserved example names; set the real ones on the vehicle.
DASHBOARD_HOSTNAME = os.environ.get("UBTS_AP_DASHBOARD_HOSTNAME", "dashboard.example.invalid")
PORTAINER_HOSTNAME = os.environ.get("UBTS_AP_PORTAINER_HOSTNAME", "docker.example.invalid")
SSH_HOSTNAME = os.environ.get("UBTS_AP_SSH_HOSTNAME", "ssh.example.invalid")
AP_IDLE_S = max(10, int(os.environ.get("UBTS_AP_IDLE_SECONDS", "60")))
WIFI_SCAN_S = max(10, int(os.environ.get("UBTS_WIFI_SCAN_SECONDS", "60")))
POLL_S = max(0.5, float(os.environ.get("UBTS_NETWORK_POLL_SECONDS", "1")))

# ---- Wi-Fi management (dashboard Network page) ------------------------------
# A trial ends within TRIAL_S: confirmed by the operator, or reverted. The new
# connection must have an IPv4 address and a default route within TRIAL_LINK_S.
TRIAL_S = min(300, max(60, int(os.environ.get("UBTS_WIFI_TRIAL_SECONDS", "150"))))
TRIAL_LINK_S = min(90, max(20, int(os.environ.get("UBTS_WIFI_TRIAL_LINK_SECONDS", "45"))))
TRIAL_LOST_GRACE_S = 10.0
SCAN_FILE = RUNTIME_DIR / "scan.json"
TRIAL_FILE = RUNTIME_DIR / "trial.json"
BACKUP_DIR = RUNTIME_DIR / "backup"
NM_PERSISTENT_DIR = Path("/etc/NetworkManager/system-connections")
NM_RUNTIME_DIR = Path("/run/NetworkManager/system-connections")
TRIAL_KEYFILE_PREFIX = "ubts-wifi-trial-"
SCAN_MIN_INTERVAL_S = 10.0
SCAN_MAX_AGE_FOR_CONNECT_S = 600.0
COMMAND_MAX_AGE_S = 30.0
TRIAL_STATUS_MAX_AGE_S = 60.0
STDIN_MAX_BYTES = 4096
PRIORITY_MIN, PRIORITY_MAX = -999, 999
LINK_REFRESH_S = 5.0
SCAN_RESULT_LIMIT = 60
WIFI_TYPE = "802-11-wireless"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TXN_RE = re.compile(r"[0-9a-f]{16}")
WIFI_COMMANDS = ("wifi-scan", "wifi-saved", "wifi-connect", "wifi-confirm", "wifi-revert", "wifi-profile")
PROFILE_DETAIL_FIELDS = (
    "802-11-wireless.ssid",
    "802-11-wireless.mode",
    "802-11-wireless.band",
    "802-11-wireless-security.key-mgmt",
)


class CommandError(RuntimeError):
    pass


class InputError(ValueError):
    """A request that cannot be accepted. The message is a short code and never contains a value."""


def run(
    args: list[str],
    *,
    timeout: float = 15.0,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"{args[0]} execution failed: {exc}") from exc
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        raise CommandError(f"{args[0]} failed: {message[:500]}")
    return result


RECORDER_CONTAINER = "unist-orachard-ros2-data-recorder"
RECORDER_STATUS_URL = "http://127.0.0.1:8790/status"


def refresh_ros_discovery(reason: str) -> str:
    """After the Wi-Fi interface changed (AP up, or a known Wi-Fi joined): the data recorder's ROS participant may keep
    seeing only part of the topics (2026-11-18 field: 9 of 17 after the AP switch until it was restarted). Restart the
    recorder container, but only while it is idle (never during a recording). Returns 'restarted', 'busy', 'unknown'
    or 'failed'; never raises."""
    try:
        answer = run(["curl", "-fsS", "--max-time", "3", RECORDER_STATUS_URL], check=False)
        status = json.loads(answer.stdout or "{}")
    except Exception:
        return "unknown"
    if status.get("active") or status.get("state") not in ("idle", None):
        return "busy"
    try:
        done = run(["docker", "restart", "-t", "20", RECORDER_CONTAINER], timeout=40, check=False)
    except Exception:
        return "failed"
    return "restarted" if getattr(done, "returncode", 1) == 0 else "failed"


def write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Replace a file atomically. The temporary file has its final mode from the start."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        temporary.unlink()
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    write_private(path, data.encode("utf-8"), mode)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def nmcli_value(*args: str, check: bool = True) -> str:
    return run(["nmcli", *args], check=check).stdout.strip()


def interface_snapshot() -> dict[str, Any]:
    output = nmcli_value("-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION", "device", "show", INTERFACE, check=False)
    state = ""
    connection = ""
    for line in output.splitlines():
        if line.startswith("GENERAL.STATE:"):
            state = line.split(":", 1)[1]
        elif line.startswith("GENERAL.CONNECTION:"):
            connection = line.split(":", 1)[1]
    connected = state.startswith("100")
    return {
        "state": state or "unknown",
        "connection": connection,
        "connected": connected,
        "ap_active": connected and connection == AP_CONNECTION,
        "wifi_connected": connected and bool(connection) and connection not in {"--", AP_CONNECTION},
    }


def ethernet_connected() -> bool:
    output = nmcli_value("-t", "--escape", "no", "-f", "TYPE,STATE", "device", "status", check=False)
    return any(line.strip() == "ethernet:connected" for line in output.splitlines())


def ap_client_count() -> int:
    result = run(["iw", "dev", INTERFACE, "station", "dump"], timeout=4, check=False)
    return sum(1 for line in result.stdout.splitlines() if line.lstrip().startswith("Station "))


def read_ap_uuid() -> str:
    try:
        value = AP_UUID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value if re.fullmatch(r"[0-9a-fA-F-]{36}", value) else ""


def validate_password() -> str:
    try:
        stat_result = PASSWORD_FILE.stat()
        password = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CommandError(f"AP password file unavailable: {PASSWORD_FILE}") from exc
    if stat_result.st_uid != 0 or stat_result.st_mode & 0o077:
        raise CommandError("AP password file must be root-owned and mode 0600")
    if not 8 <= len(password) <= 63:
        raise CommandError("AP WPA2 password must contain 8-63 characters")
    return password


def connection_exists(identifier: str) -> bool:
    result = run(["nmcli", "connection", "show", "uuid", identifier], check=False)
    return result.returncode == 0


def ensure_ap_profile() -> str:
    password = validate_password()
    managed_uuid = read_ap_uuid()
    if managed_uuid and connection_exists(managed_uuid):
        mode = nmcli_value("-g", "802-11-wireless.mode", "connection", "show", "uuid", managed_uuid)
        profile_ssid = nmcli_value("-g", "802-11-wireless.ssid", "connection", "show", "uuid", managed_uuid)
        autoconnect = nmcli_value("-g", "connection.autoconnect", "connection", "show", "uuid", managed_uuid)
        address = nmcli_value("-g", "ipv4.addresses", "connection", "show", "uuid", managed_uuid)
        if mode == "ap" and profile_ssid == SSID and autoconnect == "no" and AP_CIDR in address:
            band = nmcli_value("-g", "802-11-wireless.band", "connection", "show", "uuid", managed_uuid)
            channel = nmcli_value("-g", "802-11-wireless.channel", "connection", "show", "uuid", managed_uuid)
            powersave = nmcli_value("-g", "802-11-wireless.powersave", "connection", "show", "uuid", managed_uuid)
            if band != "bg" or channel != "6" or not (powersave.startswith("2") or powersave == "disable"):
                if interface_snapshot()["ap_active"]:
                    raise CommandError("active AP must be disconnected before changing its 2.4GHz profile")
                run(["nmcli", "connection", "modify", "uuid", managed_uuid,
                     "802-11-wireless.band", "bg", "802-11-wireless.channel", "6",
                     "802-11-wireless.powersave", "2"])
            return managed_uuid
        snapshot = interface_snapshot()
        if snapshot["ap_active"]:
            raise CommandError("active managed AP profile differs from requested configuration")
        run(["nmcli", "connection", "delete", "uuid", managed_uuid], check=False)
    elif nmcli_value("-t", "-f", "NAME", "connection", "show", check=False).splitlines().count(AP_CONNECTION):
        raise CommandError(f"unowned NetworkManager profile exists: {AP_CONNECTION}")

    managed_uuid = str(uuid.uuid4())
    keyfile = "\n".join(
        [
            "[connection]",
            f"id={AP_CONNECTION}",
            f"uuid={managed_uuid}",
            "type=wifi",
            f"interface-name={INTERFACE}",
            "autoconnect=false",
            "",
            "[wifi]",
            "mode=ap",
            f"ssid={SSID}",
            "band=bg",
            "channel=6",
            "powersave=2",
            "",
            "[wifi-security]",
            "key-mgmt=wpa-psk",
            f"psk={password}",
            "",
            "[ipv4]",
            f"address1={AP_CIDR}",
            "method=manual",
            "never-default=true",
            "",
            "[ipv6]",
            "method=disabled",
            "",
        ]
    )
    atomic_write(AP_KEYFILE, keyfile, 0o600)
    run(["nmcli", "connection", "load", str(AP_KEYFILE)])
    loaded_uuid = nmcli_value("-g", "connection.uuid", "connection", "show", AP_CONNECTION)
    if loaded_uuid != managed_uuid:
        AP_KEYFILE.unlink(missing_ok=True)
        raise CommandError("loaded AP profile UUID mismatch")
    atomic_write(AP_UUID_FILE, managed_uuid + "\n")
    return managed_uuid


def render_dnsmasq() -> None:
    template = (OFFLINE_AP_DIR / "dnsmasq.conf.template").read_text(encoding="utf-8")
    rendered = template.replace("${AP_INTERFACE}", INTERFACE)
    for placeholder, name in (("${DASHBOARD_HOSTNAME}", DASHBOARD_HOSTNAME), ("${PORTAINER_HOSTNAME}", PORTAINER_HOSTNAME),
                              ("${SSH_HOSTNAME}", SSH_HOSTNAME)):
        rendered = rendered.replace(placeholder, name)
    atomic_write(DNSMASQ_CONFIG, rendered, 0o600)


def proxy_environment() -> dict[str, str]:
    files = [
        OFFLINE_AP_DIR / "compose.yaml",
        OFFLINE_AP_DIR / "nginx/default.conf.template",
        OFFLINE_AP_DIR / "render-and-run.sh",
        Path("/etc/ubts-offline-ap/portainer-ca.pem"),
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.read_bytes())
    image_id = run(["docker", "image", "inspect", "-f", "{{.Id}}", PROXY_IMAGE]).stdout.strip()
    digest.update(f"proxy-image={PROXY_IMAGE}\nproxy-image-id={image_id}\nportainer-tls-name={PORTAINER_TLS_NAME}\n".encode())
    digest.update(f"dashboard-hostname={DASHBOARD_HOSTNAME}\nportainer-hostname={PORTAINER_HOSTNAME}\n".encode())
    environment = os.environ.copy()
    environment.update(
        {
            "AP_ADDRESS": AP_ADDRESS,
            "PORTAINER_TLS_NAME": PORTAINER_TLS_NAME,
            "DASHBOARD_HOSTNAME": DASHBOARD_HOSTNAME,
            "PORTAINER_HOSTNAME": PORTAINER_HOSTNAME,
            "UBTS_PROXY_CONFIG_FINGERPRINT": digest.hexdigest(),
            "UBTS_OFFLINE_PROXY_IMAGE": PROXY_IMAGE,
        }
    )
    return environment


def compose(*args: str, check: bool = True, timeout: float = 45.0) -> subprocess.CompletedProcess[str]:
    return run(
        ["docker", "compose", "-f", str(OFFLINE_AP_DIR / "compose.yaml"), *args],
        check=check,
        timeout=timeout,
        env=proxy_environment(),
    )


def validate_backends() -> None:
    run(["curl", "-fsS", "--max-time", "3", "http://127.0.0.1:8080/api/health"])
    run(["curl", "-kfsS", "--max-time", "3", "https://127.0.0.1:9443/api/status"])
    for path in [
        OFFLINE_AP_DIR / "compose.yaml",
        OFFLINE_AP_DIR / "dnsmasq.conf.template",
        Path("/etc/ubts-offline-ap/portainer-ca.pem"),
    ]:
        if not path.is_file():
            raise CommandError(f"offline AP prerequisite missing: {path}")


# ---- Wi-Fi management: parsing and validation (pure functions) --------------

def split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t -e yes` output; `\\:` and `\\\\` are escapes."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    fields.append("".join(current))
    return fields


def leading_int(text: str) -> int | None:
    match = re.match(r"\s*(-?\d+)", text or "")
    return int(match.group(1)) if match else None


def band_name(freq_mhz: int | None) -> str:
    if not freq_mhz:
        return ""
    if freq_mhz < 2500:
        return "2.4GHz"
    if freq_mhz < 5925:
        return "5GHz"
    return "6GHz"


def key_mgmt_for_security(security: str) -> str | None:
    """nmcli SECURITY column -> keyfile key-mgmt; None when the dashboard cannot join it."""
    words = set((security or "").upper().replace("/", " ").split()) - {"--"}
    if not words:
        return "none"
    if "802.1X" in words or "WEP" in words or "OWE" in words or any(word.startswith("EAP") for word in words):
        return None
    if words & {"WPA", "WPA1", "WPA2"}:
        return "wpa-psk"
    if words & {"WPA3", "SAE"}:
        return "sae"
    return None


def validate_ssid(value: Any) -> str:
    if not isinstance(value, str):
        raise InputError("ssid_required")
    if not 1 <= len(value.encode("utf-8")) <= 32:
        raise InputError("ssid_length")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise InputError("ssid_control_character")
    return value


def validate_wifi_password(value: Any, key_mgmt: str) -> str:
    if not isinstance(value, str) or not value:
        raise InputError("password_required")
    if key_mgmt == "wpa-psk" and len(value) == 64 and all(character in string.hexdigits for character in value):
        return value
    if any(not 0x20 <= ord(character) <= 0x7E for character in value):
        raise InputError("password_characters")
    upper = 63 if key_mgmt == "wpa-psk" else 128
    if not 8 <= len(value) <= upper:
        raise InputError("password_length")
    return value


def validate_priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError("priority_integer")
    if not PRIORITY_MIN <= value <= PRIORITY_MAX:
        raise InputError("priority_range")
    return value


def validate_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise InputError(f"{name}_boolean")
    return value


def validate_uuid(value: Any) -> str:
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        raise InputError("uuid_invalid")
    return value.lower()


def validate_txn(value: Any) -> str:
    if not isinstance(value, str) or not TXN_RE.fullmatch(value):
        raise InputError("txn_invalid")
    return value


def keyfile_string(value: str) -> str:
    """Escape a value for a GLib keyfile: every space becomes \\s, so no value depends on trimming."""
    escaped: list[str] = []
    for character in value:
        if character == "\\":
            escaped.append("\\\\")
        elif character == " ":
            escaped.append("\\s")
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            raise InputError("control_character")
        else:
            escaped.append(character)
    return "".join(escaped)


def keyfile_ssid(ssid: str) -> str:
    """SSID as the keyfile byte list (e.g. 85;43;), which NetworkManager reads without ambiguity."""
    return "".join(f"{byte};" for byte in ssid.encode("utf-8"))


def new_profile_keyfile(*, profile_uuid: str, name: str, ssid: str, key_mgmt: str, password: str | None) -> str:
    lines = [
        "[connection]",
        f"id={keyfile_string(name)}",
        f"uuid={profile_uuid}",
        "type=wifi",
        "autoconnect=false",
        "autoconnect-priority=0",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={keyfile_ssid(ssid)}",
        "",
    ]
    if key_mgmt != "none":
        if password is None:
            raise InputError("password_required")
        lines += ["[wifi-security]", f"key-mgmt={key_mgmt}", f"psk={keyfile_string(password)}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "addr-gen-mode=stable-privacy", "method=auto", ""]
    return "\n".join(lines)


def edit_keyfile_for_trial(text: str, password: str) -> str:
    """The saved keyfile with a new PSK and autoconnect off for the trial; every other line is kept.

    The PSK becomes a system-owned secret (psk-flags removed), so the root service can use it."""
    replacement = {"connection": "autoconnect=false", "wifi-security": f"psk={keyfile_string(password)}"}
    keys = {"connection": "autoconnect", "wifi-security": "psk"}
    out: list[str] = []
    seen: set[str] = set()
    done: set[str] = set()
    section = ""

    def close(name: str) -> None:
        if name in replacement and name not in done:
            index = len(out)
            while index > 0 and not out[index - 1].strip():
                index -= 1
            out.insert(index, replacement[name])
            done.add(name)

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            close(section)
            section = stripped[1:-1]
            seen.add(section)
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip() if "=" in line and not stripped.startswith(("#", ";")) else None
        if section == "wifi-security" and key == "psk-flags":
            continue
        if section in keys and key == keys[section]:
            if section not in done:
                out.append(replacement[section])
                done.add(section)
            continue
        out.append(line)
    close(section)
    if not {"connection", "wifi-security"} <= seen:
        raise InputError("profile_without_security")
    return "\n".join(out)


def parse_scan(output: str, dbm_by_bssid: dict[str, int] | None = None) -> tuple[list[dict[str, Any]], int]:
    """Group `nmcli -t -e yes -f IN-USE,BSSID,SSID,MODE,CHAN,FREQ,RATE,SIGNAL,SECURITY device wifi list`
    rows by SSID and band; the shown access point is the one in use, else the strongest."""
    dbm_by_bssid = dbm_by_bssid or {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    hidden = 0
    for line in output.splitlines():
        parts = split_terse(line)
        if len(parts) != 9:
            continue
        in_use, bssid, ssid, mode, channel, freq, rate, signal, security = parts
        if mode.strip() and mode.strip().lower() not in {"infra", "infrastructure"}:
            continue
        if not ssid:
            hidden += 1
            continue
        freq_mhz = leading_int(freq)
        security = security.strip()
        key_mgmt = key_mgmt_for_security(security)
        entry = {
            "ssid": ssid,
            "band": band_name(freq_mhz),
            "channel": leading_int(channel),
            "freq_mhz": freq_mhz,
            "signal_pct": leading_int(signal),
            "signal_dbm": dbm_by_bssid.get(bssid.strip().lower()),
            "rate_mbps": leading_int(rate),
            "security": security if security and security != "--" else "open",
            "key_mgmt": key_mgmt or "unsupported",
            "supported": key_mgmt is not None,
            "in_use": in_use.strip() == "*",
            "ap_count": 1,
        }
        key = (ssid, entry["band"])
        best = groups.get(key)
        if best is None:
            groups[key] = entry
            continue
        count = best["ap_count"] + 1
        in_use_any = best["in_use"] or entry["in_use"]
        if (entry["in_use"], entry["signal_pct"] or 0) > (best["in_use"], best["signal_pct"] or 0):
            best.update(entry)
        best["ap_count"] = count
        best["in_use"] = in_use_any
    networks = sorted(groups.values(), key=lambda item: (not item["in_use"], -(item["signal_pct"] or 0), item["ssid"]))
    return networks[:SCAN_RESULT_LIMIT], hidden


def parse_scan_dump(output: str) -> dict[str, int]:
    """Signal in dBm per BSSID from `iw dev <if> scan dump` (the kernel's last results, no new scan)."""
    values: dict[str, int] = {}
    current = ""
    for line in output.splitlines():
        match = re.match(r"BSS ([0-9a-fA-F:]{17})", line)
        if match:
            current = match.group(1).lower()
            continue
        match = re.match(r"\s+signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", line)
        if match and current:
            values[current] = round(float(match.group(1)))
    return values


def parse_link(output: str) -> dict[str, Any] | None:
    """Current association from `iw dev <if> link`."""
    if not output or "Not connected" in output:
        return None

    def number(pattern: str) -> float | None:
        match = re.search(pattern, output)
        return float(match.group(1)) if match else None

    ssid = re.search(r"^\s*SSID: (.*)$", output, re.MULTILINE)
    freq = number(r"freq:\s*(\d+(?:\.\d+)?)")
    signal = number(r"signal:\s*(-?\d+)")
    return {
        "ssid": ssid.group(1) if ssid else "",
        "freq_mhz": int(freq) if freq else None,
        "band": band_name(int(freq) if freq else None),
        "signal_dbm": int(signal) if signal is not None else None,
        "tx_mbps": number(r"tx bitrate:\s*(\d+(?:\.\d+)?)"),
        "rx_mbps": number(r"rx bitrate:\s*(\d+(?:\.\d+)?)"),
    }


def keyfile_storage(filename: str) -> str:
    if not filename:
        return "other"
    parent = Path(filename).parent
    if parent == NM_PERSISTENT_DIR:
        return "disk"
    if parent == NM_RUNTIME_DIR:
        return "runtime"
    return "other"


# ---- Wi-Fi management: NetworkManager access -------------------------------

def connection_names() -> set[str]:
    output = run(["nmcli", "-t", "-e", "yes", "-f", "NAME", "connection", "show"], timeout=10, check=False).stdout
    return {split_terse(line)[0] for line in output.splitlines() if line}


def unique_profile_name(ssid: str, taken: set[str]) -> str:
    if ssid not in taken:
        return ssid
    for index in range(2, 100):
        candidate = f"{ssid} ({index})"
        if candidate not in taken:
            return candidate
    return f"{ssid} ({uuid.uuid4().hex[:6]})"


def profile_detail(profile_uuid: str) -> dict[str, str]:
    base = ["nmcli", "-t", "-e", "no", "-f"]
    result = run([*base, ",".join(PROFILE_DETAIL_FIELDS), "connection", "show", "uuid", profile_uuid], timeout=8, check=False)
    if result.returncode != 0:
        # An open network has no security setting; ask for the wireless fields only.
        result = run([*base, ",".join(PROFILE_DETAIL_FIELDS[:3]), "connection", "show", "uuid", profile_uuid], timeout=8, check=False)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator and key in PROFILE_DETAIL_FIELDS:
            values[key] = value if key == "802-11-wireless.ssid" else value.strip()
    return values


def saved_profiles(include_private: bool = False) -> list[dict[str, Any]]:
    """Saved Wi-Fi station profiles (the managed AP profile and AP-mode profiles left out)."""
    fields = "NAME,UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,ACTIVE,DEVICE,TIMESTAMP,FILENAME"
    output = run(["nmcli", "-t", "-e", "yes", "-f", fields, "connection", "show"], timeout=10).stdout
    ap_uuid = read_ap_uuid()
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = split_terse(line)
        if len(parts) != 9:
            continue
        name, profile_uuid, profile_type, autoconnect, priority, active, device, timestamp, filename = parts
        if profile_type != WIFI_TYPE or not UUID_RE.fullmatch(profile_uuid):
            continue
        if profile_uuid == ap_uuid or name == AP_CONNECTION:
            continue
        detail = profile_detail(profile_uuid)
        if detail.get("802-11-wireless.mode", "") not in {"", "infrastructure"}:
            continue
        key_mgmt = detail.get("802-11-wireless-security.key-mgmt", "") or "none"
        storage = keyfile_storage(filename)
        last_used = leading_int(timestamp) or 0
        row: dict[str, Any] = {
            "uuid": profile_uuid.lower(),
            "name": name,
            "ssid": detail.get("802-11-wireless.ssid", ""),
            "autoconnect": autoconnect == "yes",
            "priority": leading_int(priority) or 0,
            "active": active == "yes" and device == INTERFACE,
            "key_mgmt": key_mgmt,
            "band": {"bg": "2.4GHz", "a": "5GHz"}.get(detail.get("802-11-wireless.band", ""), ""),
            "last_used": last_used if last_used > 0 else None,
            "storage": storage,
            "password_editable": storage == "disk" and key_mgmt in {"wpa-psk", "sae"},
            "trial": Path(filename).name.startswith(TRIAL_KEYFILE_PREFIX) if filename else False,
        }
        if include_private:
            row["filename"] = filename
        rows.append(row)
    rows.sort(key=lambda item: (not item["active"], -item["priority"], item["name"].lower()))
    return rows


def active_connection() -> tuple[str, str] | None:
    """(uuid, state) of the connection on the Wi-Fi interface; state is activating or activated."""
    output = run(["nmcli", "-t", "-e", "yes", "-f", "UUID,DEVICE,STATE", "connection", "show", "--active"],
                 timeout=6, check=False).stdout
    for line in output.splitlines():
        parts = split_terse(line)
        if len(parts) == 3 and parts[1] == INTERFACE:
            return parts[0].lower(), parts[2]
    return None


def ipv4_state() -> dict[str, str | None]:
    address = run(["ip", "-4", "-o", "address", "show", "dev", INTERFACE], timeout=4, check=False).stdout
    route = run(["ip", "-4", "route", "show", "default", "dev", INTERFACE], timeout=4, check=False).stdout
    inet = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/\d+", address)
    gateway = re.search(r"\bvia (\d+\.\d+\.\d+\.\d+)", route)
    return {"ipv4": inet.group(1) if inet else None, "gateway": gateway.group(1) if gateway else None}


def ping_once(host: str) -> bool:
    return run(["ping", "-c", "1", "-W", "2", host], timeout=4, check=False).returncode == 0


def wifi_link() -> dict[str, Any] | None:
    return parse_link(run(["iw", "dev", INTERFACE, "link"], timeout=3, check=False).stdout)


def check_root_keyfile(path: Path) -> None:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise InputError("profile_file_not_regular")
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise InputError("profile_file_not_private")


def perform_scan() -> dict[str, Any]:
    fields = "IN-USE,BSSID,SSID,MODE,CHAN,FREQ,RATE,SIGNAL,SECURITY"
    base = ["nmcli", "-t", "-e", "yes", "-f", fields, "device", "wifi", "list", "ifname", INTERFACE]
    result = run([*base, "--rescan", "yes"], timeout=30, check=False)
    rescanned = result.returncode == 0
    if not rescanned:
        # NetworkManager refuses a scan right after another one; its last results are still useful.
        result = run([*base, "--rescan", "no"], timeout=10)
    dump = run(["iw", "dev", INTERFACE, "scan", "dump"], timeout=6, check=False).stdout
    networks, hidden = parse_scan(result.stdout, parse_scan_dump(dump))
    return {
        "ok": True,
        "schema": "ubts.wifi_scan.v1",
        "scanned_at": time.time(),
        "interface": INTERFACE,
        "rescanned": rescanned,
        "networks": networks,
        "hidden_count": hidden,
    }


def stage_new_profile(ssid: str, key_mgmt: str, password: str | None) -> dict[str, Any]:
    """An in-memory profile (lost on reboot) with autoconnect off: the trial candidate."""
    profile_uuid = str(uuid.uuid4())
    name = unique_profile_name(ssid, connection_names())
    path = NM_RUNTIME_DIR / f"{TRIAL_KEYFILE_PREFIX}{profile_uuid}.nmconnection"
    content = new_profile_keyfile(profile_uuid=profile_uuid, name=name, ssid=ssid, key_mgmt=key_mgmt, password=password)
    write_private(path, content.encode("utf-8"))
    try:
        run(["nmcli", "connection", "load", str(path)])
        if not connection_exists(profile_uuid):
            raise CommandError("new Wi-Fi profile did not load")
    except CommandError:
        run(["nmcli", "connection", "delete", "uuid", profile_uuid], check=False)
        path.unlink(missing_ok=True)
        raise
    return {"kind": "new", "uuid": profile_uuid, "keyfile": str(path)}


def stage_password_change(profile: dict[str, Any], password: str, txn: str) -> dict[str, Any]:
    """New PSK in a saved keyfile, with the original kept in /run until the trial ends."""
    path = Path(str(profile.get("filename") or ""))
    if path.parent != NM_PERSISTENT_DIR or not path.name.endswith(".nmconnection"):
        raise InputError("password_not_editable")
    check_root_keyfile(path)
    original = path.read_bytes()
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("profile_file_not_utf8") from exc
    edited = edit_keyfile_for_trial(text, password)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = BACKUP_DIR / f"{txn}.nmconnection"
    write_private(backup, original)
    try:
        write_private(path, edited.encode("utf-8"))
        run(["nmcli", "connection", "load", str(path)])
    except (CommandError, OSError):
        write_private(path, original)
        run(["nmcli", "connection", "load", str(path)], check=False)
        backup.unlink(missing_ok=True)
        raise
    return {"kind": "saved_password", "uuid": profile["uuid"], "keyfile": str(path), "backup": str(backup)}


def undo_prepared(prepared: dict[str, Any]) -> None:
    """Undo what a connect request staged: delete a trial profile made here, or put a keyfile back."""
    kind = prepared.get("kind")
    profile_uuid = str(prepared.get("uuid") or "")
    if kind == "new":
        path = Path(str(prepared.get("keyfile") or ""))
        expected = NM_RUNTIME_DIR / f"{TRIAL_KEYFILE_PREFIX}{profile_uuid}.nmconnection"
        if not UUID_RE.fullmatch(profile_uuid) or path != expected:
            raise CommandError("refusing to remove a profile this service did not create")
        run(["nmcli", "connection", "delete", "uuid", profile_uuid], check=False)
        path.unlink(missing_ok=True)
    elif kind == "saved_password":
        path = Path(str(prepared.get("keyfile") or ""))
        backup = Path(str(prepared.get("backup") or ""))
        if path.parent != NM_PERSISTENT_DIR or backup.parent != BACKUP_DIR:
            raise CommandError("refusing to restore a keyfile outside the profile folders")
        if backup.is_file():
            write_private(path, backup.read_bytes())
            run(["nmcli", "connection", "load", str(path)], check=False)
            backup.unlink(missing_ok=True)


@contextlib.contextmanager
def request_lock(wait_s: float = 5.0) -> Iterator[None]:
    """Serialise dashboard requests (one queued command at a time). Lives next to the command file."""
    if fcntl is None:
        yield
        return
    lock_path = COMMAND_FILE.with_name("request.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+") as handle:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CommandError("another network request is being handled")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fresh_status(max_age: float = 10.0) -> tuple[dict[str, Any], dict[str, Any] | None]:
    current = load_json(STATUS_FILE)
    if not current or time.time() - float(current.get("updated_at") or 0) > max_age:
        return current, {"ok": False, "state": "controller_unavailable", "error": "network controller status is stale"}
    return current, None


def command_pending() -> bool:
    return COMMAND_FILE.exists()


def public_scan(scan: dict[str, Any]) -> dict[str, Any] | None:
    if not scan.get("networks") and not scan.get("scanned_at"):
        return None
    age = max(0.0, time.time() - float(scan.get("scanned_at") or 0))
    return {**scan, "age_s": round(age, 1)}


def wifi_scan_request(_request: dict[str, Any]) -> dict[str, Any]:
    cached = load_json(SCAN_FILE)
    if cached:
        age = time.time() - float(cached.get("scanned_at") or 0)
        if 0 <= age < SCAN_MIN_INTERVAL_S:
            return {**(public_scan(cached) or {}), "ok": True, "cached": True}
    current, error = fresh_status()
    if error:
        return {**error, "scan": public_scan(cached) if cached else None}
    if not current.get("can_scan"):
        reason = "ap_active" if current.get("ap_active") else current.get("controls_locked_reason") or current.get("mode")
        return {"ok": False, "state": "scan_not_available", "reason": reason,
                "scan": public_scan(cached) if cached else None}
    result = perform_scan()
    atomic_json(SCAN_FILE, result)
    return {**result, "age_s": 0.0, "cached": False}


def wifi_saved_request(_request: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "schema": "ubts.wifi_saved.v1", "read_at": time.time(), "profiles": saved_profiles()}


def wifi_profile_request(request: dict[str, Any]) -> dict[str, Any]:
    profile_uuid = validate_uuid(request.get("uuid"))
    autoconnect = validate_bool(request.get("autoconnect"), "autoconnect")
    priority = validate_priority(request.get("priority"))
    with request_lock():
        current, error = fresh_status()
        if error:
            return error
        if current.get("trial") or current.get("transition") or command_pending():
            return {"ok": False, "state": "busy", "error": "a network change is in progress"}
        if not any(row["uuid"] == profile_uuid for row in saved_profiles()):
            raise InputError("unknown_saved_profile")
        run(["nmcli", "connection", "modify", "uuid", profile_uuid,
             "connection.autoconnect", "yes" if autoconnect else "no",
             "connection.autoconnect-priority", str(priority)])
    return {"ok": True, "state": "updated", "uuid": profile_uuid, "autoconnect": autoconnect, "priority": priority}


def wifi_connect_request(request: dict[str, Any]) -> dict[str, Any]:
    """Stage the candidate profile and queue a trial for the controller. Nothing is echoed back."""
    autoconnect = validate_bool(request.get("autoconnect", True), "autoconnect")
    priority = validate_priority(request.get("priority", 0))
    password = request.get("password")
    if password == "":
        password = None
    with request_lock():
        current, error = fresh_status()
        if error:
            return error
        if command_pending():
            return {"ok": False, "state": "busy", "error": "another network request is pending"}
        if current.get("ethernet_connected"):
            return {"ok": False, "state": "ethernet_ap_locked", "error": "Ethernet keeps the Wi-Fi AP active."}
        if not current.get("can_request_connect"):
            return {"ok": False, "state": "action_not_available", "mode": current.get("mode")}
        txn = secrets.token_hex(8)
        profiles = saved_profiles(include_private=True)
        if request.get("uuid") is not None:
            profile_uuid = validate_uuid(request.get("uuid"))
            profile = next((row for row in profiles if row["uuid"] == profile_uuid), None)
            if profile is None:
                raise InputError("unknown_saved_profile")
            ssid = profile["ssid"]
            if password is None:
                if profile["active"]:
                    raise InputError("already_connected")
                prepared = {"kind": "saved", "uuid": profile_uuid}
            else:
                if not profile["password_editable"]:
                    raise InputError("password_not_editable")
                validate_wifi_password(password, profile["key_mgmt"])
                prepared = stage_password_change(profile, password, txn)
        else:
            ssid = validate_ssid(request.get("ssid"))
            if any(row["ssid"] == ssid for row in profiles):
                raise InputError("ssid_already_saved")
            scan = load_json(SCAN_FILE)
            if time.time() - float(scan.get("scanned_at") or 0) > SCAN_MAX_AGE_FOR_CONNECT_S:
                raise InputError("scan_too_old")
            network = next((item for item in scan.get("networks") or [] if item.get("ssid") == ssid), None)
            if network is None:
                raise InputError("ssid_not_in_scan")
            key_mgmt = key_mgmt_for_security("" if network.get("security") == "open" else str(network.get("security") or ""))
            if key_mgmt is None:
                raise InputError("security_not_supported")
            if key_mgmt == "none":
                if password is not None:
                    raise InputError("open_network_takes_no_password")
            else:
                validate_wifi_password(password, key_mgmt)
            prepared = stage_new_profile(ssid, key_mgmt, password)
        password = None
        command = {
            "action": "connect",
            "txn": txn,
            "ssid": ssid,
            "prepared": prepared,
            "commit": {"autoconnect": autoconnect, "priority": priority},
            "requested_at": time.time(),
        }
        try:
            atomic_json(COMMAND_FILE, command)
        except OSError:
            undo_prepared(prepared)
            raise
    return {"ok": True, "state": "accepted", "txn": txn, "ssid": ssid, "kind": prepared["kind"],
            "trial_seconds": TRIAL_S, "link_seconds": TRIAL_LINK_S}


def wifi_trial_request(action: str, request: dict[str, Any]) -> dict[str, Any]:
    txn = validate_txn(request.get("txn"))
    with request_lock():
        current = load_json(STATUS_FILE)
        if not current or time.time() - float(current.get("updated_at") or 0) > TRIAL_STATUS_MAX_AGE_S:
            return {"ok": False, "state": "controller_unavailable", "error": "network controller status is stale"}
        trial = current.get("trial") or {}
        if trial.get("txn") != txn:
            return {"ok": False, "state": "no_such_trial"}
        if action == "confirm" and not trial.get("can_confirm"):
            return {"ok": False, "state": "not_ready", "phase": trial.get("phase")}
        atomic_json(COMMAND_FILE, {"action": action, "txn": txn, "requested_at": time.time()})
    return {"ok": True, "state": "accepted", "action": action, "txn": txn}


def read_stdin_payload() -> dict[str, Any]:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    data = sys.stdin.buffer.read(STDIN_MAX_BYTES + 1)
    if len(data) > STDIN_MAX_BYTES:
        raise InputError("request_too_large")
    if not data.strip():
        return {}
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError("request_not_json") from exc
    if not isinstance(value, dict):
        raise InputError("request_not_object")
    return value


def run_wifi_command(command: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        if command == "wifi-scan":
            return wifi_scan_request(request)
        if command == "wifi-saved":
            return wifi_saved_request(request)
        if command == "wifi-connect":
            return wifi_connect_request(request)
        if command == "wifi-profile":
            return wifi_profile_request(request)
        if command in {"wifi-confirm", "wifi-revert"}:
            return wifi_trial_request(command.removeprefix("wifi-"), request)
    except InputError as exc:
        return {"ok": False, "state": "invalid_request", "error": str(exc)}
    except CommandError as exc:
        return {"ok": False, "state": "helper_failed", "error": str(exc)[:300]}
    return {"ok": False, "state": "bad_command"}


class Controller:
    def __init__(self) -> None:
        self.mode = "starting"
        self.transition = ""
        self.last_error = ""
        self.last_reason = "service_start"
        self.ap_idle_since: float | None = None
        self.scan_deadline: float | None = None
        self.last_transition_at = time.time()
        self.trial: dict[str, Any] | None = None
        self.last_trial: dict[str, Any] | None = None
        self._link: dict[str, Any] | None = None
        self._link_at = 0.0

    def link_info(self, wifi_connected: bool) -> dict[str, Any] | None:
        if not wifi_connected:
            self._link = None
            return None
        now = time.monotonic()
        if self._link is None or now - self._link_at >= LINK_REFRESH_S:
            self._link = wifi_link()
            self._link_at = now
        return self._link

    def trial_view(self) -> dict[str, Any] | None:
        trial = self.trial
        if not trial:
            return None
        previous = trial.get("previous") or {}
        return {
            "txn": trial.get("txn"),
            "ssid": trial.get("ssid") or "",
            "kind": (trial.get("prepared") or {}).get("kind"),
            "phase": trial.get("phase"),
            "seconds_left": round(max(0.0, float(trial.get("deadline_mono", 0.0)) - time.monotonic()), 1),
            "trial_seconds": TRIAL_S,
            "previous": "AP" if previous.get("kind") == "ap" else previous.get("name") or None,
            "can_confirm": trial.get("phase") == "waiting_confirm",
            "ipv4": trial.get("ipv4"),
            "gateway_reply": trial.get("gateway_reply"),
            "internet_reply": trial.get("internet_reply"),
        }

    def snapshot(self) -> dict[str, Any]:
        device = interface_snapshot()
        ethernet = ethernet_connected()
        clients = ap_client_count() if device["ap_active"] else 0
        now_mono = time.monotonic()
        countdown: float | None = None
        if device["ap_active"] and not ethernet and clients == 0 and self.ap_idle_since is not None and not self.trial:
            countdown = max(0.0, AP_IDLE_S - (now_mono - self.ap_idle_since))
        elif self.transition == "wifi_scan" and self.scan_deadline is not None:
            countdown = max(0.0, self.scan_deadline - now_mono)
        if ethernet and device["ap_active"]:
            mode = "ethernet_ap"
        elif device["ap_active"]:
            mode = "ap"
        elif device["wifi_connected"]:
            mode = "wifi"
        elif self.transition:
            mode = "transition"
        else:
            mode = "disconnected"
        idle = not self.transition and self.trial is None
        locked_reason = (
            "ethernet_keeps_ap_active" if ethernet
            else "wifi_trial_in_progress" if self.trial is not None
            else "transition_in_progress" if self.transition
            else None
        )
        payload = {
            "ok": True,
            "schema": "ubts.network_mode.v1",
            "updated_at": time.time(),
            "mode": mode,
            "transition": self.transition or None,
            "reason": self.last_reason,
            "last_transition_at": self.last_transition_at,
            "last_error": self.last_error or None,
            "interface": INTERFACE,
            "connection": device["connection"],
            "ethernet_connected": ethernet,
            "wifi_connected": device["wifi_connected"],
            "ap_active": device["ap_active"],
            "ap_ssid": SSID,
            "ap_address": AP_ADDRESS,
            "ap_clients": clients,
            "cycle": {"ap_idle_s": AP_IDLE_S, "wifi_scan_s": WIFI_SCAN_S},
            "seconds_until_auto_switch": None if countdown is None else round(countdown, 1),
            "can_request_ap": bool(device["wifi_connected"] and not ethernet and idle),
            "can_request_wifi": bool(device["ap_active"] and not ethernet and idle),
            "can_request_connect": bool((device["wifi_connected"] or device["ap_active"]) and not ethernet and idle),
            "can_scan": bool(device["wifi_connected"] and not ethernet and idle),
            "controls_locked_reason": locked_reason,
            "link": self.link_info(device["wifi_connected"]),
            "trial": self.trial_view(),
            "last_trial": self.last_trial,
            "trial_seconds": TRIAL_S,
        }
        atomic_json(STATUS_FILE, payload)
        return payload

    def set_transition(self, transition: str, reason: str) -> None:
        self.transition = transition
        self.last_reason = reason
        self.last_transition_at = time.time()
        self.snapshot()

    def stop_ap_services(self) -> None:
        try:
            compose("down", "--remove-orphans", check=False)
        except CommandError:
            pass
        run(["systemctl", "stop", DNSMASQ_UNIT], check=False)
        DNSMASQ_CONFIG.unlink(missing_ok=True)

    def activate_ap(self, reason: str) -> None:
        self.set_transition("ap_start", reason)
        validate_backends()
        ap_uuid = ensure_ap_profile()
        run(["nmcli", "--wait", "20", "connection", "up", "uuid", ap_uuid, "ifname", INTERFACE], timeout=25)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if AP_CIDR in run(["ip", "-4", "address", "show", "dev", INTERFACE]).stdout:
                break
            time.sleep(0.25)
        else:
            raise CommandError("AP address did not appear")
        render_dnsmasq()
        run(["systemctl", "restart", DNSMASQ_UNIT])
        compose("up", "-d", "--wait", "--wait-timeout", "30")
        for hostname in [DASHBOARD_HOSTNAME, PORTAINER_HOSTNAME, SSH_HOSTNAME]:
            answer = run(["dig", "+short", f"@{AP_ADDRESS}", hostname, "A"]).stdout.strip()
            if answer != AP_ADDRESS:
                raise CommandError(f"local DNS validation failed for {hostname}")
        run(["curl", "-fsS", "--max-time", "3", "--resolve", f"{DASHBOARD_HOSTNAME}:80:{AP_ADDRESS}",
             f"http://{DASHBOARD_HOSTNAME}/api/health"])
        run(["curl", "-fsS", "--max-time", "3", "--resolve", f"{PORTAINER_HOSTNAME}:80:{AP_ADDRESS}",
             f"http://{PORTAINER_HOSTNAME}/api/status"])
        self.mode = "ap"
        self.transition = ""
        self.last_error = ""
        self.ap_idle_since = time.monotonic()
        self.scan_deadline = None
        self.snapshot()
        self.last_ros_refresh = refresh_ros_discovery("ap_started")

    def stop_ap(self) -> None:
        self.stop_ap_services()
        ap_uuid = read_ap_uuid()
        if ap_uuid:
            run(["nmcli", "connection", "down", "uuid", ap_uuid], check=False)

    def known_wifi_profiles(self) -> list[tuple[int, str, str]]:
        output = nmcli_value("-t", "--escape", "no", "-f", "UUID,TYPE", "connection", "show", check=False)
        ap_uuid = read_ap_uuid()
        rows: list[tuple[int, str, str]] = []
        for line in output.splitlines():
            try:
                profile_uuid, profile_type = line.split(":", 1)
            except ValueError:
                continue
            if profile_type != "802-11-wireless" or profile_uuid == ap_uuid:
                continue
            ssid = nmcli_value("-g", "802-11-wireless.ssid", "connection", "show", "uuid", profile_uuid, check=False)
            priority_raw = nmcli_value("-g", "connection.autoconnect-priority", "connection", "show", "uuid", profile_uuid, check=False)
            try:
                priority = int(priority_raw or 0)
            except ValueError:
                priority = 0
            if ssid:
                rows.append((priority, profile_uuid, ssid))
        return sorted(rows, reverse=True)

    def visible_ssids(self) -> set[str]:
        run(["nmcli", "device", "wifi", "rescan", "ifname", INTERFACE], timeout=12, check=False)
        output = nmcli_value("-t", "--escape", "no", "-f", "SSID", "device", "wifi", "list", "ifname", INTERFACE, check=False)
        return {line for line in output.splitlines() if line}

    def scan_wifi(self, reason: str) -> bool:
        self.set_transition("wifi_scan", reason)
        self.stop_ap()
        self.ap_idle_since = None
        self.scan_deadline = time.monotonic() + WIFI_SCAN_S
        attempted: dict[str, float] = {}
        while time.monotonic() < self.scan_deadline:
            if ethernet_connected():
                self.activate_ap("ethernet_connected_during_scan")
                return False
            device = interface_snapshot()
            if device["wifi_connected"]:
                self.transition = ""
                self.last_error = ""
                self.last_reason = "known_wifi_connected"
                self.scan_deadline = None
                self.snapshot()
                return True
            visible = self.visible_ssids()
            for _priority, profile_uuid, profile_ssid in self.known_wifi_profiles():
                if profile_ssid not in visible or time.monotonic() - attempted.get(profile_uuid, 0.0) < 15.0:
                    continue
                attempted[profile_uuid] = time.monotonic()
                remaining = max(3, min(15, int(self.scan_deadline - time.monotonic())))
                run(
                    ["nmcli", "--wait", str(remaining), "connection", "up", "uuid", profile_uuid, "ifname", INTERFACE],
                    timeout=remaining + 3,
                    check=False,
                )
                if interface_snapshot()["wifi_connected"]:
                    self.transition = ""
                    self.last_error = ""
                    self.last_reason = "known_wifi_connected"
                    self.scan_deadline = None
                    self.snapshot()
                    self.last_ros_refresh = refresh_ros_discovery("known_wifi_connected")
                    return True
            self.snapshot()
            time.sleep(min(5.0, max(0.2, self.scan_deadline - time.monotonic())))
        self.scan_deadline = None
        self.activate_ap("wifi_scan_timeout")
        return False

    # ---- Wi-Fi trial: try a network, keep it only when the operator confirms ----

    def previous_connection(self, device: dict[str, Any]) -> dict[str, Any]:
        if device["ap_active"]:
            return {"kind": "ap"}
        if device["wifi_connected"]:
            current = active_connection()
            if current:
                return {"kind": "wifi", "uuid": current[0], "name": device["connection"]}
        return {"kind": "none"}

    def persist_trial(self) -> None:
        trial = self.trial or {}
        atomic_json(TRIAL_FILE, {key: value for key, value in trial.items() if not key.endswith("_mono")})

    def record_trial(self, trial: dict[str, Any], result: str, reason: str, warnings: list[str] | None = None) -> None:
        self.last_trial = {
            "txn": trial.get("txn"),
            "ssid": trial.get("ssid") or "",
            "result": result,
            "reason": reason,
            "at": time.time(),
            "warnings": list(warnings or []),
        }

    def start_trial(self, command: dict[str, Any]) -> None:
        prepared = command.get("prepared") if isinstance(command.get("prepared"), dict) else {}
        candidate = str(prepared.get("uuid") or "")
        commit = command.get("commit") if isinstance(command.get("commit"), dict) else {}
        priority = commit.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            priority = 0
        now = time.monotonic()
        self.trial = {
            "txn": str(command.get("txn") or ""),
            "ssid": str(command.get("ssid") or "")[:64],
            "prepared": prepared,
            "commit": {"autoconnect": commit.get("autoconnect") is True,
                       "priority": min(PRIORITY_MAX, max(PRIORITY_MIN, priority))},
            "previous": self.previous_connection(interface_snapshot()),
            "phase": "activating",
            "started_at": time.time(),
            "deadline_at": time.time() + TRIAL_S,
            "started_mono": now,
            "deadline_mono": now + TRIAL_S,
            "link_deadline_mono": now + TRIAL_LINK_S,
        }
        if not UUID_RE.fullmatch(candidate) or not TXN_RE.fullmatch(self.trial["txn"]):
            self.revert_trial("invalid_command")
            return
        self.persist_trial()
        self.set_transition("wifi_trial", "dashboard_request_connect")
        if self.trial["previous"]["kind"] == "ap":
            self.stop_ap_services()
        # --wait 0: NetworkManager starts the activation; the loop watches it so the status stays fresh.
        result = run(["nmcli", "--wait", "0", "connection", "up", "uuid", candidate, "ifname", INTERFACE],
                     timeout=15, check=False)
        if result.returncode != 0:
            self.revert_trial("activation_rejected", (result.stderr or result.stdout or "").strip()[:200])
            return
        self.snapshot()

    def trial_step(self, request: dict[str, Any]) -> None:
        trial = self.trial
        assert trial is not None
        action = request.get("action")
        same_txn = request.get("txn") == trial["txn"]
        if action == "revert" and same_txn:
            self.revert_trial("operator_revert")
            return
        if ethernet_connected():
            self.revert_trial("ethernet_connected")
            return
        now = time.monotonic()
        candidate = str(trial["prepared"].get("uuid") or "")
        current = active_connection()
        on_candidate = current is not None and current[0] == candidate.lower()
        activated = on_candidate and current is not None and current[1] == "activated"
        if trial["phase"] == "activating":
            if activated:
                state = ipv4_state()
                if state["ipv4"] and state["gateway"]:
                    trial.update(phase="waiting_confirm", ipv4=state["ipv4"], link_up_at=time.time(),
                                 gateway_reply=ping_once(state["gateway"]), internet_reply=ping_once("1.1.1.1"))
                    self.persist_trial()
            if trial["phase"] == "activating":
                if now >= trial["link_deadline_mono"]:
                    self.revert_trial("no_address_or_route_in_time")
                    return
                if now - trial["started_mono"] > 5 and not on_candidate:
                    self.revert_trial("activation_failed")
                    return
        elif trial["phase"] == "waiting_confirm":
            if action == "confirm" and same_txn:
                self.commit_trial()
                return
            if activated:
                trial.pop("lost_since_mono", None)
            else:
                lost_since = trial.setdefault("lost_since_mono", now)
                if now - lost_since >= TRIAL_LOST_GRACE_S:
                    self.revert_trial("link_lost")
                    return
        if now >= trial["deadline_mono"]:
            self.revert_trial("not_confirmed_in_time")
            return
        self.snapshot()

    def commit_trial(self) -> None:
        trial = self.trial
        assert trial is not None
        prepared = trial["prepared"]
        commit = trial["commit"]
        warnings: list[str] = []
        result = run(["nmcli", "connection", "modify", "uuid", prepared["uuid"],
                      "connection.autoconnect", "yes" if commit["autoconnect"] else "no",
                      "connection.autoconnect-priority", str(commit["priority"])], check=False)
        if result.returncode != 0:
            warnings.append("settings_not_applied")
        if prepared.get("kind") == "saved_password":
            backup = Path(str(prepared.get("backup") or ""))
            if backup.parent == BACKUP_DIR:
                backup.unlink(missing_ok=True)
        if prepared.get("kind") == "new":
            stored = next((row for row in saved_profiles() if row["uuid"] == prepared["uuid"]), None)
            if stored is None or stored["storage"] != "disk":
                warnings.append("profile_not_on_disk")
        self.finish_trial("kept", "operator_confirmed", warnings)

    def finish_trial(self, result: str, reason: str, warnings: list[str] | None = None) -> None:
        trial = self.trial or {}
        self.record_trial(trial, result, reason, warnings)
        self.trial = None
        TRIAL_FILE.unlink(missing_ok=True)
        self.transition = ""
        self.last_reason = f"wifi_trial_{result}"
        self.last_transition_at = time.time()
        self.last_error = "; ".join(warnings or []) if warnings else ""
        self.snapshot()

    def revert_trial(self, reason: str, detail: str = "") -> None:
        """Undo the staged profile change and go back to the connection the trial started from."""
        trial = self.trial
        assert trial is not None
        trial["phase"] = "reverting"
        warnings: list[str] = [detail] if detail else []
        prepared = trial.get("prepared") or {}
        previous = trial.get("previous") or {"kind": "none"}
        fallback = previous.get("kind") not in {"ap", "wifi"}
        try:
            with contextlib.suppress(OSError, CommandError):
                self.snapshot()
            try:
                if prepared.get("kind") in {"new", "saved_password"}:
                    undo_prepared(prepared)
            except (OSError, CommandError) as exc:
                warnings.append(f"undo_failed: {str(exc)[:120]}")
            try:
                if previous.get("kind") == "ap":
                    self.activate_ap("wifi_trial_reverted")
                elif previous.get("kind") == "wifi":
                    current = active_connection()
                    same_profile = prepared.get("uuid") == previous.get("uuid")
                    if same_profile or current is None or current[0] != previous.get("uuid") or current[1] != "activated":
                        result = run(["nmcli", "--wait", "30", "connection", "up", "uuid", str(previous["uuid"]),
                                      "ifname", INTERFACE], timeout=35, check=False)
                        if result.returncode != 0:
                            warnings.append("previous_wifi_failed")
                            fallback = True
            except (OSError, CommandError) as exc:
                warnings.append(f"restore_failed: {str(exc)[:120]}")
                fallback = True
        finally:
            self.trial = trial
            self.finish_trial("reverted", reason, warnings)
        if fallback:
            # The existing safety net: try saved profiles for WIFI_SCAN_S, then the AP.
            self.scan_wifi("wifi_trial_revert_fallback")

    def resume_unfinished_trial(self) -> None:
        """A trial that was running when the service stopped is reverted at once."""
        saved = load_json(TRIAL_FILE)
        if not saved:
            return
        now = time.monotonic()
        self.trial = {**saved, "started_mono": now, "deadline_mono": now, "link_deadline_mono": now}
        self.revert_trial("controller_restarted")

    def read_request(self) -> dict[str, Any]:
        taken = COMMAND_FILE.with_name(COMMAND_FILE.name + ".taken")
        try:
            os.replace(COMMAND_FILE, taken)
        except OSError:
            return {}
        payload = load_json(taken)
        taken.unlink(missing_ok=True)
        action = str(payload.get("action") or "")
        if action not in {"ap", "wifi", "connect", "confirm", "revert"}:
            return {}
        age = time.time() - float(payload.get("requested_at") or 0)
        if action == "connect" and not -5 <= age <= COMMAND_MAX_AGE_S:
            self.reject_connect(payload, "request_expired")
            return {}
        return payload

    def reject_connect(self, command: dict[str, Any], reason: str) -> None:
        warnings: list[str] = []
        prepared = command.get("prepared") if isinstance(command.get("prepared"), dict) else {}
        try:
            undo_prepared(prepared)
        except (OSError, CommandError) as exc:
            warnings.append(f"undo_failed: {str(exc)[:120]}")
        self.record_trial({"txn": command.get("txn"), "ssid": command.get("ssid")}, "rejected", reason, warnings)

    def step(self, request: dict[str, Any]) -> None:
        action = request.get("action", "")
        device = interface_snapshot()
        ethernet = ethernet_connected()
        if action == "connect" and (ethernet or not (device["wifi_connected"] or device["ap_active"])):
            self.reject_connect(request, "state_changed")
            action = ""
        if ethernet:
            if not device["ap_active"]:
                self.activate_ap("ethernet_requires_continuous_ap")
            else:
                self.transition = ""
                self.ap_idle_since = None
                self.snapshot()
        elif action == "connect":
            self.start_trial(request)
        elif action == "ap" and device["wifi_connected"]:
            self.activate_ap("dashboard_request_ap")
        elif action == "wifi" and device["ap_active"]:
            self.scan_wifi("dashboard_request_wifi")
        elif device["wifi_connected"]:
            self.transition = ""
            self.ap_idle_since = None
            self.snapshot()
        elif device["ap_active"]:
            clients = ap_client_count()
            if clients > 0:
                self.ap_idle_since = None
            elif self.ap_idle_since is None:
                self.ap_idle_since = time.monotonic()
            elif time.monotonic() - self.ap_idle_since >= AP_IDLE_S:
                self.scan_wifi("ap_without_clients_timeout")
            self.snapshot()
        else:
            self.scan_wifi("wireless_disconnected")

    def recover(self, error: Exception) -> None:
        original_error = str(error)[:500]
        self.last_error = original_error
        self.transition = "recovery"
        self.last_reason = "transition_failure"
        print(
            json.dumps(
                {"event": "network_transition_failure", "error": original_error},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
            flush=True,
        )
        self.snapshot()
        try:
            self.scan_wifi("recover_after_failure")
            self.last_error = original_error
            self.last_reason = "recovered_after_transition_failure"
            self.snapshot()
        except Exception as recovery_error:  # noqa: BLE001 - service must retain diagnostics
            self.last_error = f"{original_error}; recovery failed: {recovery_error}"[:500]
            self.transition = "recovery_failed"
            self.snapshot()

    def loop(self) -> None:
        if fcntl is None:
            raise SystemExit("fcntl is required on the Linux host")
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        with LOCK_FILE.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self.resume_unfinished_trial()
            except Exception as exc:  # noqa: BLE001 - keep supervised recovery alive
                self.trial = None
                TRIAL_FILE.unlink(missing_ok=True)
                self.recover(exc)
            while True:
                try:
                    request = self.read_request()
                    if self.trial is not None:
                        self.trial_step(request)
                    else:
                        self.step(request)
                except BlockingIOError:
                    raise SystemExit("another ubts-network-mode controller is active")
                except Exception as exc:  # noqa: BLE001 - keep supervised recovery alive
                    if self.trial is not None:
                        try:
                            self.revert_trial(f"controller_error: {str(exc)[:120]}")
                        except Exception as revert_error:  # noqa: BLE001
                            self.trial = None
                            TRIAL_FILE.unlink(missing_ok=True)
                            self.recover(revert_error)
                    else:
                        self.recover(exc)
                time.sleep(POLL_S)


def queue_request(action: str) -> dict[str, Any]:
    with request_lock():
        current = load_json(STATUS_FILE)
        if not current or time.time() - float(current.get("updated_at") or 0) > 10:
            return {"ok": False, "state": "controller_unavailable", "error": "network controller status is stale"}
        if current.get("ethernet_connected"):
            return {"ok": False, "state": "ethernet_ap_locked", "error": "Ethernet keeps the Wi-Fi AP active."}
        allowed = current.get("can_request_ap") if action == "ap" else current.get("can_request_wifi")
        if not allowed:
            return {"ok": False, "state": "action_not_available", "mode": current.get("mode")}
        if command_pending():
            return {"ok": False, "state": "busy", "error": "another network request is pending"}
        request_id = str(uuid.uuid4())
        atomic_json(COMMAND_FILE, {"action": action, "request_id": request_id, "requested_at": time.time()})
    return {"ok": True, "state": "accepted", "action": action, "request_id": request_id, "current": current}


def status_payload() -> dict[str, Any]:
    payload = load_json(STATUS_FILE)
    if not payload:
        return {"ok": False, "state": "controller_unavailable", "error": "status file is missing"}
    payload["status_age_s"] = round(max(0.0, time.time() - float(payload.get("updated_at") or 0)), 3)
    if payload["status_age_s"] > 10:
        payload["ok"] = False
        payload["state"] = "controller_stale"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["controller", "status", "request-ap", "request-wifi", "prepare", *WIFI_COMMANDS])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "root is required"}))
        return 1
    if args.command == "controller":
        Controller().loop()
        return 0
    if args.command == "prepare":
        validate_backends()
        profile_uuid = ensure_ap_profile()
        payload = {"ok": True, "state": "prepared", "interface": INTERFACE, "ssid": SSID, "profile_uuid": profile_uuid}
    elif args.command == "status":
        payload = status_payload()
    elif args.command in WIFI_COMMANDS:
        try:
            request = read_stdin_payload()
        except InputError as exc:
            payload = {"ok": False, "state": "invalid_request", "error": str(exc)}
        else:
            payload = run_wifi_command(args.command, request)
    else:
        try:
            payload = queue_request("ap" if args.command == "request-ap" else "wifi")
        except CommandError as exc:
            payload = {"ok": False, "state": "busy", "error": str(exc)[:300]}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
