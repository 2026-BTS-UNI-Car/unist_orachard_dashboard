"""Convert 2026-10-03 full extracts to the 2026-10-10 replay layout (read-only on the sources).

Input : orchard-realdata-audit/20261003-field/full-extracted/<rid>/{sensors.jsonl.gz, motor.jsonl}
Output: <out>/lidar/<rid>.scans.npz          (same fields as 20261010-field-manual/scripts/extract_scans.py)
        <out>/drive-extract/<rid>.drive.json.gz (imu rows + motor rows as extract_drive.py; no GNSS)
        <out>/lidar/<rid>.lidar_motion.npz   (offline point-to-line ICP reference: the unmodified
                                              20261010-field-manual/scripts/lidar_motion.py run(), imported by path)
The output holds raw ranges: keep it in the private gate folder, never in the repository.

Usage: python -I -B convert_1003.py --out D:/.../gate/data_1003 20261003T080351Z_both 20261003T080909Z_both
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import numpy as np

SRC = Path('D:/Develop/UBTS/orchard-realdata-audit/20261003-field/full-extracted')
LIDAR_MOTION = Path('D:/Develop/UBTS/orchard-realdata-audit/20261010-field-manual/scripts/lidar_motion.py')


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def stamp_ns(s):
    return int(s['sec']) * 1_000_000_000 + int(s['nanosec'])


def convert(rid, out):
    src = SRC / rid / 'sensors.jsonl.gz'
    bag, header, rows, imu = [], [], [], []
    geometry = None
    skipped = 0
    with gzip.open(src, 'rt', encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('{"topic":"/scan"'):
                r = json.loads(line)
                m = r['message']
                g = (m['angle_min'], m['angle_increment'], m['range_min'], m['range_max'], m.get('scan_time'),
                     m.get('time_increment'), len(m['ranges']), m['header']['frame_id'])
                if geometry is None:
                    geometry = g
                elif g[:4] != geometry[:4] or g[6] != geometry[6]:
                    skipped += 1
                    continue
                a = np.array([np.nan if v is None else v for v in m['ranges']], dtype=np.float64)
                ok = np.isfinite(a) & (a >= g[2]) & (a <= g[3])
                mm = np.zeros(a.shape, dtype=np.uint16)
                mm[ok] = np.clip(np.round(a[ok] * 1000.), 1, 65535).astype(np.uint16)
                rows.append(mm)
                bag.append(r['bag_time_ns'])
                header.append(stamp_ns(m['header']['stamp']))
            elif line.startswith('{"topic":"/imu/data_raw"'):
                r = json.loads(line)
                m = r['message']
                st = m['header']['stamp'] if 'header' in m else m['stamp']
                av, la, q = m['angular_velocity'], m['linear_acceleration'], m['orientation']
                imu.append([r['bag_time_ns'], stamp_ns(st), av['x'], av['y'], av['z'], la['x'], la['y'], la['z'],
                            q['x'], q['y'], q['z'], q['w']])
    (out / 'lidar').mkdir(parents=True, exist_ok=True)
    (out / 'drive-extract').mkdir(parents=True, exist_ok=True)
    npz = out / 'lidar' / f'{rid}.scans.npz'
    np.savez_compressed(npz, ranges_mm=np.stack(rows), bag_time_ns=np.array(bag, dtype=np.int64),
                        header_ns=np.array(header, dtype=np.int64), angle_min=geometry[0],
                        angle_increment=geometry[1], range_min=geometry[2], range_max=geometry[3],
                        scan_time=geometry[4] if geometry[4] is not None else np.nan,
                        time_increment=geometry[5] if geometry[5] is not None else np.nan,
                        frame_id=geometry[7], recording_id=rid, source_sha256=sha(src))
    motor = []
    with open(SRC / rid / 'motor.jsonl', encoding='utf-8') as fh:
        for line in fh:
            r = json.loads(line)
            p = r.get('pins') or {}
            st = r.get('status') or {}
            rq = r.get('requested') or {}
            motor.append({'received_at': r.get('received_at'), 'source_updated_at': r.get('source_updated_at'),
                          'source_stale': r.get('source_stale'), 'state': st.get('state'), 'moving': st.get('moving'),
                          'req_l': rq.get('left'), 'req_r': rq.get('right'),
                          'rep': {k: (p.get(k) or {}).get('reported_pwm')
                                  for k in ('D8', 'D5', 'D3', 'D0', 'D1', 'D2', 'D6', 'D7')}})
    imu.sort(key=lambda row: row[0])
    drive = dict(schema='ubts.private.drive_extract.v1', recording_id=rid,
                 source_sha256={'sensors.jsonl.gz': sha(src), 'motor.jsonl': sha(SRC / rid / 'motor.jsonl')},
                 imu_columns=['bag_time_ns', 'header_ns', 'gx', 'gy', 'gz', 'ax', 'ay', 'az', 'qx', 'qy', 'qz', 'qw'],
                 imu=imu, motor=motor, note='converted from the 10/03 full extract; no GNSS')
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(json.dumps(drive, separators=(',', ':')).encode())
    (out / 'drive-extract' / f'{rid}.drive.json.gz').write_bytes(buf.getvalue())
    spec = importlib.util.spec_from_file_location('lidar_motion_1010', LIDAR_MOTION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reference = module.run(npz)
    return dict(rid=rid, scans=len(rows), skipped_geometry=skipped, imu=len(imu), motor=len(motor),
                beams=int(geometry[6]), reference_matched_fraction=round(reference['matched_fraction'], 4),
                reference_inlier_p50=round(reference['inlier_p50'], 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('rids', nargs='+')
    args = ap.parse_args()
    for rid in args.rids:
        print(json.dumps(convert(rid, Path(args.out))), flush=True)


if __name__ == '__main__':
    main()
