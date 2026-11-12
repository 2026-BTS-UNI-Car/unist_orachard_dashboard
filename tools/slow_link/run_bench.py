#!/usr/bin/env python3
"""Run one named scenario of the slow-link bench and write the result into a NEW folder.

  python run_bench.py --scenario drive --profile bad --source HEAD --inputs <inputs.json> --out-root <folder>
  python run_bench.py --scenario quick --profile all --source HEAD --inputs <inputs.json> --out-root <folder>

The source under test is an export of one commit (git archive) in a scratch folder. Every process binds to
127.0.0.1. An earlier result is never deleted or overwritten: each run creates its own folder and stops if the
name already exists. See README_KO.md.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

import analyze  # noqa: E402
import bench_inputs  # noqa: E402

REPO = TOOL_DIR.parents[1]
DRIVE_CYCLE = dict(hold_s=5, idle_s=1, after_stop_s=2, arm_timeout_s=8)
SCENARIOS = {
    # first open + open again + 2 minutes on the driving screen with the manual-drive exercise
    "drive": dict(cold_load=True, warm_load=True, live_s=120, sample_every_s=5, drive=DRIVE_CYCLE),
    # the same with the live map drawing, for 10 minutes: does anything grow with time?
    "drive_map": dict(cold_load=True, warm_load=False, live_s=600, sample_every_s=10, drive=DRIVE_CYCLE, start_live_map=True, map_publish_mark=True),
    # first open and open again only
    "load": dict(cold_load=True, warm_load=True, live_s=0),
    # one minute visible, then the tab is hidden for one minute
    "hidden": dict(cold_load=True, warm_load=False, live_s=120, sample_every_s=5, hidden_after_s=60),
    # the dashboard session expires after 50 s: what happens to the streams?
    "session_expiry": dict(cold_load=True, warm_load=False, live_s=150, sample_every_s=5, env={"DASHBOARD_SESSION_TTL_S": "50"}),
    # short version of "drive" for a check after a change (first open is not timed apart from the page becoming usable)
    "quick": dict(cold_load=True, warm_load=False, live_s=40, sample_every_s=5, drive=DRIVE_CYCLE, load_timeout_s=300),
}


def load_profiles():
    return json.loads((TOOL_DIR / "profiles.json").read_text(encoding="utf-8"))["profiles"]


def write_json(path, value):
    Path(path).write_bytes((json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def new_result_folder(root, name):
    """A new folder under `root`; never an existing one."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 100):
        folder = root / (name if attempt == 1 else f"{name}_{attempt}")
        try:
            folder.mkdir(exist_ok=False)
            return folder
        except FileExistsError:
            continue
    raise FileExistsError(f"could not create a new result folder for {name}")


def refuse_unsafe_folder(path, what):
    resolved = Path(path).resolve()
    if resolved == REPO.resolve() or REPO.resolve() in resolved.parents:
        raise SystemExit(f"{what} must be outside the repository work tree: {resolved}")
    if any(part.lower() == "_archive" for part in resolved.parts):
        raise SystemExit(f"{what} must not be inside an archive folder: {resolved}")
    return resolved


def read_inputs(args):
    inputs = {}
    if args.inputs:
        inputs.update(json.loads(Path(args.inputs).read_text(encoding="utf-8")))
    for key in ("lidar_npz", "drive_json", "camera_dir"):
        if getattr(args, key):
            inputs[key] = getattr(args, key)
    missing = [key for key in ("lidar_npz", "drive_json", "camera_dir") if not inputs.get(key)]
    if missing:
        raise SystemExit(f"missing input path(s): {missing}; give --inputs <json> or --lidar-npz/--drive-json/--camera-dir")
    for key in ("lidar_npz", "drive_json", "camera_dir"):
        if not Path(inputs[key]).exists():
            raise SystemExit(f"input not found: {key} = {inputs[key]}")
    return inputs


def tool_version():
    try:
        head = bench_inputs.git(REPO, "rev-parse", "HEAD")
        dirty = bool(bench_inputs.git(REPO, "status", "--porcelain", "--", "tools/slow_link"))
        return dict(commit=head, tool_folder_modified=dirty)
    except (OSError, subprocess.CalledProcessError):
        return dict(commit=None, tool_folder_modified=None)


