"""Test rig for TurnAssist: kinematic plant + synthetic real-format scans + a scripted estimator (fast).

The scripted estimator stands in for the vendored SensorEstimator (pose = the plant truth unless a test
scripts an error); clearances are computed from the SAME synthetic scan the executor adapter converts,
with the vendored formulas. Scans are real S2 format (3240 beams, raw order, mount offset 179.08 deg,
0 = no return) built from trunk circles, so ScanAdapter and the line-end tracker run unchanged.
"""
from dataclasses import replace
import math

import numpy as np

import rowexec_testlib as L
from row_executor.config import ExecutorConfig

LIDAR_X = .18
HALF_L, HALF_W = 1.22 / 2, .70 / 2
ICR_BEHIND_LIDAR = .18


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def turn_config(**kw):
    """Field config with the turn feature ON and SIM-like 'measured' values (motor output enabled).

    Merge with the day-2 hardening (claude/field-weekend-20261022): breakaway 25 (N1: >= 20 %) and a C7 stop distance
    (N8: row motor output needs it); 0.25 m is inside the stop model at 0.47 m/s (0.29 m)."""
    values = dict(motor_output_enabled=True, breakaway_duty_pct=25, v_measured_m_s=.47,
                  measured_yaw_at_max_diff_deg_s=6., max_yaw_rate_deg_s=3.6, turn_assist_enabled=True,
                  measured_stop_distance_m=.25,
                  pivot_duty_start_pct=60, pivot_duty_cap_pct=80, pivot_surface_ok=True, turn_drive_duty_pct=55)
    values.update(kw)
    return replace(ExecutorConfig(), **values).validate()


def orchard(width=3.5, x0=-12., x_end=2., spacing=2.5, rows=3, radius=.12):
    """Trunk circles (world): rows at y = (r - 0.5) * width, r = 0..rows-1, from x0 to x_end."""
    trunks = []
    for r in range(rows):
        y = (r - .5) * width
        x = x_end
        while x >= x0 - 1e-9:
            trunks.append((x, y, radius))
            x -= spacing
    return trunks


class Plant:
    """Kinematic skid-steer stand-in: MCU ramp 100 %/s, pivot breakaway / rate, forward speed, lags."""

    def __init__(self, x=0., y=0., yaw=0., pivot_min=55, rate_per_pct=.25, deadband=5, v_per_pct=.0094,
                 arc_deg_s_per_pct=.15, w_tau=.12, v_tau=.25):
        self.x, self.y, self.yaw = x, y, yaw
        self.v = self.w = 0.
        self.applied = [0., 0.]
        self.target = [0., 0.]
        self.pivot_min, self.rate_per_pct = pivot_min, rate_per_pct
        self.deadband, self.v_per_pct, self.arc = deadband, v_per_pct, arc_deg_s_per_pct
        self.w_tau, self.v_tau = w_tau, v_tau
        self.t = 0.
        self.distance = 0.
        self.w_hook = None          # f(t, w_target_rad_s, plant) -> w_target (faults)
        self.push = None            # f(t) -> (vx_world, vy_world, w) external motion (uncommanded)
        self.pivot_rotated = 0.     # |rotation| accumulated while pivoting [rad]

    def command(self, left, right):
        self.target = [float(left), float(right)]

    def step(self, dt):
        self.t += dt
        change = 100. * dt
        for k in range(2):
            a, g = self.applied[k], self.target[k]
            if a != 0. and a * g < 0:
                new = math.copysign(max(0., abs(a) - change), a)
            else:
                new = a + max(-change, min(change, g - a))
            self.applied[k] = new
        left, right = self.applied
        v_t = w_t = 0.
        if left * right < 0:
            d = min(abs(left), abs(right))
            if d >= self.pivot_min:
                w_t = math.copysign(math.radians(self.rate_per_pct * d), right)
        elif left > 0 and right > 0:
            mean = (left + right) / 2.
            v_t = max(0., mean - self.deadband) * self.v_per_pct
            w_t = math.radians(self.arc * (right - left))
        if self.w_hook is not None:
            w_t = self.w_hook(self.t, w_t, self)
        self.v += (v_t - self.v) * (1. - math.exp(-dt / self.v_tau))
        self.w += (w_t - self.w) * (1. - math.exp(-dt / self.w_tau))
        if v_t == 0. and abs(self.v) < .003:
            self.v = 0.
        if w_t == 0. and abs(self.w) < math.radians(.05):
            self.w = 0.
        vx, vy, w_ext = (0., 0., 0.) if self.push is None else self.push(self.t)
        if left * right < 0:
            self.pivot_rotated += abs(self.w) * dt
        mid = self.yaw + .5 * (self.w + w_ext) * dt
        self.x += (self.v * math.cos(mid) + vx) * dt
        self.y += (self.v * math.sin(mid) + vy) * dt
        self.yaw = wrap(self.yaw + (self.w + w_ext) * dt)
        self.distance += abs(self.v) * dt

    @property
    def rate(self):
        w_ext = 0. if self.push is None else self.push(self.t)[2]
        return self.w + w_ext

    def lidar_pose(self):
        return (self.x + LIDAR_X * math.cos(self.yaw), self.y + LIDAR_X * math.sin(self.yaw), self.yaw)


