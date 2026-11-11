"""Open-loop replay of the REAL 2026-10-10 pivots through TurnAssist's detection logic (read-only, no output).

V-T2 of TURN_DESIGN.txt: what would the assist have detected on the human's pivots?
For every pivot command segment of pivot_maneuvers_1010.json (opposite-sign request held across >= 1
MCU status; person-handled 035952 spans excluded), the real scans and IMU of a window around it go
through the SAME code as the live turn: core.ScanAdapter (+179.08 deg, LOO body mask) and
core.ImuYawRate -> a fresh vendored SensorEstimator (scan stamp = receipt - 0.08 s) and TurnAssist's
own gyro integral (bias from the still window before the segment) -> turn_assist.PivotTracker with
the human's request as a fixed duty at the cap (the human used no ladder): confirmation time,
'pivot_stall' / 'pivot_too_slow' / 'pivot_wrong_direction' / 'pivot_rate_excess', the same-stamp
gyro vs scan-matching heading difference (warn 5 / abort 10 deg), IMU receipt gaps (abort 0.15 s) and,
where the release was measured, the coast predicted by the initial coast time vs the measured coast.
The request start is not resolved by the ~2 Hz status: it is taken as the first status with the pivot
request - 0.25 s; a reversal from forward driving adds the MCU fade (|d|/100) + BREAK 0.12 s to the
ramp time. Offline labels (rotating / stalled from settled IMU+LiDAR) come from the maneuvers file.
Nothing is commanded; the records are only read.

Usage: python -B tools/row_executor/turn_real_replay.py --maneuvers <pivot_maneuvers_1010.json>
       --masks <gate/masks> --out <file.json> [--workers 6]
"""
import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402

SCAN_LATENCY_S = .08
TICK = .05
PRE_S = 3.
POST_S = 2.5


def mask_segments(mask):
    out, k = [], 0
    if mask is None:
        return out
    while k < len(mask):
        if mask[k] > 0:
            j = k
            while j + 1 < len(mask) and mask[j + 1] == mask[k]:
                j += 1
            out.append((k, j, float(mask[k])))
            k = j + 1
        else:
            k += 1
    return out


