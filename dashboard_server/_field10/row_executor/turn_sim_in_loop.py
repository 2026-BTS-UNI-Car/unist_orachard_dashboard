"""SIM-in-the-loop CLOSED-LOOP test of TurnAssist in headland scenes (SIM only: no hardware, ROS or Docker).

Per episode (deterministic for (scenario, plant, direction, seed)):
  SIM FieldPlant (sim/ubts/field_response.py, 100 Hz, MCU ramp 100 %/s, BREAK 120 ms, pivot breakaway /
  stall / ICR draws) in a closed_loop/world.py orchard (trunks + canopy at 0.60 m, optional headland fence /
  next block / clutter); truth is used ONLY to make sensor packets and to score.
    LiDAR 10 Hz, SIM 3200 beams -> REAL S2 format: 3240 beams, raw order, angle_min float32(-pi), beam j
      looks at body angle angle_min + j*inc + 179.08 deg (the mount the executor undoes), nearest SIM beam,
      no return -> 0.0, 1 mm quantisation. Receipt = SIM time (+ latency), fed to TurnAssist.feed_scan.
    IMU 33 Hz (the live rate; the plant's imu_sample: gyro z + noise/bias/vibration, accel) ->
      TurnAssist.feed_imu((0, 0, gz), accel).
    TurnAssist.tick at 20 Hz -> SignedBridgeClient.drive_signed (allow_signed=True, guard_check =
      TurnGuard.shape_problem, deadman:true) -> the dashboard's REAL RoverControl core (read-only
      DASH/scripts/rover_control_core.py, in-process, fake motor) -> the duty the fake motor holds reaches
      the plant after the 50 ms command latency, then the MCU ramp.
  Operator emulation: TurnAssist.start at 0.6 s (vehicle standing at the row end); for every step a NEW
  hold press after a 0.3 s release; hold kept while active; scripted faults (hold release mid-pivot,
  IMU / LiDAR blackouts) per scenario.
  Optional follow check (--follow-check, default on): after DONE ready, a core.Runner row run (the same
  SIM 'measured' config) drives 6 m into the next corridor with the same sensor stream.
Plants: sim_default (field_response defaults: forward deadband 50 %, pivot min duty 80 %); P_fit1003
(speed_at_90 0.80, forward deadband 5, arc 0.15, drive vibration 0.7, pivot min duty 55); stress variants
P_fit1003_min70 (pivot min duty 70) and P_fit1003_stickslip (rate jitter 0.3, breakaway delay 0.3-1.2 s,
ICR sd 0.35 m, rotation sticks 50 % of every 1.0 s while pivoting); surface stall_prone (stall p 0.75).
Usage (worktree root):  python -B tools/row_executor/turn_sim_in_loop.py --out <file.json> --seeds 0 1 2 --workers 8
"""
import argparse
import importlib.util
import json
import math
import os
import statistics
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
SIM_DIR = os.path.join(REPO, 'sim', 'ubts')
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402

from row_executor import config as XC  # noqa: E402
from row_executor import core as XCORE  # noqa: E402
from row_executor.turn_assist import DONE, ENDED, MOTION_PHASES, TurnAssist, TurnRequest  # noqa: E402
from row_executor.turn_bridge import SignedBridgeClient  # noqa: E402

RAW_AMIN = -3.1415927410125732
RAW_INC = .0019398535368964076
BEAMS = 3240
SIM_BEAMS = 3200
DT = .01
DASH_CANDIDATES = ('D:/Develop/UBTS/dashboard-live-raw', '/mnt/d/Develop/UBTS/dashboard-live-raw', '/dash')
P_FIT1003 = (('speed_at_90_m_s', .80), ('forward_deadband_pct', 5.), ('arc_yaw_deg_s_per_pct_diff', .15),
             ('drive_vibration_rms_m_s2', .7), ('pivot_min_duty_pct', 55.))
