"""deploy/network_helper_update/update_network_helper.py: check, install with verification, automatic rollback
and manual rollback, with the target file, the status file and systemctl replaced by fakes in a temporary folder."""
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("update_network_helper", ROOT / "deploy" / "network_helper_update" / "update_network_helper.py")
assert SPEC and SPEC.loader
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)

NEW_SOURCE = (ROOT / "deploy" / "offline_ap" / "ubts_network_mode.py").read_bytes()
OLD_SOURCE = b"#!/usr/bin/env python3\nprint('old helper')\n"


class FakeSystem:
    """systemctl and the helper command; a restart writes a new status like the controller would."""

    def __init__(self, status_file, target):
        self.status_file = status_file
        self.target = target
        self.calls = []
        self.state = "active"
        self.new_status_has_wifi = True

    def __call__(self, args, timeout=30.0):
        self.calls.append(list(args))
        out, code = "", 0
        if args[:2] == ["systemctl", "is-active"]:
            out = self.state + "\n"
        elif args[:2] == ["systemctl", "restart"]:
            new = self.target.read_bytes() == NEW_SOURCE
            status = {"ok": True, "updated_at": time.time() + 0.5, "mode": "wifi", "transition": None}
            if new and self.new_status_has_wifi:
                status.update(can_request_connect=True, trial=None)
            self.status_file.write_text(json.dumps(status), encoding="utf-8")
        elif args[:2] == [str(self.target), "wifi-saved"]:
            out = '{"ok":true,"profiles":[]}\n'
        else:
            raise AssertionError(f"unexpected command {args}")

        class Done:
            returncode = code
            stdout = out
            stderr = ""
        return Done()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.target = root / "ubts-network-mode"
        self.target.write_bytes(OLD_SOURCE)
        self.status = root / "status.json"
        self.status.write_text(json.dumps({"ok": True, "updated_at": time.time(), "mode": "wifi", "transition": None}), encoding="utf-8")
        self.source = root / "kit" / "ubts_network_mode.py"
        self.source.parent.mkdir()
        self.source.write_bytes(NEW_SOURCE)
        self.system = FakeSystem(self.status, self.target)
        for patch in (
            mock.patch.object(updater, "TARGET", self.target),
            mock.patch.object(updater, "STATUS_FILE", self.status),
            mock.patch.object(updater, "BACKUP_DIR", root / "backups"),
            mock.patch.object(updater, "KNOWN_PREVIOUS", {updater.sha256(OLD_SOURCE): "test old"}),
            mock.patch.object(updater, "run", self.system),
            mock.patch.object(updater.time, "sleep", lambda seconds: None),
            mock.patch.object(updater.os, "chown", lambda *args: None, create=True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_check_mode_changes_nothing(self):
        info = updater.plan(self.source, False)
        self.assertEqual(info["problems"], [])
        self.assertFalse(info["already_installed"])
        self.assertEqual(self.target.read_bytes(), OLD_SOURCE)
        self.assertFalse(any(call[:2] == ["systemctl", "restart"] for call in self.system.calls))

    def test_check_refuses_unknown_installed_and_bad_source(self):
        self.target.write_bytes(b"print('changed by hand')\n")
        self.assertTrue(any("알려진 판" in problem for problem in updater.plan(self.source, False)["problems"]))
        self.assertEqual(updater.plan(self.source, True)["problems"], [])
        self.source.write_bytes(NEW_SOURCE.replace(b"\n", b"\r\n"))
        self.assertTrue(any("CRLF" in problem for problem in updater.plan(self.source, True)["problems"]))
        self.source.write_bytes(b"def broken(:\n")
        self.assertTrue(any("구문" in problem for problem in updater.plan(self.source, True)["problems"]))

    def test_apply_installs_keeps_a_backup_and_writes_a_receipt(self):
        self.assertEqual(updater.apply(self.source, False), 0)
        self.assertEqual(self.target.read_bytes(), NEW_SOURCE)
        backups = list((Path(self.tmp.name) / "backups").glob("ubts-network-mode.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), OLD_SOURCE)
        receipt = json.loads(next((Path(self.tmp.name) / "backups").glob("receipt-apply-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["result"], "installed")
        self.assertEqual(receipt["after"]["mode"], "wifi")
        self.assertTrue(receipt["after"]["wifi_management"])
        self.assertIn([str(self.target), "wifi-saved", "--json"], self.system.calls)
        self.assertEqual(updater.apply(self.source, False), 0)        # a second run has nothing to do

    def test_failed_verification_puts_the_old_helper_back(self):
        self.system.new_status_has_wifi = False
        self.assertEqual(updater.apply(self.source, False), 1)
        self.assertEqual(self.target.read_bytes(), OLD_SOURCE)
        receipt = json.loads(next((Path(self.tmp.name) / "backups").glob("receipt-apply-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(receipt["result"], "rolled_back")
        self.assertEqual(sum(call[:2] == ["systemctl", "restart"] for call in self.system.calls), 2)

    def test_busy_controller_is_not_touched(self):
        self.status.write_text(json.dumps({"updated_at": time.time(), "mode": "transition", "transition": "wifi_scan"}), encoding="utf-8")
        with mock.patch.object(updater, "IDLE_WAIT_S", 0):
            self.assertEqual(updater.apply(self.source, False), 2)
        self.assertEqual(self.target.read_bytes(), OLD_SOURCE)

    def test_manual_rollback_restores_the_newest_backup(self):
        self.assertEqual(updater.apply(self.source, False), 0)
        self.assertEqual(updater.rollback(None), 0)
        self.assertEqual(self.target.read_bytes(), OLD_SOURCE)


if __name__ == "__main__":
    unittest.main()