def run_record(job):
    rid, segments, masks_dir = job
    from row_executor import config as XC
    from row_executor import core as X
    from row_executor.turn_assist import PivotTracker, TurnSettings
    import field_records as F
    import body_mask_fit as M
    E, _ = X.load_vendor()
    st = TurnSettings.from_config(XC.ExecutorConfig())
    rec = F.load_record(rid)
    mask, _ = M.load_mask(Path(masks_dir) / ('mask_loo_%s.json' % rid[9:15]))
    adapter = X.ScanAdapter(yaw_offset_deg=179.08, body_mask=mask_segments(mask))
    imu = rec['imu']
    order = np.argsort(imu[:, 0], kind='stable')
    t_imu = imu[order, 0] / 1e9
    yaw_conv = X.ImuYawRate()
    rates = np.full(len(t_imu), np.nan)
    accs = np.zeros((len(t_imu), 3))
    for n in range(len(t_imu)):
        r = imu[order[n]]
        out = yaw_conv.update(t_imu[n], r[2:5], r[5:8])
        if out is not None:
            rates[n] = out[0]
            accs[n] = out[1]
    ts = rec['t_scan']
    rec_start = float(rec['motor'][0]['received_at']) - .3
    ok_rates = np.abs(rates) < math.radians(1.)
    record_bias = float(np.nanmedian(rates[ok_rates])) if ok_rates.any() else 0.
    results = []
    for seg in segments:
        t_first = rec_start + seg['t_rel']
        t_cmd = t_first - .25
        t_end = t_first + seg['span_s']
        level = int(seg['level'])
        s = int(seg['direction'])
        ramp = level / 100. + (0. if seg.get('from_rest') else 1.12)
        # bias: still window just before the request, else the record's still median
        pre = (t_imu >= t_cmd - 2.3) & (t_imu < t_cmd - .3) & np.isfinite(rates)
        bias, bias_src = record_bias, 'record'
        if pre.sum() >= 20 and np.all(np.abs(np.degrees(rates[pre] - np.median(rates[pre]))) < 3.) and \
                abs(math.degrees(np.median(rates[pre]))) < .5:
            bias, bias_src = float(np.mean(rates[pre])), 'pre_window'
        est = E.SensorEstimator(E.EstimatorConfig())
        w0, w1 = t_cmd - PRE_S, t_end + POST_S
        si = np.nonzero((ts >= w0) & (ts <= w1))[0]
        ii = np.nonzero((t_imu >= w0) & (t_imu <= w1) & np.isfinite(rates))[0]
        events = []
        for k in si:
            events.append((ts[k], 1, int(k)))
        for n in ii:
            events.append((t_imu[n], 0, int(n)))
        events.sort()
        tracker = PivotTracker(st, s, t_cmd)
        g = 0.
        rate_s = 0.
        g_hist = []
        last_imu = None
        lidar_cont = lidar_prev = None
        lidar_at_cmd = None
        g_at_cmd = None
        max_gap = 0.
        disagree = []
        first_event = None
        t_confirm = None
        peak = 0.
        next_tick = t_cmd
        lidar_valid = False

        def g_at(stamp):
            if not g_hist:
                return g
            if stamp >= g_hist[-1][0]:
                return g
            j = int(np.searchsorted([h[0] for h in g_hist], stamp))
            if j <= 0:
                return g_hist[0][1]
            t0, g0 = g_hist[j - 1]
            t1, g1 = g_hist[j]
            return g0 + (g1 - g0) * (stamp - t0) / max(t1 - t0, 1e-9)

        for t, kind, idx in events:
            while next_tick <= t and next_tick <= t_end + POST_S:
                if next_tick >= t_cmd and g_at_cmd is None:
                    g_at_cmd, lidar_at_cmd = g, lidar_cont
                if g_at_cmd is not None and first_event is None and next_tick <= t_end:
                    turned = s * math.degrees(g - g_at_cmd)
                    lidar_turned = None
                    if lidar_valid and lidar_at_cmd is not None:
                        lidar_turned = s * math.degrees(lidar_cont - lidar_at_cmd)
                    ev = tracker.update(next_tick, math.degrees(rate_s), turned, lidar_turned, level, t_cmd, ramp,
                                        True)
                    peak = max(peak, abs(math.degrees(rate_s)))
                    if tracker.rotating and t_confirm is None:
                        t_confirm = tracker.t_rotating
                    if ev is not None:
                        first_event = (ev, round(next_tick - t_first, 2), tracker.stall_kind)
                next_tick += TICK
            if kind == 0:
                if last_imu is not None:
                    dt = t - last_imu
                    if t_cmd <= t <= t_end + 1.:
                        max_gap = max(max_gap, dt)
                    step = min(.2, dt)
                else:
                    step = .05
                last_imu = t
                r = rates[idx] - bias
                g += r * step
                rate_s += (r - rate_s) * (1. - math.exp(-step / .25))
                g_hist.append((t, g))
                if len(g_hist) > 400:
                    g_hist = g_hist[-300:]
                est.on_imu(float(t), float(rates[idx]), tuple(accs[idx]))
            else:
                try:
                    rr, a0, inc, rmax, _ = adapter.convert(rec['ranges_mm'][idx].astype(np.float64) / 1000.,
                                                           rec['angle_min'], rec['increment'], rec['range_max'])
                except X.ScanRejected:
                    continue
                stamp = float(t - SCAN_LATENCY_S)
                est.on_scan(stamp, rr, a0, inc, rmax)
                heading = float(est.pose[2])
                lidar_cont = heading if lidar_cont is None else lidar_cont + X.wrap(heading - lidar_prev)
                lidar_prev = heading
                lidar_valid = bool(est.odom_valid)
                if g_at_cmd is not None and lidar_at_cmd is not None and lidar_valid and t <= t_end + POST_S:
                    d = math.degrees((lidar_cont - lidar_at_cmd) - (g_at(stamp) - g_at_cmd))
                    disagree.append(abs(d))
        turned_total = None if g_at_cmd is None else s * math.degrees(g - g_at_cmd)
        lidar_total = None if lidar_at_cmd is None or lidar_cont is None else s * math.degrees(lidar_cont - lidar_at_cmd)
        out = dict(rid=rid[9:15], t_rel=seg['t_rel'], level=level, direction=s, span_s=seg['span_s'],
                   from_rest=bool(seg.get('from_rest')), settled_s=seg.get('settled_s'),
                   offline_rotating=seg.get('rotating'), offline_stalled=seg.get('stalled'),
                   offline_lidar_rot_deg=seg.get('lidar_rot_deg'), bias_src=bias_src,
                   bias_deg_s=round(math.degrees(bias), 3),
                   confirmed_after_first_status_s=None if t_confirm is None else round(t_confirm - t_first, 2),
                   event=None if first_event is None else first_event[0],
                   event_t_s=None if first_event is None else first_event[1],
                   stall_kind=None if first_event is None else first_event[2],
                   peak_rate_deg_s=round(peak, 1), max_imu_gap_s=round(max_gap, 3),
                   yaw_disagree_max_deg=round(max(disagree), 2) if disagree else None,
                   yaw_disagree_scans=len(disagree),
                   gyro_turned_deg=None if turned_total is None else round(turned_total, 1),
                   lidar_turned_deg=None if lidar_total is None else round(lidar_total, 1),
                   stall_decidable=bool(seg['span_s'] + .25 >= ramp + st.grace_s))
        rel = seg.get('release')
        if rel and rel.get('rate_dps'):
            coast_pred = abs(rel['rate_dps']) * st.coast_initial(level)
            out['release'] = dict(rate_deg_s=rel['rate_dps'], measured_coast_gyro_deg=rel.get('coast_gyro_deg'),
                                  measured_coast_lidar_deg=rel.get('coast_lidar_deg'),
                                  predicted_coast_deg=round(coast_pred, 2),
                                  coast_time_equiv_s=rel.get('coast_time_equiv_s'))
        results.append(out)
    return dict(rid=rid, segments=results, record_bias_deg_s=round(math.degrees(record_bias), 4))