PLANTS = {
    'sim_default': dict(overrides=(), stickslip=None, cfg='default'),
    'P_fit1003': dict(overrides=P_FIT1003, stickslip=None, cfg='fit'),
    'P_fit1003_min70': dict(overrides=P_FIT1003[:-1] + (('pivot_min_duty_pct', 70.),), stickslip=None, cfg='fit'),
    'P_fit1003_stickslip': dict(overrides=P_FIT1003 + (('pivot_rate_jitter_log_sd', .3),
                                                       ('pivot_breakaway_delay_s', (.3, 1.2)),
                                                       ('pivot_icr_sd_m', .35)),
                                stickslip=(1.0, .5), cfg='fit'),
}
# SIM 'measured' executor values per plant family (what C2/C3/C5/C6 would give on this plant).
# Merged with the day-2 hardening and the field decision rules (claude/field-integrated-20261022): turn output needs
# motor_output_enabled, so these configs pass the row MOTOR rules too: breakaway >= 20 % (N1; the plants' deadband is
# 5 %, 'fit' 10 -> 20 never binds at diff 20 / base 55), measured_stop_distance_m 0.25 (N8; the stop model at 0.45-0.47
# m/s is 0.27-0.29 m) and a yaw authority >= 4.0 deg/s at max_differential 10-20 (steering GO rule). sim_default's arc
# gain gives only 2.4 deg/s at diff 10: its 4.0 is NOT a SIM measurement - only the config rules read it; the row
# controller of the follow check uses max_yaw_rate_deg_s, which stays at the plant-consistent 1.4 (= 0.6 x 2.4).
SIM_MEASURED = {
    'fit': dict(breakaway_duty_pct=20, v_measured_m_s=.47, measured_yaw_at_max_diff_deg_s=6., max_yaw_rate_deg_s=3.6,
                measured_stop_distance_m=.25),
    'default': dict(breakaway_duty_pct=50, base_duty_pct=75, reduce_duty_pct=75, max_differential_pct=10,
                    duty_cap_pct=85, diff_cap_pct=20, v_measured_m_s=.45, measured_yaw_at_max_diff_deg_s=4.,
                    max_yaw_rate_deg_s=1.4, min_yaw_authority_deg_s=2., measured_stop_distance_m=.25),
}
TURN_ON = dict(turn_assist_enabled=True, motor_output_enabled=True, pivot_surface_ok=True)
PIVOT_DUTIES = {'uncal': (60, 80), 'cal_default': (85, 90)}   # cal_default = C6 on sim_default (min 80) + 5
# canopy class -> (x_c, reach) from TURN_DESIGN 2 (W 3.5): SIM branch reach 0.45 (+radius 0.1) ~ r_c 0.5-0.6,
# 0.9 ~ r_c 1.0 -> the r_c 1.2 column (conservative).
TABLE = {.45: {3.: (1.5, 3.17), 3.5: (1.5, 3.19), 4.2: (1.5, 3.25)},
         .9: {3.: (2.1, 3.77), 3.5: (2.1, 3.81), 4.2: (2.1, 3.81)}}
OPEN_DEPTH = 20.


def scenario_table():
    """name -> dict(world=..., depth_true, depth_input, faults, expect)."""
    base = dict(scenario='open_end', row_count=3, row_width_m=3.5, tree_spacing_m=2.5, row_length_m=20.)
    sc = {
        'open_W3.5': dict(world=base, depth=None),
        'fence5_W3.5': dict(world=dict(base, scenario='wall_beyond_rows', headland_depth_m=5.), depth=5.),
        'fence4_W3.5': dict(world=dict(base, scenario='wall_beyond_rows', headland_depth_m=4.), depth=4.,
                            expect='any_safe'),
        'fence3_W3.5_refused': dict(world=dict(base, scenario='wall_beyond_rows', headland_depth_m=3.), depth=3.,
                                    expect='refused'),
        'fence3_W3.5_misreported': dict(world=dict(base, scenario='wall_beyond_rows', headland_depth_m=3.),
                                        depth=3., depth_input=5., expect='safe_stop'),
        'open_W3.0': dict(world=dict(base, row_width_m=3., tree_spacing_m=2.), depth=None),
        'open_W4.2': dict(world=dict(base, row_width_m=4.2, tree_spacing_m=3.), depth=None),
        'canopy0.9_fence5': dict(world=dict(base, scenario='wall_beyond_rows', headland_depth_m=5.,
                                            branch_reach_m=.9), depth=5.),
        'rotated30_open': dict(world=dict(base, field_yaw_deg=30.), depth=None),
        'next_block_6m': dict(world=dict(base, extra_trees='next_block_6'), depth=6.),
        'fence_right_0p5': dict(world=dict(base, extra_walls='fence_right'), depth=None, expect='any_safe'),
        'release_mid_pivot': dict(world=base, depth=None, faults=('release_mid_pivot',)),
        'imu_gap_pivot': dict(world=base, depth=None, faults=('imu_gap_pivot',), expect='safe_stop'),
        'imu_gap_short': dict(world=base, depth=None, faults=('imu_gap_short',)),
        'lidar_blackout_traverse': dict(world=base, depth=None, faults=('lidar_blackout_traverse',)),
    }
    return sc


