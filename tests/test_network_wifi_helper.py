"""Wi-Fi management in the host helper (deploy/offline_ap/ubts_network_mode.py), against a fake nmcli.

FakeNM answers the nmcli / iw / ip / ping command lines the helper runs, keeps profiles and the active
connection, and records every command line, so the tests can check that a password never reaches a
command line, a queued command, the trial file or an answer. All names and passwords here are made up.
"""
import importlib.util
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "offline_ap" / "ubts_network_mode.py"
SPEC = importlib.util.spec_from_file_location("ubts_network_mode_wifi", MODULE_PATH)
assert SPEC and SPEC.loader
nm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nm)

IFACE = nm.INTERFACE
PASSWORD = "fake pass\\word 123"     # a space and a backslash: both must be escaped in the keyfile
NEW_PASSWORD = "another-fake-pw-9"
PREVIOUS_UUID = "11111111-1111-4111-8111-111111111111"
SAVED_5G_UUID = "22222222-2222-4222-8222-222222222222"
OPEN_UUID = "33333333-3333-4333-8333-333333333333"


def terse(*values):
    return ":".join(str(value).replace("\\", "\\\\").replace(":", "\\:") for value in values)


def unescape_keyfile(value):
    out, index = [], 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            out.append({"s": " ", "\\": "\\", "n": "\n", "t": "\t"}.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            out.append(value[index])
            index += 1
    return "".join(out)


def parse_keyfile(text):
    sections, section = {}, None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = sections.setdefault(stripped[1:-1], {})
        elif "=" in line and section is not None:
            key, value = line.split("=", 1)
            section[key.strip()] = value
    return sections


class FakeNM:
    def __init__(self):
        self.profiles = {}
        self.active = None              # [uuid, "activating" | "activated"]
        self.pending = "ok"             # what a --wait 0 activation turns into: ok | fail | no_ip
        self.ethernet = False
        self.ipv4 = "192.0.2.10"
        self.gateway = "192.0.2.1"
        self.scan_text = ""
        self.dump_text = ""
        self.scan_calls = 0
        self.calls = []

    def add(self, profile_uuid, name, ssid, *, priority=0, autoconnect=True, key_mgmt="wpa-psk", filename="", mode="infrastructure"):
        self.profiles[profile_uuid] = {"name": name, "ssid": ssid, "priority": priority, "autoconnect": autoconnect,
                                       "key_mgmt": key_mgmt, "filename": filename, "mode": mode, "psk": None}

    def connected(self):
        return bool(self.active and self.active[1] == "activated")

    def settle(self):
        if self.active and self.active[1] == "activating":
            if self.pending == "fail":
                self.active = None
            else:
                self.active[1] = "activated"

    def __call__(self, args, *, timeout=15.0, check=True, env=None):
        args = list(args)
        self.calls.append(args)
        out, code = self.handle(args)
        result = subprocess.CompletedProcess(args, code, out, "" if code == 0 else "Error: fake failure")
        if check and code != 0:
            raise nm.CommandError(f"{args[0]} failed: fake failure")
        return result

    def handle(self, a):
        if a[:4] == ["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION"]:
            if self.connected():
                return f"GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:{self.profiles[self.active[0]]['name']}\n", 0
            return "GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n", 0
        if a[:6] == ["nmcli", "-t", "--escape", "no", "-f", "TYPE,STATE"]:
            return ("ethernet:connected\n" if self.ethernet else "ethernet:unavailable\n") + "wifi:connected\n", 0
        if a[:2] == ["iw", "dev"]:
            if a[3] == "station":
                return "", 0
            if a[3] == "link":
                if not self.connected():
                    return "Not connected.\n", 0
                return "Connected to 02:00:00:00:00:01 (on wlP1p1s0)\n\tSSID: x\n\tfreq: 2442\n\tsignal: -56 dBm\n\ttx bitrate: 52.0 MBit/s MCS 11\n", 0
            if a[3:5] == ["scan", "dump"]:
                return self.dump_text, 0
        if a[:5] == ["nmcli", "-t", "-e", "yes", "-f"]:
            fields = a[5]
            if fields.startswith("NAME,UUID,TYPE"):
                rows = []
                for profile_uuid, p in self.profiles.items():
                    active = bool(self.active and self.active[0] == profile_uuid)
                    rows.append(terse(p["name"], profile_uuid, "802-11-wireless", "yes" if p["autoconnect"] else "no", p["priority"],
                                      "yes" if active else "no", IFACE if active else "", 1763223404, p["filename"]))
                rows.append(terse("docker0", "44444444-4444-4444-8444-444444444444", "bridge", "no", 0, "yes", "docker0", 0, "/run/x"))
                return "\n".join(rows) + "\n", 0
            if fields == "NAME":
                return "\n".join(terse(p["name"]) for p in self.profiles.values()) + "\n", 0
            if fields == "UUID,DEVICE,STATE":
                self.settle()
                return (terse(self.active[0], IFACE, self.active[1]) + "\n") if self.active else "", 0
            if fields.startswith("IN-USE"):
                self.scan_calls += 1
                return self.scan_text, 0
        if a[:5] == ["nmcli", "-t", "-e", "no", "-f"] and a[6:9] == ["connection", "show", "uuid"]:
            p = self.profiles.get(a[9])
            if p is None:
                return "", 10
            wanted = a[5].split(",")
            if "802-11-wireless-security.key-mgmt" in wanted and p["key_mgmt"] == "none":
                return "", 10
            values = {"802-11-wireless.ssid": p["ssid"], "802-11-wireless.mode": p["mode"], "802-11-wireless.band": "",
                      "802-11-wireless-security.key-mgmt": p["key_mgmt"]}
            return "\n".join(f"{key}:{values[key]}" for key in wanted) + "\n", 0
        if a[:3] == ["nmcli", "connection", "load"]:
            return self.load(Path(a[3])), 0
        if a[:4] == ["nmcli", "connection", "show", "uuid"]:
            return "", 0 if a[4] in self.profiles else 10
        if a[:4] == ["nmcli", "connection", "delete", "uuid"]:
            removed = self.profiles.pop(a[4], None)
            if removed and removed["filename"]:
                Path(removed["filename"]).unlink(missing_ok=True)
            if self.active and self.active[0] == a[4]:
                self.active = None
            return "", 0 if removed else 10
        if a[:4] == ["nmcli", "connection", "modify", "uuid"]:
            p = self.profiles.get(a[4])
            if p is None:
                return "", 10
            settings = dict(zip(a[5::2], a[6::2]))
            p["autoconnect"] = settings.get("connection.autoconnect", "yes" if p["autoconnect"] else "no") == "yes"
            p["priority"] = int(settings.get("connection.autoconnect-priority", p["priority"]))
            old = Path(p["filename"]) if p["filename"] else None
            if old is not None and old.parent == nm.NM_RUNTIME_DIR:
                # NetworkManager makes an in-memory profile persistent on a to-disk update.
                target = nm.NM_PERSISTENT_DIR / f"{p['name']}.nmconnection"
                target.write_bytes(old.read_bytes())
                old.unlink()
                p["filename"] = str(target)
            return "", 0
        if a[0] == "nmcli" and a[1] == "--wait" and a[3:5] == ["connection", "up"]:
            profile_uuid = a[6]
            if profile_uuid not in self.profiles:
                return "", 10
            if a[2] == "0":
                if self.pending == "reject":
                    return "", 4
                self.active = [profile_uuid, "activating"]
            else:
                self.active = [profile_uuid, "activated"]
            return "", 0
        if a[:5] == ["ip", "-4", "-o", "address", "show"]:
            ok = self.connected() and self.pending != "no_ip"
            return (f"3: {IFACE}    inet {self.ipv4}/24 brd 192.0.2.255 scope global dynamic\n" if ok else ""), 0
        if a[:5] == ["ip", "-4", "route", "show", "default"]:
            ok = self.connected() and self.pending != "no_ip"
            return (f"default via {self.gateway} proto dhcp metric 600\n" if ok else ""), 0
        if a[0] == "ping":
            return "", 0
        raise AssertionError(f"unexpected command: {a}")

    def load(self, path):
        sections = parse_keyfile(path.read_text(encoding="utf-8"))
        connection, wifi, security = sections["connection"], sections.get("wifi", {}), sections.get("wifi-security", {})
        raw_ssid = wifi.get("ssid", "")
        if raw_ssid and all(part.isdigit() for part in raw_ssid.rstrip(";").split(";")):
            ssid = bytes(int(part) for part in raw_ssid.rstrip(";").split(";")).decode("utf-8")
        else:
            ssid = unescape_keyfile(raw_ssid)
        profile_uuid = connection["uuid"]
        p = self.profiles.setdefault(profile_uuid, {"priority": 0, "mode": "infrastructure"})
        p.update(name=unescape_keyfile(connection["id"]), ssid=ssid, filename=str(path),
                 autoconnect=connection.get("autoconnect", "true") != "false",
                 priority=int(connection.get("autoconnect-priority", p.get("priority", 0))),
                 key_mgmt=security.get("key-mgmt", "none"),
                 psk=unescape_keyfile(security["psk"]) if "psk" in security else None)
        return ""


SCAN_TEXT = "\n".join([
    terse("*", "02:00:00:00:00:01", "Field-2G", "Infra", 7, "2442 MHz", "130 Mbit/s", 80, "WPA2"),
    terse(" ", "02:00:00:00:00:02", "Field-5G", "Infra", 149, "5745 MHz", "270 Mbit/s", 40, "WPA2"),
    terse(" ", "02:00:00:00:00:03", "New Net", "Infra", 1, "2412 MHz", "65 Mbit/s", 60, "WPA1 WPA2"),
    terse(" ", "02:00:00:00:00:04", "New Net", "Infra", 6, "2437 MHz", "65 Mbit/s", 70, "WPA1 WPA2"),
    terse(" ", "02:00:00:00:00:05", "Cafe Open", "Infra", 11, "2462 MHz", "54 Mbit/s", 30, ""),
    terse(" ", "02:00:00:00:00:06", "Office", "Infra", 36, "5180 MHz", "540 Mbit/s", 55, "WPA2 802.1X"),
    terse(" ", "02:00:00:00:00:07", "", "Infra", 1, "2412 MHz", "65 Mbit/s", 20, "WPA2"),
    terse(" ", "02:00:00:00:00:08", "Only3", "Infra", 44, "5220 MHz", "540 Mbit/s", 45, "WPA3"),
]) + "\n"
DUMP_TEXT = "BSS 02:00:00:00:00:01(on wlP1p1s0) -- associated\n\tsignal: -56.00 dBm\nBSS 02:00:00:00:00:04(on wlP1p1s0)\n\tsignal: -61.00 dBm\n"

SAVED_KEYFILE = """[connection]
id=Field-5G
uuid=22222222-2222-4222-8222-222222222222
type=wifi
autoconnect-priority=100
timestamp=1763223404

[wifi]
mode=infrastructure
ssid=Field-5G

[wifi-security]
key-mgmt=wpa-psk
psk-flags=1
psk=old-fake-password

[ipv4]
method=auto

[ipv6]
addr-gen-mode=stable-privacy
method=auto
"""


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class HelperTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        runtime = root / "run-ubts"
        persistent = root / "etc-nm"
        nm_runtime = root / "run-nm"
        for folder in (runtime, persistent, nm_runtime):
            folder.mkdir()
        self.fake = FakeNM()
        self.fake.scan_text = SCAN_TEXT
        self.fake.dump_text = DUMP_TEXT
        self.fake.add(PREVIOUS_UUID, "Field-2G", "Field-2G", priority=200, filename=str(persistent / "Field-2G.nmconnection"))
        saved_path = persistent / "Field-5G.nmconnection"
        saved_path.write_bytes(SAVED_KEYFILE.encode("utf-8"))
        self.saved_path = saved_path
        self.fake.add(SAVED_5G_UUID, "Field-5G", "Field-5G", priority=100, filename=str(saved_path))
        self.fake.add(OPEN_UUID, "Old Open", "Old Open", key_mgmt="none", filename=str(persistent / "Old Open.nmconnection"))
        self.fake.active = [PREVIOUS_UUID, "activated"]
        self.clock = Clock()
        patches = [
            mock.patch.object(nm, "run", self.fake),
            mock.patch.object(nm, "STATUS_FILE", runtime / "status.json"),
            mock.patch.object(nm, "COMMAND_FILE", runtime / "command.json"),
            mock.patch.object(nm, "SCAN_FILE", runtime / "scan.json"),
            mock.patch.object(nm, "TRIAL_FILE", runtime / "trial.json"),
            mock.patch.object(nm, "BACKUP_DIR", runtime / "backup"),
            mock.patch.object(nm, "AP_UUID_FILE", root / "ap_uuid"),
            mock.patch.object(nm, "NM_PERSISTENT_DIR", persistent),
            mock.patch.object(nm, "NM_RUNTIME_DIR", nm_runtime),
            mock.patch.object(nm, "check_root_keyfile", lambda path: None),
            mock.patch.object(nm.time, "monotonic", self.clock),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.controller = nm.Controller()

    # -- helpers --
    def status(self):
        return self.controller.snapshot()

    def scan(self):
        self.status()
        return nm.wifi_scan_request({})

    def tick(self):
        request = self.controller.read_request()
        if self.controller.trial is not None:
            self.controller.trial_step(request)
        else:
            self.controller.step(request)

    def assert_no_password_anywhere(self, *passwords):
        for password in passwords:
            for call in self.fake.calls:
                self.assertNotIn(password, " ".join(call), call)
            for path in (nm.COMMAND_FILE, nm.TRIAL_FILE, nm.STATUS_FILE, nm.SCAN_FILE):
                if path.exists():
                    self.assertNotIn(password, path.read_text(encoding="utf-8"), path)


class ParsingTests(unittest.TestCase):
    def test_terse_split_handles_escaped_colons(self):
        self.assertEqual(nm.split_terse(r"a\:b:c\\d:"), ["a:b", "c\\d", ""])

    def test_scan_groups_by_ssid_and_band_and_maps_security(self):
        networks, hidden = nm.parse_scan(SCAN_TEXT, nm.parse_scan_dump(DUMP_TEXT))
        self.assertEqual(hidden, 1)
        by_name = {item["ssid"]: item for item in networks}
        self.assertEqual(networks[0]["ssid"], "Field-2G")            # in use first
        self.assertTrue(by_name["Field-2G"]["in_use"])
        self.assertEqual(by_name["Field-2G"]["signal_dbm"], -56)
        self.assertEqual(by_name["New Net"]["ap_count"], 2)
        self.assertEqual(by_name["New Net"]["channel"], 6)           # the stronger of the two
        self.assertEqual(by_name["New Net"]["signal_dbm"], -61)
        self.assertEqual(by_name["Field-5G"]["band"], "5GHz")
        self.assertEqual(by_name["Cafe Open"]["key_mgmt"], "none")
        self.assertEqual(by_name["Cafe Open"]["security"], "open")
        self.assertFalse(by_name["Office"]["supported"])
        self.assertEqual(by_name["Only3"]["key_mgmt"], "sae")

    def test_link_parse(self):
        link = nm.parse_link("Connected to 02:00:00:00:00:01 (on wlP1p1s0)\n\tSSID: Field-2G\n\tfreq: 2442\n\tsignal: -58 dBm\n"
                             "\trx bitrate: 104.0 MBit/s MCS 13\n\ttx bitrate: 78.0 MBit/s MCS 12\n")
        self.assertEqual(link, {"ssid": "Field-2G", "freq_mhz": 2442, "band": "2.4GHz", "signal_dbm": -58,
                                "tx_mbps": 78.0, "rx_mbps": 104.0})
        self.assertIsNone(nm.parse_link("Not connected.\n"))

    def test_validation(self):
        self.assertEqual(nm.validate_ssid("가" * 10), "가" * 10)           # 30 bytes
        for bad in ("", "x" * 33, "가" * 11, "a\nb", None):
            with self.assertRaises(nm.InputError):
                nm.validate_ssid(bad)
        self.assertEqual(nm.validate_wifi_password("a" * 64, "wpa-psk"), "a" * 64)  # 64 hex
        for bad in ("short", "x" * 64, "pässword-1", "tab\there12", 12345678, None):
            with self.assertRaises(nm.InputError):
                nm.validate_wifi_password(bad, "wpa-psk")
        self.assertEqual(len(nm.validate_wifi_password("s" * 100, "sae")), 100)
        for bad in (True, 1000, -1000, "5", 1.5):
            with self.assertRaises(nm.InputError):
                nm.validate_priority(bad)
        with self.assertRaises(nm.InputError):
            nm.validate_uuid("../../etc/passwd")
        with self.assertRaises(nm.InputError):
            nm.validate_txn("ABC")

    def test_error_messages_never_carry_the_value(self):
        try:
            nm.validate_wifi_password("p" * 70, "wpa-psk")
        except nm.InputError as exc:
            self.assertNotIn("p" * 10, str(exc))

    def test_new_keyfile_escapes_and_uses_byte_list_ssid(self):
        text = nm.new_profile_keyfile(profile_uuid=PREVIOUS_UUID, name=" A b", ssid="A b", key_mgmt="wpa-psk", password=PASSWORD)
        sections = parse_keyfile(text)
        self.assertEqual(sections["connection"]["autoconnect"], "false")
        self.assertEqual(sections["wifi"]["ssid"], "65;32;98;")
        self.assertEqual(unescape_keyfile(sections["wifi-security"]["psk"]), PASSWORD)
        self.assertNotIn(" ", sections["wifi-security"]["psk"])
        self.assertEqual(unescape_keyfile(sections["connection"]["id"]), " A b")
        open_text = nm.new_profile_keyfile(profile_uuid=PREVIOUS_UUID, name="o", ssid="o", key_mgmt="none", password=None)
        self.assertNotIn("wifi-security", open_text)

    def test_trial_edit_keeps_every_other_line(self):
        edited = nm.edit_keyfile_for_trial(SAVED_KEYFILE, NEW_PASSWORD)
        sections = parse_keyfile(edited)
        self.assertEqual(sections["wifi-security"]["psk"], NEW_PASSWORD)
        self.assertNotIn("psk-flags", sections["wifi-security"])
        self.assertEqual(sections["connection"]["autoconnect"], "false")
        self.assertEqual(sections["connection"]["timestamp"], "1763223404")
        kept = [line for line in SAVED_KEYFILE.split("\n") if not line.startswith(("psk", "autoconnect="))]
        self.assertEqual([line for line in edited.split("\n") if not line.startswith(("psk", "autoconnect="))], kept)
        with self.assertRaises(nm.InputError):
            nm.edit_keyfile_for_trial("[connection]\nid=x\n[wifi]\nssid=x\n", NEW_PASSWORD)


class ScanAndSavedTests(HelperTestCase):
    def test_scan_is_rate_limited_and_cached(self):
        first = self.scan()
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertEqual(self.fake.scan_calls, 1)
        second = nm.wifi_scan_request({})
        self.assertTrue(second["cached"])
        self.assertEqual(self.fake.scan_calls, 1)
        self.assertEqual(second["networks"], first["networks"])

    def test_scan_is_refused_while_the_ap_is_on(self):
        self.status()
        status = json.loads(nm.STATUS_FILE.read_text(encoding="utf-8"))
        status.update(can_scan=False, ap_active=True, mode="ap")
        nm.atomic_json(nm.STATUS_FILE, status)
        result = nm.wifi_scan_request({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "scan_not_available")
        self.assertEqual(self.fake.scan_calls, 0)

    def test_saved_list_leaves_out_the_ap_and_other_types(self):
        nm.AP_UUID_FILE.write_text("55555555-5555-4555-8555-555555555555\n", encoding="utf-8")
        self.fake.add("55555555-5555-4555-8555-555555555555", nm.AP_CONNECTION, "BTS_Jetson", mode="ap")
        self.fake.add("66666666-6666-4666-8666-666666666666", "Phone hotspot share", "x", mode="ap")
        rows = nm.wifi_saved_request({})["profiles"]
        self.assertEqual([row["name"] for row in rows], ["Field-2G", "Field-5G", "Old Open"])
        first = rows[0]
        self.assertTrue(first["active"])
        self.assertEqual(first["storage"], "disk")
        self.assertNotIn("filename", first)
        self.assertEqual(rows[2]["key_mgmt"], "none")
        self.assertFalse(rows[2]["password_editable"])

    def test_profile_update(self):
        self.status()
        result = nm.wifi_profile_request({"uuid": SAVED_5G_UUID, "autoconnect": False, "priority": 150})
        self.assertTrue(result["ok"])
        self.assertIn(["nmcli", "connection", "modify", "uuid", SAVED_5G_UUID, "connection.autoconnect", "no",
                       "connection.autoconnect-priority", "150"], self.fake.calls)
        self.assertEqual(self.fake.profiles[SAVED_5G_UUID]["priority"], 150)
        with self.assertRaises(nm.InputError):
            nm.wifi_profile_request({"uuid": SAVED_5G_UUID, "autoconnect": "yes", "priority": 1})
        result = nm.run_wifi_command("wifi-profile", {"uuid": "77777777-7777-4777-8777-777777777777", "autoconnect": True, "priority": 1})
        self.assertEqual(result["state"], "invalid_request")


class TrialTests(HelperTestCase):
    def start_new_network_trial(self, password=PASSWORD):
        self.scan()
        result = nm.wifi_connect_request({"ssid": "New Net", "password": password, "autoconnect": True, "priority": 300})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["kind"], "new")
        self.assertNotIn(password, json.dumps(result))
        self.tick()                                   # controller takes the command and starts the trial
        return result

    def candidate_uuid(self):
        return next(u for u, p in self.fake.profiles.items() if p["ssid"] == "New Net")

    def test_new_network_confirmed_keeps_it_with_the_chosen_settings(self):
        accepted = self.start_new_network_trial()
        candidate = self.candidate_uuid()
        profile = self.fake.profiles[candidate]
        self.assertEqual(profile["psk"], PASSWORD)
        self.assertFalse(profile["autoconnect"])           # off during the trial
        self.assertEqual(Path(profile["filename"]).parent, nm.NM_RUNTIME_DIR)
        self.assertEqual(self.controller.trial["phase"], "activating")
        self.tick()
        self.assertEqual(self.controller.trial["phase"], "waiting_confirm")
        status = self.status()
        self.assertEqual(status["trial"]["txn"], accepted["txn"])
        self.assertTrue(status["trial"]["can_confirm"])
        self.assertEqual(status["trial"]["previous"], "Field-2G")
        self.assertFalse(status["can_request_ap"])
        self.assertFalse(status["can_request_connect"])
        self.assertEqual(nm.wifi_trial_request("confirm", {"txn": accepted["txn"]})["state"], "accepted")
        self.tick()
        self.assertIsNone(self.controller.trial)
        self.assertEqual(self.controller.last_trial["result"], "kept")
        self.assertEqual(self.controller.last_trial["warnings"], [])
        self.assertTrue(profile["autoconnect"])
        self.assertEqual(profile["priority"], 300)
        self.assertEqual(Path(profile["filename"]).parent, nm.NM_PERSISTENT_DIR)
        self.assertFalse(nm.TRIAL_FILE.exists())
        self.assert_no_password_anywhere(PASSWORD)

    def test_unconfirmed_trial_goes_back_and_removes_the_new_profile(self):
        self.start_new_network_trial()
        candidate = self.candidate_uuid()
        self.tick()
        self.assertEqual(self.controller.trial["phase"], "waiting_confirm")
        self.clock.value += nm.TRIAL_S + 1
        self.tick()
        self.assertIsNone(self.controller.trial)
        self.assertEqual(self.controller.last_trial["result"], "reverted")
        self.assertEqual(self.controller.last_trial["reason"], "not_confirmed_in_time")
        self.assertNotIn(candidate, self.fake.profiles)
        self.assertEqual(self.fake.active, [PREVIOUS_UUID, "activated"])
        self.assertIn(["nmcli", "--wait", "30", "connection", "up", "uuid", PREVIOUS_UUID, "ifname", IFACE], self.fake.calls)
        self.assertEqual(list(nm.NM_RUNTIME_DIR.iterdir()), [])
        self.assert_no_password_anywhere(PASSWORD)

    def test_wrong_password_reverts_early(self):
        self.fake.pending = "fail"
        self.start_new_network_trial()
        self.clock.value += 6
        self.tick()
        self.assertIsNone(self.controller.trial)
        self.assertEqual(self.controller.last_trial["reason"], "activation_failed")
        self.assertEqual(self.fake.active, [PREVIOUS_UUID, "activated"])

    def test_no_address_reverts_after_the_link_time(self):
        self.fake.pending = "no_ip"
        self.start_new_network_trial()
        self.tick()
        self.assertEqual(self.controller.trial["phase"], "activating")
        self.clock.value += nm.TRIAL_LINK_S + 1
        self.tick()
        self.assertEqual(self.controller.last_trial["reason"], "no_address_or_route_in_time")

    def test_operator_revert(self):
        accepted = self.start_new_network_trial()
        self.tick()
        self.assertEqual(nm.wifi_trial_request("revert", {"txn": accepted["txn"]})["state"], "accepted")
        self.tick()
        self.assertEqual(self.controller.last_trial["reason"], "operator_revert")
        self.assertEqual(self.fake.active, [PREVIOUS_UUID, "activated"])

    def test_confirm_needs_the_running_trial_and_a_ready_link(self):
        accepted = self.start_new_network_trial()
        self.status()
        self.assertEqual(nm.wifi_trial_request("confirm", {"txn": accepted["txn"]})["state"], "not_ready")
        self.assertEqual(nm.wifi_trial_request("confirm", {"txn": "0" * 16})["state"], "no_such_trial")

    def test_password_change_of_a_saved_profile_is_restored_on_revert(self):
        original = self.saved_path.read_bytes()
        self.status()
        result = nm.wifi_connect_request({"uuid": SAVED_5G_UUID, "password": NEW_PASSWORD, "autoconnect": True, "priority": 100})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["kind"], "saved_password")
        self.assertEqual(self.fake.profiles[SAVED_5G_UUID]["psk"], NEW_PASSWORD)
        self.assertFalse(self.fake.profiles[SAVED_5G_UUID]["autoconnect"])
        backups = list(nm.BACKUP_DIR.iterdir())
        self.assertEqual(len(backups), 1)
        self.tick()
        self.tick()
        self.clock.value += nm.TRIAL_S + 1
        self.tick()
        self.assertEqual(self.saved_path.read_bytes(), original)
        self.assertEqual(self.fake.profiles[SAVED_5G_UUID]["psk"], "old-fake-password")
        self.assertEqual(list(nm.BACKUP_DIR.iterdir()), [])
        self.assertEqual(self.fake.active, [PREVIOUS_UUID, "activated"])
        self.assert_no_password_anywhere(NEW_PASSWORD)

    def test_password_change_confirmed_drops_the_backup(self):
        self.status()
        result = nm.wifi_connect_request({"uuid": SAVED_5G_UUID, "password": NEW_PASSWORD, "autoconnect": False, "priority": 5})
        self.tick()
        self.tick()
        nm.wifi_trial_request("confirm", {"txn": result["txn"]})
        self.tick()
        self.assertEqual(self.controller.last_trial["result"], "kept")
        self.assertEqual(list(nm.BACKUP_DIR.iterdir()), [])
        self.assertEqual(parse_keyfile(self.saved_path.read_text(encoding="utf-8"))["wifi-security"]["psk"], NEW_PASSWORD)
        self.assertEqual(self.fake.profiles[SAVED_5G_UUID]["priority"], 5)

    def test_saved_profile_without_password_and_back_to_the_ap(self):
        self.fake.active = None
        self.status()
        status = json.loads(nm.STATUS_FILE.read_text(encoding="utf-8"))
        status.update(can_request_connect=True)
        nm.atomic_json(nm.STATUS_FILE, status)
        result = nm.wifi_connect_request({"uuid": SAVED_5G_UUID, "autoconnect": True, "priority": 100})
        self.assertEqual(result["kind"], "saved")
        with mock.patch.object(nm, "interface_snapshot", return_value={"state": "100", "connection": nm.AP_CONNECTION,
                                                                     "connected": True, "ap_active": True, "wifi_connected": False}), \
             mock.patch.object(self.controller, "stop_ap_services") as stop_ap:
            self.controller.start_trial(self.controller.read_request())
            stop_ap.assert_called_once()
        self.assertEqual(self.controller.trial["previous"], {"kind": "ap"})
        with mock.patch.object(self.controller, "activate_ap") as activate_ap:
            self.clock.value += nm.TRIAL_S + 1
            self.tick()
            activate_ap.assert_called_once_with("wifi_trial_reverted")
        self.assertIn(SAVED_5G_UUID, self.fake.profiles)        # a saved profile is never deleted

    def test_restart_during_a_trial_reverts(self):
        self.start_new_network_trial()
        candidate = self.candidate_uuid()
        self.tick()
        self.assertTrue(nm.TRIAL_FILE.exists())
        restarted = nm.Controller()
        restarted.resume_unfinished_trial()
        self.assertEqual(restarted.last_trial["reason"], "controller_restarted")
        self.assertNotIn(candidate, self.fake.profiles)
        self.assertEqual(self.fake.active, [PREVIOUS_UUID, "activated"])
        self.assertFalse(nm.TRIAL_FILE.exists())

    def test_requests_that_are_refused(self):
        self.scan()
        cases = [
            ({"ssid": "Field-5G", "password": PASSWORD}, "ssid_already_saved"),
            ({"ssid": "Not Here", "password": PASSWORD}, "ssid_not_in_scan"),
            ({"ssid": "Office", "password": PASSWORD}, "security_not_supported"),
            ({"ssid": "Cafe Open", "password": PASSWORD}, "open_network_takes_no_password"),
            ({"ssid": "New Net"}, "password_required"),
            ({"ssid": "New Net", "password": "short"}, "password_length"),
            ({"uuid": PREVIOUS_UUID}, "already_connected"),
            ({"uuid": OPEN_UUID, "password": PASSWORD}, "password_not_editable"),
            ({"ssid": "New Net", "password": PASSWORD, "priority": 5000}, "priority_range"),
        ]
        for request, code in cases:
            with self.subTest(code=code):
                result = nm.run_wifi_command("wifi-connect", request)
                self.assertEqual(result, {"ok": False, "state": "invalid_request", "error": code})
        self.assertFalse(nm.COMMAND_FILE.exists())
        self.assertEqual(list(nm.NM_RUNTIME_DIR.iterdir()), [])
        self.assert_no_password_anywhere(PASSWORD)

    def test_old_scan_is_not_enough(self):
        self.scan()
        scan = json.loads(nm.SCAN_FILE.read_text(encoding="utf-8"))
        scan["scanned_at"] = time.time() - nm.SCAN_MAX_AGE_FOR_CONNECT_S - 5
        nm.atomic_json(nm.SCAN_FILE, scan)
        self.assertEqual(nm.run_wifi_command("wifi-connect", {"ssid": "New Net", "password": PASSWORD})["error"], "scan_too_old")

    def test_open_network_needs_no_password(self):
        self.scan()
        result = nm.wifi_connect_request({"ssid": "Cafe Open", "autoconnect": False, "priority": 0})
        self.assertTrue(result["ok"])
        candidate = next(u for u, p in self.fake.profiles.items() if p["ssid"] == "Cafe Open")
        self.assertEqual(self.fake.profiles[candidate]["key_mgmt"], "none")

    def test_one_request_at_a_time_and_ethernet_lock(self):
        self.start_new_network_trial()
        self.assertEqual(nm.queue_request("ap")["state"], "action_not_available")
        self.assertEqual(nm.wifi_connect_request({"uuid": SAVED_5G_UUID})["state"], "action_not_available")
        self.assertEqual(nm.wifi_profile_request({"uuid": SAVED_5G_UUID, "autoconnect": True, "priority": 1})["state"], "busy")
        nm.atomic_json(nm.COMMAND_FILE, {"action": "confirm", "txn": "0" * 16, "requested_at": time.time()})
        status = json.loads(nm.STATUS_FILE.read_text(encoding="utf-8"))
        status.update(can_request_ap=True, can_request_connect=True, trial=None, transition=None)
        nm.atomic_json(nm.STATUS_FILE, status)
        self.assertEqual(nm.queue_request("ap")["state"], "busy")
        self.assertEqual(nm.wifi_connect_request({"uuid": SAVED_5G_UUID})["state"], "busy")
        nm.COMMAND_FILE.unlink()
        status.update(ethernet_connected=True)
        nm.atomic_json(nm.STATUS_FILE, status)
        self.assertEqual(nm.wifi_connect_request({"uuid": SAVED_5G_UUID})["state"], "ethernet_ap_locked")

    def test_expired_connect_command_is_undone(self):
        self.scan()
        nm.wifi_connect_request({"ssid": "New Net", "password": PASSWORD, "autoconnect": True, "priority": 0})
        command = json.loads(nm.COMMAND_FILE.read_text(encoding="utf-8"))
        command["requested_at"] = time.time() - nm.COMMAND_MAX_AGE_S - 1
        nm.atomic_json(nm.COMMAND_FILE, command)
        self.assertEqual(self.controller.read_request(), {})
        self.assertEqual(self.controller.last_trial["result"], "rejected")
        self.assertFalse(any(p["ssid"] == "New Net" for p in self.fake.profiles.values()))

    def test_undo_refuses_paths_it_did_not_create(self):
        with self.assertRaises(nm.CommandError):
            nm.undo_prepared({"kind": "new", "uuid": SAVED_5G_UUID, "keyfile": str(self.saved_path)})
        self.assertIn(SAVED_5G_UUID, self.fake.profiles)
        with self.assertRaises(nm.CommandError):
            nm.undo_prepared({"kind": "saved_password", "uuid": SAVED_5G_UUID, "keyfile": str(self.saved_path),
                              "backup": str(Path(self.tmp.name) / "elsewhere.nmconnection")})

    def test_status_shows_link_and_permissions(self):
        status = self.status()
        self.assertTrue(status["can_scan"])
        self.assertTrue(status["can_request_connect"])
        self.assertEqual(status["link"]["signal_dbm"], -56)
        self.assertEqual(status["trial_seconds"], nm.TRIAL_S)
        self.assertIsNone(status["trial"])


if __name__ == "__main__":
    unittest.main()