class Band:
    def __init__(self, heading, centre_y, width):
        self.heading, self.centre_y, self.width = heading, centre_y, width


class ScriptedEstimator:
    """SensorEstimator stand-in driven by the rig truth (and the scan it is given)."""

    def __init__(self, rig):
        self.rig = rig
        self.last_scan_stamp = None
        self.last_imu_stamp = None
        self.pose = np.zeros(3)
        self.odom_valid = False
        self.pivot_clearance = math.inf
        self.front = self.side_left = self.side_right = math.inf
        self.scans = 0
        self.band = None
        self.distance = 0.
        x, y, yaw = rig.plant.lidar_pose()
        self.forward = abs(wrap(yaw)) <= math.pi / 2       # band oriented with the travel at creation

    def on_imu(self, stamp, gyro, accel):
        self.last_imu_stamp = stamp

    def on_scan(self, stamp, ranges, angle_min, increment, range_max):
        rig = self.rig
        self.last_scan_stamp = stamp
        self.scans += 1
        lx, ly, lyaw = rig.plant.lidar_pose()
        self.pose = np.array((lx, ly, wrap(lyaw + rig.lidar_heading_error(stamp))))
        self.odom_valid = rig.odom_valid(stamp)
        self.distance = rig.plant.distance
        r = np.asarray(ranges, dtype=float)
        a = angle_min + np.arange(len(r)) * increment
        ok = np.isfinite(r) & (r > 0) & (r < range_max)
        px, py = r[ok] * np.cos(a[ok]), r[ok] * np.sin(a[ok])
        bx, by = px + LIDAR_X, py
        ahead = (bx > HALF_L - .05) & (np.abs(by) <= HALF_W + .10)
        self.front = float(bx[ahead].min() - HALF_L) if ahead.any() else math.inf
        beside = (np.abs(bx) <= HALF_L) & (np.abs(by) > HALF_W - .05)
        left, right = beside & (by > 0), beside & (by < 0)
        self.side_left = float(by[left].min() - HALF_W) if left.any() else math.inf
        self.side_right = float((-by[right]).min() - HALF_W) if right.any() else math.inf
        self.pivot_clearance = float(np.hypot(px + ICR_BEHIND_LIDAR, py).min()) if ok.any() else math.inf
        if rig.band_enabled and self.scans >= 3:
            centre = rig.corridor_centre(self.pose[1])
            self.band = Band(0. if self.forward else math.pi, centre, rig.band_width)
        elif not rig.band_enabled:
            self.band = None

    def snapshot(self, now):
        rig = self.rig
        p = rig.plant
        still_s = 0.
        if rig.still_since is not None and self.last_scan_stamp is not None and now - self.last_scan_stamp < .5:
            still_s = max(0., self.last_scan_stamp - rig.still_since)
        s = dict(lidar_age_s=math.inf if self.last_scan_stamp is None else now - self.last_scan_stamp,
                 imu_age_s=math.inf if self.last_imu_stamp is None else now - self.last_imu_stamp,
                 still=still_s > 0., still_s=still_s, vibration_rms=rig.vibration, speed=p.v,
                 distance_m=self.distance, odom_valid=self.odom_valid, front_clearance_m=self.front,
                 side_clearance_left_m=self.side_left, side_clearance_right_m=self.side_right,
                 pivot_clearance_m=self.pivot_clearance, pivot_radius_required_m=1.03,
                 next_corridor=dict(rig.next_votes), corridor_valid=False, corridor_reason='scripted',
                 corridor_full_updates=self.scans, yaw_rate=p.rate, lidar_heading=float(self.pose[2]))
        band = self.band
        if band is not None:
            theta = float(self.pose[2])
            bx = self.pose[0] - LIDAR_X * math.cos(theta)
            by = self.pose[1] - LIDAR_X * math.sin(theta)
            normal = (-math.sin(band.heading), math.cos(band.heading))
            lateral = bx * normal[0] + by * normal[1] - (band.centre_y * normal[1])
            s.update(corridor_valid=True, heading_error=wrap(band.heading - theta), lateral_offset=lateral,
                     corridor_width=band.width)
        return s