GROUPS = {
    'core': ('open_W3.5', 'fence5_W3.5', 'fence4_W3.5', 'fence3_W3.5_refused', 'fence3_W3.5_misreported',
             'open_W3.0', 'open_W4.2', 'canopy0.9_fence5', 'rotated30_open', 'next_block_6m', 'fence_right_0p5'),
    'faults': ('release_mid_pivot', 'imu_gap_pivot', 'imu_gap_short', 'lidar_blackout_traverse'),
    'stress': ('open_W3.5', 'fence4_W3.5', 'canopy0.9_fence5'),
    'stall': ('open_W3.5', 'fence5_W3.5'),
}


# --- module loading (vendored closed_loop / orchard_perception stay the only ones under those names) ----
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sim_modules():
    if 'ubts_sim_world' in sys.modules:
        return sys.modules['ubts_sim_world'], sys.modules['field_response']
    XCORE.load_vendor()
    if SIM_DIR not in sys.path:
        sys.path.append(SIM_DIR)          # after the vendor dir: cpu_lidar / field_response only exist here
    scenarios = _load('ubts_sim_scenarios', os.path.join(SIM_DIR, 'orchard_perception', 'scenarios.py'))
    package = sys.modules['orchard_perception']          # the vendored package
    sys.modules['orchard_perception.scenarios'] = scenarios
    try:
        world = _load('ubts_sim_world', os.path.join(SIM_DIR, 'closed_loop', 'world.py'))
    finally:
        del sys.modules['orchard_perception.scenarios']
        if hasattr(package, 'scenarios'):
            delattr(package, 'scenarios')
    import field_response  # noqa: E402
    XCORE.verify_vendor()
    return world, field_response


def rover_control_class():
    for root in ((os.environ.get('UBTS_DASH_DIR'),) if os.environ.get('UBTS_DASH_DIR') else ()) + DASH_CANDIDATES:
        path = os.path.join(root, 'scripts', 'rover_control_core.py') if root else None
        if path and os.path.isfile(path):
            module = _load('dash_rover_control_core', path)
            return module.RoverControl, module.ControlError
    raise RuntimeError('dashboard-live-raw/scripts/rover_control_core.py not found (set UBTS_DASH_DIR)')


class SimMotor:
    """EspMotor stand-in for RoverControl: the duty it holds is what the SIM plant receives."""
    firmware = 'UBTS_WHEEL_PWM_V2'
    error = None

    def __init__(self):
        self.duty = (0, 0)
        self.outputs = {}
        self.hard_stops = 0

    @property
    def ready(self):
        return True

    @property
    def stopped(self):
        return self.duty == (0, 0)

    def enable(self):
        pass

    def drive(self, left, right):
        self.duty = (int(left), int(right))

    def release(self):
        self.duty = (0, 0)

    def hard_stop(self):
        self.duty = (0, 0)
        self.hard_stops += 1


class CoreTransport:
    def __init__(self, control, control_error):
        self.control, self.control_error = control, control_error
        self.ops = dict(status=0, arm=0, drive=0, stop=0, other=0)
        self.negative_drives = 0

    def __call__(self, request):
        op = request.get('op')
        self.ops[op if op in self.ops else 'other'] += 1
        if op == 'drive' and (request.get('left', 0) < 0 or request.get('right', 0) < 0):
            self.negative_drives += 1
        if op == 'status':
            self.control.tick()
            return dict(self.control.status(), updated_at=time.time(), mode='manual')
        if op == 'stop':
            self.control.stop(str(request.get('reason', 'operator_stop'))[:80])
            return {'ok': True, 'state': 'stop_requested'}
        try:
            return self.control.dispatch(request)
        except (self.control_error, ValueError) as exc:
            return {'ok': False, 'error': str(exc)}


class RealFormat:
    """SIM 3200-beam body-frame scan -> real S2 LaserScan ranges (raw order, 0 = no return)."""

    def __init__(self, offset_deg=179.08):
        body = RAW_AMIN + np.arange(BEAMS) * RAW_INC + math.radians(offset_deg)
        body = np.arctan2(np.sin(body), np.cos(body))
        self.index = np.rint((body + math.pi) / (2 * math.pi / SIM_BEAMS)).astype(np.int64) % SIM_BEAMS

    def convert(self, sim_ranges):
        r = np.asarray(sim_ranges, dtype=np.float64)[self.index]
        return np.where(np.isfinite(r) & (r < 30.), np.round(r * 1000.) / 1000., 0.)


