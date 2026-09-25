#!/usr/bin/env python3
"""Supervise the Jetson's single Wi-Fi radio between station and offline AP.

The service never changes Ethernet, never creates a default route for AP
clients, and only activates already-saved Wi-Fi station profiles. Dashboard
requests are queued so the HTTP response can complete before Wi-Fi is cut over.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

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
AP_IDLE_S = max(10, int(os.environ.get("UBTS_AP_IDLE_SECONDS", "60")))
WIFI_SCAN_S = max(10, int(os.environ.get("UBTS_WIFI_SCAN_SECONDS", "60")))
POLL_S = max(0.5, float(os.environ.get("UBTS_NETWORK_POLL_SECONDS", "1")))


class CommandError(RuntimeError):
    pass


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


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(data, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


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
        stat = PASSWORD_FILE.stat()
        password = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CommandError(f"AP password file unavailable: {PASSWORD_FILE}") from exc
    if stat.st_uid != 0 or stat.st_mode & 0o077:
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
    environment = os.environ.copy()
    environment.update(
        {
            "AP_ADDRESS": AP_ADDRESS,
            "PORTAINER_TLS_NAME": PORTAINER_TLS_NAME,
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


class Controller:
    def __init__(self) -> None:
        self.mode = "starting"
        self.transition = ""
        self.last_error = ""
        self.last_reason = "service_start"
        self.ap_idle_since: float | None = None
        self.scan_deadline: float | None = None
        self.last_transition_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        device = interface_snapshot()
        ethernet = ethernet_connected()
        clients = ap_client_count() if device["ap_active"] else 0
        now_mono = time.monotonic()
        countdown: float | None = None
        if device["ap_active"] and not ethernet and clients == 0 and self.ap_idle_since is not None:
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
            "can_request_ap": bool(device["wifi_connected"] and not ethernet and not self.transition),
            "can_request_wifi": bool(device["ap_active"] and not ethernet and not self.transition),
            "controls_locked_reason": "ethernet_keeps_ap_active" if ethernet else "transition_in_progress" if self.transition else None,
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
        for hostname in ["bts.sarihub.com", "bts-docker.sarihub.com", "jetson-ssh.sarihub.com"]:
            answer = run(["dig", "+short", f"@{AP_ADDRESS}", hostname, "A"]).stdout.strip()
            if answer != AP_ADDRESS:
                raise CommandError(f"local DNS validation failed for {hostname}")
        run(["curl", "-fsS", "--max-time", "3", "--resolve", f"bts.sarihub.com:80:{AP_ADDRESS}", "http://bts.sarihub.com/api/health"])
        run(["curl", "-fsS", "--max-time", "3", "--resolve", f"bts-docker.sarihub.com:80:{AP_ADDRESS}", "http://bts-docker.sarihub.com/api/status"])
        self.mode = "ap"
        self.transition = ""
        self.last_error = ""
        self.ap_idle_since = time.monotonic()
        self.scan_deadline = None
        self.snapshot()

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
                    return True
            self.snapshot()
            time.sleep(min(5.0, max(0.2, self.scan_deadline - time.monotonic())))
        self.scan_deadline = None
        self.activate_ap("wifi_scan_timeout")
        return False

    def read_request(self) -> str:
        payload = load_json(COMMAND_FILE)
        COMMAND_FILE.unlink(missing_ok=True)
        action = str(payload.get("action") or "")
        return action if action in {"ap", "wifi"} else ""

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
            while True:
                try:
                    request = self.read_request()
                    device = interface_snapshot()
                    ethernet = ethernet_connected()
                    if ethernet:
                        if not device["ap_active"]:
                            self.activate_ap("ethernet_requires_continuous_ap")
                        else:
                            self.transition = ""
                            self.ap_idle_since = None
                            self.snapshot()
                    elif request == "ap" and device["wifi_connected"]:
                        self.activate_ap("dashboard_request_ap")
                    elif request == "wifi" and device["ap_active"]:
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
                except BlockingIOError:
                    raise SystemExit("another ubts-network-mode controller is active")
                except Exception as exc:  # noqa: BLE001 - keep supervised recovery alive
                    self.recover(exc)
                time.sleep(POLL_S)


def queue_request(action: str) -> dict[str, Any]:
    current = load_json(STATUS_FILE)
    if not current or time.time() - float(current.get("updated_at") or 0) > 10:
        return {"ok": False, "state": "controller_unavailable", "error": "network controller status is stale"}
    if current.get("ethernet_connected"):
        return {"ok": False, "state": "ethernet_ap_locked", "error": "Ethernet keeps the Wi-Fi AP active."}
    allowed = current.get("can_request_ap") if action == "ap" else current.get("can_request_wifi")
    if not allowed:
        return {"ok": False, "state": "action_not_available", "mode": current.get("mode")}
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
    parser.add_argument("command", choices=["controller", "status", "request-ap", "request-wifi", "prepare"])
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
    else:
        payload = queue_request("ap" if args.command == "request-ap" else "wifi")
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