def leftover_browsers(kill=False):
    """Bench browsers still alive (matched by the bench's own profile-folder name)."""
    import psutil
    import stack
    found = []
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            if (process.info["name"] or "").lower().startswith("chrome") and stack.CHROME_PROFILE_PREFIX in " ".join(process.info["cmdline"] or []):
                found.append(process.pid)
                if kill:
                    process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


def run_once(args, scenario_name, profile_name, source, inputs, out_root, work):
    import stack
    scenario = dict(SCENARIOS[scenario_name])
    if args.live_s is not None:
        scenario["live_s"] = args.live_s
    if args.no_drive:
        scenario["drive"] = None
    profile = None if profile_name == "none" else dict(load_profiles()[profile_name])
    if profile is not None and args.queue_bytes is not None:
        profile["queue_bytes"] = args.queue_bytes
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = f"_{args.label}" if args.label else ""
    out = new_result_folder(out_root, f"{stamp}_{scenario_name}_{profile_name}_{source['short']}{label}")
    scratch = Path(tempfile.mkdtemp(prefix="scratch_", dir=work))
    run = dict(schema="ubts.slow_link.run", version=1, started_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               scenario=scenario_name, scenario_settings=scenario, profile=profile_name, profile_settings=profile, source=source,
               tool=tool_version(), label=args.label, clients=args.clients,
               host=dict(platform=platform.platform(), machine=platform.machine(), cpus=os.cpu_count(), python=platform.python_version()),
               inputs={key: str(value) for key, value in inputs.items()}, result_folder=str(out), chrome_args=list(args.chrome_arg),
               limits="x86 PC, TCP-level link emulation, replayed sensor data; see README_KO.md")
    write_json(out / "run.json", run)
    started = time.time()
    bench = None
    try:
        app = bench_inputs.export_source(REPO if args.repo is None else args.repo, source["commit"], scratch / "app")
        bench_inputs.make_map_stub(scratch / "maps", app)
        kernel, how = bench_inputs.ensure_raster_kernel(work, app, args.python)
        if kernel:
            shutil.copy2(kernel, app / "dashboard_server" / "_native_raster.so")
        run["raster_kernel"] = how
        stack_inputs = dict(inputs)
        if not args.no_camera_original:
            original_dir, original_info = bench_inputs.ensure_original_frames(work, inputs["camera_dir"])
            stack_inputs["camera_original_dir"] = str(original_dir)
            run["camera_original"] = original_info
        ports = stack.shifted_ports(args.port_offset)
        bench = stack.Stack(python=args.python, app=app, scratch=scratch, out=out, inputs=stack_inputs, profile=profile, clients=args.clients,
                            ports=ports, map_publish_mark=bool(scenario.get("map_publish_mark")), extra_env=scenario.get("env"))
        bench.start()
        run["ports"] = ports
        run["listeners"] = {str(port): name for port, name in bench.listeners.items()}
        phases = {key: value for key, value in scenario.items() if key not in ("env", "map_publish_mark", "hidden_after_s")}
        clients = [dict(origin=origin, width=args.width, height=args.height, mobile=args.mobile) for origin in bench.client_origins()]
        if scenario.get("hidden_after_s") is not None:
            clients[0]["hidden_after_s"] = scenario["hidden_after_s"]
        if args.network_profile:
            for client in clients:
                client["network_profile"] = args.network_profile
        cfg = dict(label=out.name, out=str(out), route="/rover-control", clock_url=f"http://127.0.0.1:{ports['recorder']}/clock",
                   screenshot=True, clients=clients, phases=phases, chrome_args=list(args.chrome_arg))
        write_json(out / "cfg.json", cfg)
        write_json(out / "run.json", run)
        budget = 120 + scenario.get("live_s", 0) + (scenario.get("load_timeout_s", 600) + 10) * (bool(scenario.get("cold_load")) + bool(scenario.get("warm_load")))
        completed = subprocess.run([args.node, str(TOOL_DIR / "browser_runner.mjs"), str(out / "cfg.json")], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=budget)
        (out / "logs" / "browser_runner.stdout.txt").write_bytes(completed.stdout.encode("utf-8"))
        (out / "logs" / "browser_runner.stderr.txt").write_bytes(completed.stderr.encode("utf-8"))
        if completed.returncode != 0 or not (out / "RAW.json").is_file():
            raise RuntimeError(f"browser runner failed (exit {completed.returncode}); partial files are kept in {out}")
    except BaseException as error:
        run["failed"] = f"{type(error).__name__}: {error}"
        write_json(out / "FAILED.json", dict(error=run["failed"], result_folder=str(out)))
        if isinstance(error, KeyboardInterrupt):
            raise
    finally:
        left = bench.stop() if bench is not None else []
        browsers = leftover_browsers(kill=True)
        run["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run["wall_s"] = round(time.time() - started, 1)
        run["cleanup"] = dict(ports_still_open=left, bench_browsers_killed=browsers)
        write_json(out / "run.json", run)
        if not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    summary = analyze.analyze(out)
    write_json(out / "SUMMARY.json", summary)
    return out, run, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    parser.add_argument("--profile", required=True, help="good, normal, bad, very_bad, none (no emulator) or all (the four profiles in turn)")
    parser.add_argument("--source", default="HEAD", help="commit, tag or branch to measure (exported with git archive)")
    parser.add_argument("--out-root", required=True, help="folder that receives one new sub-folder per run")
    parser.add_argument("--work", help="scratch folder (exports, generated inputs); default <out-root>/_work")
    parser.add_argument("--inputs", help="JSON file with lidar_npz, drive_json, camera_dir")
    parser.add_argument("--lidar-npz")
    parser.add_argument("--drive-json")
    parser.add_argument("--camera-dir")
    parser.add_argument("--no-camera-original", action="store_true", help="do not feed the 1920x1080 original-preview topic")
    parser.add_argument("--repo", help="repository to export from (default: the one this tool is in)")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the stack (needs numpy, websockets, psutil, opencv)")
    parser.add_argument("--node", default="node")
    parser.add_argument("--label", help="short text added to the result folder name")
    parser.add_argument("--repeat", type=int, default=1, help="run the same condition this many times (each in its own folder)")
    parser.add_argument("--live-s", type=float, help="override the live time of the scenario")
    parser.add_argument("--no-drive", action="store_true", help="leave out the manual-drive exercise")
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--queue-bytes", type=int, help="override the bottleneck queue size of the profile")
    parser.add_argument("--network-profile", choices=["auto", "tiny", "slow", "balanced", "detail"], help="the page's transfer-quality choice (default: the page's own default)")
    parser.add_argument("--width", type=int, default=1366)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--mobile", action="store_true")
    parser.add_argument("--port-offset", type=int, default=0)
    parser.add_argument("--chrome-arg", action="append", default=[], help="extra Chrome command-line flag (repeatable), recorded in run.json")
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)
    profiles = load_profiles()
    names = list(profiles) if args.profile == "all" else [args.profile]
    for name in names:
        if name != "none" and name not in profiles:
            raise SystemExit(f"unknown profile {name}; choose from {sorted(profiles)}, none, all")
    if not args.label or all(ch.isalnum() or ch in "-_" for ch in args.label):
        pass
    else:
        raise SystemExit("--label may only hold letters, digits, - and _")
    out_root = refuse_unsafe_folder(args.out_root, "--out-root")
    work = refuse_unsafe_folder(args.work or out_root / "_work", "--work")
    work.mkdir(parents=True, exist_ok=True)
    inputs = read_inputs(args)
    source = bench_inputs.describe_source(args.repo or REPO, args.source)
    results, failed = [], 0
    for name in names:
        for _ in range(args.repeat):
            out, run, summary = run_once(args, args.scenario, name, source, inputs, out_root, work)
            results.append(out)
            state = "FAILED: " + run["failed"] if run.get("failed") else "ok"
            if run.get("failed") or summary.get("error"):
                failed += 1
            print(f"{out.name}: {state} ({run['wall_s']} s)" + (f" · browser: {summary['error'][:200]}" if summary.get("error") else ""), flush=True)
            if run["cleanup"]["ports_still_open"]:
                raise SystemExit(f"ports still open after the run: {run['cleanup']['ports_still_open']}")
    import summarize
    print()
    print(summarize.render_text(summarize.collect(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