def make_plant_class(field_response, stickslip):
    if stickslip is None:
        return field_response.FieldPlant
    period, fraction = stickslip

    class StickSlipPlant(field_response.FieldPlant):
        """Pivot rotation sticks for fraction*period of every period (rate scaled to keep the mean)."""

        def _targets(self, left, right, dt):
            v, w, icr_x, regime = super()._targets(left, right, dt)
            if regime == 'pivot':
                phase = (self.pivot.loaded_s % period) / period
                w = 0. if phase < fraction else w / (1. - fraction)
            return v, w, icr_x, regime
    return StickSlipPlant


def executor_config(plant_name, duties='uncal', turn=True):
    family = PLANTS[plant_name]['cfg']
    values = dict(SIM_MEASURED[family])
    if turn:
        start, cap = PIVOT_DUTIES[duties]
        values.update(TURN_ON, pivot_duty_start_pct=start, pivot_duty_cap_pct=cap,
                      turn_drive_duty_pct=55 if family == 'fit' else 75)
    return replace(XC.ExecutorConfig(), **values).validate()


def world_config(world_mod, spec, seed):
    kw = dict(spec['world'])
    trees = kw.pop('extra_trees', None)
    walls = kw.pop('extra_walls', None)
    cfg = world_mod.WorldConfig(seed=10_000 + seed, **kw)
    if trees == 'next_block_6':
        w, end = cfg.row_width_m, cfg.row_length_m
        rows = tuple((r - .5) * w for r in range(cfg.row_count))
        cfg = replace(cfg, extra_trees=tuple((end + 6. + k * 2.5, y) for y in rows for k in range(4)))
    if walls == 'fence_right':
        cfg = replace(cfg, extra_walls=((10., -cfg.row_width_m / 2 - .5, 32., -cfg.row_width_m / 2 - .5),))
    return cfg


