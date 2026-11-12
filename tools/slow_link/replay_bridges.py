#!/usr/bin/env python3
"""Replay recorded sensors through the dashboard's real WebSocket bridge code.

Only the ROS subscription of each bridge is replaced. Recorded data is turned
into ROS-message-shaped objects and handed to the same state objects the
bridge nodes use, and the unmodified serve functions of
scripts/{lidar,camera,imu,gps}_ws_bridge.py of the source under test send
them. Message format, latest-only / acknowledgement flow and stream budgets are
therefore those of that source.

Header stamps are rewritten to the publish wall time so the page can measure
"published by the bridge -> drawn" against one clock.

GNSS: only the recorded east / north offsets in metres are read. They are added
to a synthetic origin; no recorded coordinate is read, written or logged.

  replay_bridges.py --bridge lidar|camera|imugps --app <exported source> --out <result folder> ...
"""
import argparse
import asyncio
import gzip
import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

# Synthetic origin: round numbers, not a field position.
SYNTHETIC_LAT, SYNTHETIC_LON = 36.0, 128.0
CAMERA_PREVIEW_FORMAT = "bgr8; jpeg compressed bgr8; width=1280; height=720"


def stamp_ns(t):
    ns = int(t * 1e9)
    return NS(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)


def write_json(path, payload):
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_bytes(json.dumps(payload).encode("utf-8"))
    tmp.replace(path)


class Stats:
    def __init__(self, path, name):
        self.path, self.name = Path(path), name
        self.counts = {}
        self.started = time.time()
        self.late = []
        self.lock = threading.Lock()

    def tick(self, key, lateness=None):
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1
            if lateness is not None:
                self.late.append(lateness)
                if len(self.late) > 5000:
                    self.late = self.late[-2500:]

    def dump(self, extra=None):
        with self.lock:
            late = np.array(self.late) if self.late else np.zeros(1)
            payload = dict(bridge=self.name, pid=os.getpid(), started=self.started, now=time.time(), counts=dict(self.counts),
                           publish_lateness_ms=dict(p50=round(float(np.percentile(late, 50)) * 1000, 2),
                                                    p95=round(float(np.percentile(late, 95)) * 1000, 2),
                                                    max=round(float(late.max()) * 1000, 2)), **(extra or {}))
        write_json(self.path, payload)


class ReplayClock:
    """Recording time shared by all bridge processes: wall time minus one common origin."""

    def __init__(self, epoch):
        self.epoch = float(epoch)

    def now(self):
        return time.time() - self.epoch


def scheduled(times, duration, clock, stop, stats, key, fn):
    """Publish item k when the shared replay clock passes times[k] (recorded spacing, looping)."""
    loop = 0
    k = 0
    while not stop.is_set():
        if k >= len(times):
            k, loop = 0, loop + 1
        due = loop * duration + times[k]
        wait = due - clock.now()
        if wait > 0:
            time.sleep(min(wait, 0.5))
            continue
        if -wait > 0.25:  # more than 250 ms behind: skip ahead, never burst
            stats.tick(key + "_skipped")
            k += 1
            continue
        fn(k, time.time())
        stats.tick(key, -wait)
        k += 1


def paced(rate, stop, stats, key, fn):
    """Call fn(index, wall_time) at a fixed rate on an absolute schedule (no catch-up bursts)."""
    period = 1.0 / rate
    start = time.monotonic()
    index = 0
    while not stop.is_set():
        due = start + index * period
        now = time.monotonic()
        if due > now:
            time.sleep(due - now)
            now = time.monotonic()
        elif now - due > period:  # missed whole periods: skip, never burst
            skipped = int((now - due) / period)
            index += skipped
            stats.tick(key + "_skipped")
            due = start + index * period
        fn(index, time.time())
        stats.tick(key, now - due)
        index += 1