class Rig:
    """Feeds a TurnAssist: plant 100 Hz, IMU 33 Hz, scans 10 Hz, ticks 20 Hz, command latency 50 ms."""

    def __init__(self, assist_factory, trunks=None, width=3.5, x=-.5, y=0., yaw=0., plant=None, t0=100.,
                 obstacles=()):
        self.plant = plant or Plant(x, y, yaw)
        self.width = width
        self.trunks = list(orchard(width) if trunks is None else trunks)
        self.obstacles = list(obstacles)       # extra circles (x, y, r), may be changed by a test
        self.t = t0
        self.band_enabled = True
        self.band_width = width
        self.vibration = .05
        self.next_votes = dict(left='UNKNOWN', right='UNKNOWN')
        self.imu_on = self.scan_on = True
        self.gyro_bias = 0.
        self.gyro_scale = 1.
        self.lidar_heading_error = lambda stamp: 0.
        self.odom_valid = lambda stamp: True
        self.still_since = t0
        self.commands = []
        self.decisions = []
        self.next_imu = t0
        self.next_scan = t0 + .013
        self.manual = None          # f(rig) -> (left, right): a person drives (observe mode); else the decision
        self.assist = assist_factory(lambda: ScriptedEstimator(self))

    def corridor_centre(self, y):
        k = round(y / self.width)
        return k * self.width

    def scene(self):
        lx, ly, lyaw = self.plant.lidar_pose()
        c, s = math.cos(-lyaw), math.sin(-lyaw)
        out = []
        for cx, cy, r in self.trunks + self.obstacles:
            dx, dy = cx - lx, cy - ly
            if dx * dx + dy * dy > 30. * 30.:
                continue
            out.append((c * dx - s * dy, s * dx + c * dy, r))
        return L.raw_scan(out)

    def run(self, duration, hold=False):
        out = []
        end = self.t + duration - 1e-9
        while self.t + .05 <= end + 1e-6:
            target = self.t + .05
            sub = self.t
            while sub + .01 <= target + 1e-9:
                sub += .01
                while self.commands and self.commands[0][0] <= sub + 1e-9:
                    _, left, right = self.commands.pop(0)
                    self.plant.command(left, right)
                self.plant.step(.01)
                moving = abs(self.plant.v) > .005 or abs(self.plant.rate) > math.radians(.3)
                if moving:
                    self.still_since = None
                elif self.still_since is None:
                    self.still_since = sub
                while self.next_imu <= sub:
                    if self.imu_on:
                        g = self.plant.rate * self.gyro_scale + self.gyro_bias
                        self.assist.feed_imu(self.next_imu, (0., 0., g), (0., 0., 9.81))
                    self.next_imu += 1 / 33.
                while self.next_scan <= sub:
                    if self.scan_on:
                        self.assist.feed_scan(self.next_scan, self.scene(), L.RAW_AMIN, L.RAW_INC, 30., .05)
                    self.next_scan += .1
            self.t = target
            h = hold(self) if callable(hold) else hold
            d = self.assist.tick(self.t, h)
            cmd = (d.left, d.right) if self.manual is None else self.manual(self)
            self.commands.append((self.t + .05,) + tuple(cmd))
            out.append((self.t, d))
            self.decisions.append((self.t, d))
        return out


class Operator:
    """Hold logic: a new press for every step/pause after a 0.3 s release; holds while active."""

    def __init__(self, release_s=.3, max_presses=40):
        self.hold = False
        self.release_until = None
        self.release_s = release_s
        self.presses = 0
        self.max_presses = max_presses
        self.forced_release = None    # f(rig) -> True to release now (fault injection)

    def __call__(self, rig):
        a = rig.assist
        t = rig.t
        if self.forced_release is not None and self.forced_release(rig):
            self.hold = False
            self.release_until = t + 1.
            return False
        if a.phase in ('CREEP_OUT', 'PIVOT_1', 'TRAVERSE', 'PIVOT_2') and a.mode in ('await_press', 'paused'):
            if self.hold:
                self.hold = False
                self.release_until = t + self.release_s
            elif self.release_until is None:
                self.release_until = t + self.release_s
            elif t >= self.release_until and self.presses < self.max_presses:
                self.hold = True
                self.presses += 1
                self.release_until = None
        return self.hold


def outputs(decisions):
    return [(round(t, 2), d.left, d.right, d.state, d.reason) for t, d in decisions]