# --- one episode -----------------------------------------------------------------------------------------
def run_episode(job):
    name, plant_name, surface, direction, seed, duties, follow = job
    started = time.perf_counter()
    world_mod, field_response = sim_modules()
    RoverControl, ControlError = rover_control_class()
    spec = scenario_table()[name]
    wcfg = world_config(world_mod, spec, seed)
    world = world_mod.build_world(wcfg)
    width = wcfg.row_width_m
    corridor = 0 if direction == 'left' else 1
    next_corridor = 1 if direction == 'left' else 0
    sign = 1 if direction == 'left' else -1
    rng = np.random.default_rng([seed, zlib.crc32(('%s|%s|%s' % (name, plant_name, direction)).encode())])
    start_x = 18. + (2. if seed % 2 else 0.) + float(rng.uniform(-.3, .3))   # backstop (L-2) / row-end stop
    start_y = corridor * width + float(rng.uniform(-.15, .15))
    start_yaw = float(rng.uniform(-3., 3.))
    pose = world.pose_in_field(start_x, start_y, math.radians(start_yaw))
    pinfo = PLANTS[plant_name]
    params = replace(field_response.FieldResponseParams(), **dict(pinfo['overrides']))
    plant_cls = make_plant_class(field_response, pinfo['stickslip'])
    plant = plant_cls(params, surface=surface, seed=(seed, zlib.crc32(name.encode()), 1), pose=pose)
    caster, lidar = world_mod.make_lidar(world, seed=int(rng.integers(1 << 31)))
    fmt = RealFormat()
    cfg = executor_config(plant_name, duties)
    assist = TurnAssist(cfg)
    reach_class = .9 if wcfg.branch_reach_m > .6 else .45
    x_c, reach = TABLE[reach_class][min(TABLE[reach_class], key=lambda k: abs(k - width))]
    depth_true = spec.get('depth')
    depth_input = spec.get('depth_input', depth_true if depth_true is not None else OPEN_DEPTH)
    request = TurnRequest(direction=direction, row_width_m=width, x_c_m=x_c, reach_m=reach,
                          headland_depth_m=depth_input, creep_max_m=6.)
    faults = spec.get('faults', ())
    # bridge core
    sim_now = [0.]
    motor = SimMotor()
    control = RoverControl(motor, lambda: sim_now[0])
    transport = CoreTransport(control, ControlError)
    client = SignedBridgeClient(owner='turn-assist:sim', transport=transport, allow_signed=True)
    line_end_truth = world.line_end_x(1)
    end_x_truth = max(x for x in (world.line_end_x(r) for r in range(wcfg.row_count)) if x is not None)
    result = dict(name=name, plant=plant_name, surface=surface, direction=direction, seed=seed, duties=duties,
                  start=dict(x=round(start_x - line_end_truth, 3), y_off=round(start_y - corridor * width, 3),
                             yaw_deg=round(start_yaw, 2)),
                  request=dict(x_c=x_c, reach=reach, depth_input=depth_input, depth_true=depth_true))
    commands = []
    started_turn = refused = False
    hold = False
    release_until = None
    presses = 0
    metrics = dict(min_hard=math.inf, min_branch=math.inf, collisions=0, branch_contacts=0,
                   branch_contacts_moving=0, first_collision=None, sent_pivot=0, sent_forward=0,
                   max_reach=-math.inf)
    pivot_truth = {}
    phase_time = {}
    last_phase = None
    imu_next = 0.
    imu_block = None
    lidar_block = None
    fault_done = set()
    terminal_at = None
    stop_sent = False
    decision = None
    t = 0.
    max_time = 150.
    steps = int(max_time / DT)
    field_yaw = math.radians(wcfg.field_yaw_deg)

    def truth_field():
        fx, fy, fyaw = world.to_field(plant.x, plant.y, plant.yaw)
        return fx, fy, fyaw

    for k in range(steps):
        t = (k + 1) * DT
        sim_now[0] = t
        while commands and commands[0][0] <= t - DT + 1e-9:
            _, left, right = commands.pop(0)
            plant.command(left, right)
        plant.step(DT)
        # sensors
        if (k + 1) % 10 == 0:
            lx, ly, lyaw = plant.lidar_pose()
            frame = lidar.sample(int(round(t * 1e9)), (lx, ly, .6), world_mod.yaw_quaternion(lyaw))
            blocked = lidar_block is not None and lidar_block[0] <= t < lidar_block[1]
            if frame is not None and not blocked:
                assist.feed_scan(t, fmt.convert(frame.ranges), RAW_AMIN, RAW_INC, 30., .05)
        if t >= imu_next - 1e-9:
            imu_next += 1. / 33.
            sample = plant.imu_sample()
            blocked = imu_block is not None and imu_block[0] <= t < imu_block[1]
            if not blocked:
                assist.feed_imu(t + .005, (0., 0., sample['gyro_z_rad_s']), sample['accel_m_s2'])
        if (k + 1) % 5 != 0:
            continue
        # --- 20 Hz: operator, assist, bridge -------------------------------------------------------------
        if not started_turn and t >= .6:
            started_turn = True
            try:
                assist.start(t, request, motor_output=True)
                client.arm()
            except Exception as exc:
                refused = True
                result['refused'] = str(exc)[:300]
                break
        if not started_turn:
            continue
        mode = assist.mode
        phase = assist.phase
        # operator: new press per step / after a pause; scripted releases
        if phase in MOTION_PHASES and mode in ('await_press', 'paused'):
            if hold:
                hold = False
                release_until = t + .3
            elif release_until is not None and t >= release_until and presses < 40:
                hold = True
                presses += 1
                release_until = None
            elif release_until is None:
                release_until = t + .3
        elif phase in MOTION_PHASES and mode == 'active':
            if 'release_mid_pivot' in faults and 'release' not in fault_done and phase == 'PIVOT_1' and \
                    assist.step is not None and assist.step.get('sub') == 'ROTATE' and \
                    t - assist.step['tracker'].t_rotating > 1.5:
                fault_done.add('release')
                hold = False
                release_until = t + 1.
        if ('imu_gap_pivot' in faults or 'imu_gap_short' in faults) and 'imu' not in fault_done and \
                phase == 'PIVOT_1' and assist.step is not None and assist.step.get('sub') == 'ROTATE':
            fault_done.add('imu')
            imu_block = (t, t + (.6 if 'imu_gap_pivot' in faults else .25))
        if 'lidar_blackout_traverse' in faults and 'lidar' not in fault_done and phase == 'TRAVERSE' and \
                mode == 'active' and assist.step is not None and assist.step['active_s'] > 2.:
            fault_done.add('lidar')
            lidar_block = (t, t + 1.)
        decision = assist.tick(t, hold)
        phase_time[decision.state] = phase_time.get(decision.state, 0.) + .05
        if decision.state != last_phase:
            fx, fy, fyaw = truth_field()
            if last_phase in ('PIVOT_1', 'PIVOT_2'):
                target = sign * (math.pi / 2 if last_phase == 'PIVOT_1' else math.pi)
                rec = pivot_truth.setdefault(last_phase, {})
                rec['end_error_deg'] = round(math.degrees(_wrap(fyaw - target)) * sign, 2)
            last_phase = decision.state
        if decision.state in ('PIVOT_1', 'PIVOT_2'):
            fx, fy, fyaw = truth_field()
            target = sign * (math.pi / 2 if decision.state == 'PIVOT_1' else math.pi)
            rec = pivot_truth.setdefault(decision.state, dict(max_over_deg=-180.))
            rec['max_over_deg'] = max(rec['max_over_deg'], round(math.degrees(_wrap(fyaw - target)) * sign, 2))
        left, right = decision.left, decision.right
        if client.state == 'ARMED':
            try:
                client.drive_signed(left, right, assist.guard.shape_problem)
            except Exception as exc:
                result['bridge_error'] = str(exc)[:200]
                assist.abort(t, 'bridge_error')
        if left < 0 or right < 0:
            metrics['sent_pivot'] += 1
        elif left or right:
            metrics['sent_forward'] += 1
        commands.append((t + .05,) + motor.duty)
        control.tick()
        # truth metrics
        fx_, fy_, fyaw_ = truth_field()
        c_, s_ = math.cos(fyaw_), math.sin(fyaw_)
        hl, hw = params.footprint_length_m / 2, params.footprint_width_m / 2
        metrics['max_reach'] = max(metrics['max_reach'], max(fx_ + c_ * a - s_ * b for a in (-hl, hl)
                                                             for b in (-hw, hw)) - line_end_truth)
        hard, branch = world_mod.footprint_clearance(world, plant.x, plant.y, plant.yaw, params.footprint_length_m,
                                                      params.footprint_width_m)
        metrics['min_hard'] = min(metrics['min_hard'], hard)
        metrics['min_branch'] = min(metrics['min_branch'], branch)
        if branch <= 0:
            metrics['branch_contacts'] += 1
            if abs(plant.v) > .01 or abs(plant.w) > .01:
                metrics['branch_contacts_moving'] += 1
        if hard <= 0:
            metrics['collisions'] += 1
            metrics['first_collision'] = round(t, 2)
            break
        if decision.terminal:
            if terminal_at is None:
                terminal_at = t
            if not stop_sent and (abs(plant.v) < .01 and abs(plant.w) < .01 or t - terminal_at > 1.5):
                stop_sent = True
                if client.state == 'ARMED':
                    client.stop('turn_end')
            if stop_sent and abs(plant.v) < .005 and abs(plant.w) < .005 and t - terminal_at > 1.:
                break
    fx, fy, fyaw = truth_field()
    status = assist.status()
    target_y = next_corridor * width
    heading_err = math.degrees(_wrap(fyaw - (math.pi if True else 0.)))     # into the next corridor: -x
    result.update(
        end_reason=status['end_reason'], phase=status['phase'], time_s=round(t, 2),
        final=dict(x_rel_line_end=round(fx - line_end_truth, 3), lateral_next_m=round(fy - target_y, 3),
                   heading_err_deg=round(heading_err, 2)),
        handoff=status['handoff'], pivots=status['pivots'], pivot_truth=pivot_truth,
        warnings=status['warnings'], guard=status['guard'], events=status['events'][-40:],
        min_hard_clearance_m=_fin(metrics['min_hard']), min_branch_clearance_m=_fin(metrics['min_branch']),
        max_reach_beyond_last_trunk_m=_fin(metrics['max_reach']),
        collisions=metrics['collisions'], branch_contact_samples=metrics['branch_contacts'],
        branch_contact_samples_moving=metrics['branch_contacts_moving'],
        plant_pivots=plant.pivot_count, plant_pivot_stalls=plant.pivot_stalls,
        plant_pivot_log=[list(p) for p in plant.pivot_log][:10], presses=presses,
        phase_time_s={k: round(v, 2) for k, v in phase_time.items()},
        bridge=dict(ops=dict(transport.ops), negative_drives=transport.negative_drives,
                    client_state=client.state, latch=client.latch_reason, signed=client.counts.get('signed_drives')),
        max_imu_gap_in_pivot_s=status['max_imu_gap_in_pivot_s'], yaw_disagree_max_deg=status['yaw_disagree_max_deg'],
        refused=result.get('refused'))
    in_next = (abs(fy - target_y) <= .5 and abs(heading_err) <= 12.
               and line_end_truth - 1.5 <= fx <= end_x_truth + 4.)
    result['aligned_in_next_corridor'] = bool(in_next)
    result['ready'] = status['end_reason'] in ('turn_done_ready', 'turn_done_ready_odometry')
    result['completed'] = bool(result['ready'] and in_next and metrics['collisions'] == 0
                               and metrics['branch_contacts_moving'] == 0)
    expect = spec.get('expect', 'complete')
    safe = metrics['collisions'] == 0 and metrics['branch_contacts_moving'] == 0
    if expect == 'refused':
        result['as_expected'] = bool(result.get('refused'))
    elif expect == 'safe_stop':
        result['as_expected'] = bool(safe and status['phase'] in (DONE, ENDED) and not result['completed'])
    elif expect == 'any_safe':
        result['as_expected'] = bool(safe)
    else:
        result['as_expected'] = result['completed']
    result['expect'] = expect
    if follow and (result['ready'] or (follow == 'all' and status['phase'] == DONE)) and metrics['collisions'] == 0:
        result['follow'] = follow_check(world, world_mod, plant, lidar, fmt, plant_name, t, next_corridor, width,
                                        line_end_truth, params)
    result['wall_s'] = round(time.perf_counter() - started, 2)
    return result


