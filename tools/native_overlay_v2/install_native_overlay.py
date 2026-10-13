#!/usr/bin/env python3
"""Detached native observation overlay install (file overlay on the running image) with rollback.

OVERLAY.json lists {"files": {"<release relative source>": "<absolute target in image>"}, "tag": ...,
"label": ..., "tests": ["module", ...], "parent": "<sha256 image id>"}.
Steps (Jetson host, docker group):
 1 preflight  exact parent image, service running, observation owner idle (never started here)
 2 backup     container inspect + every target file copied out of the running container
 3 build      FROM the running image, COPY only the listed files; verify each SHA inside the image
 4 ARM tests  listed unittest modules inside the new image (map sources loaded from the image)
 5 replace    retain the old container under a new name (restart=no), create the new one from the
              exact old Config/HostConfig with only Image changed, wait idle, compare runtime config
 6 restart    one normal restart must come back idle without stale-socket recovery
On any failure after step 5 started: stop/rename the new container, restore the retained one.
--dry-run stops after step 4 (image built and tested; the running service is untouched).
Never starts observation, mapping, recording or touches motors.
"""
import hashlib
import fcntl
import os
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

REL = Path(__file__).resolve().parent
NAME = "ubts-native-sensor-observation"
HOST_SOCKET = Path("/home/unist/ubts-native-stream-socket/observe.sock")
# Single owner and no implicit retry of a prior install attempt.
LOCK_FD = os.open(str(REL / '.install.lock'), os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('installer_already_owned')
if '--dry-run' not in sys.argv and (REL / 'INSTALL_RESULT.json').exists():
    raise SystemExit('install_attempt_already_recorded_create_new_reviewed_release')
SPEC = json.loads((REL / "OVERLAY.json").read_text())
PARENT, TAG, LABEL = SPEC["parent"], SPEC["tag"], SPEC["label"]
DRY_RUN = "--dry-run" in sys.argv
RESULT_NAME = "DRY_RUN_RESULT.json" if DRY_RUN else "INSTALL_RESULT.json"
result = dict(schema="ubts.native_overlay_install.v1", label=LABEL, dry_run=DRY_RUN, started_at=time.time(), steps=[], ok=False)


def log(step, **fields):
    result["steps"].append(dict(step=step, at=time.time(), **fields))
    (REL / RESULT_NAME).write_text(json.dumps(result, indent=1))


def run(*args, timeout=1800, check=True):
    p = subprocess.run(list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = p.stdout.decode(errors="replace")
    with (REL / "install.log").open("a") as stream:
        stream.write(f"$ {' '.join(args)}\n{out}\n[rc={p.returncode}]\n")
    if check and p.returncode:
        raise RuntimeError(f"{args[:3]} rc={p.returncode}: {out[-400:]}")
    return out


def inspect(name):
    return json.loads(run("docker", "inspect", name))[0]


class DockerAPI(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=60)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def api(method, path, body=None):
    conn = DockerAPI()
    conn.request(method, path, body=None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    if response.status >= 300:
        raise RuntimeError(f"docker_api {method} {path} {response.status}: {data[:400]!r}")
    return json.loads(data) if data else {}


def native_status():
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(3)
    try:
        client.connect(str(HOST_SOCKET))
        client.sendall(b'{"op":"status"}\n')
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
        reply = json.loads(data)
        owner = (reply.get("status") or {}).get("owner") or {}
        return dict(ok=reply.get("ok"), state=reply.get("state"), owned=owner.get("owned"),
                    runtime_authority=reply.get("runtime_authority"), motor_command_sent=reply.get("motor_command_sent"))
    finally:
        client.close()


def map_status():
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(3)
    try:
        client.connect(str(HOST_SOCKET))
        client.sendall(b'{"op":"map_status"}\n')
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
        reply = json.loads(data)
        return {k: reply.get(k) for k in ("ok", "available", "reason", "runtime_authority")}
    finally:
        client.close()


def wait_idle(name, timeout=120):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            c = inspect(name)
            if c["State"]["Running"]:
                s = native_status()
                last = s
                if s["ok"] is True and s["state"] == "idle" and s["owned"] is False:
                    return c, s
        except Exception as error:
            last = repr(error)[:200]
        time.sleep(2)
    raise RuntimeError(f"native_not_idle: {last}")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def restore_original(old, *, new_id=None, replacement_image=None):
    """Restore the exact original ID/config, including pre-rename failures.

    Only this release's proven replacement may be stopped/renamed. Its inspect
    evidence and container are retained. An unknown name occupant is an error,
    never permission to remove another operator's container.
    """
    original_id = old["Id"]
    original_name = old["Name"].lstrip("/")
    original = inspect(original_id)
    if original["Image"] != old["Image"] or original["Config"] != old["Config"]:
        raise RuntimeError("original_container_identity_or_config_changed")
    occupant = None
    try:
        occupant = inspect(original_name)
    except Exception:
        # The Docker API listing proves absence rather than treating any failed
        # inspect (daemon timeout/permission) as an available container name.
        listed = api("GET", "/containers/json?all=1")
        if any("/" + original_name in value.get("Names", ()) for value in listed):
            raise RuntimeError("original_name_lookup_failed")
    replacement = inspect(new_id) if new_id is not None else None
    if replacement is None and occupant is not None and occupant["Id"] != original_id:
        replacement = occupant  # create may have succeeded before its reply was lost
    if replacement is not None:
        labels = replacement["Config"].get("Labels") or {}
        if (replacement["Id"] == original_id or replacement["Image"] != replacement_image or
            labels.get("ubts.release") != LABEL or labels.get("ubts.replaces") != original_id):
            raise RuntimeError("unowned_replacement_container_during_rollback")
        proof = REL / ("rollback-replacement-" + replacement["Id"][:12] + ".inspect.private.json")
        proof.write_text(json.dumps(replacement, indent=1))
        log("rollback_replacement_evidence", container=replacement["Id"], file=proof.name)
        run("docker", "update", "--restart=no", replacement["Id"])
        if replacement["State"]["Running"]:
            run("docker", "stop", "-t", "20", replacement["Id"])
        stopped = inspect(replacement["Id"])
        if stopped["State"]["Running"]:
            raise RuntimeError("replacement_not_stopped_before_original_restore")
        if stopped["Name"].lstrip("/") == original_name:
            failed_name = f"{original_name}-failed-{LABEL}-{time.time_ns()}"
            run("docker", "rename", replacement["Id"], failed_name)
        log("rollback_replacement_retained", container=replacement["Id"],
            name=inspect(replacement["Id"])["Name"])
    elif occupant is not None and occupant["Id"] != original_id:
        raise RuntimeError("original_name_occupied_during_rollback")
    current = inspect(original_id)
    if current["Name"].lstrip("/") != original_name:
        run("docker", "rename", original_id, original_name)
    # Preserve MaximumRetryCount as well as Name; do not substitute unless-stopped.
    api("POST", f"/containers/{original_id}/update", {"RestartPolicy": old["HostConfig"]["RestartPolicy"]})
    current = inspect(original_id)
    if old["State"]["Running"] and not current["State"]["Running"]:
        run("docker", "start", original_id)
    elif not old["State"]["Running"] and current["State"]["Running"]:
        run("docker", "stop", "-t", "20", original_id)
    restored = inspect(original_id)
    if (restored["Image"] != old["Image"] or restored["Config"] != old["Config"] or
        restored["HostConfig"] != old["HostConfig"] or
        restored["State"]["Running"] != old["State"]["Running"] or
        restored["Name"].lstrip("/") != original_name):
        raise RuntimeError("original_runtime_not_exactly_restored")
    return dict(original_id=original_id, name=original_name, running=restored["State"]["Running"],
                restart_policy=restored["HostConfig"]["RestartPolicy"], config_unchanged=True)


retained_name = None
new_id = None
image = None
old = None
mutation_started = False
try:
    # 1 preflight
    old = inspect(NAME)
    s = native_status()
    if old["Image"] != PARENT or not old["State"]["Running"] or s["owned"] is not False or s["state"] not in ("idle", "stopped"):  # stopped = explicit Stop, no owner
        log("preflight", ok=False, image=old["Image"], status=s)
        raise SystemExit(2)
    log("preflight", ok=True, container=old["Id"], status=s, restart_count=old["RestartCount"])

    # 2 backup of every target as it runs now
    backup = REL / time.strftime("backup-%Y%m%dT%H%M%S")
    backup.mkdir()
    (backup / "native_inspect.json").write_text(json.dumps(old, indent=1))
    saved = {}
    for source, target in SPEC["files"].items():
        dest = backup / Path(target).name
        run("docker", "cp", f"{NAME}:{target}", str(dest))
        saved[target] = sha(dest)
    log("backup", ok=True, dir=backup.name, files=saved)

    # 3 build + exact bytes inside the image
    lines = [f"FROM {PARENT}"] + [f"COPY {source} {target}" for source, target in SPEC["files"].items()]
    # Generated bindings (e.g. BUILD_PROVENANCE.json) and the image's own import self-checks.
    lines += [f"RUN {command}" for command in SPEC.get("run", [])]
    (REL / "Release.Dockerfile").write_text("\n".join(lines) + "\n")
    run("docker", "build", "-f", str(REL / "Release.Dockerfile"), "-t", TAG, str(REL))
    image = json.loads(run("docker", "image", "inspect", TAG))[0]["Id"]
    want = {target: sha(REL / source) for source, target in SPEC["files"].items()}
    got_text = run("docker", "run", "--rm", "--network", "none", "--entrypoint", "sha256sum", image, *sorted(want))
    got = {line.split()[1]: line.split()[0] for line in got_text.splitlines() if line.strip()}
    if got != want:
        raise RuntimeError(f"image_file_mismatch {got} != {want}")
    generated = {}
    for target in SPEC.get("generated", []):
        generated[target] = run("docker", "run", "--rm", "--network", "none", "--entrypoint", "sha256sum", image, target).split()[0]

    # 4 ARM tests from the image's own sources (copied so nothing in /opt is written)
    if SPEC.get("tests"):
        # tests/run_arm_tests.sh runs one interpreter per module (shared module names) on a copy of /opt.
        tests = run("docker", "run", "--rm", "--network", "none", "--cpus", "2", "--entrypoint", "bash",
                    "-v", f"{REL / 'tests'}:/rt:ro", image, "/rt/run_arm_tests.sh", *SPEC["tests"], timeout=3600)
        if "ALL_TESTS_OK" not in tests:
            raise RuntimeError("arm_tests_failed: " + tests[-1200:])
    else:
        tests = "no tests listed"
    loader = run("docker", "run", "--rm", "--network", "none", "--entrypoint", "bash", image, "-c",
                 "cd /opt/ubts-native-stream && python3 -B -c \"import sys,hashlib; sys.path[:0]=['/opt/ubts-native-stream','/opt/ubts-shadow/package']; "
                 "import stream_runner; s=hashlib.sha256(open('NATIVE_MAP_SOURCE.json','rb').read()).hexdigest(); "
                 "print('map_sources_admitted', stream_runner.load_native_map_sources('/opt/ubts-native-stream', s), s)\"")
    if "map_sources_admitted True" not in loader:
        raise RuntimeError("map_source_admission_failed: " + loader[-400:])
    log("image_check", ok=True, image=image, files=got, generated=generated, arm_tests=tests.strip().splitlines()[-30:],
        loader=loader.strip().splitlines()[-1])
    if DRY_RUN:
        result["ok"] = True
        log("dry_run_stop", ok=True, note="image built and checked; running service untouched")
        raise SystemExit(0)

    # Revalidate the parent and idle owner after the potentially long build/tests.
    fresh = inspect(NAME)
    latest = native_status()
    if (fresh['Id'] != old['Id'] or fresh['Image'] != PARENT or
        not fresh['State']['Running'] or fresh['Config'] != old['Config'] or
        fresh['HostConfig'] != old['HostConfig'] or latest['owned'] is not False or
        latest['state'] not in ('idle', 'stopped') or latest['runtime_authority'] is not False or
        latest['motor_command_sent'] is not False):
        log('pre_replace_recheck', ok=False, status=latest)
        raise RuntimeError('native_parent_or_owner_changed_during_qualification')
    log('pre_replace_recheck', ok=True, status=latest)
    # 5 replace: retain old under a new name, create the new one from exact config
    stamp = time.strftime("%Y%m%dT%H%M%S")
    planned_retained_name = f"{NAME}-retained-{LABEL}-{stamp}"
    # Start the rollback fence before the first mutation, not after rename.
    mutation_started = True
    log("mutation_begin", original_id=old["Id"], original_name=old["Name"],
        original_running=old["State"]["Running"], original_restart_policy=old["HostConfig"]["RestartPolicy"])
    run("docker", "update", "--restart=no", NAME)
    run("docker", "stop", "-t", "20", NAME)
    run("docker", "rename", NAME, planned_retained_name)
    retained_name = planned_retained_name
    config = dict(old["Config"])
    config["Image"] = image
    config["Labels"] = dict(config.get("Labels") or {}, **{"ubts.release": LABEL, "ubts.replaces": old["Id"]})
    if old["HostConfig"].get("NetworkMode") == "host":
        config.pop("Hostname", None)
        config.pop("Domainname", None)
    body = dict(config, HostConfig=old["HostConfig"])
    created = api("POST", f"/containers/create?name={NAME}", body)
    new_id = created["Id"]
    api("POST", f"/containers/{new_id}/start")
    new, s = wait_idle(NAME)
    same = all(new["HostConfig"].get(k) == old["HostConfig"].get(k) for k in
               ("Binds", "Mounts", "NetworkMode", "IpcMode", "RestartPolicy", "Memory", "NanoCpus",
                "ReadonlyRootfs", "SecurityOpt", "CapAdd", "CapDrop", "Privileged"))
    same &= new["Config"]["Cmd"] == old["Config"]["Cmd"] and sorted(new["Config"]["Env"]) == sorted(old["Config"]["Env"])
    if not same:
        raise RuntimeError("runtime_config_changed")
    log("replace", ok=True, container=new_id, retained=retained_name, status=s, map=map_status())

    # 6 normal restart must return idle without socket recovery
    t0 = time.time() - 1
    run("docker", "restart", "-t", "20", NAME)
    _, s = wait_idle(NAME)
    text = run("docker", "logs", "--since", str(int(t0)), NAME, check=False)
    if "recovered_stale_socket" in text:
        raise RuntimeError("unexpected_stale_socket_recovery_on_normal_restart")
    final = inspect(NAME)
    log("final", ok=True, restart_policy=final["HostConfig"]["RestartPolicy"], running=final["State"]["Running"],
        image=final["Image"], status=native_status(), map=map_status())
    result["ok"] = True
    original_policy = old["HostConfig"]["RestartPolicy"]
    restart_arg = original_policy.get("Name") or "no"
    if restart_arg == "on-failure" and original_policy.get("MaximumRetryCount", 0):
        restart_arg += ":" + str(original_policy["MaximumRetryCount"])
    result["rollback"] = dict(retained=retained_name, image=PARENT,
                              original_id=old["Id"], original_running=old["State"]["Running"],
                              original_restart_policy=original_policy,
                              steps=[f"docker stop {NAME}", f"docker rename {NAME} {NAME}-failed-<ts>",
                                     f"docker rename {retained_name} {NAME}",
                                     f"docker update --restart={restart_arg} {NAME}"] +
                                     ([f"docker start {NAME}"] if old["State"]["Running"] else []))
except SystemExit:
    raise
except Exception as error:
    log("error", ok=False, error=repr(error)[:800])
    if mutation_started and old is not None:
        try:
            restored = restore_original(old, new_id=new_id, replacement_image=image)
            log("rollback", ok=True, restored=restored)
        except Exception as rollback_error:
            log("rollback", ok=False, error=repr(rollback_error)[:600])
    raise SystemExit(1)
finally:
    result["finished_at"] = time.time()
    (REL / RESULT_NAME).write_text(json.dumps(result, indent=1))