# ---------------------------------------------------------------- LiDAR
def run_lidar(args, stop):
    from scripts import lidar_ws_bridge as LB
    data = np.load(args.lidar_npz)
    ranges_mm = data["ranges_mm"]
    scan_t = (data["header_ns"] - data["header_ns"][0]) / 1e9
    meta = dict(angle_min=float(data["angle_min"]), angle_increment=float(data["angle_increment"]),
                range_min=float(data["range_min"]), range_max=float(data["range_max"]),
                scan_time=float(data["scan_time"]), time_increment=float(data["time_increment"]))
    state = LB.ScanState()
    stats = Stats(Path(args.out) / "bridge_lidar.json", "lidar")
    status_path = Path(args.status_dir) / "lidar_bridge_status.json"
    convert_ms = []

    def publish(index, wall):
        row = ranges_mm[index].astype(np.float32) / 1000.0
        row[row <= 0] = np.inf
        stamp = stamp_ns(wall)
        msg = NS(header=NS(stamp=stamp, frame_id="laser"), ranges=row.tolist(), intensities=[], **meta)
        started = time.perf_counter()
        # Same steps as lidar_ws_bridge.ros_thread.BridgeNode.on_scan.
        points = LB.scan_to_points(msg, args.max_points)
        raw_scan = dict(header=dict(stamp=dict(sec=msg.header.stamp.sec, nanosec=msg.header.stamp.nanosec), frame_id=msg.header.frame_id),
                        ranges=[float(v) if math.isfinite(v) else None for v in msg.ranges],
                        angle_min=float(msg.angle_min), angle_increment=float(msg.angle_increment),
                        range_min=float(msg.range_min), range_max=float(msg.range_max),
                        scan_time=float(msg.scan_time), time_increment=float(msg.time_increment))
        state.update("laser", msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9, points, "ros2", raw_scan)
        convert_ms.append((time.perf_counter() - started) * 1000)
        if len(convert_ms) > 2000:
            del convert_ms[:1000]

    def status_loop():
        while not stop.wait(0.5):
            LB.write_bridge_status(status_path, LB.bridge_status_payload(state))
            stats.dump(dict(beams=int(ranges_mm.shape[1]), record_hz=round(len(scan_t) / max(1e-6, float(scan_t[-1])), 2),
                            on_scan_convert_ms_p50=round(float(np.median(convert_ms)), 2) if convert_ms else None))

    clock = ReplayClock(args.epoch)
    duration = float(scan_t[-1]) + 0.1
    threading.Thread(target=scheduled, args=(list(scan_t), duration, clock, stop, stats, "scan", publish), daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()
    asyncio.run(LB.serve_websocket(state, "127.0.0.1", args.lidar_port, "/ws/lidar", 10.0, args.max_points))


# ---------------------------------------------------------------- Camera
def load_frames(folder):
    frames = [path.read_bytes() for path in sorted(Path(folder).glob("*.jpg"))]
    if not frames:
        raise SystemExit(f"no *.jpg frames in {folder}")
    return frames


def run_camera(args, stop):
    from scripts import camera_ws_bridge as CB
    frames = load_frames(args.camera_dir)
    original, original_format = [], ""
    if args.camera_original_dir:
        original = load_frames(args.camera_original_dir)
        original_format = (Path(args.camera_original_dir) / "FORMAT.txt").read_text(encoding="utf-8").strip()
    state = CB.CameraState()
    stats = Stats(Path(args.out) / "bridge_camera.json", "camera")

    def publish(index, wall):
        stamp = stamp_ns(wall)
        # The compressed preview topic and, when a viewer asked for it, the original JPEG topic.
        state.update_preview("camera_link_optical", stamp.sec, stamp.nanosec, frames[index % len(frames)], CAMERA_PREVIEW_FORMAT)
        if original and state.original.update("camera_link_optical", stamp.sec, stamp.nanosec,
                                              original[index % len(original)], original_format):
            stats.tick("original_frame")
            if state.frame_notify is not None:
                state.frame_notify()

    def status_loop():
        kwargs = dict(image_topic="/camera/image_raw", compressed_topic="/camera/image_jpeg", camera_info_topic="/camera/camera_info")
        path = Path(args.status_dir) / "camera_bridge_status.json"
        while not stop.wait(0.5):
            CB.write_status(path, CB.status_payload(state, **kwargs))
            stats.dump(dict(preview_frames=len(frames), preview_mean_bytes=int(np.mean([len(f) for f in frames])),
                            original_frames=len(original), original_format=original_format,
                            original_mean_bytes=int(np.mean([len(f) for f in original])) if original else None))

    threading.Thread(target=paced, args=(args.camera_fps, stop, stats, "frame", publish), daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()
    asyncio.run(CB.serve_websocket(state, "127.0.0.1", args.camera_port, "/ws/camera", stop))


# ---------------------------------------------------------------- IMU + GPS (+ recorder stub, clock)
def run_imugps(args, stop):
    from scripts import imu_ws_bridge as IB
    from scripts import gps_ws_bridge as GB
    import websockets
    drive = json.loads(gzip.open(args.drive_json).read())
    cols = drive["imu_columns"]
    imu = np.array(drive["imu"], dtype=np.float64)
    scan_cols = drive["scan_columns"]
    scan_header0 = float(drive["scan"][0][scan_cols.index("header_ns")])
    scan_bag0 = float(drive["scan"][0][scan_cols.index("bag_time_ns")])
    t_imu = (imu[:, cols.index("header_ns")] - scan_header0) / 1e9
    # Only the relative metres and the quality fields are taken from the record.
    keep = ("received_at", "e_m", "n_m", "valid", "fix_type", "quality", "h_acc_m", "satellites", "speed_mps", "course_deg")
    gps = [{key: row.get(key) for key in keep} for row in drive["gps"]]
    duration = float((drive["scan"][-1][scan_cols.index("header_ns")] - scan_header0) / 1e9) + 0.1
    del drive
    clock = ReplayClock(args.epoch)
    state = IB.ImuBridgeState()
    gstate = GB.GpsBridgeState()
    stats = Stats(Path(args.out) / "bridge_imugps.json", "imugps")
    zeros9 = [0.0] * 9

    def imu_publish(index, wall):
        t = clock.now() % duration
        k = int(np.searchsorted(t_imu, t))
        k = min(max(k, 1), len(t_imu) - 1)
        a = (t - t_imu[k - 1]) / max(1e-6, t_imu[k] - t_imu[k - 1])
        row = imu[k - 1] * (1 - a) + imu[k] * a
        g = [row[cols.index(c)] for c in ("gx", "gy", "gz")]
        acc = [row[cols.index(c)] for c in ("ax", "ay", "az")]
        q = np.array([row[cols.index(c)] for c in ("qx", "qy", "qz", "qw")])
        q /= np.linalg.norm(q) or 1.0
        msg = NS(header=NS(stamp=stamp_ns(wall), frame_id="imu_link"),
                 orientation=NS(x=q[0], y=q[1], z=q[2], w=q[3]), orientation_covariance=zeros9,
                 angular_velocity=NS(x=g[0], y=g[1], z=g[2]), angular_velocity_covariance=zeros9,
                 linear_acceleration=NS(x=acc[0], y=acc[1], z=acc[2]), linear_acceleration_covariance=zeros9)
        state.update_imu(msg)
        if index % 2 == 0:
            state.update_magnetic(NS(header=msg.header, magnetic_field=NS(x=2.1e-5, y=-1.2e-5, z=4.3e-5)))

    gps_times = [(g["received_at"] * 1e9 - scan_bag0) / 1e9 for g in gps]

    def gps_publish(index, wall):
        if not gps:
            return
        t = min(max(clock.now() % duration, gps_times[0]), gps_times[-1])
        k = int(np.searchsorted(gps_times, t))
        k = min(max(k, 1), len(gps) - 1)
        a, b = gps[k - 1], gps[k]
        w = (t - gps_times[k - 1]) / max(1e-6, gps_times[k] - gps_times[k - 1])
        east = (a.get("e_m") or 0) * (1 - w) + (b.get("e_m") or 0) * w
        north = (a.get("n_m") or 0) * (1 - w) + (b.get("n_m") or 0) * w
        payload = dict(schema="orchard.gps.v1", sequence=index + 1, received_at=wall, frame_id="gps_link",
                       protocol="UBX_NAV_PVT", valid=bool(a.get("valid", True)), fix_type=a.get("fix_type") or "autonomous",
                       quality=a.get("quality", 1),
                       latitude=SYNTHETIC_LAT + north / 111320.0,
                       longitude=SYNTHETIC_LON + east / (111320.0 * math.cos(math.radians(SYNTHETIC_LAT))),
                       altitude_msl_m=50.0, h_acc_m=a.get("h_acc_m"), satellites=a.get("satellites"), hdop=0.9,
                       speed_mps=a.get("speed_mps"), course_deg=a.get("course_deg"), fix_dimension=3)
        gstate.update(NS(data=json.dumps(payload)))
        if index % max(1, int(args.gps_hz)) == 0:
            gstate.update(NS(data=json.dumps(dict(schema="orchard.gps.v1", driver=dict(serial_open=True, baud=115200,
                          bytes_received=index * 100, valid_packets=index)))), True)

    class Handler(BaseHTTPRequestHandler):
        """Recorder status stub (idle) and the wall clock used for the clock check of the browser runner."""

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/clock"):
                body = json.dumps(dict(t=time.time())).encode()
            elif self.path.startswith("/status"):
                body = json.dumps(dict(ok=True, active=False, state="idle", finalizing=False, kind=None,
                                       note="slow_link bench recorder stub")).encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", args.recorder_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def status_loop():
        path = Path(args.status_dir) / "imu_bridge_status.json"
        while not stop.wait(0.5):
            IB.write_status(path, state)
            stats.dump(dict(imu_record_hz=round(len(t_imu) / duration, 2), replay_imu_hz=args.imu_hz, replay_gps_hz=args.gps_hz,
                            gps_rows=len(gps), gnss="synthetic origin plus recorded metres"))

    threading.Thread(target=paced, args=(args.imu_hz, stop, stats, "imu", imu_publish), daemon=True).start()
    threading.Thread(target=paced, args=(args.gps_hz, stop, stats, "gps", gps_publish), daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()

    async def both():
        imu_args = NS(host="127.0.0.1", port=args.imu_port, path="/ws/imu", max_hz=20.0)
        gps_server = await websockets.serve(lambda ws: GB.stream(ws, gstate), "127.0.0.1", args.gps_port, max_size=2048,
                                            max_queue=1, write_limit=8192, compression=None, close_timeout=1)
        try:
            await IB.serve(state, imu_args, stop)
        finally:
            gps_server.close()
    asyncio.run(both())


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bridge", required=True, choices=["lidar", "camera", "imugps"])
    parser.add_argument("--app", required=True, help="exported dashboard source (the folder that holds scripts/ and dashboard_server/)")
    parser.add_argument("--out", required=True, help="result folder for bridge_*.json")
    parser.add_argument("--status-dir", required=True, help="folder the dashboard reads bridge status files from")
    parser.add_argument("--lidar-npz", help="recorded scans (.scans.npz)")
    parser.add_argument("--drive-json", help="recorded IMU / GPS extract (.drive.json.gz)")
    parser.add_argument("--camera-dir", help="1280x720 preview JPEG frames")
    parser.add_argument("--camera-original-dir", help="1920x1080 JPEG frames plus FORMAT.txt for the original-preview path")
    parser.add_argument("--epoch", type=float, default=None, help="shared replay origin (wall time); default: now")
    parser.add_argument("--camera-fps", type=float, default=30.0)
    parser.add_argument("--imu-hz", type=float, default=33.0)
    parser.add_argument("--gps-hz", type=float, default=5.0)
    parser.add_argument("--max-points", type=int, default=3200)
    parser.add_argument("--lidar-port", type=int, default=28765)
    parser.add_argument("--camera-port", type=int, default=28770)
    parser.add_argument("--imu-port", type=int, default=28775)
    parser.add_argument("--gps-port", type=int, default=28778)
    parser.add_argument("--recorder-port", type=int, default=28790)
    args = parser.parse_args()
    sys.path.insert(0, args.app)
    os.chdir(args.app)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    Path(args.status_dir).mkdir(parents=True, exist_ok=True)
    if args.epoch is None:
        args.epoch = time.time()
    stop = threading.Event()
    {"lidar": run_lidar, "camera": run_camera, "imugps": run_imugps}[args.bridge](args, stop)


if __name__ == "__main__":
    main()