def follow_check(world, world_mod, plant, lidar, fmt, plant_name, t0, corridor, width, line_end, params):
    """Row run (core.Runner, same SIM-measured config) 6 m into the next corridor after the hand-off."""
    cfg = executor_config(plant_name, turn=False)
    runner = XCORE.Runner(cfg)
    runner.start_run(t0, 8.)
    hold, imu_next, t = False, t0, t0
    press_at, presses = t0 + 1.2, 0
    res = dict(end=None, collisions=0, min_hard=math.inf, branch_moving=0)
    commands = []
    for k in range(int(40. / DT)):
        t = t0 + (k + 1) * DT
        while commands and commands[0][0] <= t - DT + 1e-9:
            _, left, right = commands.pop(0)
            plant.command(left, right)
        plant.step(DT)
        if (k + 1) % 10 == 0:
            lx, ly, lyaw = plant.lidar_pose()
            frame = lidar.sample(int(round(t * 1e9)), (lx, ly, .6), world_mod.yaw_quaternion(lyaw))
            if frame is not None:
                runner.feed_scan(t, fmt.convert(frame.ranges), RAW_AMIN, RAW_INC, 30., .05)
        if t >= imu_next - 1e-9:
            imu_next += 1. / 33.
            s = plant.imu_sample()
            runner.feed_imu(t + .005, (0., 0., s['gyro_z_rad_s']), s['accel_m_s2'])
        if (k + 1) % 5:
            continue
        if runner.state == XCORE.PAUSED:
            if hold:
                hold, press_at = False, t + .3
            elif t >= press_at and presses < 20:
                hold, presses = True, presses + 1
        d = runner.tick(t, hold)
        commands.append((t + .05, d.left, d.right))
        hard, branch = world_mod.footprint_clearance(world, plant.x, plant.y, plant.yaw, params.footprint_length_m,
                                                      params.footprint_width_m)
        res['min_hard'] = min(res['min_hard'], hard)
        if branch <= 0 and (abs(plant.v) > .01 or abs(plant.w) > .01):
            res['branch_moving'] += 1
        if hard <= 0:
            res['collisions'] += 1
            break
        if d.terminal:
            res['end'] = runner.end_reason
            break
    fx, fy, fyaw = world.to_field(plant.x, plant.y, plant.yaw)
    res.update(end=res['end'] or runner.reason, presses=presses, progress_into_row_m=round(line_end - fx, 3),
               lateral_m=round(fy - corridor * width, 3), heading_err_deg=round(math.degrees(_wrap(fyaw - math.pi)), 2),
               min_hard=_fin(res['min_hard']))
    res['ok'] = bool(res['collisions'] == 0 and res['branch_moving'] == 0 and res['end'] in (
        'row_distance_backstop', 'row_time_backstop') and abs(res['lateral_m']) < .6)
    return res


