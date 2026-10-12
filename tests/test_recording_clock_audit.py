"""Display-only clock sidecar from recorded receiver UTC; originals untouched."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from dashboard_server.recording_clock import display_clock, timestamp
from scripts.recording_clock_audit import audit, write

OFFSET = 10594.97
HOST_START = '2026-10-10T03:59:52.649163+00:00'


def pvt(epoch, received):
    whole = int(epoch)
    return dict(schema='orchard.gps.v1', protocol='UBX_NAV_PVT',
                gps_time=datetime.fromtimestamp(whole, timezone.utc).isoformat(),
                gps_time_fraction_ns=int(round((epoch - whole) * 1e9)), gps_time_valid_date=True,
                gps_time_valid_time=True, gps_time_fully_resolved=True, gps_time_accuracy_s=5e-08,
                received_at=received)


def make(root, *, epochs=8, snapshot='host_unverified', jump_at=None):
    path = Path(root) / '20261010T035952Z_both'
    (path / 'mapping_source').mkdir(parents=True)
    (path / 'metadata.json').write_text(json.dumps(dict(
        id=path.name, started_at=HOST_START,
        clock_quality_snapshot=dict(clock_quality=snapshot, reason='receiver_stale'))))
    host0 = timestamp(HOST_START) + 0.4
    with gzip.open(path / 'mapping_source/sensors.jsonl.gz', 'wt') as stream:
        for i in range(epochs):
            received = host0 + i * 4 + (30 if jump_at is not None and i >= jump_at else 0)
            sample = pvt(host0 + i * 4 + OFFSET + (0.01 if i % 2 else 0), received)
            stream.write(json.dumps(dict(topic='/gps/data', message=dict(data=json.dumps(sample)),
                                         bag_time_ns=int(received * 1e9))) + '\n')
            stream.write(json.dumps(dict(topic='/imu/data_raw', message={}, bag_time_ns=1)) + '\n')
    with (path / 'gps.jsonl').open('w') as stream:
        stream.write(json.dumps(dict(t_s=0.3, sample=dict(pvt(host0 + OFFSET, host0), protocol='UBX_NAV_HPPOSLLH'))) + '\n')
    return path


def digests(path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}


class RecordingClockAuditTests(unittest.TestCase):
    def test_sidecar_is_display_only_and_accepted_by_dashboard(self):
        with tempfile.TemporaryDirectory() as root:
            path = make(root)
            before = digests(path)
            result = audit(path)
            self.assertTrue(result['ok'], result)
            self.assertAlmostEqual(result['value']['offset_s'], OFFSET + 0.005, places=3)
            self.assertEqual(result['value']['evidence']['epochs'], 8)
            self.assertTrue(write(path, result['value'])['ok'])
            after = digests(path)
            self.assertEqual({k: v for k, v in after.items() if k != 'recording_clock.json'}, before)
            shown = display_clock(path, json.loads((path / 'metadata.json').read_text()))
            self.assertEqual(shown['clock_quality'], 'gnss_estimated')
            self.assertAlmostEqual(timestamp(shown['display_started_at']) - timestamp(HOST_START),
                                   result['value']['offset_s'], places=3)
            # Never overwritten.
            sidecar = (path / 'recording_clock.json').read_bytes()
            again = write(path, dict(result['value'], offset_s=1.0))
            self.assertEqual(again['reason'], 'recording_clock_json_exists_not_overwritten')
            self.assertEqual((path / 'recording_clock.json').read_bytes(), sidecar)
            self.assertEqual(sorted(p.name for p in path.iterdir() if p.name.startswith('.')), [])

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(audit(make(root, snapshot='gnss_receiver_estimated'))['reason'],
                             'start_snapshot_already_gnss_estimated')
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(audit(make(root, epochs=3))['reason'], 'insufficient_valid_receiver_epochs')
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(audit(make(root, jump_at=4))['reason'], 'host_clock_jump_inside_recording')


if __name__ == '__main__':
    unittest.main()