def summarise(rows):
    def pct(v, ps=(50, 90, 100)):
        v = [x for x in v if x is not None]
        return [round(float(np.percentile(v, p)), 2) for p in ps] if v else None

    rot = [r for r in rows if r['offline_rotating'] and (r['settled_s'] or 0) >= 1.]
    stl = [r for r in rows if r['offline_stalled']]
    short = [r for r in rows if not (r['settled_s'] or 0) >= 1.]
    out = dict(
        segments=len(rows),
        rotating_settled_ge_1s=dict(
            n=len(rot), confirmed=sum(r['confirmed_after_first_status_s'] is not None for r in rot),
            confirm_after_first_status_s=pct([r['confirmed_after_first_status_s'] for r in rot]),
            false_stall=sum(r['event'] == 'pivot_stall' for r in rot),
            too_slow=sum(r['event'] == 'pivot_too_slow' for r in rot),
            other_events={e: sum(r['event'] == e for r in rot) for e in sorted({r['event'] for r in rot if r['event']})}),
        stalled=dict(n=len(stl), decidable=sum(r['stall_decidable'] for r in stl),
                     declared_stall=sum(r['event'] == 'pivot_stall' for r in stl),
                     confirmed_rotation=sum(r['confirmed_after_first_status_s'] is not None for r in stl),
                     rows=stl),
        short_or_unsettled=dict(n=len(short), events={e: sum(r['event'] == e for r in short)
                                                      for e in sorted({r['event'] for r in short if r['event']})}),
        all_events={e: sum(r['event'] == e for r in rows) for e in sorted({r['event'] for r in rows if r['event']})},
        yaw_disagree_deg_p50_p90_max=pct([r['yaw_disagree_max_deg'] for r in rows]),
        yaw_disagree_over_warn=sum((r['yaw_disagree_max_deg'] or 0) > 5. for r in rows),
        yaw_disagree_over_abort=[dict(rid=r['rid'], t_rel=r['t_rel'], max_deg=r['yaw_disagree_max_deg'],
                                      span_s=r['span_s'], level=r['level']) for r in rows
                                 if (r['yaw_disagree_max_deg'] or 0) > 10.],
        imu_gap_s_p50_p90_max=pct([r['max_imu_gap_s'] for r in rows]),
        imu_gap_over_abort=[dict(rid=r['rid'], t_rel=r['t_rel'], gap_s=r['max_imu_gap_s']) for r in rows
                            if r['max_imu_gap_s'] > .15],
        peak_rate_deg_s_max=max((r['peak_rate_deg_s'] for r in rows), default=None),
        releases=[dict(rid=r['rid'], t_rel=r['t_rel'], **r['release']) for r in rows if r.get('release')])
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    p.add_argument('--maneuvers', required=True)
    p.add_argument('--masks', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--workers', type=int, default=6)
    args = p.parse_args(argv)
    if args.workers > 8:
        p.error('at most 8 workers on this host')
    data = json.load(open(args.maneuvers, encoding='utf-8'))
    jobs = []
    for rec in data['records']:
        segs = [sg for sg in rec['segments'] if not sg.get('human_suspect')]
        if segs:
            jobs.append((rec['rid'], segs, args.masks))
    began = time.time()
    with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(jobs)))) as pool:
        results = list(pool.map(run_record, jobs))
    rows = [r for res in results for r in res['segments']]
    out = dict(schema='ubts.row_executor.turn_real_replay.v1', method=__doc__.split('\n\n')[0],
               created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), wall_s=round(time.time() - began, 1),
               summary=summarise(rows), records=results)
    with open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, indent=1, allow_nan=False, default=str)
        fh.write('\n')
    print(json.dumps({k: v for k, v in out['summary'].items() if k != 'stalled'}, indent=1, default=str))
    print(json.dumps({k: v for k, v in out['summary']['stalled'].items() if k != 'rows'}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