def _wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def _fin(v):
    return round(float(v), 3) if math.isfinite(v) else None


# --- matrix -------------------------------------------------------------------------------------------------
def jobs_for(seeds, follow, groups):
    jobs = []
    for group in groups:
        names = GROUPS[group]
        if group in ('core', 'faults'):
            plant_sets = [('P_fit1003', 'normal', 'uncal'), ('sim_default', 'normal', 'uncal'),
                          ('sim_default', 'normal', 'cal_default')]
            if group == 'faults':
                plant_sets = plant_sets[:1]
        elif group == 'stress':
            plant_sets = [('P_fit1003_min70', 'normal', 'uncal'), ('P_fit1003_stickslip', 'normal', 'uncal')]
        else:
            plant_sets = [('P_fit1003', 'stall_prone', 'uncal')]
        for name in names:
            for plant, surface, duties in plant_sets:
                for direction in ('left', 'right'):
                    for seed in seeds:
                        jobs.append((name, plant, surface, direction, seed, duties, follow))
    return jobs


def summarise(rows):
    out = {}
    keys = sorted({(r['plant'], r['surface'], r['duties']) for r in rows})
    for plant, surface, duties in keys:
        rs = [r for r in rows if (r['plant'], r['surface'], r['duties']) == (plant, surface, duties)]
        block = {}
        for name in sorted({r['name'] for r in rs}):
            group = [r for r in rs if r['name'] == name]
            ends = {}
            for r in group:
                ends[r['end_reason'] or ('refused' if r.get('refused') else 'none')] = \
                    ends.get(r['end_reason'] or ('refused' if r.get('refused') else 'none'), 0) + 1
            over = [p['max_over_deg'] for r in group for p in r['pivot_truth'].values() if 'max_over_deg' in p]
            follow = [r['follow']['ok'] for r in group if r.get('follow')]
            block[name] = dict(
                runs=len(group), expect=group[0]['expect'], as_expected=sum(r['as_expected'] for r in group),
                completed=sum(r['completed'] for r in group), collisions=sum(r['collisions'] > 0 for r in group),
                moving_branch_contact_runs=sum(r['branch_contact_samples_moving'] > 0 for r in group),
                min_hard_m=min((r['min_hard_clearance_m'] for r in group if r['min_hard_clearance_m'] is not None),
                               default=None),
                min_branch_m=min((r['min_branch_clearance_m'] for r in group
                                  if r['min_branch_clearance_m'] is not None), default=None),
                max_overshoot_truth_deg=max(over) if over else None,
                plant_pivot_stalls=sum(r['plant_pivot_stalls'] for r in group),
                ends=dict(sorted(ends.items())),
                topups=sum(sum(1 for p in r['pivots'] if p.get('kind') == 'topup') for r in group),
                pauses_resumed=sum(sum(1 for p in r['pivots'] if p.get('kind') == 'resume') for r in group),
                presses_mean=round(statistics.fmean(r['presses'] for r in group), 1),
                time_s_completed=_pcts([r['time_s'] for r in group if r['completed']]),
                final_lateral_m=_pcts([abs(r['final']['lateral_next_m']) for r in group if r['completed']]),
                final_heading_deg=_pcts([abs(r['final']['heading_err_deg']) for r in group if r['completed']]),
                follow_ok='%d/%d' % (sum(follow), len(follow)) if follow else None,
                guard_violations=sum((r['guard'] or {}).get('violations', 0) for r in group),
                bridge_latches=sum(r['bridge']['client_state'] == 'LATCHED' for r in group))
        out['%s|%s|%s' % (plant, surface, duties)] = block
    return out


