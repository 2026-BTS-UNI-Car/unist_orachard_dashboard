#!/usr/bin/env python3
"""Replace the Jetson's network helper (/usr/local/sbin/ubts-network-mode) with the Wi-Fi management version,
and put the previous one back if anything is wrong. Python 3 standard library only (Ubuntu 22.04, Python 3.10).

Modes (README_KO.md):
  (default)    check only, changes nothing; works without root (the status file is read only as root)
  --apply      as root: back up the installed helper, install the new one, restart ubts-network-mode.service,
               verify; on any failure the backup goes back automatically
  --rollback   as root: put the newest backup (or --backup FILE) back and restart

The service restart does not touch NetworkManager connections: the controller starts again, finds the current
Wi-Fi or AP connection and keeps it. Nothing here reads, prints or stores a Wi-Fi password; the receipt holds file
hashes, the service state and the network mode word (wifi / ap / ...), nothing else.

Exit codes: 0 ok (or nothing to do), 1 failed but the previous helper runs again, 2 refused before any change,
3 the automatic rollback failed too (see the receipt and README_KO.md section 5).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = Path("/usr/local/sbin/ubts-network-mode")
SERVICE = "ubts-network-mode.service"
STATUS_FILE = Path("/run/ubts-network-mode/status.json")
BACKUP_DIR = Path("/var/lib/ubts-network-mode/helper-backups")
# Since 2026-11-22 the helper checks the AP's three host names from these settings (defaults *.example.invalid);
# an offline AP template installed before that maps fixed names, which the settings then have to repeat.
AP_TEMPLATE = Path("/opt/ubts-offline-ap/dnsmasq.conf.template")
NETWORK_CONF = Path("/etc/ubts-network-mode.conf")
HOST_NAME_KEYS = ("UBTS_AP_DASHBOARD_HOSTNAME", "UBTS_AP_PORTAINER_HOSTNAME", "UBTS_AP_SSH_HOSTNAME")
# The helper installed on the Jetson before this change (read 2026-11-16 without sudo; = repository 671672a).
KNOWN_PREVIOUS = {"94f61dca48386e05f9261b8a473f574bb56c3ec70d9dec0f45d552523cb59651": "671672a (AP switch only)"}
NEW_COMMANDS = ("wifi-scan", "wifi-saved", "wifi-connect", "wifi-confirm", "wifi-revert", "wifi-profile")
VERIFY_S = 30.0
IDLE_WAIT_S = 90.0


class Refused(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_source() -> Path:
    kit_copy = HERE / "ubts_network_mode.py"          # the kit folder made for the Jetson
    return kit_copy if kit_copy.is_file() else HERE.parent / "offline_ap" / "ubts_network_mode.py"


def run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def service_state() -> str:
    try:
        return run(["systemctl", "is-active", SERVICE], timeout=10).stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def read_status() -> dict:
    try:
        value = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def status_summary(status: dict) -> dict:
    """Only words and times: the receipt and the screen never get SSIDs, addresses or errors of the status."""
    updated = float(status.get("updated_at") or 0)
    return {
        "mode": status.get("mode"),
        "transition": status.get("transition"),
        "trial": bool(status.get("trial")),
        "age_s": round(time.time() - updated, 1) if updated else None,
        "wifi_management": "can_request_connect" in status,
    }


def check_source(source: Path) -> tuple[bytes, list[str]]:
    data = source.read_bytes()
    problems = []
    try:
        compile(data, str(source), "exec")
    except SyntaxError as exc:
        problems.append(f"새 도우미 구문 오류: {exc}")
    text = data.decode("utf-8", "replace")
    missing = [command for command in NEW_COMMANDS if f'"{command}"' not in text]
    if missing:
        problems.append(f"새 도우미에 명령이 없음: {', '.join(missing)}")
    if b"\r\n" in data:
        problems.append("새 도우미 줄 끝이 CRLF")
    return data, problems


def host_name_problems() -> list[str]:
    """The installed AP template maps fixed host names (installed before 2026-11-22): the new helper would check the
    names of NETWORK_CONF instead and the AP would not start unless they are the same three. The names are not
    printed. An unreadable settings file (check without root) is left to the root run."""
    try:
        template = AP_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return []
    fixed = sorted(name for name in re.findall(r"^address=/([^/\s]+)/", template, re.M) if "${" not in name)
    if not fixed:
        return []
    try:
        conf = NETWORK_CONF.read_text(encoding="utf-8")
    except OSError:
        return []
    values = {}
    for line in conf.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip()
    if sorted(values.get(key, "") for key in HOST_NAME_KEYS) != fixed:
        return [f"{NETWORK_CONF} 에 AP 이름 {', '.join(HOST_NAME_KEYS)} 를 설치된 {AP_TEMPLATE} 의 이름과 같게 적어야 한다"
                " (없으면 새 도우미가 AP 를 켜지 못한다)"]
    return []


def plan(source: Path, allow_unknown: bool) -> dict:
    data, problems = check_source(source)
    problems.extend(host_name_problems())
    installed = TARGET.read_bytes() if TARGET.is_file() else None
    installed_sha = sha256(installed) if installed is not None else None
    new_sha = sha256(data)
    if installed is None:
        problems.append(f"설치된 도우미가 없음: {TARGET} (이 도구는 교체만 한다. 처음 설치는 deploy/offline_ap/install-network-mode.sh)")
    elif installed_sha != new_sha and installed_sha not in KNOWN_PREVIOUS and not allow_unknown:
        problems.append("설치된 도우미가 알려진 판이 아님(손으로 고쳤을 수 있음). 확인 뒤 --allow-unknown-installed")
    state = service_state()
    if state != "active":
        problems.append(f"{SERVICE} 상태가 active 가 아님: {state}")
    return {
        "source": str(source),
        "new_sha256": new_sha,
        "installed_sha256": installed_sha,
        "installed_known_as": KNOWN_PREVIOUS.get(installed_sha or "", "새 판과 같음" if installed_sha == new_sha else "모름"),
        "already_installed": installed_sha == new_sha,
        "service": state,
        "problems": problems,
        "data": data,
    }


def wait_idle(deadline_s: float) -> dict:
    """Wait until the controller is between transitions (no AP start, no saved Wi-Fi search, no trial)."""
    end = time.monotonic() + deadline_s
    while True:
        status = read_status()
        fresh = status and time.time() - float(status.get("updated_at") or 0) <= 10
        if fresh and not status.get("transition") and not status.get("trial"):
            return status
        if time.monotonic() >= end:
            raise Refused("제어 서비스가 전환 중이거나 상태가 오래됨. 잠시 뒤 다시: " + json.dumps(status_summary(status), ensure_ascii=False))
        time.sleep(2)


def install_bytes(data: bytes) -> None:
    temporary = TARGET.with_name(f".{TARGET.name}.{os.getpid()}.new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o755)
        os.replace(temporary, TARGET)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def restart_and_verify(expect_wifi_management: bool, mode_before: str | None) -> list[str]:
    started = time.time()
    result = run(["systemctl", "restart", SERVICE], timeout=60)
    if result.returncode != 0:
        return [f"systemctl restart 실패 (exit {result.returncode})"]
    end = time.monotonic() + VERIFY_S
    problems = ["확인 시간 안에 새 상태가 오지 않음"]
    while time.monotonic() < end:
        time.sleep(1)
        status = read_status()
        if service_state() != "active" or float(status.get("updated_at") or 0) <= started:
            continue
        problems = []
        if expect_wifi_management and "can_request_connect" not in status:
            problems.append("새 상태에 Wi-Fi 관리 항목이 없음")
        if mode_before in {"wifi", "ap", "ethernet_ap"} and status.get("mode") not in {mode_before, None} and not status.get("transition"):
            problems.append(f"재시작 뒤 방식이 바뀜: {mode_before} -> {status.get('mode')}")
        break
    if not problems and expect_wifi_management:
        # The new read-only command answers (the saved list; the answer itself is not printed or kept).
        check = run([str(TARGET), "wifi-saved", "--json"], timeout=30)
        try:
            answer = json.loads((check.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            answer = {}
        if not answer.get("ok"):
            problems.append(f"wifi-saved 명령이 실패: {answer.get('state') or check.returncode}")
    return problems


def write_receipt(receipt: dict) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = BACKUP_DIR / f"receipt-{receipt['action']}-{receipt['started_at'].replace(':', '')}.json"
    path.write_bytes((json.dumps(receipt, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)
    return path


def now_text() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply(source: Path, allow_unknown: bool) -> int:
    info = plan(source, allow_unknown)
    if info["problems"]:
        for problem in info["problems"]:
            print(f"거절: {problem}")
        return 2
    if info["already_installed"]:
        print("이미 새 판이 설치되어 있다. 바꾸지 않음.")
        return 0
    receipt = {"schema": "ubts.network_helper_update.v1", "action": "apply", "started_at": now_text(),
               "installed_sha256": info["installed_sha256"], "new_sha256": info["new_sha256"]}
    try:
        before = wait_idle(IDLE_WAIT_S)
    except Refused as exc:
        print(f"거절: {exc}")
        return 2
    receipt["before"] = status_summary(before)
    previous = TARGET.read_bytes()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = BACKUP_DIR / f"ubts-network-mode.{sha256(previous)[:12]}.{receipt['started_at'].replace(':', '')}"
    backup.write_bytes(previous)
    os.chmod(backup, 0o700)
    receipt["backup"] = str(backup)
    print(f"보존: {backup}")
    install_bytes(info["data"])
    print(f"설치: {TARGET} sha256 {info['new_sha256'][:12]}")
    problems = restart_and_verify(True, before.get("mode"))
    if not problems:
        receipt.update(result="installed", after=status_summary(read_status()), finished_at=now_text())
        print(f"영수증: {write_receipt(receipt)}")
        print("결과: installed")
        return 0
    receipt["problems"] = problems
    for problem in problems:
        print(f"확인 실패: {problem}")
    print("되돌리는 중...")
    install_bytes(previous)
    rollback_problems = restart_and_verify(False, None)
    receipt.update(result="rolled_back" if not rollback_problems else "rollback_failed",
                   rollback_problems=rollback_problems, after=status_summary(read_status()), finished_at=now_text())
    print(f"영수증: {write_receipt(receipt)}")
    if rollback_problems:
        print("결과: rollback_failed (README_KO.md 5절)")
        return 3
    print("결과: rolled_back (이전 도우미가 다시 돈다)")
    return 1


def rollback(backup: Path | None) -> int:
    if backup is None:
        candidates = sorted(BACKUP_DIR.glob("ubts-network-mode.*"), key=lambda path: path.stat().st_mtime)
        if not candidates:
            print(f"거절: 보존본이 없음 ({BACKUP_DIR})")
            return 2
        backup = candidates[-1]
    data = backup.read_bytes()
    try:
        compile(data, str(backup), "exec")
    except SyntaxError as exc:
        print(f"거절: 보존본 구문 오류 {exc}")
        return 2
    receipt = {"schema": "ubts.network_helper_update.v1", "action": "rollback", "started_at": now_text(),
               "installed_sha256": sha256(TARGET.read_bytes()) if TARGET.is_file() else None,
               "restored_sha256": sha256(data), "backup": str(backup)}
    try:
        before = wait_idle(IDLE_WAIT_S)
    except Refused as exc:
        print(f"거절: {exc}")
        return 2
    receipt["before"] = status_summary(before)
    install_bytes(data)
    problems = restart_and_verify(False, before.get("mode"))
    receipt.update(result="restored" if not problems else "restore_failed", problems=problems,
                   after=status_summary(read_status()), finished_at=now_text())
    print(f"영수증: {write_receipt(receipt)}")
    print(f"결과: {receipt['result']}")
    return 0 if not problems else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="설치 (root)")
    parser.add_argument("--rollback", action="store_true", help="보존본으로 되돌리기 (root)")
    parser.add_argument("--backup", type=Path, help="--rollback 에 쓸 보존본 (기본: 가장 새 것)")
    parser.add_argument("--source", type=Path, default=None, help="새 도우미 파일 (기본: 같은 폴더 또는 ../offline_ap)")
    parser.add_argument("--allow-unknown-installed", action="store_true", help="설치된 도우미가 알려진 판이 아니어도 바꾼다")
    args = parser.parse_args(argv)
    source = args.source or default_source()
    if args.apply and args.rollback:
        parser.error("--apply 와 --rollback 은 같이 쓰지 않는다")
    if (args.apply or args.rollback) and os.geteuid() != 0:
        print("거절: --apply / --rollback 은 root 로 (sudo python3 update_network_helper.py ...)")
        return 2
    if args.rollback:
        return rollback(args.backup)
    if args.apply:
        return apply(source, args.allow_unknown_installed)
    info = plan(source, args.allow_unknown_installed)
    print(f"새 도우미: {info['source']}")
    print(f"  sha256 {info['new_sha256']}")
    print(f"설치된 도우미: {TARGET}")
    print(f"  sha256 {info['installed_sha256']} ({info['installed_known_as']})")
    print(f"서비스 {SERVICE}: {info['service']}")
    if os.geteuid() == 0:
        print(f"상태: {json.dumps(status_summary(read_status()), ensure_ascii=False)}")
    else:
        print("상태 파일은 root 만 읽는다: --apply 때 전환 중이 아닌지 확인한다")
    if info["already_installed"]:
        print("점검 결과: 이미 새 판이 설치되어 있다")
        return 0
    for problem in info["problems"]:
        print(f"문제: {problem}")
    if info["problems"]:
        print("점검 결과: 지금은 설치할 수 없다")
        return 2
    print("점검 통과: sudo python3 update_network_helper.py --apply 로 설치할 수 있다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