def _pcts(values):
    if not values:
        return None
    a = np.asarray(values, dtype=float)
    return [round(float(np.percentile(a, p)), 2) for p in (50, 90, 100)]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    p.add_argument('--out', required=True)
    p.add_argument('--seeds', type=int, nargs='+', default=[0, 1, 2])
    p.add_argument('--groups', nargs='+', default=['core', 'faults', 'stress', 'stall'], choices=sorted(GROUPS))
    p.add_argument('--scenarios', nargs='*')
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--no-follow-check', action='store_true')
    p.add_argument('--follow-not-ready', action='store_true',
                   help='also run the follow check after turn_done_not_ready (how the row follower copes)')
    args = p.parse_args(argv)
    if args.workers > 8:
        p.error('at most 8 workers on this host')
    hold = os.path.join(os.path.dirname(REPO), 'HOLD_MEASUREMENT')
    while os.path.exists(hold):
        print('HOLD_MEASUREMENT present, waiting 60 s', flush=True)
        time.sleep(60)
    follow = False if args.no_follow_check else ('all' if args.follow_not_ready else True)
    jobs = jobs_for(args.seeds, follow, args.groups)
    if args.scenarios:
        jobs = [j for j in jobs if j[0] in args.scenarios]
    rover_control_class()
    began = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for k, row in enumerate(pool.map(run_episode, jobs, chunksize=1)):
            rows.append(row)
            if (k + 1) % 10 == 0:
                print('%d/%d %.0f s' % (k + 1, len(jobs), time.time() - began), flush=True)
    result = dict(schema='ubts.row_executor.turn_sim_in_loop.v1',
                  created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), seeds=args.seeds,
                  groups=args.groups, plants={k: dict(overrides=[list(x) for x in v['overrides']],
                                                      stickslip=v['stickslip']) for k, v in PLANTS.items()},
                  sim_measured=SIM_MEASURED, pivot_duties=PIVOT_DUTIES, table=str(TABLE),
                  wall_s=round(time.time() - began, 1), summary=summarise(rows), runs=rows)
    with open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(result, fh, indent=1, default=str)
        fh.write('\n')
    print(json.dumps(result['summary'], indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
